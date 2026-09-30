"""Тексты сообщений и форматирование ответов (HTML-разметка)."""
import html
from typing import Any

from bot.common.api_client import RateLimited
from bot.common.models import display_value, normalize_item, person_items
from bot.common.wardrobe import EDIT_FIELDS

# Кнопки главного меню

PROFILE_BUTTON_TEXT = "Профиль"
OUTFIT_BUTTON_TEXT = "Собрать образ"
SHOW_ITEMS_BUTTON_TEXT = "Посмотреть гардероб"
SHOW_OUTFITS_BUTTON_TEXT = "Посмотреть старые луки"
RETURN_TO_PROFILE_BUTTON_TEXT = "В профиль"
CANCEL_OUTFIT_BUTTON_TEXT = "Выйти из подбора"

# Общие сообщения

WELCOME_TEXT = (
    "Привет! Я бот для Smart Wardrobe.\n\n"
    "Главные действия вынесены в меню ниже:\n"
    f"• <b>{PROFILE_BUTTON_TEXT}</b>\n"
    f"• <b>{OUTFIT_BUTTON_TEXT}</b>\n\n"
    "Также можно просто отправить фото вещи, и я загружу её в гардероб."
    "\n\nОдин профиль в Telegram и MAX: /link — подключить второй мессенджер."
)
FALLBACK_TEXT = (
    "Не понял сообщение.\n"
    "Отправьте фото вещи или используйте команды: /me, /outfit"
)
ACCESS_DENIED_TEXT = (
    "Доступ к боту запрещен.\n"
    "Ваш профиль деактивирован. Обратитесь к администратору."
)
FINISH_OUTFIT_FIRST_TEXT = "Сначала завершите текущий сбор образа."
OUTFIT_CANCELLED_TEXT = "Подбор образа отменён."
OUTFIT_HISTORY_EMPTY_TEXT = "Сохранённых луков пока нет."

# Согласие с документами

CONSENT_ACCEPTED_TEXT = "Спасибо! Согласие сохранено."
CONSENT_ALREADY_ACCEPTED_TEXT = "Согласие уже получено."

# Регистрация

REGISTRATION_START_TEXT = (
    "Я не нашёл вас в Smart Wardrobe.\n"
    "Если вы уже пользуетесь другим нашим ботом, получите там код командой /link "
    "и отправьте сюда /link КОД — ваш гардероб станет доступен здесь.\n\n"
    "Для нового профиля отправьте ваш email."
)
REGISTRATION_EMAIL_REMINDER_TEXT = "Для регистрации отправьте email текстом."
REGISTRATION_NICKNAME_REMINDER_TEXT = "Для завершения регистрации напишите предпочитаемое имя."
EMAIL_AS_TEXT_TEXT = "Отправьте email текстом."
INVALID_EMAIL_TEXT = "Это не похоже на корректный email. Попробуйте ещё раз."
ASK_NICKNAME_TEXT = "Теперь напишите предпочитаемое имя."
NICKNAME_AS_TEXT_TEXT = "Предпочитаемое имя нужно отправить текстом."
EMAIL_LOST_TEXT = "Не нашёл сохранённый email. Отправьте его ещё раз."
REGISTERING_TEXT = "Регистрирую вас в Smart Wardrobe..."
EMAIL_REJECTED_TEXT = (
    "Не удалось завершить регистрацию с этим email.\n"
    "Если профиль уже есть в другом мессенджере, получите там код через /link "
    "и отправьте сюда /link КОД. Иначе отправьте другой email."
)

LINK_DONE_TEXT = "Мессенджер подключён. Теперь профиль, гардероб и образы общие для Telegram и MAX."
LINK_PRIVATE_TEXT = "Для подключения аккаунта напишите боту в личные сообщения."


def link_code_text(result: dict) -> str:
    target = "MAX" if result["target_platform"] == "max" else "Telegram"
    return (
        f"Откройте нашего бота в {target} и отправьте:\n"
        f"<code>/link {html.escape(result['code'])}</code>\n\n"
        "Код действует 10 минут и подходит для одного подключения. Не передавайте его другим людям."
    )
PROFILE_ALREADY_EXISTS_TEXT = "Профиль уже существует."
REGISTRATION_DONE_TEXT = "Регистрация завершена."


def user_guide_text(*, with_manual: bool) -> str:
    """Инструкция после регистрации."""
    text = (
        "<b>Как пользоваться ботом</b>\n\n"
        "• <b>Добавить вещь.</b> Просто пришлите фото — я распознаю вещь и сохраню её в гардероб.\n"
        f"• <b>{PROFILE_BUTTON_TEXT}.</b> Здесь ваш гардероб, подбор образа и все созданные образы.\n"
        f"• <b>{OUTFIT_BUTTON_TEXT}.</b> Кнопка появится, как только в гардеробе будет хотя бы одна вещь."
    )
    if with_manual:
        text += "\n\nПодробности — в руководстве во вложении."
    return text

