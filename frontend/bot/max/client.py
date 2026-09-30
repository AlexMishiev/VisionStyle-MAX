"""Клиент для работы с API MAX."""
import asyncio
import logging
import socket
import ssl
import time
from html.parser import HTMLParser
from pathlib import Path

import httpx

from bot.common.logger import get_logger

logger = get_logger(__name__)

UPDATE_TYPES = ["message_created", "message_callback", "bot_started"]
MAX_IMAGE_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_CA_FILE = Path(__file__).resolve().parents[1] / "assets" / "tls" / "russian_trusted_root_ca.pem"
MAX_API_HOSTS = {"platform-api2.max.ru", "platform-api.max.ru"}
# Паузы между повторами, пока MAX обрабатывает загруженный файл
NOT_READY_DELAYS = (1, 2, 4, 8, 8, 8)
RATE_LIMIT_DELAYS = (1, 2, 4)
LOGGED_PARAMS = ("chat_id", "message_id", "callback_id", "type", "user_id")


class MaxApiError(Exception):
    def __init__(self, status: int | None, code: str = "request.failed", detail: str = ""):
        self.status = status
        self.code = code
        # ссылки и тело ответа сюда не кладём, там могут быть токены
        self.detail = detail
        super().__init__(f"MAX API: {status or 'network'} ({code})" + (f": {detail}" if detail else ""))


def transport_error(exc: httpx.HTTPError) -> MaxApiError:
    """Превращает сетевую ошибку httpx в понятную MaxApiError."""
    cause = exc
    visited = set()
    while cause is not None and id(cause) not in visited:
        visited.add(id(cause))
        if isinstance(cause, ssl.SSLCertVerificationError):
            return MaxApiError(None, "tls.verify.failed",
                               "Не удалось проверить сертификат MAX. Проверьте MAX_TLS_CA_FILE; "
                               "для официального API сертификат Минцифры поставляется вместе с ботом.")
        if isinstance(cause, socket.gaierror):
            return MaxApiError(None, "dns.failed", "Не удалось определить адрес MAX. Проверьте DNS и MAX_API_BASE_URL.")
        cause = cause.__cause__ or cause.__context__
    if isinstance(exc, httpx.TimeoutException):
        return MaxApiError(None, "request.timeout", "Истекло время ожидания ответа MAX. Проверьте подключение к сети.")
    if isinstance(exc, httpx.ProxyError):
        return MaxApiError(None, "proxy.failed", "Не удалось подключиться через прокси. Проверьте настройки прокси.")
    return MaxApiError(None, "connection.failed",
                       f"Не удалось подключиться к MAX ({type(exc).__name__}). Проверьте сеть, VPN и прокси.")


def tls_context(base_url: str, ca_file: Path | None = None) -> ssl.SSLContext:
    context = ssl.create_default_context()
    # для официального API MAX подключаем сертификат Минцифры из assets
    if ca_file is None and httpx.URL(base_url).host in MAX_API_HOSTS:
        ca_file = DEFAULT_MAX_CA_FILE
    if ca_file is not None:
        try:
            context.load_verify_locations(cafile=str(ca_file))
        except (OSError, ValueError):
            raise MaxApiError(None, "tls.ca.invalid",
                               f"Не удалось загрузить сертификат MAX: {ca_file}. "
                               "Проверьте PEM-файл и путь MAX_TLS_CA_FILE.") from None
    return context


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


