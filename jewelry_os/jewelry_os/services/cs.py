"""카카오 상담 수동 기록·AI 초안·승인·복사·발송 기록(FUN-007, EXP-011, SRS-060~063·120·140)."""
from __future__ import annotations

import re
import secrets

from ..adapters.ai import AnalyzerError
from ..core import clock
from ..core.db import jdump, jload
from ..domain.guards import TransitionBlocked, unmet
from ..domain.states import LABEL, PHASE_LABEL, PHASE_OF, S
from . import audit, settings

_PHONE = re.compile(r"01[016789][-\s.]?(\d{3,4})[-\s.]?(\d{4})")


def redact(s: str | None) -> str | None:
    if not s:
        return s
    return _PHONE.sub(lambda m: f"010-****-{m.group(2)}", s)


def thread_for(ctx, c, order_id):
    t = c.execute("SELECT * FROM cs_threads WHERE order_id=? ORDER BY id LIMIT 1", (order_id,)).fetchone()
    if t:
        return t["id"]
    return c.execute("INSERT INTO cs_threads(order_id, created_at) VALUES (?,?)", (order_id, clock.now_iso())).lastrowid


def record_message(ctx, o, actor, summary, content=None, direction="in", link=None, event_at=None) -> int:
    if not (summary or "").strip():
        raise TransitionBlocked([unmet("G-REASON")])
    at, src = clock.from_kst_input(event_at)
    with ctx.db.tx() as c:
        tid = thread_for(ctx, c, o["id"])
        mid = c.execute("INSERT INTO cs_messages(thread_id, provider, sender_key, message_key, direction, summary, "
                        "content_redacted, link, event_at, event_at_src, recorded_by, recorded_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (tid, "kakao_manual", f"order:{o['id']}", secrets.token_hex(8), direction, redact(summary),
                         redact(content), link, at, src, actor.id, clock.now_iso())).lastrowid
        audit.record(c, "cs_message", mid, actor, "cs_message_recorded", order_id=o["id"], next=direction)
        return mid


def messages_of(ctx, order_id):
    return ctx.db.all("SELECT m.*, u.display_name AS recorder FROM cs_messages m JOIN cs_threads t ON t.id=m.thread_id "
                      "LEFT JOIN users u ON u.id=m.recorded_by WHERE t.order_id=? ORDER BY m.event_at", (order_id,))


def _phase_label(o) -> str:
    return PHASE_LABEL[PHASE_OF[S(o["lifecycle_state"])]]


INTERNAL_LABELS = set(LABEL.values()) - set(PHASE_LABEL.values())


def scrub_internal_labels(text: str) -> str:
    """고객 문구에 내부 단계명이 섞이지 않게(SRS-144)."""
    for lab in sorted(INTERNAL_LABELS, key=len, reverse=True):
        text = text.replace(lab, "진행")
    return text


INTAKE_KEYS = ("product_type", "description", "material", "size", "stones_engraving", "budget_range", "desired_date")


def contact_pref(answers: dict) -> str:
    """Form A ‘선호 연락 방법’ → sms | phone | kakao."""
    v = str((answers or {}).get("contact_kind") or "")
    return "sms" if "문자" in v else "phone" if "전화" in v else "kakao"


def product_words(o) -> str:
    return (o["product_type"] or "주얼리").replace(" / ", "·").replace("/", "·")


def first_contact_sms(ctx, o, answers: dict, q=None) -> str:
    """처음 보내는 문자 — 누가·왜 보냈는지 첫 줄에(S-14). 문자를 고른 고객에게는 카톡으로 옮기라고 하지 않고
    견적 요약을 문자에 그대로 담는다(S-45·S-48). q가 없으면 정보 요청 단계용 인사만."""
    from ..domain.money import won

    def kdate(v):
        try:
            d = __import__("datetime").date.fromisoformat(str(v)[:10])
            return f"{d.month}월 {d.day}일"
        except ValueError:
            return "견적서 참고"
    shop, name = settings.get(ctx, "shop_name"), o["customer_name"] or "고객"
    head = f"[{shop}] {name} 고객님, 보내 주신 {product_words(o)} 의뢰서 잘 받았어요."
    if q is None:
        return head
    if contact_pref(answers) == "sms":
        pay = (f"총 {won(q['total_krw'])}(예약금 {won(q['deposit_krw'])} · 잔금 {won(q['balance_krw'])})"
               if q["deposit_krw"] else f"총 {won(q['total_krw'])}")
        return (f"[{shop}] {name} 고객님, 의뢰하신 {product_words(o)} 시안과 견적이 준비됐어요.\n"
                f"{pay} · 완성 예정 {kdate(q['promised_date'])}.\n"
                "시안 그림과 견적서 파일을 이 문자에 함께 보내 드려요. 궁금한 점은 이 번호로 답장 주세요.")
    url = settings.kakao_chat_url(settings.get(ctx, "kakao_channel_url"))
    return (f"[{shop}] {name} 고객님, 의뢰하신 {product_words(o)} 시안과 견적이 준비됐어요. 카카오톡 채널({url})로 "
            "\"시안 받을게요\"라고 보내 주시면 바로 보내 드릴게요. 문자가 편하시면 이 번호로 답장 주셔도 돼요.")


