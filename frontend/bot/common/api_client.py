"""Клиент для backend API. Используется обоими ботами."""
import asyncio
import logging
import ssl
import time
from math import ceil
from pathlib import Path
from typing import Any, Optional

import httpx

from bot.common.config import CommonSettings
from bot.common.logger import get_logger

logger = get_logger(__name__)

USER_ID_HEADER = "X-Telegram-User-Id"

# обновляем токен немного заранее
TOKEN_EXPIRY_MARGIN_SECONDS = 30
# сколько ждать, если API временно не пускает (429/503)
MAX_RETRY_AFTER_SECONDS = 900
DEFAULT_RETRY_AFTER_SECONDS = 5
# паузы между попытками подключиться к backend при запуске
STARTUP_RETRY_MIN_SECONDS = 2.0
STARTUP_RETRY_MAX_SECONDS = 60.0


def _is_tls_verify_error(exc: BaseException) -> bool:
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, ssl.SSLCertVerificationError):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


class ApiError(Exception):
    """Ошибка от backend."""

    def __init__(self, status_code: Optional[int], detail: Any):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}" if status_code is not None else str(detail))

    @property
    def is_not_found(self) -> bool:
        return self.status_code == 404

    @property
    def is_bad_request(self) -> bool:
        return self.status_code == 400


class RateLimited(ApiError):
    """API временно не даёт войти (ответ 429 или 503)."""

    def __init__(self, status_code: Optional[int], retry_after: float, detail: Any = None):
        self.retry_after = max(float(retry_after), 1.0)
        super().__init__(status_code, detail or f"вход в API временно ограничен, повтор через {ceil(self.retry_after)} с")


def _retry_after(response: httpx.Response) -> float:
    try:
        value = float(response.headers.get("Retry-After", ""))
    except ValueError:
        value = DEFAULT_RETRY_AFTER_SECONDS
    return min(max(value, 1.0), MAX_RETRY_AFTER_SECONDS)


def _extract_detail(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict):
        return payload.get("detail", payload)
    return payload