class MaxClient:
    def __init__(self, token: str, base_url: str = "https://platform-api2.max.ru",
                 ca_file: Path | None = None, timeout: float = 60):
        if httpx.URL(base_url).scheme != "https":
            raise ValueError("MAX_API_BASE_URL должен использовать https://")
        context = tls_context(base_url, ca_file)
        self.http = httpx.AsyncClient(base_url=base_url, headers={"Authorization": token},
                                     verify=context, timeout=max(timeout, 40))
        # отдельный клиент для файлов, чтобы токен бота не уходил на чужие адреса
        self.media = httpx.AsyncClient(verify=context, timeout=timeout, follow_redirects=True)
        self._request_lock = asyncio.Lock()
        self._next_request = 0.0
        self._send_lock = asyncio.Lock()
        self._next_send: dict[int, float] = {}

    async def close(self):
        await self.http.aclose()
        await self.media.aclose()

    @staticmethod
    def _describe(method: str, path: str, kwargs: dict) -> str:
        """Короткое описание запроса для лога (без токенов и текста сообщений)."""
        params = kwargs.get("params") or {}
        parts = [f"{method} {path}"]
        parts += [f"{key}={params[key]}" for key in LOGGED_PARAMS if key in params]
        body = kwargs.get("json")
        if isinstance(body, dict):
            if "text" in body:
                parts.append(f"text={len(body.get('text') or '')} симв.")
            if body.get("attachments"):
                parts.append("вложения=" + ",".join(str(a.get("type")) for a in body["attachments"]))
            if "notification" in body:
                parts.append("notification")
        return " ".join(parts)

    async def request(self, method: str, path: str, **kwargs) -> dict:
        described = self._describe(method, path, kwargs)
        # /updates вызывается постоянно, поэтому успешные ответы пишем только в DEBUG
        success_level = logging.DEBUG if path == "/updates" else logging.INFO
        not_ready = rate_limited = 0
        while True:
            async with self._request_lock:
                await asyncio.sleep(max(0, self._next_request - time.monotonic()))
                self._next_request = time.monotonic() + 0.04
            started = time.monotonic()
            try:
                response = await self.http.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                error = transport_error(exc)
                logger.warning("MAX API %s → сеть: %s (%.0f мс)", described, error, (time.monotonic() - started) * 1000)
                raise error from None
            elapsed = (time.monotonic() - started) * 1000
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            code = payload.get("code", "request.failed")
            message = str(payload.get("message") or "")[:300]
            if code == "attachment.not.ready" and not_ready < len(NOT_READY_DELAYS):
                delay = NOT_READY_DELAYS[not_ready]
                not_ready += 1
                logger.info("MAX API %s → вложение ещё обрабатывается, повтор %s через %s с",
                            described, not_ready, delay)
                await asyncio.sleep(delay)
                continue
            if response.status_code == 429 and rate_limited < len(RATE_LIMIT_DELAYS):
                delay = RATE_LIMIT_DELAYS[rate_limited]
                rate_limited += 1
                logger.warning("MAX API %s → 429, повтор %s через %s с", described, rate_limited, delay)
                await asyncio.sleep(delay)
                continue
            if response.is_error or payload.get("success") is False:
                logger.warning("MAX API %s → %s %s%s (%.0f мс)", described, response.status_code, code,
                               f": {message}" if message else "", elapsed)
                raise MaxApiError(response.status_code, code, message)
            logger.log(success_level, "MAX API %s → %s (%.0f мс)", described, response.status_code, elapsed)
            return payload

    async def get_updates(self, marker: int | None = None) -> dict:
        params = {"timeout": 30, "limit": 100, "types": ",".join(UPDATE_TYPES)}
        if marker is not None:
            params["marker"] = marker
        return await self.request("GET", "/updates", params=params)

    async def send_message(self, chat_id: int, text: str, *, keyboard: dict | None = None,
                           image: dict | None = None, attachments: list[dict] | None = None) -> str | None:
        attachments = list(attachments) if attachments is not None else [a for a in (image, keyboard) if a]
        chunks = [text]
        format_ = "html"
        if len(text) > 4000:
            parser = _PlainText()
            parser.feed(text)
            plain = "".join(parser.parts)
            chunks = [plain[i:i + 4000] for i in range(0, len(plain), 4000)]
            format_ = None
        mid = None
        for index, chunk in enumerate(chunks):
            body = {"text": chunk} if chunk else {}  # вложение можно отправить без текста
            if format_ and chunk:
                body["format"] = format_
            if index == len(chunks) - 1 and attachments:
                body["attachments"] = attachments
            async with self._send_lock:
                delay = max(0, self._next_send.get(chat_id, 0) - time.monotonic())
                self._next_send[chat_id] = time.monotonic() + delay + 0.55
            await asyncio.sleep(delay)
            result = await self.request("POST", "/messages", params={"chat_id": chat_id}, json=body)
            mid = (result.get("message", {}).get("body") or {}).get("mid")
        return mid

    async def answer_callback(self, callback_id: str, notification: str):
        """Показывает уведомление после нажатия кнопки. Пустой ответ MAX не принимает."""
        await self.request("POST", "/answers", params={"callback_id": callback_id},
                           json={"notification": notification})

    async def delete_message(self, message_id: str):
        await self.request("DELETE", "/messages", params={"message_id": message_id})

    async def upload_image(self, data: bytes, filename: str = "image.jpg") -> dict:
        token = await self._upload("image", data, filename, "image/jpeg")
        return {"type": "image", "payload": {"token": token}}

    async def upload_file(self, data: bytes, filename: str, content_type: str = "application/pdf") -> dict:
        token = await self._upload("file", data, filename, content_type)
        return {"type": "file", "payload": {"token": token}}

    async def _upload(self, kind: str, data: bytes, filename: str, content_type: str) -> str:
        result = await self.request("POST", "/uploads", params={"type": kind})
        url = result.get("url")
        if not url or httpx.URL(url).scheme != "https":
            logger.warning("MAX загрузка %s: /uploads не вернул https URL (поля ответа: %s)",
                           kind, sorted(result))
            raise MaxApiError(None, "upload.url.invalid")
        host = httpx.URL(url).host  # в лог пишем только хост, в ссылке может быть подпись
        started = time.monotonic()
        try:
            response = await self.media.post(url, files={"data": (filename, data, content_type)})
            elapsed = (time.monotonic() - started) * 1000
            if response.is_error:
                logger.warning("MAX загрузка %s «%s» (%s байт) на %s → %s (%.0f мс)",
                               kind, filename, len(data), host, response.status_code, elapsed)
                raise MaxApiError(response.status_code, "upload.failed")
            payload = response.json()
        except httpx.HTTPError as exc:
            error = transport_error(exc)
            logger.warning("MAX загрузка %s «%s» на %s → сеть: %s", kind, filename, host, error)
            raise error from None
        except ValueError:
            logger.warning("MAX загрузка %s «%s» на %s → ответ не JSON", kind, filename, host)
            raise MaxApiError(None, "upload.failed") from None
        if not isinstance(payload, dict):
            payload = {}
        token = payload.get("token")
        if not token:
            photos = payload.get("photos") or {}
            token = next((photo.get("token") for photo in photos.values() if photo.get("token")), None)
        if not token:
            token = result.get("token")  # иногда MAX возвращает token сразу в ответе /uploads
        logger.info("MAX загрузка %s «%s» (%s байт) на %s → %s (%.0f мс), поля ответа: %s, token: %s",
                    kind, filename, len(data), host, response.status_code, elapsed,
                    sorted(payload), "есть" if token else "НЕТ")
        if not token:
            raise MaxApiError(None, "upload.token.missing")
        return token

    async def send_photo(self, chat_id: int, data: bytes, caption: str, *, keyboard: dict | None = None):
        attachment = await self.upload_image(data)
        return await self.send_message(chat_id, caption, keyboard=keyboard, image=attachment)

    async def send_media(self, chat_id: int, text: str, images: list[dict], *, keyboard: dict | None = None):
        attachments = list(images)
        if keyboard:
            attachments.append(keyboard)
        return await self.send_message(chat_id, text, attachments=attachments)

    async def download_image(self, url: str) -> bytes:
        if httpx.URL(url).scheme != "https":
            raise MaxApiError(None, "image.url.invalid")
        try:
            async with self.media.stream("GET", url) as response:
                if response.is_error:
                    raise MaxApiError(response.status_code, "image.download.failed")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise MaxApiError(None, "image.too.large")
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.HTTPError as exc:
            raise transport_error(exc) from None