def intake_context(ctx, o) -> dict:
    """AI에 함께 주는 의뢰서 내용 — 이름·연락처는 빼고 전화·이메일·주소는 가린다."""
    from . import analysis, intake
    answers, _rev = intake.answers_of(ctx, o)
    return {k: analysis.redact_text(str(answers[k]))[:400] for k in INTAKE_KEYS if answers.get(k)}


def public_process_facts(ctx) -> list[str]:
    """의뢰서(Form A) 안내·계약 동의서(Form B)로 이미 고객에게 공개된 진행 안내 — AI 답변 초안의 근거.
    상담 정책이 자리표시뿐이어도 기본 절차는 답할 수 있게(2026-10-25 시뮬레이션 S-11)."""
    facts = ["의뢰서를 검토한 뒤 무료 1차 시안과 확정 견적을 카카오톡으로 보내 드려요. 의뢰서 제출만으로 계약이 되지는 않아요.",
             "견적에 동의하시면 별도 계약 동의서 링크(Google 로그인 필요)로 동의를 받아요.",
             "예약금은 총액의 1/3로 계약 동의 후·상세 CAD 설계 시작 전에, 잔금 2/3는 최종 CAD 디자인 승인 후·공장 발주 전에 결제해요.",
             "일반적인 제조 기간은 약 2주이고, 상세 설계·고객 승인·배송 기간은 별도예요. 정확한 완성일은 견적서에 적어 드려요.",
             "예약금 결제 전 취소는 낼 돈이 없고, 예약금 결제 후 상세 CAD 시작 전 취소는 예약금 전액 환불이에요. 그 뒤 단계는 계약 "
             "동의서의 단계별 취소·환불 안내를 따라요.",
             "상품 하자나 계약한 사양과 다른 제작은 법령에 따라 수리·교환·환불해 드려요."]
    if settings.get(ctx, "quote_revision_scope_default"):
        facts.append(settings.get(ctx, "quote_revision_scope_default"))
    return facts


def create_draft(ctx, o, actor, question: str, body: str | None = None, level: str | None = None) -> int:
    """AI 초안(또는 직접 작성 — body·level 지정). 승인된 상담 정책만 근거로 참조(SRS-090)."""
    pol = settings.approved_policy(ctx, "cs")
    source = "manual" if body else "ai"
    unclear: list[str] = []
    if body is None:
        try:
            from . import cs_digest
            octx = {"phase_label": _phase_label(o), "shop_name": settings.get(ctx, "shop_name"),
                    "process_facts": public_process_facts(ctx),
                    # 의뢰서 내용(이름·연락처 제외) — 대화만 보고 '예산 미기재'라고 하지 않게(S-12)
                    "intake": intake_context(ctx, o)}
            dg = cs_digest.for_draft(ctx, o)
            if dg:
                octx["chat_digest"] = dg  # D-31: 대화 정리를 답변 근거로
            r = ctx.analyzer.draft_cs(question, octx, dict(pol) if pol else None)
            body, level, unclear = r["draft_reply"], r["level"], r.get("unclear") or []
        except AnalyzerError:
            body, level, source = "", level or "L2", "manual"
            unclear = ["AI 초안을 만들지 못했어요. 직접 작성하세요."]
    if not pol:
        unclear = list(unclear) + ["승인된 상담 정책 없음"]
    body = scrub_internal_labels(body or "")
    with ctx.db.tx() as c:
        tid = thread_for(ctx, c, o["id"])
        v = (c.execute("SELECT COALESCE(MAX(version),0) FROM cs_drafts WHERE thread_id=?", (tid,)).fetchone()[0]) + 1
        did = c.execute("INSERT INTO cs_drafts(thread_id, version, level, policy_id, policy_version, body, unclear_json, "
                        "status, source, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (tid, v, level or "L2", pol["id"] if pol else None, pol["version"] if pol else None, body,
                         jdump(unclear), "DRAFT", source, actor.id, clock.now_iso())).lastrowid
        audit.record(c, "cs_draft", did, actor, "cs_draft_created", order_id=o["id"], next=level)
        return did


def draft(ctx, draft_id):
    return ctx.db.one("SELECT d.*, t.order_id FROM cs_drafts d JOIN cs_threads t ON t.id=d.thread_id WHERE d.id=?",
                      (draft_id,))


