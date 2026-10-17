"""견적 계산(DD-004)."""
from __future__ import annotations


def split(total: int, policy: str) -> tuple[int, int | None]:
    """(예약금, 잔금). FULL이면 잔금 None(NOT_APPLICABLE). 1/3 정책: 예약금 = ceil(total/3)."""
    if total < 0:
        raise ValueError("음수 금액")
    if policy == "FULL":
        return total, None
    deposit = -(-total // 3)
    return deposit, total - deposit


def won(amount: int | None) -> str:
    if amount is None:
        return "—"
    return f"{amount:,}원"


def parse_won(s: str | int | None) -> int | None:
    if s is None:
        return None
    if isinstance(s, int):
        v = s
    else:
        t = str(s).replace(",", "").replace("원", "").strip()
        if t == "":
            return None
        if not t.lstrip("-").isdigit():
            raise ValueError("숫자만 입력하세요")
        v = int(t)
    if v < 0:
        raise ValueError("음수 금액은 입력할 수 없어요")
    return v
