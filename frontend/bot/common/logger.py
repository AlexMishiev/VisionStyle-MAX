import logging
import sys
from logging.config import dictConfig
from pathlib import Path

from colorlog import ColoredFormatter


LOG_FORMAT = "%(log_color)s%(asctime)s | %(levelname)s | %(name)s | %(message)s%(reset)s"
FILE_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_file: Path, log_level: str = "INFO") -> None:
    log_level = log_level.upper()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": ColoredFormatter,
                    "format": LOG_FORMAT,
                    "datefmt": DATE_FORMAT,
                    "log_colors": {
                        "DEBUG": "cyan",
                        "INFO": "green",
                        "WARNING": "yellow",
                        "ERROR": "red",
                        "CRITICAL": "bold_red",
                    },
                },
                "file": {
                    "format": FILE_LOG_FORMAT,
                    "datefmt": DATE_FORMAT,
                },
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": sys.stdout,
                },
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "file",
                    "filename": str(log_file),
                    "maxBytes": 5_000_000,
                    "backupCount": 5,
                    "encoding": "utf-8",
                },
            },
            "root": {
                "level": log_level,
                "handlers": ["default", "file"],
            },
            "loggers": {
                "aiogram": {"level": log_level},
                "httpx": {"level": "WARNING"},
                "httpcore": {"level": "WARNING"},
                "PIL": {"level": "WARNING"},
            },
        }
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