# Подбор образа

NO_ITEMS_FOR_OUTFIT_TEXT = (
    "В вашем гардеробе пока нет вещей.\n"
    "Сначала отправьте хотя бы одну фотографию вещи, и затем можно будет собрать образ."
)
ASK_EVENT_TEXT = (
    "Напишите событие. Например: <code>офис</code>, <code>свидание</code>, "
    "<code>прогулка</code>, <code>вечеринка</code>."
)
EVENT_AS_TEXT_TEXT = "Нужно текстом указать событие."
ASK_WISHES_TEXT = (
    "Есть пожелания? Например: <code>что-то тёплое</code>, "
    "<code>в чёрных тонах</code>. Можно написать <code>-</code>."
)
WISHES_AS_TEXT_TEXT = "Нужно текстом указать пожелания или написать <code>-</code>."
GENERATING_OUTFIT_TEXT = "Подбираю outfit..."

# Вещи

UPLOAD_ACCEPTED_TEXT = "Запрос принят. Обрабатываю фото..."
UPLOAD_UNEXPECTED_ERROR_TEXT = "Не удалось обработать фото. Попробуйте ещё раз чуть позже."
OUTFIT_ITEMS_TITLE = "Вещи для образа:"
OUTFIT_ITEMS_EMPTY_TEXT = "Для этого образа не удалось получить список вещей."
WARDROBE_ITEMS_TITLE = "Ваши вещи:"
WARDROBE_EMPTY_TEXT = "В вашем гардеробе пока нет вещей."
PHOTO_NOT_LOADED_SUFFIX = "\n<b>Фото:</b> не удалось загрузить"


# Ошибки

def code(value: Any) -> str:
    return f"<code>{html.escape(str(value))}</code>"


