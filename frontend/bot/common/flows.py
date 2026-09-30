"""Общая логика ботов: проверка доступа, регистрация, подбор образа."""
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from bot.common.api_client import ApiError, VisionStyleApi
from bot.common.logger import get_logger
from bot.common.models import has_wardrobe_items

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NO_WISHES_MARKER = "-"

logger = get_logger("bot.flows")


# Проверка ввода

def normalize_email(text: Optional[str]) -> str:
    return (text or "").strip().lower()


def is_valid_email(value: str) -> bool:
    return bool(EMAIL_PATTERN.fullmatch(value))


def parse_wishes(text: str) -> Optional[str]:
    """Если пользователь отправил "-", значит пожеланий нет."""
    wishes = text.strip()
    return None if wishes == NO_WISHES_MARKER else wishes


# Доступ

class AccessStatus(str, Enum):
    ALLOWED = "allowed"
    NOT_REGISTERED = "not_registered"
    INACTIVE = "inactive"
    ERROR = "error"


@dataclass
class AccessCheck:
    status: AccessStatus
    person: Optional[dict] = None
    error: Optional[ApiError] = None

    @property
    def allowed(self) -> bool:
        return self.status is AccessStatus.ALLOWED


async def check_access(api: VisionStyleApi, user_id: int) -> AccessCheck:
    try:
        person = await api.get_wardrobe_summary(user_id)
    except ApiError as exc:
        if exc.is_not_found:
            logger.info("Доступ user=%s: не зарегистрирован", user_id)
            return AccessCheck(AccessStatus.NOT_REGISTERED, error=exc)
        logger.warning("Доступ user=%s: не удалось проверить профиль: %s", user_id, exc)
        return AccessCheck(AccessStatus.ERROR, error=exc)

    if person.get("is_active") is False:
        logger.warning("Доступ user=%s: профиль person_id=%s заблокирован", user_id, person.get("id"))
        return AccessCheck(AccessStatus.INACTIVE, person=person)
    logger.debug("Доступ user=%s: разрешён (person_id=%s)", user_id, person.get("id"))
    return AccessCheck(AccessStatus.ALLOWED, person=person)


async def can_build_outfit(api: VisionStyleApi, user_id: int) -> tuple[bool, Optional[dict]]:
    try:
        person = await api.get_wardrobe_summary(user_id)
    except ApiError:
        person = None
    return has_wardrobe_items(person), person


# Регистрация

class RegistrationStatus(str, Enum):
    CREATED = "created"
    ALREADY_EXISTS = "already_exists"
    EMAIL_REJECTED = "email_rejected"
    ERROR = "error"


@dataclass
class RegistrationResult:
    status: RegistrationStatus
    person: Optional[dict] = None
    error: Optional[ApiError] = None


async def register_person(api: VisionStyleApi, user_id: int, email: str, nickname: str) -> RegistrationResult:
    logger.info("Регистрация user=%s: отправляем профиль в backend", user_id)
    try:
        person = await api.create_person(user_id=user_id, email=email, nickname=nickname)
        logger.info("Регистрация user=%s: создан профиль person_id=%s", user_id, person.get("id"))
        return RegistrationResult(RegistrationStatus.CREATED, person=person)
    except ApiError as exc:
        if not (exc.is_bad_request or exc.status_code == 409):
            logger.warning("Регистрация user=%s: ошибка backend: %s", user_id, exc)
            return RegistrationResult(RegistrationStatus.ERROR, error=exc)

    # 400 или 409: профиль уже есть или email занят
    try:
        person = await api.get_person(user_id)
    except ApiError as exc:
        logger.info("Регистрация user=%s: email отклонён или занят (%s)", user_id, exc)
        return RegistrationResult(RegistrationStatus.EMAIL_REJECTED, error=exc)
    logger.info("Регистрация user=%s: профиль уже существует (person_id=%s)", user_id, person.get("id"))
    return RegistrationResult(RegistrationStatus.ALREADY_EXISTS, person=person)
