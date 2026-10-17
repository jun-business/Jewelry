"""작업량: 상한·휴무일·CAD 슬롯·휴지 시간(FR-ADM-004·005, EXP-005·010, DD-009)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ..core import clock
from ..domain.states import ACTIVE_ACCEPTED
from . import settings

# 2026–2027 대한민국 공휴일 기본 목록(ASM-007 — 설정에서 추가·삭제)
DEFAULT_HOLIDAYS = {
    "2026-01-01": "신정", "2026-02-16": "설날 연휴", "2026-02-17": "설날", "2026-02-18": "설날 연휴",
    "2026-03-01": "삼일절", "2026-03-02": "대체공휴일", "2026-05-05": "어린이날", "2026-05-24": "부처님오신날",
    "2026-05-25": "대체공휴일", "2026-06-03": "전국동시지방선거", "2026-06-06": "현충일", "2026-08-15": "광복절",
    "2026-08-17": "대체공휴일", "2026-09-24": "추석 연휴", "2026-09-25": "추석", "2026-09-26": "추석 연휴",
    "2026-10-03": "개천절", "2026-10-05": "대체공휴일", "2026-10-09": "한글날", "2026-12-25": "성탄절",
    "2027-01-01": "신정", "2027-02-06": "설날 연휴", "2027-02-07": "설날", "2027-02-08": "설날 연휴",
    "2027-02-09": "대체공휴일", "2027-03-01": "삼일절", "2027-05-05": "어린이날", "2027-05-13": "부처님오신날",
    "2027-06-06": "현충일", "2027-08-15": "광복절", "2027-08-16": "대체공휴일", "2027-09-14": "추석 연휴",
    "2027-09-15": "추석", "2027-09-16": "추석 연휴", "2027-10-03": "개천절", "2027-10-04": "대체공휴일",
    "2027-10-09": "한글날", "2027-10-11": "대체공휴일", "2027-12-25": "성탄절",
}


def load_default_holidays(ctx) -> int:
    with ctx.db.tx() as c:
        n = 0
        for d, name in DEFAULT_HOLIDAYS.items():
            n += c.execute("INSERT OR IGNORE INTO holidays(date, name) VALUES (?,?)", (d, name)).rowcount
    return n


def holiday_name(ctx, d: date) -> str | None:
    r = ctx.db.one("SELECT name FROM holidays WHERE date=?", (d.isoformat(),))
    return r["name"] if r else None


def is_holiday_today(ctx) -> bool:
    return holiday_name(ctx, clock.today_kst()) is not None


def active_count(ctx) -> int:
    marks = ",".join("?" * len(ACTIVE_ACCEPTED))
    return ctx.db.scalar(f"SELECT COUNT(*) FROM orders WHERE lifecycle_state IN ({marks})",
                         [s.value for s in ACTIVE_ACCEPTED])


def limit(ctx) -> int:
    return int(settings.get(ctx, "capacity_limit"))


def at_capacity(ctx) -> bool:
    return active_count(ctx) >= limit(ctx)


# ---- 휴지 시간(DD-009) ----
def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def release_at(ctx, created: datetime) -> datetime:
    """휴지 시간 안에 생긴 알림은 휴지 종료 시각에 배지에 반영."""
    qs, qe = _hm(settings.get(ctx, "quiet_start")), _hm(settings.get(ctx, "quiet_end"))
    k = clock.kst(created)
    t = k.timetz().replace(tzinfo=None)
    if qs == qe:
        return created
    if qs < qe:
        inside = qs <= t < qe
        end_day = k.date()
    else:  # 자정을 넘는 구간(예 22:00–06:00)
        inside = t >= qs or t < qe
        end_day = k.date() + timedelta(days=1) if t >= qs else k.date()
    if not inside:
        return created
    return datetime.combine(end_day, qe, tzinfo=clock.KST).astimezone(created.tzinfo)


def in_quiet_hours(ctx, at: datetime | None = None) -> bool:
    at = at or clock.now()
    return release_at(ctx, at) != at


def after_midnight(at: datetime | None = None) -> bool:
    k = clock.kst(at or clock.now())
    return 0 <= k.hour < 5


# ---- CAD 슬롯(EXP-010, SRS-125) ----
def suggest_cad_slots(ctx, n: int = 2, start: date | None = None) -> list[date]:
    weekdays = set(settings.get(ctx, "cad_weekdays"))
    on_hol = bool(settings.get(ctx, "cad_on_holidays"))
    d = start or clock.today_kst()
    out: list[date] = []
    taken = {r["date"] for r in ctx.db.all("SELECT date FROM cad_slots")}
    for _ in range(120):
        d += timedelta(days=1)
        if d.weekday() not in weekdays:
            continue
        if not on_hol and holiday_name(ctx, d):
            continue
        if d.isoformat() in taken:
            continue
        out.append(d)
        if len(out) >= n:
            break
    return out


def internal_eta(ctx, start: date | None = None) -> date:
    """내부 예상일 = 다음 CAD 슬롯 + 제조 기간(기본 14일). 고객 약속일로 자동 쓰지 않는다."""
    slots = suggest_cad_slots(ctx, 1, start)
    base = slots[0] if slots else (start or clock.today_kst())
    return base + timedelta(days=int(settings.get(ctx, "factory_days")))