class VisionStyleApi:
    """Клиент backend API: получает токены и отправляет запросы."""

    def __init__(self, base_url: str, username: str, password: str, timeout: float = 60.0,
                 tls_ca_file: Optional[Path] = None, *, platform: str = "telegram"):
        if platform not in {"telegram", "max"}:
            raise ValueError("Unknown messenger platform")
        self.platform = platform
        self.user_id_header = USER_ID_HEADER if platform == "telegram" else "X-Max-User-Id"
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.tls_ca_file = tls_ca_file
        if httpx.URL(self.base_url).scheme != "https":
            raise ValueError("API_BASE_URL должен использовать https://")

        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expires_at: float = 0.0
        self._blocked_until: float = 0.0  # до этого времени не пытаемся войти заново
        self._token_lock = asyncio.Lock()
        self._client: Optional[httpx.AsyncClient] = None

    @classmethod
    def from_settings(cls, settings: CommonSettings, *, platform: str = "telegram") -> "VisionStyleApi":
        settings.validate_api_credentials()
        return cls(
            base_url=settings.api_base_url,
            username=settings.api_username,
            password=settings.api_password,
            timeout=settings.request_timeout,
            tls_ca_file=settings.api_tls_ca_file,
            platform=platform,
        )

    # HTTP

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            try:
                # если файл не указан, используются системные сертификаты
                cafile = str(self.tls_ca_file) if self.tls_ca_file else None
                context = ssl.create_default_context(cafile=cafile)
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    f"Не удалось загрузить сертификат API: {self.tls_ca_file}. "
                    "Скопируйте cert.pem backend и укажите путь в API_TLS_CA_FILE "
                    "(пусто — системные CA)."
                ) from exc
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, verify=context)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # Токены

    async def _get_token(self) -> None:
        """Обновляет токены. Если refresh не сработал, входит по логину и паролю."""
        wait = self._blocked_until - time.monotonic()
        if wait > 0:
            raise RateLimited(None, wait)
        logger.info("Получение токенов API")

        response = None
        if self.refresh_token:
            response = await self.client.post("/auth/refresh", json={"refresh_token": self.refresh_token})
            if response.status_code == 401:
                # refresh-токен больше не действует, входим заново
                logger.warning("Refresh-токен больше не действует, выполняю вход по паролю")
                self.access_token = None
                self.refresh_token = None
                response = None
        if response is None:
            response = await self.client.post(
                "/auth/login",
                data={"username": self.username, "password": self.password},
            )
            if response.status_code == 401:
                logger.error("API отклонил вход: проверьте API_USERNAME / API_PASSWORD бота")

        if response.status_code in (429, 503):
            retry_after = _retry_after(response)
            self._blocked_until = time.monotonic() + retry_after
            logger.warning("API временно ограничил вход (%s), повтор не раньше чем через %.0f с",
                           response.status_code, retry_after)
            raise RateLimited(response.status_code, retry_after)
        if response.status_code >= 400:
            raise ApiError(response.status_code, f"Не удалось получить token: {_extract_detail(response)}")

        payload = response.json()
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        expires_in = int(payload.get("expires_in", 300))
        if not access_token or not refresh_token:
            raise ApiError(None, "API не вернул пару токенов")

        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_expires_at = time.monotonic() + max(expires_in - TOKEN_EXPIRY_MARGIN_SECONDS, 0)
        logger.info("Access token успешно получен")

    async def _ensure_token(self) -> None:
        async with self._token_lock:
            if not self.access_token or time.monotonic() >= self.token_expires_at:
                await self._get_token()

    async def _request(self, method: str, path: str, *, user_id: Optional[int] = None,
                       retry_on_401: bool = True, **kwargs) -> Any:
        await self._ensure_token()

        headers = dict(kwargs.pop("headers", None) or {})
        if user_id is not None:
            headers[self.user_id_header] = str(user_id)

        request_token = self.access_token
        headers["Authorization"] = f"Bearer {request_token}"
        started = time.monotonic()
        who = f"user={self.platform}:{user_id}" if user_id is not None else "служебный"
        try:
            response = await self.client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            logger.warning("Backend %s %s %s → сеть: %s (%.0f мс)", method, path, who,
                           type(exc).__name__, (time.monotonic() - started) * 1000)
            raise

        if response.status_code == 401 and retry_on_401:
            logger.warning("Получен 401, обновляю token и повторяю запрос")
            async with self._token_lock:
                # токен мог уже обновить другой запрос
                if self.access_token == request_token:
                    await self._get_token()
            headers["Authorization"] = f"Bearer {self.access_token}"
            response = await self.client.request(method, path, headers=headers, **kwargs)

        elapsed = (time.monotonic() - started) * 1000
        if response.status_code >= 400:
            detail = _extract_detail(response)
            level = logging.INFO if response.status_code == 404 else logging.WARNING  # 404 - пользователь не зарегистрирован
            logger.log(level, "Backend %s %s %s → %s: %s (%.0f мс)", method, path, who,
                       response.status_code, str(detail)[:300], elapsed)
            raise ApiError(response.status_code, detail)
        logger.info("Backend %s %s %s → %s (%.0f мс)", method, path, who, response.status_code, elapsed)

        if "application/json" in response.headers.get("content-type", ""):
            return response.json()
        return response.content

    # Методы API

    async def wait_until_ready(self, *, sleep=asyncio.sleep) -> None:
        """Ждёт, пока backend станет доступен, и проверяет учётную запись бота.

        При ошибке сертификата сразу выдаёт ошибку, ждать тут бесполезно.
        """
        delay = STARTUP_RETRY_MIN_SECONDS
        while True:
            try:
                await self.validate_bot_account()
                return
            except RateLimited as exc:
                logger.warning("API пока не пускает бота, жду %.0f с", exc.retry_after)
                await sleep(exc.retry_after)
                self._blocked_until = 0.0
            except httpx.TransportError as exc:
                if _is_tls_verify_error(exc):
                    raise RuntimeError(
                        f"Сертификат API {self.base_url} не прошёл проверку. Проверьте API_TLS_CA_FILE "
                        "(копия актуального cert.pem backend) и что адрес есть в TLS_HOSTS backend."
                    ) from exc
                logger.warning("API %s недоступен (%s), повтор через %.0f с",
                               self.base_url, type(exc).__name__, delay)
                await sleep(delay)
                delay = min(delay * 2, STARTUP_RETRY_MAX_SECONDS)

    async def validate_bot_account(self) -> None:
        """Проверяет, что у учётной записи бота правильная роль."""
        account = await self._request("GET", "/protected")
        role = f"bot:{self.platform}"
        if set(account.get("roles") or []) != {role} or account.get("person_id") is not None:
            raise ApiError(403, f"Для {self.platform} нужна отдельная учётная запись API с ролью {role}. "
                           f"Проверьте API_USERNAME / API_PASSWORD в конфиге frontend/.env.{self.platform} "
                           "(или ENV_FILE). Учётная запись администратора не подходит.")

    async def get_consents(self, user_id: int) -> list[dict]:
        return await self._request("GET", "/consents", user_id=user_id)

    async def accept_consents(self, user_id: int, documents: list[dict]) -> list[dict]:
        return await self._request("POST", "/consents", user_id=user_id, json={"documents": documents})

    async def get_person(self, user_id: int) -> dict:
        prefix = "/persons/max" if self.platform == "max" else "/persons"
        return await self._request("GET", f"{prefix}/{user_id}", user_id=user_id)

    async def get_wardrobe_summary(self, user_id: int) -> dict:
        return await self._request("GET", "/wardrobe/summary", user_id=user_id)

    async def get_wardrobe_categories(self, user_id: int) -> dict:
        return await self._request("GET", "/wardrobe/categories", user_id=user_id)

    async def get_wardrobe_page(self, user_id: int, category: str, page: int = 1,
                                page_size: int = 9) -> dict:
        return await self._request("GET", "/wardrobe/items", user_id=user_id,
                                   params={"category": category, "page": page, "page_size": page_size})

    async def get_wardrobe_item(self, user_id: int, item_id: int) -> dict:
        return await self._request("GET", f"/wardrobe/items/{item_id}", user_id=user_id)

    async def update_wardrobe_item(self, user_id: int, item_id: int, field: str, value: str) -> dict:
        return await self._request("PATCH", f"/wardrobe/items/{item_id}", user_id=user_id,
                                   json={field: value})

    async def get_wardrobe_edit_options(self, user_id: int, field: str, page: int = 1,
                                        page_size: int = 20) -> dict:
        return await self._request("GET", f"/wardrobe/edit-options/{field}", user_id=user_id,
                                   params={"page": page, "page_size": page_size})

    async def update_wardrobe_item_option(self, user_id: int, item_id: int,
                                          field: str, value_id: int) -> dict:
        return await self._request("PATCH", f"/wardrobe/items/{item_id}/attribute", user_id=user_id,
                                   json={"field": field, "value_id": value_id})

    async def archive_wardrobe_item(self, user_id: int, item_id: int) -> dict:
        return await self._request("DELETE", f"/wardrobe/items/{item_id}", user_id=user_id)

    async def save_item_media_id(self, user_id: int, item_id: int, media_id: str) -> None:
        await self._request("PUT", f"/wardrobe/items/{item_id}/media-cache", user_id=user_id,
                            json={"media_id": media_id})

    async def get_wardrobe_view_or_none(self, user_id: int) -> Optional[dict]:
        try:
            return await self._request("GET", "/wardrobe/view", user_id=user_id)
        except ApiError as exc:
            if exc.is_not_found:
                return None
            raise

    async def save_wardrobe_view(self, user_id: int, view: dict) -> dict:
        return await self._request("PUT", "/wardrobe/view", user_id=user_id, json=view)

    async def clear_wardrobe_view(self, user_id: int) -> None:
        await self._request("DELETE", "/wardrobe/view", user_id=user_id)

    async def get_person_or_none(self, user_id: int) -> Optional[dict]:
        try:
            return await self.get_person(user_id)
        except ApiError:
            return None

    async def create_person(self, user_id: int, email: str, nickname: Optional[str]) -> dict:
        payload = {f"{self.platform}_id": user_id, "email": email, "nickname": nickname}
        return await self._request("POST", "/persons/", user_id=user_id, json=payload)

    async def upload_item(self, user_id: int, filename: str, image_bytes: bytes,
                          content_type: str = "image/jpeg") -> dict:
        return await self._request(
            "POST",
            "/upload-item/",
            user_id=user_id,
            files={"file": (filename, image_bytes, content_type)},
            data={f"{self.platform}_id": str(user_id)},
        )

    async def get_item_image(self, item_id: int, user_id: int) -> bytes:
        # просим JPEG на белом фоне (в базе картинка хранится как PNG без фона)
        return await self._request("GET", f"/items/{item_id}/image", user_id=user_id,
                                   params={"flatten": "true", "max_side": "1280"})

    async def generate_outfit(self, user_id: int, event: str, location: Optional[str] = None,
                              wishes: Optional[str] = None) -> dict:
        payload = {"user_id": user_id, "event": event, "location": location, "wishes": wishes}
        payload["platform"] = self.platform
        return await self._request("POST", "/outfits/generate", user_id=user_id, json=payload)

    async def get_outfits(self, user_id: int, page: int = 1, page_size: int = 10) -> dict:
        return await self._request("GET", "/outfits", user_id=user_id,
                                   params={"page": page, "page_size": page_size})

    async def get_outfit(self, user_id: int, outfit_id: int) -> dict:
        return await self._request("GET", f"/outfits/{outfit_id}", user_id=user_id)

    async def issue_link_code(self, user_id: int) -> dict:
        return await self._request("POST", "/persons/link-code", user_id=user_id)

    async def link_messenger(self, user_id: int, code: str) -> dict:
        return await self._request("POST", "/persons/link", user_id=user_id, json={"code": code})
