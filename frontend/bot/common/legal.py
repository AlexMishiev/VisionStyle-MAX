"""Документы, которые пользователь должен принять перед работой с ботом.

Файлы лежат в bot/assets/legal. Версия документа - это его sha256,
поэтому после замены PDF нужно перезапустить бота.
"""
import hashlib
import html
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from bot.common.config import ASSETS_DIR
from bot.common.logger import get_logger

logger = get_logger("bot.consent")

LEGAL_DIR = ASSETS_DIR / "legal"
USER_AGREEMENT = "user_agreement"
PERSONAL_DATA_CONSENT = "personal_data_consent"
CONSENT_CALLBACK_PREFIX = "consent:accept"
ACCEPT_BUTTON_TEXT = "Принимаю"


@dataclass(frozen=True)
class LegalDocument:
    document_type: str
    title: str
    display_filename: str  # имя файла, которое увидит пользователь
    path: Path
    sha256: str
    content: bytes = field(repr=False)


def read_url(path: Path) -> Optional[str]:
    """Читает ссылку из txt-файла (первая строка без #)."""
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = urlsplit(line)
        return line if parsed.scheme in {"http", "https"} and parsed.netloc else None
    return None


def _document(directory: Path, document_type: str, title: str) -> LegalDocument:
    path = directory / f"{document_type}.pdf"
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"Не найден документ «{title}»: {path}") from exc
    if not content:
        raise RuntimeError(f"Пустой документ «{title}»: {path}")
    return LegalDocument(document_type, title, f"{title}.pdf", path, hashlib.sha256(content).hexdigest(), content)


@dataclass(frozen=True)
class LegalDocuments:
    user_agreement: LegalDocument
    personal_data_consent: LegalDocument
    policy_url: Optional[str]

    @property
    def files(self) -> tuple[LegalDocument, LegalDocument]:
        return self.user_agreement, self.personal_data_consent

    @property
    def version(self) -> str:
        """Версия документов, чтобы кнопка работала только для актуальной версии."""
        joined = ":".join(doc.sha256 for doc in self.files)
        return hashlib.sha256(joined.encode()).hexdigest()[:16]

    @property
    def accept_callback(self) -> str:
        return f"{CONSENT_CALLBACK_PREFIX}:{self.version}"

    def payload(self) -> list[dict]:
        return [{"document_type": doc.document_type, "document_hash": doc.sha256} for doc in self.files]

    def accepted_by(self, records: list[dict]) -> bool:
        accepted = {(r.get("document_type"), str(r.get("document_hash", "")).lower()) for r in records or []}
        return all((doc.document_type, doc.sha256) in accepted for doc in self.files)

    def request_text(self, *, updated: bool = False) -> str:
        def link(title: str, url: Optional[str]) -> str:
            if not url:
                return title
            return f'<a href="{html.escape(url, quote=True)}">{title}</a>'

        intro = ("Документы обновились. Пожалуйста, ознакомьтесь с новой редакцией:"
                 if updated else "Прежде чем начать, ознакомьтесь с приложенными документами:")
        return (
            f"{intro}\n\n"
            "• Пользовательское соглашение\n"
            f"• Согласие на обработку персональных данных — в соответствии с "
            f"{link('Положением об обработке персональных данных', self.policy_url)}\n\n"
            f"Нажимая «{ACCEPT_BUTTON_TEXT}», вы принимаете Пользовательское соглашение и даёте "
            "согласие на обработку персональных данных. Без этого бот недоступен."
        )

    @staticmethod
    def reminder_text() -> str:
        return (f"Чтобы пользоваться ботом, ознакомьтесь с документами выше и нажмите «{ACCEPT_BUTTON_TEXT}».\n"
                "Документы заново — /start.")



def load_documents(directory: Path = LEGAL_DIR) -> LegalDocuments:
    return LegalDocuments(
        user_agreement=_document(directory, USER_AGREEMENT, "Пользовательское соглашение"),
        personal_data_consent=_document(directory, PERSONAL_DATA_CONSENT,
                                        "Согласие на обработку персональных данных"),
        policy_url=read_url(directory / "personal_data_policy_url.txt"),
    )


class ConsentGate:
    """Проверка и сохранение согласия пользователя."""

    def __init__(self, api, documents: LegalDocuments):
        self.api = api
        self.platform = getattr(api, "platform", "?")
        self.documents = documents
        self._accepted: set[int] = set()
        self._shown: set[int] = set()

    def is_cached(self, user_id: int) -> bool:
        return user_id in self._accepted

    async def is_accepted(self, user_id: int) -> bool:
        if user_id in self._accepted:
            return True
        records = await self.api.get_consents(user_id)
        if self.documents.accepted_by(records):
            self._accepted.add(user_id)
            logger.info("Согласие user=%s:%s: текущая версия принята ранее", self.platform, user_id)
            return True
        logger.info("Согласие user=%s:%s: НЕТ согласия на текущую версию документов (записей в backend: %s)",
                    self.platform, user_id, len(records or []))
        return False

    def should_send_files(self, user_id: int, *, force: bool = False) -> bool:
        """Файлы отправляем в первый раз и по /start, в остальных случаях только напоминание."""
        first = user_id not in self._shown
        self._shown.add(user_id)
        send = first or force
        logger.info("Согласие user=%s:%s: отправляем %s", self.platform, user_id,
                    "документы файлами" if send else "напоминание с кнопкой")
        return send

    async def accept(self, user_id: int, callback_data: Optional[str]) -> bool:
        """Сохраняет согласие. Возвращает False, если кнопка от старой версии документов."""
        if callback_data != self.documents.accept_callback:
            logger.warning("Согласие user=%s:%s: кнопка устаревшей версии %r (текущая %r) — не засчитано",
                           self.platform, user_id, callback_data, self.documents.accept_callback)
            return False
        await self.api.accept_consents(user_id, self.documents.payload())
        self._accepted.add(user_id)
        logger.info("Согласие user=%s:%s: принято и записано (%s)", self.platform, user_id,
                    ", ".join(f"{d.document_type}={d.sha256[:12]}…" for d in self.documents.files))
        return True
