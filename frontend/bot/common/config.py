"""Настройки ботов. Берутся из переменных окружения и файла .env.<бот>."""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values, load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]  # frontend/
LOGS_DIR = BASE_DIR / "logs"
ASSETS_DIR = BASE_DIR / "bot" / "assets"
# путь к сертификату backend (cert.pem), если пусто - используются системные сертификаты
API_TLS_CA_FILE_DEFAULT = "bot/assets/tls/cert.pem"

DEFAULT_BOT_DESCRIPTION = "Помогаю собрать образ из вашего гардероба с учетом события, погоды и локации."
DEFAULT_BOT_SHORT_DESCRIPTION = "Соберу стильный образ и покажу вещи из гардероба."


def _resolve_path(raw: str) -> Optional[Path]:
    raw = raw.strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else BASE_DIR / path


@dataclass(frozen=True)
class CommonSettings:
    """Настройки, общие для обоих ботов."""

    api_base_url: str
    api_username: str
    api_password: str
    request_timeout: float
    log_level: str
    bot_description: str
    bot_short_description: str
    bot_profile_photo_path: Optional[Path]
    api_tls_ca_file: Optional[Path] = None

    @classmethod
    def from_env(cls) -> "CommonSettings":
        return cls(
            api_base_url=os.getenv("API_BASE_URL", "https://localhost:8000").strip().rstrip("/"),
            api_tls_ca_file=_resolve_path(os.getenv("API_TLS_CA_FILE", "")),
            api_username=os.getenv("API_USERNAME", "").strip(),
            api_password=os.getenv("API_PASSWORD", ""),
            request_timeout=float(os.getenv("REQUEST_TIMEOUT", "60")),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip(),
            bot_description=os.getenv("BOT_DESCRIPTION", DEFAULT_BOT_DESCRIPTION).strip(),
            bot_short_description=os.getenv("BOT_SHORT_DESCRIPTION", DEFAULT_BOT_SHORT_DESCRIPTION).strip(),
            bot_profile_photo_path=_resolve_path(
                os.getenv("BOT_PROFILE_PHOTO_PATH", str(ASSETS_DIR / "logo.png"))
            ),
        )

    def validate_api_credentials(self) -> None:
        """Проверяет, что заданы логин и пароль для API."""
        if not self.api_username:
            raise RuntimeError("Не задан API_USERNAME")
        if not self.api_password:
            raise RuntimeError("Не задан API_PASSWORD")


def require_env(name: str) -> str:
    """Возвращает переменную окружения или выдаёт ошибку, если её нет."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Не задан {name}")
    return value


def env_defaults(platform: str) -> dict[str, str]:
    if platform not in {"telegram", "max"}:
        raise ValueError("Unknown messenger platform")
    values = {
        "API_BASE_URL": "https://localhost:8000",
        "API_TLS_CA_FILE": API_TLS_CA_FILE_DEFAULT,
        "API_USERNAME": "",
        "API_PASSWORD": "",
        "REQUEST_TIMEOUT": "60",
        "LOG_LEVEL": "INFO",
        f"{platform.upper()}_BOT_TOKEN": "",
    }
    if platform == "telegram":
        values.update(BOT_DESCRIPTION=DEFAULT_BOT_DESCRIPTION,
                      BOT_SHORT_DESCRIPTION=DEFAULT_BOT_SHORT_DESCRIPTION,
                      BOT_PROFILE_PHOTO_PATH="bot/assets/logo.png")
    else:
        values.update(MAX_API_BASE_URL="https://platform-api2.max.ru", MAX_TLS_CA_FILE="",
                      MAX_MODE="polling", MAX_WEBHOOK_URL="", MAX_WEBHOOK_SECRET="",
                      MAX_WEBHOOK_HOST="127.0.0.1", MAX_WEBHOOK_PORT="8081")
    return values


def create_env_file(platform: str, path: Path, *, migrate_legacy: bool = False) -> None:
    """Создаёт файл настроек, если его ещё нет."""
    values = env_defaults(platform)
    if path.exists():
        return
    if migrate_legacy:
        legacy = dotenv_values(BASE_DIR / ".env", interpolate=False, encoding="utf-8-sig")
        values.update({key: legacy[key] for key in values if legacy.get(key) is not None})
        if platform == "max":
            for key in ("API_USERNAME", "API_PASSWORD"):
                if legacy.get(f"MAX_{key}"):
                    values[key] = legacy[f"MAX_{key}"]
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            for key, value in values.items():
                escaped = value.replace("\\", "\\\\").replace("'", "\\'")
                stream.write(f"{key}='{escaped}'\n")
    except FileExistsError:
        return
    print(f"Создан конфиг {platform}: {path}")


def load_settings(platform: str) -> CommonSettings:
    """Загружает настройки для указанного бота."""
    defaults = env_defaults(platform)
    custom_path = os.getenv("ENV_FILE", "").strip()
    path = _resolve_path(custom_path) if custom_path else BASE_DIR / f".env.{platform}"
    create_env_file(platform, path, migrate_legacy=not custom_path)
    load_dotenv(path, override=False, interpolate=False, encoding="utf-8-sig")

    required = [f"{platform.upper()}_BOT_TOKEN", "API_USERNAME", "API_PASSWORD"]
    if platform == "max":
        mode = os.getenv("MAX_MODE", defaults["MAX_MODE"]).strip().lower()
        if mode not in {"polling", "webhook"}:
            raise ValueError("MAX_MODE: polling или webhook")
        if mode == "webhook":
            required.extend(["MAX_WEBHOOK_URL", "MAX_WEBHOOK_SECRET"])
    missing = [key for key in required if not os.getenv(key, "").strip()]
    if missing:
        raise RuntimeError(f"Заполните {', '.join(missing)} в {path} или переменных окружения. "
                           f"Учётная запись API должна иметь роль bot:{platform}.")
    settings = CommonSettings.from_env()
    settings.validate_api_credentials()
    return settings