def drafts_of(ctx, order_id):
    return ctx.db.all("SELECT d.*, p.title AS policy_title FROM cs_drafts d JOIN cs_threads t ON t.id=d.thread_id "
                      "LEFT JOIN policies p ON p.id=d.policy_id WHERE t.order_id=? ORDER BY d.id DESC", (order_id,))


def save_body(ctx, draft_id, actor, body):
    d = draft(ctx, draft_id)
    if d["status"] != "DRAFT":
        raise ImmutableDraft()
    with ctx.db.tx() as c:
        c.execute("UPDATE cs_drafts SET body=? WHERE id=?", (scrub_internal_labels(body), draft_id))


class ImmutableDraft(Exception):
    """승인·발송된 초안 수정 시도(405)."""


def record_judgment(ctx, draft_id, actor, text):
    if actor is None or actor.role != "operator":
        raise TransitionBlocked([unmet("G-ROLE-OPERATOR")])
    if not (text or "").strip():
        raise TransitionBlocked([unmet("G-REASON")])
    d = draft(ctx, draft_id)
    if d["status"] != "DRAFT":
        raise ImmutableDraft()
    with ctx.db.tx() as c:
        c.execute("UPDATE cs_drafts SET operator_judgment=?, judgment_by=?, judgment_at=? WHERE id=?",
                  (text, actor.id, clock.now_iso(), draft_id))
        audit.record(c, "cs_draft", draft_id, actor, "cs_judgment_recorded", order_id=d["order_id"])


def approve(ctx, draft_id, actor, confirm):
    if actor is None or actor.role != "maker":
        raise TransitionBlocked([unmet("G-ROLE-MAKER")])
    d = draft(ctx, draft_id)
    if d["status"] != "DRAFT":
        raise ImmutableDraft()
    um = []
    if d["level"] == "L3" and not d["operator_judgment"]:
        um.append(unmet("G-L3-JUDGMENT"))
    if not (d["body"] or "").strip():
        um.append(unmet("G-REASON"))
    if not confirm:
        um.append(unmet("G-CONFIRM"))
    if um:
        raise TransitionBlocked(um)
    with ctx.db.tx() as c:
        c.execute("UPDATE cs_drafts SET status='APPROVED', approved_by=?, approved_at=? WHERE id=?",
                  (actor.id, clock.now_iso(), draft_id))
        audit.record(c, "cs_draft", draft_id, actor, "cs_draft_approved", order_id=d["order_id"], next=d["level"])


def mark_copied(ctx, draft_id, actor):
    d = draft(ctx, draft_id)
    if d["status"] not in ("APPROVED", "SENT"):
        raise TransitionBlocked([unmet("G-CS-UNAPPROVED")])
    with ctx.db.tx() as c:
        c.execute("UPDATE cs_drafts SET copied_at=? WHERE id=?", (clock.now_iso(), draft_id))
        audit.record(c, "cs_draft", draft_id, actor, "cs_draft_copied", order_id=d["order_id"])
    return d["body"]


def mark_sent(ctx, draft_id, actor, sent_at=None, evidence=None):
    d = draft(ctx, draft_id)
    if d["status"] != "APPROVED":
        raise TransitionBlocked([unmet("G-CS-UNAPPROVED")])
    with ctx.db.tx() as c:
        c.execute("UPDATE cs_drafts SET status='SENT', sent_at=?, sent_evidence=? WHERE id=?",
                  (sent_at or clock.now_iso(), evidence, draft_id))
        tid = d["thread_id"]
        c.execute("INSERT INTO cs_messages(thread_id, provider, sender_key, message_key, direction, summary, "
                  "content_redacted, event_at, recorded_by, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (tid, "kakao_manual", f"order:{d['order_id']}", f"draft:{draft_id}", "out", "승인된 답변 발송",
                   d["body"], sent_at or clock.now_iso(), actor.id, clock.now_iso()))
        audit.record(c, "cs_draft", draft_id, actor, "cs_draft_sent", order_id=d["order_id"])


def rewrite(ctx, draft_id, actor) -> int:
    """고쳐 쓰기: 승인본은 그대로(SUPERSEDED), 내용을 복제한 새 초안(EXP-011)."""
    d = draft(ctx, draft_id)
    if d["status"] == "SENT":
        raise TransitionBlocked([unmet("G-WRONG-STATE")])
    if d["status"] != "APPROVED":
        raise TransitionBlocked([unmet("G-WRONG-STATE")])
    with ctx.db.tx() as c:
        v = (c.execute("SELECT MAX(version) FROM cs_drafts WHERE thread_id=?", (d["thread_id"],)).fetchone()[0]) + 1
        nid = c.execute("INSERT INTO cs_drafts(thread_id, version, level, policy_id, policy_version, body, unclear_json, "
                        "status, source, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (d["thread_id"], v, d["level"], d["policy_id"], d["policy_version"], d["body"],
                         d["unclear_json"], "DRAFT", "manual", actor.id, clock.now_iso())).lastrowid
        c.execute("UPDATE cs_drafts SET status='SUPERSEDED', superseded_by=? WHERE id=?", (nid, draft_id))
        audit.record(c, "cs_draft", draft_id, actor, "cs_draft_rewritten", order_id=d["order_id"], next=str(nid))
        return nid


