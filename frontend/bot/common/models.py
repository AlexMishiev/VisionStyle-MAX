"""Типы ответов backend и функции для работы с ними."""
from typing import Any, Optional, TypedDict


class Lookup(TypedDict):
    id: int
    name: str


class ClothingItem(TypedDict, total=False):
    id: int
    owner_id: int
    addition_time: str
    status: int
    item_type: Lookup
    color: Lookup
    style: Lookup
    season: Lookup
    material: Lookup


class Person(TypedDict, total=False):
    id: int
    telegram_id: int
    email: str
    nickname: Optional[str]
    registration_time: str
    is_active: bool
    items: list[ClothingItem]
    outfits: list[dict]


class OutfitResult(TypedDict, total=False):
    outfit_id: Optional[int]
    name: str
    owner_id: int
    event: str
    geolocation: str
    weather: Optional[str]
    user_wish: Optional[str]
    dress_code_type: Optional[str]
    llm_accessories_advice: Optional[str]
    is_partial: bool
    missing_item_types: list[str]
    recommendation: str
    items: list[ClothingItem]


ITEM_FIELDS = ("id", "item_type", "color", "style", "season", "material")


def normalize_item(payload: Any) -> dict:
    """Достаёт вещь из ответа API."""
    if not isinstance(payload, dict):
        return {}

    for key in ("item", "clothing_item", "data", "result"):
        nested = payload.get(key)
        if isinstance(nested, dict) and any(field in nested for field in ITEM_FIELDS):
            return nested

    return payload


def display_value(value: Any, default: str = "Не указано") -> str:
    """Значение поля для показа пользователю."""
    if isinstance(value, dict):
        for key in ("name", "title", "label", "value"):
            nested = value.get(key)
            if nested not in (None, ""):
                return str(nested)
        return default

    if value in (None, ""):
        return default

    return str(value)


def person_items(person: Optional[dict]) -> list[dict]:
    return [item for item in (person or {}).get("items") or [] if item.get("status", 1) == 1]


def has_wardrobe_items(person: Optional[dict]) -> bool:
    if person and "active_items_count" in person:
        return bool(person["active_items_count"])
    return bool(person_items(person))
