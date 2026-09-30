"""Клавиатуры для MAX-бота."""
from bot.common import formatters as texts
from bot.common.legal import ACCEPT_BUTTON_TEXT
from bot.common.wardrobe import EDIT_FIELDS, callback as wardrobe_callback

PROFILE_CALLBACK = "menu:profile"
OUTFIT_CALLBACK = "menu:outfit"
PROFILE_ITEMS_CALLBACK = "profile:items"
PROFILE_OUTFITS_CALLBACK = "profile:outfits"
PROFILE_OUTFIT_CALLBACK = "profile:outfit"
MAP_POINT_CALLBACK = "location:map"
OUTFIT_CANCEL_CALLBACK = "outfit:cancel"
OUTFIT_HISTORY_PREFIX = "looks"


def callback(text: str, payload: str) -> dict:
    return {"type": "callback", "text": text, "payload": payload}


def keyboard(rows: list[list[dict]]) -> dict:
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


def main_menu(can_build_outfit: bool) -> dict:
    row = [callback(texts.PROFILE_BUTTON_TEXT, PROFILE_CALLBACK)]
    if can_build_outfit:
        row.append(callback(texts.OUTFIT_BUTTON_TEXT, OUTFIT_CALLBACK))
    return keyboard([row])


def profile_inline_keyboard(can_build_outfit: bool) -> dict:
    rows = [
        [callback(texts.SHOW_ITEMS_BUTTON_TEXT, PROFILE_ITEMS_CALLBACK)],
        [callback(texts.SHOW_OUTFITS_BUTTON_TEXT, PROFILE_OUTFITS_CALLBACK)],
    ]
    if can_build_outfit:
        rows.append([callback(texts.OUTFIT_BUTTON_TEXT, PROFILE_OUTFIT_CALLBACK)])
    return keyboard(rows)


def outfit_history_callback(action: str, value: int | None = None) -> str:
    parts = [OUTFIT_HISTORY_PREFIX, action]
    if value is not None:
        parts.append(str(value))
    return ":".join(parts)


def outfit_history(result: dict) -> dict:
    page = result.get("page", 1)
    page_size = result.get("page_size", 10)
    rows = []
    for index, outfit in enumerate(result.get("items") or [], (page - 1) * page_size + 1):
        name = str(outfit.get("name") or "Без названия")
        rows.append([callback(f"{index}. {name}"[:120], outfit_history_callback("item", outfit["id"]))])
    if result.get("total_pages", 0) > 1:
        nav = []
        if page > 1:
            nav.append(callback("←", outfit_history_callback("page", page - 1)))
        nav.append(callback(f"{page}/{result['total_pages']}", outfit_history_callback("noop")))
        if page < result["total_pages"]:
            nav.append(callback("→", outfit_history_callback("page", page + 1)))
        rows.append(nav)
    rows.append([callback(texts.RETURN_TO_PROFILE_BUTTON_TEXT, outfit_history_callback("profile"))])
    return keyboard(rows)


def outfit_detail() -> dict:
    return keyboard([[callback(texts.RETURN_TO_PROFILE_BUTTON_TEXT,
                               outfit_history_callback("profile"))]])


def location_keyboard() -> dict:
    return keyboard([
        [{"type": "request_geo_location", "text": "Отправить мою геолокацию", "quick": True}],
        [callback("Выбрать точку на карте", MAP_POINT_CALLBACK)],
        [callback(texts.CANCEL_OUTFIT_BUTTON_TEXT, OUTFIT_CANCEL_CALLBACK)],
    ])


def outfit_cancel_keyboard() -> dict:
    return keyboard([[callback(texts.CANCEL_OUTFIT_BUTTON_TEXT, OUTFIT_CANCEL_CALLBACK)]])


def wardrobe_categories(categories: list[dict], token: str) -> dict:
    rows = [[callback(f"{category['label']} ({category['count']})",
                      wardrobe_callback(token, "cat", category["key"]))]
            for category in categories]
    rows.append([callback(texts.RETURN_TO_PROFILE_BUTTON_TEXT, wardrobe_callback(token, "profile"))])
    return keyboard(rows)


def wardrobe_gallery(page: dict, token: str) -> dict:
    buttons = [callback(str(index), wardrobe_callback(token, "item", index))
               for index, _ in enumerate(page.get("items") or [], 1)]
    rows = [buttons[index:index + 3] for index in range(0, len(buttons), 3)]
    nav = []
    if page.get("page", 1) > 1:
        nav.append(callback("←", wardrobe_callback(token, "page", page["page"] - 1)))
    nav.append(callback(f"{page.get('page', 1)}/{page.get('total_pages', 1)}",
                        wardrobe_callback(token, "noop")))
    if page.get("page", 1) < page.get("total_pages", 1):
        nav.append(callback("→", wardrobe_callback(token, "page", page["page"] + 1)))
    rows.append(nav)
    rows.append([callback("К категориям", wardrobe_callback(token, "categories"))])
    rows.append([callback(texts.RETURN_TO_PROFILE_BUTTON_TEXT, wardrobe_callback(token, "profile"))])
    return keyboard(rows)


def wardrobe_item(token: str) -> dict:
    return keyboard([
        [callback("Изменить", wardrobe_callback(token, "edit")),
         callback("Удалить", wardrobe_callback(token, "delete"))],
        [callback("Назад к списку", wardrobe_callback(token, "back"))],
        [callback(texts.RETURN_TO_PROFILE_BUTTON_TEXT, wardrobe_callback(token, "profile"))],
    ])


def wardrobe_edit_fields(token: str) -> dict:
    buttons = [callback(label, wardrobe_callback(token, "field", field))
               for field, label in EDIT_FIELDS.items()]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([callback("Отмена", wardrobe_callback(token, "cancel"))])
    return keyboard(rows)


def wardrobe_edit_options(result: dict, token: str) -> dict:
    buttons = [callback(option["name"], wardrobe_callback(token, "set", option["id"]))
               for option in result.get("options") or []]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    nav = []
    if result.get("page", 1) > 1:
        nav.append(callback("←", wardrobe_callback(token, "opts", result["page"] - 1)))
    nav.append(callback(f"{result.get('page', 1)}/{result.get('total_pages', 1)}",
                        wardrobe_callback(token, "noop")))
    if result.get("page", 1) < result.get("total_pages", 1):
        nav.append(callback("→", wardrobe_callback(token, "opts", result["page"] + 1)))
    rows.append(nav)
    rows.append([callback("Отмена", wardrobe_callback(token, "cancel"))])
    return keyboard(rows)


def wardrobe_delete_confirmation(token: str) -> dict:
    return keyboard([[
        callback("Удалить", wardrobe_callback(token, "confirm_delete")),
        callback("Отмена", wardrobe_callback(token, "cancel")),
    ]])


def consent_accept(callback_data: str) -> dict:
    return keyboard([[callback(ACCEPT_BUTTON_TEXT, callback_data)]])