def inbox(ctx):
    pending = ctx.db.all("SELECT d.*, t.order_id, o.order_no, c.display_name AS customer_name FROM cs_drafts d "
                         "JOIN cs_threads t ON t.id=d.thread_id JOIN orders o ON o.id=t.order_id "
                         "JOIN customers c ON c.id=o.customer_id WHERE d.status='DRAFT' "
                         "ORDER BY CASE d.level WHEN 'L3' THEN 0 WHEN 'L2' THEN 1 ELSE 2 END, d.created_at")
    to_send = ctx.db.all("SELECT d.*, t.order_id, o.order_no FROM cs_drafts d JOIN cs_threads t ON t.id=d.thread_id "
                         "JOIN orders o ON o.id=t.order_id WHERE d.status='APPROVED' ORDER BY d.approved_at")
    recent = ctx.db.all("SELECT m.*, t.order_id, o.order_no FROM cs_messages m JOIN cs_threads t ON t.id=m.thread_id "
                        "LEFT JOIN orders o ON o.id=t.order_id ORDER BY m.recorded_at DESC LIMIT 20")
    return {"pending": pending, "to_send": to_send, "recent": recent}


# ---------------- 단계별 고객 알림 문구(복사해서 카톡·문자로 보냄) — 2026-10-25 시뮬레이션 S-37·S-53·S-41 ----------------
def _hello(ctx, o) -> str:
    return f"[{settings.get(ctx, 'shop_name')}] {o['customer_name'] or '고객'} 고객님,"


def notice_cad_start(ctx, o) -> str:
    """상세 CAD 착수 알림 — 이 시점부터 취소 기준이 바뀌므로 고객이 알 수 있게 남긴다."""
    t = clock.today_kst()
    return "\n".join([f"{_hello(ctx, o)} 예약금 확인했어요. 오늘({t.month}월 {t.day}일) 상세 설계(CAD)를 시작합니다.",
                      "지금부터 취소하시면 계약 동의서의 ‘상세 CAD 작업 착수 후’ 기준으로 처리돼요.",
                      "최종 시안은 준비되는 대로 이 채팅으로 보내 드릴게요."])


def notice_design_request(ctx, o, design_version: int) -> str:
    """최종 시안 확인 요청 — 승인 전에 고객이 볼 항목을 체크리스트로(각인 철자는 확대 이미지로)."""
    from ..domain.states import SPEC_FIELDS
    from . import orders
    spec = orders.current_spec(ctx, o["id"])
    lines = [f"{_hello(ctx, o)} 최종 시안(디자인 v{design_version})을 보내 드려요. 아래를 하나씩 확인해 주세요."]
    lines += [f"· {lab}: {spec[k]}" for k, lab in SPEC_FIELDS if spec.get(k)]
    lines += ["· 각인 글자는 확대 이미지로 철자·글씨체를 꼭 확인해 주세요.",
              "이대로 괜찮으시면 ‘최종 시안 승인합니다’라고 답해 주세요. 고칠 곳이 있으면 편하게 말씀해 주세요.",
              "승인하시면 잔금 안내를 드리고, 그 뒤에는 사양을 바꾸기 어려워요."]
    return "\n".join(lines)


def notice_address_request(ctx, o) -> str:
    return "\n".join([f"{_hello(ctx, o)} 검수를 마쳤어요. 보내 드릴 곳을 알려 주세요.",
                      "· 받는 분 이름", "· 연락처", "· 주소(우편번호 포함)",
                      "배송에만 쓰고 안전하게 보관해요."])


def notice_shipped(ctx, o, shipment) -> str:
    via = " ".join(x for x in (shipment["carrier"], f"(송장 {shipment['tracking_no']})" if shipment["tracking_no"] else "")
                   if x) if shipment else ""
    return "\n".join([f"{_hello(ctx, o)} 검수를 마치고 오늘 보냈어요{(' — ' + via) if via else ''}.",
                      "받으시면 사이즈·각인·스톤 세팅을 확인해 주세요.",
                      "이상한 점이 있으면 사진과 함께 이 채팅으로 알려 주세요. 확인해서 안내드릴게요."])
