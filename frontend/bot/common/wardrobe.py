import secrets

PREFIX = "wd"
EDIT_FIELDS = {
    "item_type": "Тип",
    "color": "Цвет",
    "style": "Стиль",
    "season": "Сезон",
    "material": "Материал",
}


def new_token() -> str:
    return secrets.token_urlsafe(6)


def callback(token: str, action: str, value: str | int | None = None) -> str:
    parts = [PREFIX, token, action]
    if value is not None:
        parts.append(str(value))
    return ":".join(parts)


def parse_callback(value: str | None) -> tuple[str, str, str | None] | None:
    parts = (value or "").split(":", 3)
    if len(parts) < 3 or parts[0] != PREFIX:
        return None
    return parts[1], parts[2], parts[3] if len(parts) == 4 else None
