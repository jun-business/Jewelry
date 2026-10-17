"""앱 컨텍스트: DB·설정·어댑터를 묶어 서비스 함수에 넘긴다."""
from __future__ import annotations

from dataclasses import dataclass, field

from cryptography.fernet import Fernet

from .core.config import Config
from .core.db import Database


@dataclass
class Ctx:
    cfg: Config
    db: Database
    forms: object = None        # adapters.google_forms.FormsSource
    files: object = None        # adapters.google_forms.FileSource
    analyzer: object = None     # adapters.ai.Analyzer
    extra: dict = field(default_factory=dict)

    @property
    def fernet(self) -> Fernet:
        return Fernet(self.cfg.secret("JOS_DATA_KEY").encode())

    def enc(self, s: str | None) -> str | None:
        if s is None or s == "":
            return None
        return self.fernet.encrypt(s.encode()).decode()

    def dec(self, s: str | None) -> str | None:
        if not s:
            return None
        return self.fernet.decrypt(s.encode()).decode()
