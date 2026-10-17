"""주입 가능한 시계. 저장은 UTC, 표시는 Asia/Seoul(SRS-111)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")


class Clock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock(Clock):
    """시험용 — 시각을 고정하고 앞으로 감을 수 있다."""

    def __init__(self, at: datetime):
        self._at = at.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        self._at = at.astimezone(timezone.utc)

    def advance(self, **kw) -> None:
        self._at += timedelta(**kw)


_clock: Clock = Clock()


def set_clock(clock: Clock) -> None:
    global _clock
    _clock = clock


def get_clock() -> Clock:
    return _clock


def now() -> datetime:
    return _clock.now()


def now_iso() -> str:
    return iso(now())


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def from_kst_input(s: str | None) -> tuple[str, str | None]:
    """사람이 입력한 KST 시각(`YYYY-MM-DDTHH:MM` 또는 오프셋 포함) → (UTC ISO, 원문). DD-001 *_src."""
    if not s:
        return now_iso(), None
    raw = s.strip()
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return iso(dt), raw


def kst(dt: datetime) -> datetime:
    return dt.astimezone(KST)


def today_kst() -> date:
    return kst(now()).date()
