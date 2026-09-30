"""Запуск MAX-бота в режиме polling или webhook."""
import asyncio
import hashlib
import hmac
import json
import os
import re
from collections import OrderedDict
from urllib.parse import urlsplit

import httpx
from aiohttp import web

from bot.common.api_client import VisionStyleApi
from bot.common.config import CommonSettings, _resolve_path, load_settings, require_env
from bot.common.legal import ConsentGate, load_documents
from bot.common.logger import get_logger
from bot.common.manual import load_manual
from bot.max.client import MaxApiError, MaxClient, UPDATE_TYPES
from bot.max.handlers import MaxHandlers

logger = get_logger(__name__)

COMMANDS = [
    {"name": "start", "description": "Главное меню"},
    {"name": "me", "description": "Мой профиль"},
    {"name": "outfit", "description": "Собрать образ"},
    {"name": "link", "description": "Подключить Telegram к профилю"},
    {"name": "cancel", "description": "Отменить текущее действие"},
]


class UpdateQueue:
    """Очередь событий: разные пользователи обрабатываются параллельно, события одного пользователя по порядку."""
    def __init__(self, handlers: MaxHandlers):
        self.handlers = handlers
        self.queues = [asyncio.Queue(maxsize=100) for _ in range(8)]
        self.seen = OrderedDict()
        self.pending = set()
        self.workers = []

    def start(self):
        self.workers = [asyncio.create_task(self.worker(queue)) for queue in self.queues]

    async def close(self):
        try:
            await asyncio.wait_for(self.join(), timeout=30)
        except TimeoutError:
            logger.warning("Остановка MAX с незавершёнными событиями")
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)

    async def join(self):
        await asyncio.gather(*(queue.join() for queue in self.queues))

    def submit(self, update: dict):
        fingerprint = hashlib.sha256(json.dumps(update, sort_keys=True).encode()).hexdigest()
        if fingerprint in self.seen or fingerprint in self.pending:
            return
        message = update.get("message") or {}
        user = (update.get("callback") or {}).get("user") or update.get("user") or message.get("sender") or {}
        queue = self.queues[int(user.get("user_id", 0)) % len(self.queues)]
        queue.put_nowait((fingerprint, update))
        self.pending.add(fingerprint)

    async def worker(self, queue):
        while True:
            fingerprint, update = await queue.get()
            try:
                await self.handlers.handle(update)
            except Exception as exc:
                # само событие в лог не пишем, там могут быть личные данные
                logger.error("Ошибка обработчика MAX: %s: %s", type(exc).__name__,
                             exc if not isinstance(exc, httpx.HTTPError) else "(детали скрыты)",
                             exc_info=not isinstance(exc, httpx.HTTPError))
            finally:
                self.pending.discard(fingerprint)
                self.seen[fingerprint] = None
                if len(self.seen) > 10000:
                    self.seen.popitem(last=False)
                queue.task_done()


async def poll(bot: MaxClient, queue: UpdateQueue):
    subscriptions = await bot.request("GET", "/subscriptions")
    if subscriptions.get("subscriptions"):
        raise RuntimeError("MAX уже подписан на webhook. Используйте MAX_MODE=webhook или удалите подписку вручную.")
    marker = None
    while True:
        try:
            result = await bot.get_updates(marker)
        except MaxApiError as exc:
            if exc.status in {401, 403} or exc.code.startswith("tls."):
                raise
            logger.warning("%s; повтор через 3 секунды", exc)
            await asyncio.sleep(3)
            continue
        for update in result.get("updates", []):
            while True:
                try:
                    queue.submit(update)
                    break
                except asyncio.QueueFull:
                    await queue.join()
        await queue.join()
        marker = result.get("marker", marker)
        await asyncio.sleep(0.3)


def webhook_app(queue: UpdateQueue, secret: str, path: str) -> web.Application:
    async def receive(request):
        if not hmac.compare_digest(request.headers.get("X-Max-Bot-Api-Secret", ""), secret):
            raise web.HTTPUnauthorized()
        try:
            update = await request.json()
            if not isinstance(update, dict) or update.get("update_type") not in UPDATE_TYPES:
                raise web.HTTPBadRequest()
            queue.submit(update)
        except (ValueError, TypeError):
            raise web.HTTPBadRequest() from None
        except asyncio.QueueFull:
            raise web.HTTPServiceUnavailable() from None
        return web.json_response({"success": True})

    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_post(path, receive)
    return app


async def webhook(bot: MaxClient, queue: UpdateQueue):
    url = require_env("MAX_WEBHOOK_URL")
    secret = require_env("MAX_WEBHOOK_SECRET")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.port not in {None, 443} or parsed.query or parsed.fragment:
        raise ValueError("MAX_WEBHOOK_URL: нужен HTTPS URL на порту 443 без query/fragment")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{5,256}", secret):
        raise ValueError("MAX_WEBHOOK_SECRET: 5–256 символов A-Z, a-z, 0-9, _ или -")
    runner = web.AppRunner(webhook_app(queue, secret, parsed.path or "/"), access_log=None)
    await runner.setup()
    try:
        site = web.TCPSite(runner, os.getenv("MAX_WEBHOOK_HOST", "127.0.0.1"),
                          int(os.getenv("MAX_WEBHOOK_PORT", "8081")))
        await site.start()
        await bot.request("POST", "/subscriptions", json={"url": url, "secret": secret, "update_types": UPDATE_TYPES})
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


async def main(settings: CommonSettings | None = None) -> None:
    if settings is None:
        settings = load_settings("max")
    token = require_env("MAX_BOT_TOKEN")
    mode = os.getenv("MAX_MODE", "polling").strip().lower()
    if mode not in {"polling", "webhook"}:
        raise ValueError("MAX_MODE: polling или webhook")
    documents = load_documents()  # без документов бот не запускается
    api = VisionStyleApi.from_settings(settings, platform="max")
    bot = MaxClient(token, os.getenv("MAX_API_BASE_URL", "https://platform-api2.max.ru"),
                    _resolve_path(os.getenv("MAX_TLS_CA_FILE", "")), settings.request_timeout)
    queue = UpdateQueue(MaxHandlers(bot, api, ConsentGate(api, documents)))
    try:
        await api.wait_until_ready()
        await bot.request("GET", "/me")
        try:
            await bot.request("PATCH", "/me/commands", json={"commands": COMMANDS})
        except MaxApiError:
            logger.warning("Не удалось обновить команды MAX")
        logger.info("Документы: %s", ", ".join(f"{d.display_filename} sha256={d.sha256[:12]}…"
                                               for d in documents.files))
        logger.info("Ссылка на Положение об обработке ПДн: %s", documents.policy_url or "НЕ ЗАДАНА")
        load_manual("max")  # заранее проверяем, что руководство на месте
        queue.start()
        logger.info("MAX-бот запущен: %s", mode)
        await (webhook(bot, queue) if mode == "webhook" else poll(bot, queue))
    finally:
        await queue.close()
        await bot.close()
        await api.close()
