"""템플릿 표기 규칙(PDS §6.11): KST 날짜·금액·라벨·상대 시각."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ..core import clock
from ..domain import states as st
from ..domain.money import won

WEEKDAY = "월화수목금토일"



def asset_version() -> str:
    """static/css·js 내용의 짧은 해시. 파일이 바뀌면 값이 바뀐다(기동 시 1회 계산)."""
    import hashlib
    from pathlib import Path
    h = hashlib.sha1()
    static = Path(__file__).resolve().parent / "static"
    for f in sorted([*static.glob("css/*.css"), *static.glob("js/*.js")]):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:10]

def kdate(v) -> str:
    if not v:
        return "—"
    if isinstance(v, str):
        if len(v) == 10:
            d = date.fromisoformat(v)
        else:
            d = clock.kst(clock.parse(v)).date()
    elif isinstance(v, datetime):
        d = clock.kst(v).date()
    else:
        d = v
    today = clock.today_kst()
    s = f"{d.month}월 {d.day}일({WEEKDAY[d.weekday()]})"
    return s if d.year == today.year else f"{d.year}년 {s}"


def ktime(v) -> str:
    if not v:
        return "—"
    dt = clock.kst(clock.parse(v) if isinstance(v, str) else v)
    return f"{dt:%H:%M}"


def kdatetime(v) -> str:
    if not v:
        return "—"
    return f"{kdate(v)} {ktime(v)}"


def rel(v) -> str:
    if not v:
        return ""
    dt = clock.parse(v) if isinstance(v, str) else v
    sec = (clock.now() - dt).total_seconds()
    if sec < 0:
        return ""
    if sec < 60:
        return "방금 전"
    if sec < 3600:
        return f"{int(sec // 60)}분 전"
    if sec < 86400:
        return f"{int(sec // 3600)}시간 전"
    if sec < 7 * 86400:
        return f"{int(sec // 86400)}일 전"
    return ""


def iso(v) -> str:
    return v or ""


def state_label(s) -> str:
    return st.LABEL[st.S(s)] if s else ""


def phase_label(s) -> str:
    return st.PHASE_LABEL[st.PHASE_OF[st.S(s)]]


def chip_kind(s) -> str:
    return st.CHIP_KIND[st.S(s)]


def sync_indicator(ctx) -> dict:
    from ..services import settings
    s = settings.all_settings(ctx)
    cur = ctx.db.one("SELECT * FROM sync_cursors WHERE provider='google_forms' AND scope=?", (s["google_form_id"],))
    if cur is None or not cur["last_success_at"]:
        if cur and cur["status"] == "ERROR":
            return {"level": "danger", "text": "연결 필요" if cur["last_error_kind"] == "AUTH_EXPIRED" else "동기화 오류"}
        return {"level": "muted", "text": "아직 동기화 전"}
    if cur["status"] == "ERROR" and cur["last_error_kind"] == "AUTH_EXPIRED":
        return {"level": "danger", "text": "연결 필요"}
    age = clock.now() - clock.parse(cur["last_success_at"])
    limit = timedelta(minutes=int(s["sync_interval_min"]) * int(s["alert_sync_multiplier"]))
    r = rel(cur["last_success_at"]) or kdatetime(cur["last_success_at"])
    if age > limit:
        return {"level": "warning", "text": f"동기화 지연 · 마지막 동기화 {r}"}
    return {"level": "ok", "text": f"정상 · 마지막 동기화 {r}"}


ACTION_TEXT = {
    "order_created": "의뢰를 접수했어요", "ingest_brief": "AI가 의뢰를 정리했어요", "to_review": "수락 검토로 넘어갔어요",
    "accept": "의뢰를 수락했어요", "decline": "의뢰를 거절했어요", "approve_quote": "견적을 승인했어요",
    "record_acceptance": "고객 동의를 기록했어요", "create_payment_req": "결제 요청을 만들었어요",
    "verify_initial": "첫 결제를 확인했어요", "start_cad": "상세 CAD를 시작했어요",
    "record_design_ok": "고객 최종 시안 동의를 기록했어요", "send_factory": "공장에 발주했어요",
    "receive": "완제품을 수령했어요", "pass_qc": "검수를 통과했어요", "verify_final": "잔금을 확인했어요",
    "ship": "발송했어요", "close": "주문을 완료했어요", "cancel": "주문을 취소했어요", "revert": "직전 단계로 되돌렸어요",
    "revalidate_design": "새 디자인으로 다시 확인이 필요해졌어요", "build_packet": "발주 패킷을 만들었어요",
    "hold_set": "보류 사유를 붙였어요", "hold_cleared": "보류 사유를 풀었어요", "issue_opened": "문제를 기록했어요",
    "issue_resolved": "문제를 해결했어요", "payment_recorded": "결제 이벤트를 입력했어요",
    "payment_request_created": "결제 요청을 만들었어요", "concept_uploaded": "시안을 올렸어요",
    "quote_approved": "견적 버전을 승인했어요", "requote_approved": "재견적을 승인했어요",
    "design_uploaded": "디자인 파일을 올렸어요", "factory_spec_saved": "공장 사양서를 저장했어요", "qc_recorded": "검수를 기록했어요",
    "cs_message_recorded": "상담 내용을 기록했어요", "cs_draft_created": "답변 초안을 만들었어요",
    "cs_draft_approved": "답변을 승인했어요", "cs_draft_copied": "답변을 복사했어요", "cs_draft_sent": "답변 발송을 기록했어요",
    "cs_draft_rewritten": "답변을 고쳐 쓰기 시작했어요", "cs_judgment_recorded": "운영자 판단을 기록했어요",
    "spec_corrected": "확정 사양을 정정했어요", "intake_revised": "고객이 응답을 고쳤어요", "memo": "메모를 남겼어요",
    "cad_slot_set": "CAD 날짜를 정했어요", "reference_uploaded": "참고 이미지를 추가했어요",
    "transition_rejected": "허용되지 않은 단계 변경 요청이 거부됐어요", "cancel_detail": "취소 내용을 기록했어요",
}


def action_label(action, row=None) -> str:
    return ACTION_TEXT.get(action, "기록을 남겼어요")


def install(env) -> None:
    import json as _json
    env.filters.update(won=won, kdate=kdate, ktime=ktime, kdatetime=kdatetime, rel=rel, state_label=state_label,
                       phase_label=phase_label, chip_kind=chip_kind, action_label=action_label,
                       fromjson=lambda s: _json.loads(s) if s else {},
                       maybe_won=lambda v: won(int(str(v).replace(",", ""))) if str(v).replace(",", "").isdigit() else v,
                       role_names=lambda roles: "·".join(st.ROLE_LABEL.get(r, r) for r in roles))
    env.globals["asset_v"] = asset_version()  # CSS·JS 링크의 ?v= — 업데이트 뒤 브라우저가 옛 파일을 쓰지 않게
    env.globals.update(S=st.S, LABEL=st.LABEL, HOLD_LABEL=st.HOLD_LABEL, ISSUE_LABEL=st.ISSUE_LABEL,
                       ISSUE_ICON=st.ISSUE_ICON, DECLINE_LABEL=st.DECLINE_LABEL, FAULT_LABEL=st.FAULT_LABEL, FAULT_LABEL_ALL=st.FAULT_LABEL_ALL,
                       TAX_TYPE_LABEL=st.TAX_TYPE_LABEL,
                       SOURCE_LABEL=st.SOURCE_LABEL, PAY_PHASE_LABEL=st.PAY_PHASE_LABEL,
                       PAY_STATUS_LABEL=st.PAY_STATUS_LABEL, NAVER_STATUS=st.NAVER_STATUS,
                       CS_LEVEL_LABEL=st.CS_LEVEL_LABEL, CS_STATUS_LABEL=st.CS_STATUS_LABEL,
                       ROLE_LABEL=st.ROLE_LABEL, WORK_KIND_LABEL=st.WORK_KIND_LABEL,
                       ASSET_KIND_LABEL=st.ASSET_KIND_LABEL, QC_ITEMS=st.QC_ITEMS, SPEC_FIELDS=st.SPEC_FIELDS, CONTRACT_SPEC_FIELDS=st.CONTRACT_SPEC_FIELDS,
                       FACTORY_SPEC_FIELDS=st.FACTORY_SPEC_FIELDS, QUEUES=st.QUEUES, GATES=st.GATES)
