import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from bot.common.config import LOGS_DIR, load_settings  # noqa: E402
from bot.common.logger import get_logger, setup_logging  # noqa: E402
from bot.max.main import main  # noqa: E402
from bot.max.client import MaxApiError  # noqa: E402
from bot.common.api_client import ApiError  # noqa: E402

logger = get_logger("bot.max")

if __name__ == "__main__":
    try:
        settings = load_settings("max")
        setup_logging(LOGS_DIR / "max.log", settings.log_level)
        logger.info("Запуск MAX-бота...")
        asyncio.run(main(settings))
    except (KeyboardInterrupt, SystemExit):
        logger.info("MAX-бот остановлен")
    except (MaxApiError, ApiError, RuntimeError, ValueError, OSError) as exc:
        logger.error("MAX-бот остановлен: %s", exc)
        sys.exit(1)
