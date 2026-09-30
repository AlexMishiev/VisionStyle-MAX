"""Обработчики сообщений и кнопок MAX-бота."""
import asyncio
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from io import BytesIO

import httpx
from PIL import Image, ImageOps

from bot.common import flows, formatters as texts
from bot.common.api_client import ApiError, VisionStyleApi
from bot.common.legal import CONSENT_CALLBACK_PREFIX, ConsentGate, load_documents
from bot.common.logger import get_logger
from bot.common.manual import load_manual
from bot.common.models import has_wardrobe_items, normalize_item, person_items
from bot.common import wardrobe as wardrobe_ui
from bot.max import keyboards
from bot.max.client import MaxApiError, MaxClient

logger = get_logger(__name__)
events = get_logger("bot.max.events")
ASK_LOCATION_TEXT = "Теперь отправьте геолокацию: текущую или выбранную точку на карте."
LOCATION_REQUIRED_TEXT = "Нужно отправить геолокацию. Нажмите кнопку ниже или выберите место через меню вложений MAX."
MAP_POINT_HINT_TEXT = "Откройте меню вложений MAX → геолокация, выберите точку на карте и отправьте её боту."


@dataclass
class Session:
    step: str | None = None
    data: dict = field(default_factory=dict)

    def clear(self):
        self.step = None
        self.data.clear()


@dataclass
class Message:
    chat_id: int
    user_id: int
    text: str = ""
    attachments: list[dict] = field(default_factory=list)
    mid: str | None = None
    action: str | None = None


def jpeg_image(data: bytes) -> bytes:
    with Image.open(BytesIO(data)) as source:
        source = ImageOps.exif_transpose(source)
        rgba = source.convert("RGBA")
        image = Image.new("RGB", rgba.size, "white")
        image.paste(rgba, mask=rgba.getchannel("A"))
        result = BytesIO()
        image.save(result, "JPEG", quality=95)
        return result.getvalue()


def location_coordinates(attachments: list[dict]) -> tuple[float, float] | None:
    """Достаёт координаты из вложения с геолокацией."""
    for attachment in attachments:
        if attachment.get("type") != "location":
            continue
        sources = (attachment, attachment.get("payload"))
        for source in sources:
            if not isinstance(source, dict):
                continue
            try:
                lat = float(source["latitude"])
                lon = float(source["longitude"])
            except (TypeError, KeyError, ValueError):
                continue
            if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
    return None


def describe_message(message: Message) -> str:
    """Описание сообщения для лога. Сам текст не пишем, там могут быть личные данные."""
    parts = []
    if message.action is not None:
        parts.append(f"кнопка {message.action!r}")
    text = message.text.strip()
    if text.startswith("/"):
        parts.append(f"команда {text.split(maxsplit=1)[0]}")
    elif text:
        parts.append(f"текст ({len(text)} симв.)")
    if message.attachments:
        parts.append("вложения=" + ",".join(str(a.get("type")) for a in message.attachments))
    return ", ".join(parts) or "пусто"


