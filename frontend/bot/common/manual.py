"""Руководство пользователя (PDF), которое отправляется после регистрации.

Для Telegram и MAX разные файлы, они лежат в bot/assets/manual.
"""
import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from bot.common.config import ASSETS_DIR
from bot.common.logger import get_logger

logger = get_logger(__name__)

MANUAL_DIR = ASSETS_DIR / "manual"
MANUAL_FILES = {
    "telegram": "telegram-user-manual.pdf",
    "max": "max-user-manual.pdf",
}
MANUAL_TITLE = "Руководство пользователя"


@dataclass(frozen=True)
class UserManual:
    platform: str
    path: Path
    sha256: str
    content: bytes = field(repr=False)
    title: str = MANUAL_TITLE
    display_filename: str = f"{MANUAL_TITLE}.pdf"  # имя файла, которое увидит пользователь


def manual_path(platform: str, directory: Path = MANUAL_DIR) -> Path:
    try:
        return directory / MANUAL_FILES[platform]
    except KeyError:
        raise ValueError(f"Неизвестная платформа: {platform}") from None


@lru_cache(maxsize=None)
def load_manual(platform: str, directory: Path = MANUAL_DIR) -> Optional[UserManual]:
    path = manual_path(platform, directory)
    try:
        content = path.read_bytes()
    except OSError:
        logger.warning("Руководство для %s не найдено: %s — после регистрации уйдёт только текст", platform, path)
        return None
    if not content:
        logger.warning("Руководство для %s пустое: %s", platform, path)
        return None
    manual = UserManual(platform, path, hashlib.sha256(content).hexdigest(), content)
    logger.info("Руководство для %s: %s (%s байт, sha256=%s…)", platform, path.name, len(content), manual.sha256[:12])
    return manual
