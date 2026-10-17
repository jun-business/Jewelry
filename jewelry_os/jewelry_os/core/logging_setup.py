"""로그 마스킹(SRS-082·099): 비밀값·토큰·전화번호가 로그에 남지 않게 한다."""
from __future__ import annotations

import logging
import re

_PATTERNS = [
    (re.compile(r"(sk-ant-[A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]+"), r"\1****"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+"), r"\1****"),
    (re.compile(r"((?:access|refresh)_token[\"'=:\s]+)[A-Za-z0-9._\-/]+"), r"\1****"),
    (re.compile(r"(client_secret[\"'=:\s]+)[A-Za-z0-9._\-]+"), r"\1****"),
    (re.compile(r"01[016789]-?\d{3,4}-?(\d{4})"), r"010-****-\1"),
]
_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    if value and len(value) >= 8:
        _secrets.add(value)


def mask(text: str) -> str:
    for s in _secrets:
        text = text.replace(s, s[:4] + "****")
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


class MaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask(record.getMessage())
        record.args = ()
        return True


def setup(log_path=None) -> logging.Logger:
    log = logging.getLogger("jos")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    handlers = [logging.StreamHandler()]
    if log_path:
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        h.addFilter(MaskingFilter())
        log.addHandler(h)
    return log