class MaxHandlers:
    def __init__(self, bot: MaxClient, api: VisionStyleApi, consent: ConsentGate | None = None):
        self.bot, self.api = bot, api
        self.consent = consent or ConsentGate(api, load_documents())
        self._file_tokens: dict[str, str] = {}  # чтобы не загружать один и тот же документ повторно
        self.sessions: dict[tuple[int, int], Session] = defaultdict(Session)
        self.locks: dict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)

    async def handle(self, update: dict):
        kind = update.get("update_type")
        if kind == "bot_started":
            user = update.get("user") or {}
            chat_id = update.get("chat_id")
            message = Message(chat_id, user.get("user_id"), text="/start")
        elif kind in {"message_created", "message_callback"}:
            raw = update.get("message") or {}
            callback = update.get("callback") or {}
            if not raw:
                return
            recipient = raw.get("recipient") or {}
            user = callback.get("user") if kind == "message_callback" else raw.get("sender")
            user = user or {}
            chat_id = recipient.get("chat_id")
            if recipient.get("chat_type") != "dialog":
                return  # бот работает только в личных сообщениях
            body = raw.get("body") or {}
            message = Message(chat_id, user.get("user_id"), body.get("text") or "",
                              body.get("attachments") or [], body.get("mid"),
                              callback.get("payload") if kind == "message_callback" else None)
        else:
            return
        if message.chat_id is None or message.user_id is None or user.get("is_bot"):
            return
        key = (message.chat_id, message.user_id)
        state = self.sessions[key]
        who = f"user={message.user_id} chat={message.chat_id}"
        what = describe_message(message)
        events.info("← %s %s: %s%s", who, kind, what, f" [шаг {state.step}]" if state.step else "")
        started = time.monotonic()
        async with self.locks[key]:
            try:
                await self.dispatch(message, state)
            except ApiError as exc:
                events.warning("✖ %s %s: %s — ошибка backend: %s", who, kind, what, exc)
                await self.send(message, texts.api_error_text("выполнить действие", exc))
            except (httpx.HTTPError, MaxApiError) as exc:
                # у ошибок httpx в тексте может быть ссылка, поэтому пишем только тип
                reason = exc if isinstance(exc, MaxApiError) else type(exc).__name__
                events.warning("✖ %s %s: %s — ошибка MAX/сети: %s", who, kind, what, reason)
                await self.send(message, "Сервис временно недоступен. Попробуйте ещё раз.")
            except Exception:
                events.exception("✖ %s %s: %s — ошибка обработки", who, kind, what)
                raise
            else:
                events.debug("✓ %s %s обработано за %.0f мс", who, kind, (time.monotonic() - started) * 1000)

    async def send(self, message: Message, text: str, keyboard: dict | None = None):
        return await self.bot.send_message(message.chat_id, text, keyboard=keyboard)

    async def menu(self, message: Message, text: str, person: dict | None = None):
        if person is None:
            _, person = await flows.can_build_outfit(self.api, message.user_id)
        return await self.send(message, text, keyboards.main_menu(has_wardrobe_items(person)))

    async def delete(self, mid: str | None):
        if mid:
            try:
                await self.bot.delete_message(mid)
            except MaxApiError:
                logger.debug("Не удалось удалить служебное сообщение MAX")

    async def send_document(self, message: Message, document, text: str = "", keyboard: dict | None = None):
        extra = [keyboard] if keyboard else []
        cached = self._file_tokens.get(document.sha256)
        if cached:
            try:
                await self.bot.send_message(message.chat_id, text, attachments=[
                    {"type": "file", "payload": {"token": cached}}, *extra])
                logger.info("Документ «%s» отправлен в chat=%s (сохранённый token)", document.title, message.chat_id)
                return
            except MaxApiError as exc:
                logger.warning("Документ «%s»: сохранённый token не принят (%s), загружаю заново",
                               document.title, exc)
                self._file_tokens.pop(document.sha256, None)
        try:
            attachment = await self.bot.upload_file(document.content, document.display_filename)
            await self.bot.send_message(message.chat_id, text, attachments=[attachment, *extra])
        except MaxApiError as exc:
            logger.warning("Документ «%s» не отправлен в chat=%s: %s", document.title, message.chat_id, exc)
            raise
        self._file_tokens[document.sha256] = attachment["payload"]["token"]
        logger.info("Документ «%s» загружен и отправлен в chat=%s", document.title, message.chat_id)

    async def send_consent_request(self, message: Message, *, with_files: bool = True, updated: bool = False):
        """Отправляет документы для согласия. Текст и кнопка идут вместе с последним файлом."""
        documents = self.consent.documents
        accept = keyboards.consent_accept(documents.accept_callback)
        logger.info("Согласие chat=%s: отправка %s%s", message.chat_id,
                    "документов" if with_files else "напоминания", " (новая редакция)" if updated else "")
        if not with_files:
            await self.send(message, documents.reminder_text(), accept)
            return
        *leading, last = documents.files
        for document in leading:
            await self.send_document(message, document)
        await self.send_document(message, last, documents.request_text(updated=updated), accept)

    async def consent_accepted(self, message: Message, state: Session, command: str) -> bool:
        """Проверяет согласие. Если его нет, отправляет документы и возвращает False."""
        if await self.consent.is_accepted(message.user_id):
            return True
        state.clear()
        if command == "/start":
            await self.send(message, texts.WELCOME_TEXT, keyboards.main_menu(False))
        await self.send_consent_request(
            message, with_files=self.consent.should_send_files(message.user_id, force=command == "/start"))
        return False

    async def accept_consent(self, message: Message, state: Session):
        if self.consent.is_cached(message.user_id):
            await self.send(message, texts.CONSENT_ALREADY_ACCEPTED_TEXT)
            return
        if not await self.consent.accept(message.user_id, message.action):
            # кнопка от старой версии документов, отправляем новые
            await self.send_consent_request(message, updated=True)
            return
        state.clear()
        await self.menu(message, texts.CONSENT_ACCEPTED_TEXT)
        await self.registered(message, state)

    async def registered(self, message: Message, state: Session) -> bool:
        check = await flows.check_access(self.api, message.user_id)
        if check.allowed:
            return True
        if check.status is flows.AccessStatus.INACTIVE:
            logger.warning("Доступ user=%s: отказано — профиль заблокирован", message.user_id)
            state.clear()
            await self.send(message, texts.ACCESS_DENIED_TEXT)
        elif check.status is flows.AccessStatus.NOT_REGISTERED:
            if state.step == "email":
                await self.send(message, texts.REGISTRATION_EMAIL_REMINDER_TEXT)
            elif state.step == "nickname":
                await self.send(message, texts.REGISTRATION_NICKNAME_REMINDER_TEXT)
            else:
                state.clear()
                state.step = "email"
                logger.info("Регистрация user=%s: начата, запрошен email", message.user_id)
                await self.send(message, texts.REGISTRATION_START_TEXT)
        else:
            await self.send(message, texts.not_in_system_text(message.user_id, check.error))
        return False

    async def profile(self, message: Message):
        person = await self.api.get_wardrobe_summary(message.user_id)
        await self.send(message, texts.format_person(person),
                        keyboards.profile_inline_keyboard(has_wardrobe_items(person)))

    async def show_outfit_history(self, message: Message, result: dict):
        text = (texts.format_outfit_history(result) if result.get("items")
                else texts.OUTFIT_HISTORY_EMPTY_TEXT)
        await self.send(message, text, keyboards.outfit_history(result))

    async def show_saved_outfit(self, message: Message, result: dict, *, replace_mid: str | None = None):
        items = result.get("items") or []
        images = await self.prepare_wardrobe_images(message, items) if items else []
        await self.delete(replace_mid)
        text = texts.format_saved_outfit(result)
        if images:
            await self.bot.send_media(message.chat_id, text, images, keyboard=keyboards.outfit_detail())
            await self.cache_wardrobe_images(message, items, images)
        else:
            await self.send(message, text, keyboards.outfit_detail())

    async def outfit_history_callback(self, message: Message, state: Session):
        parts = (message.action or "").split(":", 2)
        if len(parts) < 2 or parts[0] != keyboards.OUTFIT_HISTORY_PREFIX:
            return
        action = parts[1]
        value = parts[2] if len(parts) == 3 else None
        if action == "noop":
            return
        if action == "profile":
            await self.delete(message.mid)
            state.clear()
            await self.profile(message)
            return
        if action == "page" and value and value.isdigit():
            result = await self.api.get_outfits(message.user_id, int(value), 10)
            await self.delete(message.mid)
            await self.show_outfit_history(message, result)
            return
        if action == "item" and value and value.isdigit():
            result = await self.api.get_outfit(message.user_id, int(value))
            await self.show_saved_outfit(message, result, replace_mid=message.mid)

    async def show_generated_outfit(self, message: Message, result: dict):
        items = result.get("items") or []
        prepared = await asyncio.gather(*(
            self.prepare_wardrobe_images(message, [item]) for item in items
        ), return_exceptions=True)
        pairs = [(item, value[0]) for item, value in zip(items, prepared)
                 if isinstance(value, list) and value]
        image_items = [item for item, _ in pairs]
        images = [image for _, image in pairs]
        text = texts.format_outfit_summary(result)
        keyboard = keyboards.main_menu(bool(items))
        if images:
            await self.bot.send_media(message.chat_id, text, images, keyboard=keyboard)
            await self.cache_wardrobe_images(message, image_items, images)
        else:
            await self.send(message, text, keyboard)

    async def delete_wardrobe_view(self, message: Message, view: dict | None = None):
        view = view or await self.api.get_wardrobe_view_or_none(message.user_id)
        if not view:
            return
        for mid in view.get("message_ids") or []:
            await self.delete(mid)
        await self.api.clear_wardrobe_view(message.user_id)

    async def save_wardrobe_view(self, message: Message, *, token: str, mids: list[str | None], screen: str,
                                 category: str | None = None, page: int = 1, item_id: int | None = None,
                                 edit_field: str | None = None, item_ids: list[int] | None = None):
        await self.api.save_wardrobe_view(message.user_id, {
            "token": token,
            "chat_id": message.chat_id,
            "message_ids": [mid for mid in mids if mid],
            "screen": screen,
            "category": category,
            "page": page,
            "item_id": item_id,
            "edit_field": edit_field,
            "item_ids": item_ids or [],
        })

    async def show_wardrobe_categories(self, message: Message, result: dict | None = None):
        result = result or await self.api.get_wardrobe_categories(message.user_id)
        token = wardrobe_ui.new_token()
        if result.get("categories"):
            mid = await self.send(message, texts.format_wardrobe_categories(result),
                                  keyboards.wardrobe_categories(result["categories"], token))
        else:
            mid = await self.send(message, texts.WARDROBE_EMPTY_TEXT,
                                  keyboards.wardrobe_categories([], token))
        await self.save_wardrobe_view(message, token=token, mids=[mid], screen="categories")

    async def prepare_wardrobe_images(self, message: Message, items: list[dict], *, force: bool = False):
        semaphore = asyncio.Semaphore(3)

        async def one(item: dict):
            if item.get("media_id") and not force:
                return {"type": "image", "payload": {"token": item["media_id"]}}
            async with semaphore:
                data = await self.api.get_item_image(item["id"], message.user_id)
                return await self.bot.upload_image(data, f"item_{item['id']}.jpg")

        return await asyncio.gather(*(one(item) for item in items))

    async def cache_wardrobe_images(self, message: Message, items: list[dict], images: list[dict]):
        updates = [self.api.save_item_media_id(message.user_id, item["id"], image["payload"]["token"])
                   for item, image in zip(items, images)]
        if updates:
            await asyncio.gather(*updates, return_exceptions=True)

    async def show_wardrobe_gallery(self, message: Message, result: dict):
        items = result.get("items") or []
        if not items:
            await self.show_wardrobe_categories(message)
            return
        token = wardrobe_ui.new_token()
        images = await self.prepare_wardrobe_images(message, items)
        try:
            mid = await self.bot.send_media(message.chat_id, texts.format_wardrobe_gallery(result), images,
                                            keyboard=keyboards.wardrobe_gallery(result, token))
        except MaxApiError:
            if not any(item.get("media_id") for item in items):
                raise
            images = await self.prepare_wardrobe_images(message, items, force=True)
            mid = await self.bot.send_media(message.chat_id, texts.format_wardrobe_gallery(result), images,
                                            keyboard=keyboards.wardrobe_gallery(result, token))
        await self.cache_wardrobe_images(message, items, images)
        await self.save_wardrobe_view(message, token=token, mids=[mid], screen="gallery",
                                      category=result["category"]["key"], page=result["page"],
                                      item_ids=[item["id"] for item in items])

    async def show_wardrobe_item(self, message: Message, item: dict, category: str, page: int):
        token = wardrobe_ui.new_token()
        if item.get("media_id"):
            image = {"type": "image", "payload": {"token": item["media_id"]}}
        else:
            image = (await self.prepare_wardrobe_images(message, [item]))[0]
        try:
            mid = await self.bot.send_media(message.chat_id, texts.format_wardrobe_item(item), [image],
                                            keyboard=keyboards.wardrobe_item(token))
        except MaxApiError:
            if not item.get("media_id"):
                raise
            image = (await self.prepare_wardrobe_images(message, [item], force=True))[0]
            mid = await self.bot.send_media(message.chat_id, texts.format_wardrobe_item(item), [image],
                                            keyboard=keyboards.wardrobe_item(token))
        await self.cache_wardrobe_images(message, [item], [image])
        await self.save_wardrobe_view(message, token=token, mids=[mid], screen="item", category=category,
                                      page=page, item_id=item["id"])

    async def show_wardrobe_text(self, message: Message, *, text: str, keyboard, screen: str,
                                 category: str, page: int, item_id: int, edit_field: str | None = None):
        token = wardrobe_ui.new_token()
        mid = await self.send(message, text, keyboard(token))
        await self.save_wardrobe_view(message, token=token, mids=[mid], screen=screen, category=category,
                                      page=page, item_id=item_id, edit_field=edit_field)

    async def show_wardrobe_edit_options(self, message: Message, result: dict, *, category: str,
                                         page: int, item_id: int, edit_field: str):
        token = wardrobe_ui.new_token()
        mid = await self.send(message, texts.format_edit_options(edit_field, result),
                              keyboards.wardrobe_edit_options(result, token))
        await self.save_wardrobe_view(message, token=token, mids=[mid], screen="edit_options",
                                      category=category, page=page, item_id=item_id,
                                      edit_field=edit_field)

    async def show_page_or_categories(self, message: Message, category: str, page: int):
        result = await self.api.get_wardrobe_page(message.user_id, category, page)
        if result.get("items"):
            await self.show_wardrobe_gallery(message, result)
        else:
            await self.show_wardrobe_categories(message)

    async def wardrobe_callback(self, message: Message, state: Session):
        parsed = wardrobe_ui.parse_callback(message.action)
        if not parsed:
            return
        token, action, value = parsed
        view = await self.api.get_wardrobe_view_or_none(message.user_id)
        if not view or view.get("token") != token or action == "noop":
            return
        category = view.get("category")
        page = int(view.get("page") or 1)
        if action == "profile":
            await self.delete_wardrobe_view(message, view)
            state.clear()
            await self.profile(message)
            return
        if action == "cat" and value:
            result = await self.api.get_wardrobe_page(message.user_id, value, 1)
            await self.delete_wardrobe_view(message, view)
            await self.show_wardrobe_gallery(message, result)
            return
        if action == "page" and value and category:
            result = await self.api.get_wardrobe_page(message.user_id, category, int(value))
            await self.delete_wardrobe_view(message, view)
            await self.show_wardrobe_gallery(message, result)
            return
        if action == "categories":
            result = await self.api.get_wardrobe_categories(message.user_id)
            await self.delete_wardrobe_view(message, view)
            await self.show_wardrobe_categories(message, result)
            return
        if action == "item" and value and category:
            slot = int(value) - 1
            item_ids = view.get("item_ids") or []
            if not 0 <= slot < len(item_ids):
                return
            item = await self.api.get_wardrobe_item(message.user_id, item_ids[slot])
            await self.delete_wardrobe_view(message, view)
            await self.show_wardrobe_item(message, item, category, page)
            return
        if action == "back" and category:
            result = await self.api.get_wardrobe_page(message.user_id, category, page)
            await self.delete_wardrobe_view(message, view)
            await self.show_wardrobe_gallery(message, result)
            return

        item_id = view.get("item_id")
        if not item_id or not category:
            return
        edit_field = view.get("edit_field")
        if action == "edit":
            item = await self.api.get_wardrobe_item(message.user_id, item_id)
            await self.delete_wardrobe_view(message, view)
            await self.show_wardrobe_text(message, text=texts.format_edit_fields(item),
                                          keyboard=keyboards.wardrobe_edit_fields, screen="edit_fields",
                                          category=category, page=page, item_id=item_id)
        elif action == "field" and value in wardrobe_ui.EDIT_FIELDS:
            options = await self.api.get_wardrobe_edit_options(message.user_id, value, 1)
            await self.delete_wardrobe_view(message, view)
            state.clear()
            await self.show_wardrobe_edit_options(message, options, category=category, page=page,
                                                  item_id=item_id, edit_field=value)
        elif action == "opts" and edit_field and value:
            options = await self.api.get_wardrobe_edit_options(message.user_id, edit_field, int(value))
            await self.delete_wardrobe_view(message, view)
            await self.show_wardrobe_edit_options(message, options, category=category, page=page,
                                                  item_id=item_id, edit_field=edit_field)
        elif action == "set" and edit_field and value:
            item = await self.api.update_wardrobe_item_option(
                message.user_id, item_id, edit_field, int(value))
            next_category = item["category_key"] if edit_field == "item_type" else category
            next_page = 1 if next_category != category else page
            await self.delete_wardrobe_view(message, view)
            state.clear()
            await self.show_wardrobe_item(message, item, next_category, next_page)
        elif action == "delete":
            item = await self.api.get_wardrobe_item(message.user_id, item_id)
            await self.delete_wardrobe_view(message, view)
            await self.show_wardrobe_text(message, text=texts.format_delete_confirmation(item),
                                          keyboard=keyboards.wardrobe_delete_confirmation,
                                          screen="delete_confirm", category=category, page=page,
                                          item_id=item_id)
        elif action == "confirm_delete":
            await self.api.archive_wardrobe_item(message.user_id, item_id)
            await self.delete_wardrobe_view(message, view)
            state.clear()
            await self.show_page_or_categories(message, category, page)
        elif action == "cancel":
            item = await self.api.get_wardrobe_item(message.user_id, item_id)
            await self.delete_wardrobe_view(message, view)
            state.clear()
            await self.show_wardrobe_item(message, item, category, page)

    async def process_wardrobe_edit(self, message: Message, state: Session, view: dict | None = None) -> bool:
        view = view or await self.api.get_wardrobe_view_or_none(message.user_id)
        if not view or view.get("screen") != "edit_value" or not view.get("edit_field"):
            return False
        value = " ".join(message.text.split())
        if not value or len(value) > 64:
            await self.send(message, "Введите значение длиной от 1 до 64 символов.")
            return True
        item = await self.api.update_wardrobe_item(message.user_id, view["item_id"], view["edit_field"], value)
        old_category = view.get("category") or item["category_key"]
        category = item["category_key"] if view["edit_field"] == "item_type" else old_category
        page = 1 if category != old_category else int(view.get("page") or 1)
        await self.delete_wardrobe_view(message, view)
        state.clear()
        await self.show_wardrobe_item(message, item, category, page)
        return True

    async def dispatch(self, message: Message, state: Session):
        text = message.text.strip()
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text.startswith("/") else ""
        action = message.action
        in_outfit = state.step in {"event", "location", "wishes"}
        if action and action.startswith(CONSENT_CALLBACK_PREFIX):
            await self.accept_consent(message, state)
            return
        if not await self.consent_accepted(message, state, command):
            return
        if action and action.startswith(f"{keyboards.OUTFIT_HISTORY_PREFIX}:"):
            if await self.registered(message, state):
                await self.outfit_history_callback(message, state)
            return
        if action and action.startswith("wd:"):
            await self.wardrobe_callback(message, state)
            return
        if command == "/link":
            await self.delete_wardrobe_view(message)
            parts = text.split(maxsplit=1)
            if len(parts) == 1:
                if await self.registered(message, state):
                    await self.send(message, texts.link_code_text(await self.api.issue_link_code(message.user_id)))
                    logger.info("Связка user=%s: выдан код для второго мессенджера", message.user_id)
            else:
                person = await self.api.link_messenger(message.user_id, parts[1])
                logger.info("Связка user=%s: мессенджер подключён к person_id=%s", message.user_id, person.get("id"))
                state.clear()
                await self.menu(message, texts.LINK_DONE_TEXT, person)
                await self.profile(message)
            return
        if command == "/start":
            try:
                await self.delete_wardrobe_view(message)
            except ApiError:
                pass
            state.clear()
            await self.menu(message, texts.WELCOME_TEXT)
            await self.registered(message, state)
            return
        if in_outfit and (command == "/cancel" or action == keyboards.OUTFIT_CANCEL_CALLBACK):
            logger.info("Отмена user=%s: подбор образа", message.user_id)
            await self.delete(state.data.get("prompt"))
            state.clear()
            await self.menu(message, texts.OUTFIT_CANCELLED_TEXT)
            return
        if action == keyboards.OUTFIT_CANCEL_CALLBACK:
            return
        if command == "/cancel":
            view = await self.api.get_wardrobe_view_or_none(message.user_id)
            state.clear()
            if view and view.get("item_id") and view.get("category"):
                item = await self.api.get_wardrobe_item(message.user_id, view["item_id"])
                await self.delete_wardrobe_view(message, view)
                await self.show_wardrobe_item(message, item, view["category"], view.get("page", 1))
            else:
                await self.menu(message, "Действие отменено.")
            return
        if (command == "/outfit" or text == texts.OUTFIT_BUTTON_TEXT
                or action in {keyboards.OUTFIT_CALLBACK, keyboards.PROFILE_OUTFIT_CALLBACK}):
            if action == keyboards.PROFILE_OUTFIT_CALLBACK:
                await self.delete(message.mid)
            if in_outfit:
                await self.send(message, texts.FINISH_OUTFIT_FIRST_TEXT)
            elif await self.registered(message, state):
                await self.delete_wardrobe_view(message)
                can_build, person = await flows.can_build_outfit(self.api, message.user_id)
                state.clear()
                if not can_build:
                    logger.info("Образ user=%s: гардероб пуст, подбор не начат", message.user_id)
                    await self.menu(message, texts.NO_ITEMS_FOR_OUTFIT_TEXT, person)
                else:
                    logger.info("Образ user=%s: подбор начат, запрошено событие", message.user_id)
                    state.step = "event"
                    state.data["prompt"] = await self.send(
                        message, texts.ASK_EVENT_TEXT, keyboards.outfit_cancel_keyboard()
                    )
            return
        if command == "/me" or text == texts.PROFILE_BUTTON_TEXT or action == keyboards.PROFILE_CALLBACK:
            if in_outfit and command != "/me":
                await self.send(message, texts.FINISH_OUTFIT_FIRST_TEXT)
            elif await self.registered(message, state):
                await self.delete_wardrobe_view(message)
                state.clear()
                await self.profile(message)
            return
        if action == keyboards.PROFILE_ITEMS_CALLBACK:
            if await self.registered(message, state):
                categories = await self.api.get_wardrobe_categories(message.user_id)
                await self.delete(message.mid)
                await self.show_wardrobe_categories(message, categories)
            return
        if action == keyboards.PROFILE_OUTFITS_CALLBACK:
            if await self.registered(message, state):
                result = await self.api.get_outfits(message.user_id, 1, 10)
                await self.delete_wardrobe_view(message)
                await self.delete(message.mid)
                state.clear()
                await self.show_outfit_history(message, result)
            return
        if action == keyboards.MAP_POINT_CALLBACK:
            if state.step == "location":
                await self.send(message, MAP_POINT_HINT_TEXT, keyboards.location_keyboard())
            return
        if action is not None:
            return  # остальные кнопки просто игнорируем
        if state.step in {"email", "nickname"}:
            await self.registration(message, state)
        elif in_outfit:
            await self.outfit_step(message, state)
        else:
            view = await self.api.get_wardrobe_view_or_none(message.user_id)
            if await self.process_wardrobe_edit(message, state, view):
                return
            images = [a for a in message.attachments if a.get("type") == "image"]
            if await self.registered(message, state):
                if images:
                    await self.delete_wardrobe_view(message, view)
                    for attachment in images:
                        await self.upload_photo(message, attachment)
                else:
                    await self.menu(message, texts.FALLBACK_TEXT)

    async def registration(self, message: Message, state: Session):
        if state.step == "email":
            email = flows.normalize_email(message.text)
            if not email or not flows.is_valid_email(email):
                logger.info("Регистрация user=%s: некорректный email", message.user_id)
                await self.send(message, texts.INVALID_EMAIL_TEXT if email else texts.EMAIL_AS_TEXT_TEXT)
                return
            logger.info("Регистрация user=%s: email принят, запрошено имя", message.user_id)
            state.data["email"] = email
            state.step = "nickname"
            await self.send(message, texts.ASK_NICKNAME_TEXT)
            return
        nickname = message.text.strip()
        if not nickname:
            await self.send(message, texts.NICKNAME_AS_TEXT_TEXT)
            return
        if not state.data.get("email"):
            state.step = "email"
            await self.send(message, texts.EMAIL_LOST_TEXT)
            return
        await self.send(message, texts.REGISTERING_TEXT)
        result = await flows.register_person(self.api, message.user_id, state.data["email"], nickname)
        if result.status is flows.RegistrationStatus.EMAIL_REJECTED:
            state.step = "email"
            await self.send(message, texts.EMAIL_REJECTED_TEXT)
        elif result.status is flows.RegistrationStatus.ERROR:
            await self.send(message, texts.api_error_text("зарегистрировать профиль", result.error))
        else:
            state.clear()
            await self.menu(message, texts.REGISTRATION_DONE_TEXT if result.status is flows.RegistrationStatus.CREATED
                            else texts.PROFILE_ALREADY_EXISTS_TEXT, result.person)
            await self.profile(message)
            await self.send_user_guide(message, result.person)

    async def send_user_guide(self, message: Message, person: dict | None):
        """Отправляет инструкцию и PDF-руководство."""
        keyboard = keyboards.main_menu(has_wardrobe_items(person))
        manual = load_manual("max")
        if manual is not None:
            try:
                await self.send_document(message, manual, texts.user_guide_text(with_manual=True), keyboard)
                return
            except MaxApiError:
                logger.warning("Руководство не отправлено в chat=%s — отправляю только текст", message.chat_id)
        await self.send(message, texts.user_guide_text(with_manual=False), keyboard)

    async def outfit_step(self, message: Message, state: Session):
        text = message.text.strip()
        if state.step == "event":
            if not text:
                await self.send(message, texts.EVENT_AS_TEXT_TEXT)
                return
            await self.delete(state.data.pop("prompt", None))
            state.data["event"] = text
            state.step = "location"
            state.data["prompt"] = await self.send(message, ASK_LOCATION_TEXT, keyboards.location_keyboard())
        elif state.step == "location":
            coordinates = location_coordinates(message.attachments)
            if coordinates is None:
                logger.info("Образ user=%s: ждём геолокацию, пришло другое", message.user_id)
                await self.send(message, LOCATION_REQUIRED_TEXT, keyboards.location_keyboard())
                return
            logger.info("Образ user=%s: геолокация получена, запрошены пожелания", message.user_id)
            lat, lon = coordinates
            await self.delete(state.data.pop("prompt", None))
            state.data["location"] = texts.format_coordinates(lat, lon)
            state.step = "wishes"
            state.data["prompt"] = await self.send(
                message, texts.ASK_WISHES_TEXT, keyboards.outfit_cancel_keyboard()
            )
        elif state.step == "wishes":
            if not text:
                await self.send(message, texts.WISHES_AS_TEXT_TEXT)
                return
            await self.delete(state.data.pop("prompt", None))
            loading = await self.send(message, texts.GENERATING_OUTFIT_TEXT)
            logger.info("Образ user=%s: генерация запрошена", message.user_id)
            try:
                result = await self.api.generate_outfit(message.user_id, state.data["event"],
                                                        state.data["location"], flows.parse_wishes(text))
            except ApiError as exc:
                logger.warning("Образ user=%s: генерация не удалась: %s", message.user_id, exc)
                await self.menu(message, texts.api_error_text("сгенерировать образ", exc))
                return
            finally:
                state.clear()
                await self.delete(loading)
            logger.info("Образ user=%s: готов outfit_id=%s, вещей %s%s", message.user_id, result.get("outfit_id"),
                        len(result.get("items") or []), ", неполный" if result.get("is_partial") else "")
            await self.show_generated_outfit(message, result)

    async def upload_photo(self, message: Message, attachment: dict):
        loading = await self.send(message, texts.UPLOAD_ACCEPTED_TEXT)
        try:
            data = await self.bot.download_image(attachment["payload"]["url"])
            data = await asyncio.to_thread(jpeg_image, data)
            logger.info("Вещь user=%s: загрузка фото (%s байт)", message.user_id, len(data))
            item = normalize_item(await self.api.upload_item(message.user_id, "max_upload.jpg", data, "image/jpeg"))
        except ApiError as exc:
            logger.warning("Вещь user=%s: backend отклонил фото: %s", message.user_id, exc)
            await self.menu(message, texts.api_error_text("загрузить вещь", exc))
            return
        except (MaxApiError, httpx.HTTPError, OSError, ValueError, KeyError, Image.DecompressionBombError) as exc:
            logger.warning("Вещь user=%s: не удалось обработать фото: %s", message.user_id, type(exc).__name__)
            await self.menu(message, texts.UPLOAD_UNEXPECTED_ERROR_TEXT)
            return
        finally:
            await self.delete(loading)
        logger.info("Вещь user=%s: сохранена item_id=%s", message.user_id, item.get("id"))
        if item.get("id"):
            try:
                saved = await self.api.get_item_image(item["id"], message.user_id)
                await self.bot.send_photo(message.chat_id, saved, texts.format_item(item),
                                          keyboard=keyboards.main_menu(True))
                return
            except (ApiError, MaxApiError, httpx.HTTPError):
                pass
        await self.menu(message, texts.format_item(item))