def service_busy_text(exc: RateLimited) -> str:
    minutes = -(-int(exc.retry_after) // 60)
    wait = f"{minutes} мин." if exc.retry_after >= 60 else "минуту"
    return f"Сервис временно недоступен. Попробуйте через {wait}"


def api_error_text(action: str, exc: Exception) -> str:
    """Текст ошибки для пользователя."""
    if isinstance(exc, RateLimited):
        return f"Не удалось {action}. {service_busy_text(exc)}"
    return f"Не удалось {action}: {code(exc)}"


def profile_error_text(user_id: int, exc: Exception) -> str:
    if isinstance(exc, RateLimited):
        return f"Не удалось получить профиль. {service_busy_text(exc)}"
    return (
        "Не удалось получить профиль.\n"
        f"Ваш ID: {code(user_id)}\n"
        f"Ошибка API: {code(exc)}"
    )


def not_in_system_text(user_id: int, exc: Exception) -> str:
    if isinstance(exc, RateLimited):
        return service_busy_text(exc)
    return (
        "Я не нашёл вас в системе Smart Wardrobe.\n"
        "Попросите администратора зарегистрировать вас в API через endpoint <code>POST /persons/</code>.\n\n"
        f"Ваш ID: {code(user_id)}\n"
        f"Ошибка API: {code(exc)}"
    )


# Форматирование

def format_person(person: dict) -> str:
    nickname = html.escape(str(person.get("nickname") or "Не указано"))
    email = html.escape(str(person.get("email") or "Не указан"))
    items_count = person.get("active_items_count")
    if items_count is None:
        items_count = len(person_items(person))

    return (
        "<b>Ваш профиль</b>\n"
        f"<b>Имя:</b> {nickname}\n"
        f"<b>Email:</b> {email}\n"
        f"<b>Вещей в гардеробе:</b> {items_count}"
    )


def _item_attributes(item: dict, default: str) -> list[tuple[str, str]]:
    return [
        ("Цвет", display_value(item.get("color"), default)),
        ("Стиль", display_value(item.get("style"), default)),
        ("Сезон", display_value(item.get("season"), default)),
        ("Материал", display_value(item.get("material"), default)),
    ]


def format_item(item: dict) -> str:
    """Карточка только что загруженной вещи."""
    item = normalize_item(item)
    rows = [("Название", display_value(item.get("item_type")))] + _item_attributes(item, "Не указано")
    return "\n".join(f"<b>{label}:</b> {html.escape(value)}" for label, value in rows)


def format_wardrobe_categories(result: dict) -> str:
    return (
        f"<b>Ваш гардероб</b> · {int(result.get('total') or 0)} вещей\n\n"
        "Выберите категорию:"
    )


def format_wardrobe_gallery(result: dict) -> str:
    category = result.get("category") or {}
    return (
        f"<b>{html.escape(str(category.get('label') or 'Гардероб'))}</b>\n"
        f"Страница {result.get('page', 1)} из {result.get('total_pages', 1)}"
    )


def format_wardrobe_item(item: dict) -> str:
    category = html.escape(str(item.get("category_label") or "Другое"))
    return f"<b>{category}</b>\n\n{format_item(item)}"


def format_edit_fields(item: dict) -> str:
    return f"<b>{html.escape(display_value(item.get('item_type'), 'Вещь'))}</b>\n\nКакой параметр изменить?"


def format_edit_value(field: str) -> str:
    return f"Введите новое значение для поля <b>{EDIT_FIELDS[field].lower()}</b>."


def format_edit_options(field: str, result: dict) -> str:
    return (
        f"Выберите новое значение для поля <b>{EDIT_FIELDS[field].lower()}</b>.\n"
        f"Страница {result.get('page', 1)} из {result.get('total_pages', 1)}"
    )


def format_delete_confirmation(item: dict) -> str:
    name = html.escape(display_value(item.get("item_type"), "эту вещь"))
    return f"Убрать <b>{name}</b> из гардероба?"


def format_item_caption(item: dict, index: int) -> str:
    """Подпись к вещи в списке (образ или гардероб)."""
    item = normalize_item(item)
    rows = [("Тип", display_value(item.get("item_type"), "Вещь"))] + _item_attributes(item, "Не указан")
    lines = [f"<b>Вещь {index}</b>"]
    lines += [f"<b>{label}:</b> {html.escape(value)}" for label, value in rows]
    return "\n".join(lines)


def format_outfit_summary(result: dict) -> str:
    missing_items = result.get("missing_item_types") or []
    weather = result.get("weather")
    wish = result.get("user_wish")
    accessories = result.get("llm_accessories_advice")

    lines = [
        "<b>Ваш образ готов</b>",
        "",
        f"<b>Название образа:</b> {html.escape(result.get('name') or 'Без названия')}",
        f"<b>Событие:</b> {html.escape(result.get('event') or 'Не указано')}",
        f"<b>Дресс-код:</b> {html.escape(result.get('dress_code_type') or 'Не указан')}",
        f"<b>Локация:</b> {html.escape(result.get('geolocation') or 'Не указана')}",
    ]
    if weather:
        lines.append(f"<b>Погода:</b> {html.escape(weather)}")
    if wish:
        lines.append(f"<b>Пожелания:</b> {html.escape(wish)}")

    lines.append("")
    lines.append(f"<b>Рекомендация:</b> {html.escape(result.get('recommendation') or 'Нет рекомендации')}")

    if accessories:
        lines.append(f"<b>Аксессуары:</b> {html.escape(accessories)}")
    if missing_items:
        lines.append(f"<b>Чего не хватает:</b> {html.escape(', '.join(str(i) for i in missing_items))}")

    lines.append(f"<b>Статус комплекта:</b> {'Частичный образ' if result.get('is_partial') else 'Полный образ'}")
    return "\n".join(lines)


def format_outfit_history(result: dict) -> str:
    lines = ["<b>Старые луки</b>", "", "Выберите лук:"]
    if result.get("total_pages", 0) > 1:
        lines += ["", f"Страница {result.get('page', 1)} из {result['total_pages']}"]
    return "\n".join(lines)


def format_saved_outfit(result: dict) -> str:
    lines = [
        "<b>Сохранённый лук</b>",
        "",
        f"<b>Название:</b> {html.escape(result.get('name') or 'Без названия')}",
        f"<b>Событие:</b> {html.escape(result.get('event') or 'Не указано')}",
        f"<b>Дресс-код:</b> {html.escape(result.get('dress_code_type') or 'Не указан')}",
        f"<b>Локация:</b> {html.escape(result.get('geolocation') or 'Не указана')}",
    ]
    if result.get("weather"):
        lines.append(f"<b>Погода:</b> {html.escape(result['weather'])}")
    if result.get("user_wish"):
        lines.append(f"<b>Пожелания:</b> {html.escape(result['user_wish'])}")
    if result.get("llm_accessories_advice"):
        lines.append(f"<b>Аксессуары:</b> {html.escape(result['llm_accessories_advice'])}")
    return "\n".join(lines)


def format_coordinates(latitude: float, longitude: float) -> str:
    return f"{latitude:.6f}, {longitude:.6f}"
