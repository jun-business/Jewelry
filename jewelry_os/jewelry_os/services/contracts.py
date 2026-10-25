"""Form B 계약 동의(D-24) — 계약 요청 발행·사전입력 링크·응답 수집·검증·증거 보존.

원칙
- Form B 하나를 모든 고객이 함께 쓴다. 주문번호·견적번호·확인코드는 사전입력 링크로 채운다.
- 고객 응답은 그대로 믿지 않는다. 확인코드는 응답을 계약 요청에 잇는 열쇠일 뿐 본인 인증이 아니다.
- 본인 확인은 (1) 관리자가 전에 확인한 계약 이메일과 Google 인증 이메일이 같거나 (2) 관리자가 직접 확인해야 끝난다.
- 견적·고지 문구·정책은 발행 시점 원본을 남기고, 이력(contract_events)은 지우거나 고칠 수 없다.
- AI는 이 모듈을 쓰지 않는다. 계약 확정은 규칙 판정과 사람의 확인으로만 된다.
- 확인코드와 링크 전체는 로그·감사로그·오류 문구에 남기지 않는다."""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import urllib.parse
from functools import lru_cache
from pathlib import Path

from ..adapters.google import MockForms, SourceError
from ..core import clock
from ..core.db import jdump, jload
from ..domain import transitions as tr
from ..domain.guards import TransitionBlocked, unmet
from ..domain.states import ENDED, S
from . import audit, customers, files, quotes, settings

log = logging.getLogger("jos.contracts")
MAPPINGS_DIR = Path(__file__).resolve().parents[1] / "mappings"
PROVIDER = "google_forms"   # sync_cursors 범위는 폼 ID로 나뉜다(Form A와 같은 표·같은 재시도 규칙)

OPEN = ("ISSUED", "RESPONSE_RECEIVED", "REVIEW_REQUIRED", "ACCEPTED")
STATUS_LABEL = {"NOT_ISSUED": "발행 전", "ISSUED": "고객 응답 대기", "RESPONSE_RECEIVED": "응답 받음(검증 중)",
                "REVIEW_REQUIRED": "관리자 검토 필요", "ACCEPTED": "계약 유효", "REJECTED": "고객이 동의하지 않음",
                "SUPERSEDED": "대체됨(새 요청·새 견적)"}
STATUS_CHIP = {"NOT_ISSUED": "version", "ISSUED": "progress", "RESPONSE_RECEIVED": "progress",
               "REVIEW_REQUIRED": "hold", "ACCEPTED": "done", "REJECTED": "issue", "SUPERSEDED": "ended"}
PROBLEM_TEXT = {
    "ORDER_MISMATCH": "고객이 제출한 주문번호가 발행한 주문번호와 달라요.",
    "QUOTE_MISMATCH": "고객이 제출한 견적번호·버전이 발행한 견적과 달라요.",
    "QUOTE_SUPERSEDED": "발행 뒤 견적이 새 버전으로 바뀌었어요.",
    "QUOTE_EXPIRED": "견적 유효기간이 지난 뒤 제출됐어요.",
    "CUSTOMER_MISSING": "주문에 연결된 고객 기록이 없거나 개인정보가 삭제됐어요.",
    "SUBMITTED_BEFORE_ISSUE": "제출 시각이 계약 요청 발행보다 앞서요.",
    "NOTICE_CHANGED": "발행 뒤 Form B의 고지 문구·문항이 바뀌었어요.",
    "TERMS_CHANGED": "발행 뒤 적용 정책 문구가 바뀌었어요.",
    "CONSENT_MISSING": "{title}에 답이 없어요.",
    "REFUSED": "{title}에 동의하지 않았어요.",
    "NEEDS_EXPLANATION": "{title}: 고객이 추가 설명이 필요하다고 답했어요.",
    "CONSENT_UNKNOWN": "{title}: 알 수 없는 답이에요(폼 선택지가 바뀌었을 수 있어요).",
    "CONFLICTING_RESPONSES": "같은 계약 요청에 서로 다른 응답이 여러 건 있어요.",
    "MULTIPLE_RESPONSES": "같은 계약 요청에 응답이 {n}건 있어요(내용은 같음).",
    "IDENTITY_UNVERIFIED": "고객 본인 확인이 아직 안 됐어요.",
    "IDENTITY_OTHER_CUSTOMER": "제출한 Google 계정이 다른 고객의 확인된 계약 이메일이에요.",
    "OPERATOR_REVIEW_PENDING": "운영자 검증(응답·본인 확인)이 아직 안 됐어요.",
    "SCHEMA_CHANGED": "발행 당시와 Form B 문항 구성(버전)이 달라요.",
}
# 관리자 확인으로 풀 수 있는 것. 그 밖(불일치·거부·설명 요청·누락·고지 변경)은 새로 발행해야 한다.
SOFT = {"IDENTITY_UNVERIFIED", "IDENTITY_OTHER_CUSTOMER", "MULTIPLE_RESPONSES", "OPERATOR_REVIEW_PENDING"}
IDENTITY_METHODS = {"verified_email": "확인된 계약 이메일과 일치(시스템 대조)",
                    "existing_contact": "기존 연락처로 확인(전화·문자)",
                    "consult_channel": "확인된 상담 채널(카카오톡)에서 본인 제출 확인",
                    "in_person": "대면 확인", "other": "기타(메모에 근거)"}
DELIVERY_CHANNELS = {"kakao": "카카오톡", "sms": "문자", "other": "기타"}
# D-33 고객에게 보내는 묶음: ① 확정 견적서 ② 사전 고지(주문번호·견적번호·확인코드) ③ 계약 동의 링크
SEND_ITEMS = {"quote": "확정 견적서", "notice": "사전 고지(주문번호·견적번호·확인코드)", "link": "계약 동의(Form B) 링크"}
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # 0/O·1/I/L·U 제외


class ContractError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


# ---------------- 설정·매핑 ----------------
@lru_cache(maxsize=1)
def mapping() -> dict:
    return json.loads((MAPPINGS_DIR / "contract_v1.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def bundled_form() -> dict:
    """Form B forms.get 원본(2026-10-24). Mock 모드에서 고지 문구 근거로 쓴다."""
    return json.loads((MAPPINGS_DIR / "contract_v1_form.json").read_text(encoding="utf-8"))


def form_id(ctx) -> str:
    return (settings.get(ctx, "contract_form_id") or "").strip()


def readiness(ctx) -> dict:
    """링크를 만들 수 있는지. 폼 ID가 없거나 매핑에 질문 ID가 비면 '설정 대기'."""
    m = mapping()
    fid = form_id(ctx)
    missing = [k for k, v in m["prefill"].items() if not v.get("question_id") or not v.get("entry")]
    missing += [c["key"] for c in m["consents"] if not c.get("question_id")]
    reason = None
    if not fid:
        reason = "Form B 폼 ID가 비어 있어요"
    elif missing:
        reason = "Form B 문항 연결이 비어 있어요: " + ", ".join(missing)
    return {"ready": reason is None, "reason": reason, "form_id": fid, "prefill_check": m.get("prefill_check") or {}}


def live_status(ctx) -> dict:
    """설정 화면용: 지금 Form B를 읽어 게시·문항 일치를 확인(실패해도 화면은 뜬다)."""
    if not form_id(ctx):
        return {"ok": False, "message": "Form B 폼 ID가 비어 있어요"}
    try:
        body = _form_body(ctx, sleep=lambda s: None)
    except SourceError as e:
        return {"ok": False, "message": f"Form B를 읽지 못했어요({e.kind})"}
    bad = verify_mapping(body)
    mock = isinstance(ctx.forms, MockForms)
    return {"ok": not bad and (mock or is_published(body)), "mock": mock, "published": is_published(body),
            "revision": body.get("revisionId"), "title": (body.get("info") or {}).get("documentTitle"),
            "problems": bad, "responder_uri": body.get("responderUri")}


def is_published(body: dict) -> bool:
    """Google Forms '게시' 여부(forms.get publishSettings). 게시 전이면 고객이 링크를 열 수 없다."""
    st = (body.get("publishSettings") or {}).get("publishState") or {}
    return bool(st.get("isPublished")) and st.get("isAcceptingResponses", True) is not False


def verify_mapping(body: dict) -> list[str]:
    """forms.get 결과와 매핑 대조 — 질문 ID·선택지가 실제 폼과 같은지. 반환: 문제 목록(없으면 일치)."""
    qs = {}
    for it in body.get("items", []):
        q = (it.get("questionItem") or {}).get("question") or {}
        if q.get("questionId"):
            qs[q["questionId"]] = {"title": it.get("title", ""),
                                   "options": [o.get("value") for o in (q.get("choiceQuestion") or {}).get("options", [])]}
    out = []
    m = mapping()
    for k, v in m["prefill"].items():
        if v["question_id"] not in qs:
            out.append(f"{v['title']} 문항({v['question_id']})이 폼에 없어요")
        elif v["entry"] != f"entry.{int(v['question_id'], 16)}":
            out.append(f"{v['title']}의 사전입력 번호가 질문 ID와 맞지 않아요")
    for c in m["consents"]:
        q = qs.get(c["question_id"])
        if q is None:
            out.append(f"{c['title']} 문항({c['question_id']})이 폼에 없어요")
            continue
        for v in [c["accept"], *c.get("refuse", []), *c.get("explain", [])]:
            if v not in q["options"]:
                out.append(f"{c['title']} 선택지 '{v}'가 폼에 없어요")
    return out


# ---------------- 비밀값·해시 ----------------
def _key(ctx) -> bytes:
    return (ctx.cfg.secret("JOS_SECRET") or "jos").encode()


def _norm_code(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _norm_text(s: str | None) -> str:
    return re.sub(r"\s+", "", (s or "")).upper()


def code_hash(ctx, code: str) -> str:
    return hmac.new(_key(ctx), b"contract-code:" + _norm_code(code).encode(), hashlib.sha256).hexdigest()


def email_hash(ctx, email: str | None) -> str | None:
    if not email or not email.strip():
        return None
    return hmac.new(_key(ctx), b"email:" + email.strip().lower().encode(), hashlib.sha256).hexdigest()


def new_code() -> str:
    pick = lambda n: "".join(secrets.choice(CODE_ALPHABET) for _ in range(n))  # noqa: E731
    return f"TG-{pick(4)}-{pick(4)}"


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def policy_section(body: dict, key: str) -> dict:
    """Form B 원본에서 정책 섹션(청약철회 고지·단계별 환불·배송/A/S) 원문."""
    sec = mapping().get("policy_sections", {}).get(key) or {}
    for it in body.get("items", []):
        if it.get("itemId") == sec.get("item_id"):
            return {"title": it.get("title", ""), "text": it.get("description", ""),
                    "sha256": sha256_text(jdump({"title": it.get("title", ""), "text": it.get("description", "")}))}
    return {"title": sec.get("title", ""), "text": "", "sha256": None}


def notice_sha(body: dict) -> str:
    """고객이 보는 내용(제목·설명·문항·섹션 고지·선택지·이메일 수집 방식)의 해시.
    게시 상태·revisionId·응답 주소처럼 고객이 읽지 않는 값은 빼서, 게시만 바꿔도 '고지 변경'이 되지 않게 한다."""
    return sha256_text(jdump({"info": body.get("info"), "items": body.get("items"), "settings": body.get("settings")}))


def current_form_body(ctx) -> dict:
    """견적서에 넣을 Form B 원문 — 마지막으로 받은 실제 폼, 없으면 코드에 포함된 v1.1."""
    fid = form_id(ctx)
    r = ctx.db.one("SELECT body_json FROM contract_notices WHERE form_id=? ORDER BY id DESC LIMIT 1", (fid,)) if fid else None
    return jload(r["body_json"], {}) if r else bundled_form()


def quote_ref(q) -> str:
    """Q2에 넣는 견적서 번호·버전. 견적 문서에도 같은 값을 보여 준다."""
    return f"Q{q['id']:05d}-v{q['version']}"


def mask_email(email: str | None) -> str:
    if not email or "@" not in email:
        return "—"
    name, dom = email.split("@", 1)
    return (name[:2] + "***" if len(name) > 2 else name[:1] + "***") + "@" + dom


# ---------------- 조회 ----------------
def get(ctx, req_id):
    return ctx.db.one("SELECT * FROM contract_requests WHERE id=?", (req_id,))


def of_order(ctx, order_id):
    return ctx.db.all("SELECT * FROM contract_requests WHERE order_id=? ORDER BY id DESC", (order_id,))


def of_customer(ctx, customer_id):
    """고객 묶음(‘같은 고객으로 연결’ 포함)의 계약 동의 기록 — 주문별로 따로."""
    ids = customers.group_ids(ctx.db.conn, customer_id)
    return ctx.db.all(f"SELECT r.*, o.order_no FROM contract_requests r JOIN orders o ON o.id=r.order_id "
                      f"WHERE r.customer_id IN ({','.join('?' * len(ids))}) ORDER BY r.id DESC", ids)


def _identity_match(c, group, email_hash_):
    """확인된 계약 이메일 대조: (이 고객 묶음 것인가, 다른 고객 것인가)."""
    if not email_hash_:
        return False, False
    marks = ",".join("?" * len(group))
    own = c.execute(f"SELECT 1 FROM customer_identities WHERE customer_id IN ({marks}) AND kind='email' AND value_hash=?",
                    (*group, email_hash_)).fetchone()
    other = c.execute(f"SELECT 1 FROM customer_identities WHERE customer_id NOT IN ({marks}) AND kind='email' AND "
                      f"value_hash=?", (*group, email_hash_)).fetchone()
    return bool(own), bool(other)


def current(ctx, order_id):
    """현재 확정 견적 기준 요청(없으면 None). 거절된 요청도 돌려줘 재발행 버튼을 보이게 한다."""
    q = tr.latest_quote(ctx.db.conn, order_id, "APPROVED")
    if q is None:
        return None
    return ctx.db.one("SELECT * FROM contract_requests WHERE order_id=? AND quote_id=? AND status<>'SUPERSEDED' "
                      "ORDER BY id DESC LIMIT 1", (order_id, q["id"]))


def responses_of(ctx, req_id):
    return ctx.db.all("SELECT * FROM contract_responses WHERE request_id=? ORDER BY last_submitted_time, id", (req_id,))


def unmatched_for_order(ctx, order_id):
    return ctx.db.all("SELECT * FROM contract_responses WHERE request_id IS NULL AND candidate_order_id=? ORDER BY id",
                      (order_id,))


def events_of(ctx, req_id):
    return ctx.db.all("SELECT e.*, u.display_name FROM contract_events e LEFT JOIN users u ON u.id=e.actor_id "
                      "WHERE e.request_id=? ORDER BY e.id", (req_id,))


def problems_of(req) -> list[dict]:
    out = []
    for p in jload(req["problems_json"], []):
        text = PROBLEM_TEXT.get(p["code"], p["code"]).format(title=p.get("title", ""), n=p.get("n", ""))
        out.append({**p, "text": text, "soft": p["code"] in SOFT})
    return out


def answer_rows(resp) -> list[dict]:
    """동의 문항별 결과(화면·증거용)."""
    a = jload(resp["answers_json"], {})
    rows = []
    for c in mapping()["consents"]:
        v = (a.get("consents") or {}).get(c["key"])
        ok = v == c["accept"]
        rows.append({"title": c["title"], "answer": v or "(답 없음)", "ok": ok})
    return rows


# ---------------- 발행 ----------------
def _admin(actor):
    if actor is None or actor.role not in ("maker", "operator"):
        raise TransitionBlocked([unmet("G-ROLE-OPERATOR")])


def _event(c, req, event, actor, prev=None, nxt=None, detail=None):
    c.execute("INSERT INTO contract_events(request_id, order_id, event, prev_status, next_status, actor_id, actor_role, "
              "detail_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
              (req["id"], req["order_id"], event, prev, nxt, getattr(actor, "id", None),
               getattr(actor, "role", "system"), jdump(detail or {}), clock.now_iso()))


def _set_status(c, req, status, actor, event, detail=None, **cols):
    sets = ", ".join(f"{k}=?" for k in cols)
    c.execute(f"UPDATE contract_requests SET status=?{', ' + sets if sets else ''} WHERE id=?",
              (status, *cols.values(), req["id"]))
    _event(c, req, event, actor, req["status"], status, detail)
    o = c.execute("SELECT order_no FROM orders WHERE id=?", (req["order_id"],)).fetchone()
    audit.record(c, "contract", req["id"], actor, f"contract_{event}", order_id=req["order_id"], prev=req["status"],
                 next=status, ref_version=req["quote_version"], detail={"order_no": o["order_no"]})


def _form_body(ctx, sleep=time.sleep) -> dict:
    """현재 Form B 원본(forms.get). Mock 모드에 등록된 본문이 없으면 코드에 포함된 v1 원본."""
    fid = form_id(ctx)
    try:
        return _retry(lambda: ctx.forms.get_form(fid), sleep)
    except SourceError as e:
        if isinstance(ctx.forms, MockForms) and e.kind == "NOT_FOUND" and fid == bundled_form().get("formId"):
            return bundled_form()
        raise


def _notice_id(c, fid, body) -> int:
    raw = jdump(body)
    h = notice_sha(body)
    row = c.execute("SELECT id FROM contract_notices WHERE form_id=? AND sha256=?", (fid, h)).fetchone()
    if row:
        return row["id"]
    return c.execute("INSERT INTO contract_notices(form_id, revision_id, body_json, sha256, fetched_at) VALUES (?,?,?,?,?)",
                     (fid, body.get("revisionId"), raw, h, clock.now_iso())).lastrowid


def _terms(c, q):
    if not q["terms_policy_id"]:
        return None, None
    p = c.execute("SELECT * FROM policies WHERE id=?", (q["terms_policy_id"],)).fetchone()
    return p, sha256_text(jdump({"title": p["title"], "body": p["body"], "version": p["version"]})) if p else None


def quote_snapshot(ctx, q) -> str:
    pq = quotes.public_quote(ctx, q["id"])
    return jdump({"public_quote": dataclasses.asdict(pq), "quote_ref": quote_ref(q), "text": quotes.public_text(pq)})


def is_stale(req) -> bool:
    """화면 표시용: 응답 전인데 발행 뒤 Form B 문항 구성 버전이 바뀌었는가(이 링크로는 유효한 동의를 못 받음)."""
    return req["status"] == "ISSUED" and req["contract_form_schema_version"] != mapping()["version"]


def _reissuable(c, req, body: dict | None = None) -> bool:
    if req["status"] in ("REVIEW_REQUIRED", "REJECTED"):
        return True
    if req["status"] != "ISSUED":
        return False
    if req["contract_form_schema_version"] != mapping()["version"]:
        return True  # 2026-10-25 Form B v1.1 → v1.2 처럼 응답 전 폼이 바뀐 경우
    if body is not None:
        n = c.execute("SELECT body_json FROM contract_notices WHERE id=?", (req["notice_id"],)).fetchone()
        return notice_sha(jload(n["body_json"], {})) != notice_sha(body)
    return False


def issue(ctx, o, actor, reissue: bool = False, sleep=time.sleep) -> int:
    """계약 요청 발행. 같은 견적 버전에서는 멱등(새로고침·반복 클릭해도 요청 하나). 반환 요청 id.
    reissue: 검토가 필요하거나 고객이 거절한 요청, 또는 응답 전 Form B가 바뀐 요청을 대체하고 새 확인코드로 다시 발행."""
    _admin(actor)
    r = readiness(ctx)
    if not r["ready"]:
        raise TransitionBlocked([unmet("G-CONTRACT-FORM", reason=r["reason"])])
    body = None
    with ctx.db.tx() as c:
        cur = c.execute("SELECT * FROM orders WHERE id=?", (o["id"],)).fetchone()
        if S(cur["lifecycle_state"]) in ENDED:
            raise TransitionBlocked([unmet("G-ENDED")])
        q = tr.latest_quote(c, o["id"], "APPROVED")
        if q is None:
            raise TransitionBlocked([unmet("G-QUOTE-MISSING")])
        prev = c.execute("SELECT * FROM contract_requests WHERE order_id=? AND quote_id=? AND status<>'SUPERSEDED' "
                         "ORDER BY id DESC LIMIT 1", (o["id"], q["id"])).fetchone()
        if prev is not None and not (reissue and (prev["status"] in ("REVIEW_REQUIRED", "REJECTED") or
                                                  prev["status"] == "ISSUED")):
            return prev["id"]  # 멱등: 같은 견적 버전 요청이 이미 있다(ISSUED 재발행 여부는 폼 확인 뒤 판단)
    if q["valid_until"] and q["valid_until"] < clock.today_kst().isoformat():
        raise TransitionBlocked([unmet("G-QUOTE-EXPIRED")])
    from . import loss_review  # D-36: AI 검토 뒤 사람의 최종 판단
    if not loss_review.confirmed(ctx, q):
        raise TransitionBlocked([unmet("G-LOSS-CONFIRM")])  # 견적서에 주문별 개별 제작 특성·예상 손해 사유(D-25)
    try:
        body = _form_body(ctx, sleep)  # 네트워크는 트랜잭션 밖에서
    except SourceError as e:
        raise TransitionBlocked([unmet("G-CONTRACT-FORM", reason=f"Form B를 읽지 못했어요({e.kind})")])
    bad = verify_mapping(body)
    if not isinstance(ctx.forms, MockForms) and not is_published(body):
        bad = ["Form B가 아직 게시되지 않았거나 응답을 받지 않아요(Google Forms 편집 화면의 ‘게시’)"] + bad
    if bad:
        raise TransitionBlocked([unmet("G-CONTRACT-FORM", reason="; ".join(bad[:3]))])
    with ctx.db.tx() as c:
        q = tr.latest_quote(c, o["id"], "APPROVED")
        prev = c.execute("SELECT * FROM contract_requests WHERE order_id=? AND quote_id=? AND status<>'SUPERSEDED' "
                         "ORDER BY id DESC LIMIT 1", (o["id"], q["id"])).fetchone()
        if prev is not None:
            if not (reissue and _reissuable(c, prev, body)):
                return prev["id"]
            why = "재발행" if prev["status"] != "ISSUED" else "재발행(발행 뒤 Form B 변경)"
            _set_status(c, prev, "SUPERSEDED", actor, "superseded", {"reason": why},
                        superseded_at=clock.now_iso(), superseded_reason=why)
        cur = c.execute("SELECT * FROM orders WHERE id=?", (o["id"],)).fetchone()
        code = new_code()
        policy, terms_sha = _terms(c, q)
        snap = quote_snapshot(ctx, q)
        refund = policy_section(body, "refund")
        rid = c.execute(
            "INSERT INTO contract_requests(order_id, customer_id, quote_id, quote_version, quote_ref, status, code_hash, "
            "code_enc, form_id, contract_form_schema_version, notice_id, refund_policy_version, "
            "refund_policy_text_snapshot, refund_policy_sha256, terms_policy_id, terms_version, terms_sha256, "
            "quote_snapshot_json, quote_sha256, is_test, issued_by, issued_at, created_at) "
            "VALUES (?,?,?,?,?,'ISSUED',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (o["id"], cur["customer_id"], q["id"], q["version"], quote_ref(q), code_hash(ctx, code), ctx.enc(code),
             form_id(ctx), mapping()["version"], _notice_id(c, form_id(ctx), body),
             f"{mapping()['version']} · Form B rev {body.get('revisionId') or '-'}", refund["text"] or None,
             refund["sha256"],
             policy["id"] if policy else None, policy["version"] if policy else None, terms_sha, ctx.enc(snap),
             sha256_text(snap),
             q["is_test"] or cur["is_test"], actor.id, clock.now_iso(), clock.now_iso())).lastrowid
        req = c.execute("SELECT * FROM contract_requests WHERE id=?", (rid,)).fetchone()
        _event(c, req, "issued", actor, "NOT_ISSUED", "ISSUED",
               {"quote_ref": req["quote_ref"], "notice_id": req["notice_id"], "terms_version": req["terms_version"],
                "quote_sha256": req["quote_sha256"], "reissue": reissue})
        audit.record(c, "contract", rid, actor, "contract_issued", order_id=o["id"], next="ISSUED",
                     ref_version=q["version"], detail={"order_no": cur["order_no"], "quote_ref": req["quote_ref"]})
        c.execute("UPDATE orders SET version=version+1, updated_at=? WHERE id=?", (clock.now_iso(), o["id"]))
        return rid


def snapshot_of(ctx, req) -> str:
    """발행 당시 견적 스냅숏(JSON 원문). 고객 이름이 들어 있어 DB에는 암호문으로 둔다."""
    return ctx.dec(req["quote_snapshot_json"])


def code_of(ctx, req) -> str:
    return ctx.dec(req["code_enc"])


def prefill_url(ctx, req) -> str:
    """고객별 Form B 사전입력 링크. 화면에서만 만들고 저장·로그하지 않는다."""
    n = ctx.db.one("SELECT body_json FROM contract_notices WHERE id=?", (req["notice_id"],))
    base = jload(n["body_json"], {}).get("responderUri") or \
        f"https://docs.google.com/forms/d/{req['form_id']}/viewform"
    o = ctx.db.one("SELECT order_no FROM orders WHERE id=?", (req["order_id"],))
    p = mapping()["prefill"]
    q = [("usp", "pp_url"), (p["order_no"]["entry"], o["order_no"]), (p["quote_ref"]["entry"], req["quote_ref"]),
         (p["code"]["entry"], code_of(ctx, req))]
    return base + "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


def attach_quote_document(ctx, req_id, html: str, pdf: bytes | None = None) -> None:
    """발행 당시 고객 견적 문서(HTML, 스타일·로고 포함)와 PDF를 증거로 저장. 한 번 저장하면 바꿀 수 없다."""
    with ctx.db.tx() as c:
        req = c.execute("SELECT r.*, o.order_no FROM contract_requests r JOIN orders o ON o.id=r.order_id WHERE r.id=?",
                        (req_id,)).fetchone()
        sets = {}
        if not req["quote_html_asset_id"]:
            sets["quote_html_asset_id"] = files.store(ctx, c, order_id=req["order_id"], order_no=req["order_no"],
                                                      kind="contract_quote_html", filename=f"{req['quote_ref']}.html",
                                                      data=html.encode("utf-8"), validate=False)
        if pdf and not req["quote_pdf_asset_id"]:
            sets["quote_pdf_asset_id"] = files.store(ctx, c, order_id=req["order_id"], order_no=req["order_no"],
                                                     kind="contract_quote_pdf", filename=f"{req['quote_ref']}.pdf",
                                                     data=pdf, validate=False)
        if sets:
            c.execute(f"UPDATE contract_requests SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                      (*sets.values(), req_id))
            _event(c, req, "quote_document_saved", None, detail={k: True for k in sets})


def notice_message(ctx, req) -> str:
    """② 사전 고지 + ③ 링크 안내문(카카오톡으로 그대로 붙여 보냄). 견적 금액은 발행 당시 스냅숏에서."""
    snap = jload(snapshot_of(ctx, req), {}).get("public_quote") or {}
    o = ctx.db.one("SELECT order_no FROM orders WHERE id=?", (req["order_id"],))
    from ..domain.money import won
    shop = settings.get(ctx, "shop_name")
    kakao = settings.get(ctx, "kakao_channel_url")
    pay = (f"총 {won(snap.get('total') or 0)}" + (f" · 예약금 {won(snap['deposit'])}" if snap.get("deposit") else ""))
    lines = [f"[{shop}] 맞춤 주얼리 계약 동의 안내",
             f"{snap.get('customer_name') or '고객'} 님, 앞서 보내 드린 확정 견적서 내용으로 계약 동의를 받고 있어요.", "",
             f"· 주문번호: {o['order_no']}",
             f"· 견적서 번호·버전: {req['quote_ref']} ({pay})",
             f"· 계약 확인코드: {code_of(ctx, req)}", "",
             "아래 링크를 열면 위 세 가지가 미리 채워져 있어요. 값이 같은지 확인하신 뒤 동의 항목을 골라 제출해 주세요.",
             "제출하려면 Google 계정 로그인이 필요해요. 확인코드는 다른 분께 알려 주지 마세요.",
             f"▶ 계약 동의서: {prefill_url(ctx, req)}"]
    if kakao:
        lines += ["", f"궁금한 점은 카카오톡 채널 {shop}으로 물어봐 주세요: {kakao}/chat"]
    return "\n".join(lines)


def delivered_items(ctx, req) -> list[str]:
    """마지막 ‘보냄 기록’에서 보낸 항목."""
    e = ctx.db.one("SELECT detail_json FROM contract_events WHERE request_id=? AND event='delivered' ORDER BY id DESC "
                   "LIMIT 1", (req["id"],))
    return (jload(e["detail_json"], {}) or {}).get("items") or [] if e else []


def record_delivery(ctx, req_id, actor, channel: str, note: str, evidence: tuple | None = None,
                    items: list[str] | None = None) -> list[str]:
    """고객에게 보낸 사실(채널·보낸 항목·메모·선택: 화면 캡처). 반환: 빠진 항목(① 견적서 ② 사전 고지 ③ 링크 중)."""
    _admin(actor)
    if channel not in DELIVERY_CHANNELS or not (note or "").strip():
        raise TransitionBlocked([unmet("G-CONTRACT-DELIVERY")])
    items = [i for i in SEND_ITEMS if i in (items or [])]
    if not items:
        raise TransitionBlocked([unmet("G-CONTRACT-DELIVERY")])
    missing = [i for i in SEND_ITEMS if i not in items]
    with ctx.db.tx() as c:
        req = c.execute("SELECT r.*, o.order_no FROM contract_requests r JOIN orders o ON o.id=r.order_id WHERE r.id=?",
                        (req_id,)).fetchone()
        if req is None or req["status"] == "SUPERSEDED":
            raise TransitionBlocked([unmet("G-CONTRACT-STATE", status=STATUS_LABEL.get(req["status"] if req else "", "-"))])
        aid = None
        if evidence and evidence[1]:
            aid = files.store(ctx, c, order_id=req["order_id"], order_no=req["order_no"], kind="evidence",
                              filename=evidence[0], data=evidence[1], actor=actor)
        shown = "[보낸 것: " + "·".join(SEND_ITEMS[i].split("(")[0] for i in items) + "] " + note.strip()
        c.execute("UPDATE contract_requests SET delivered_at=?, delivered_channel=?, delivered_note=?, delivered_by=?, "
                  "delivered_asset_id=COALESCE(?, delivered_asset_id) WHERE id=?",
                  (clock.now_iso(), channel, shown, actor.id, aid, req_id))
        _event(c, req, "delivered", actor, detail={"channel": channel, "note": note.strip(), "evidence_asset": aid,
                                                   "items": items, "missing": missing})
        audit.record(c, "contract", req_id, actor, "contract_delivered", order_id=req["order_id"],
                     detail={"channel": channel, "items": items})
    return missing


def supersede_for_quote(c, order_id, keep_quote_id, actor, reason: str) -> int:
    """새 견적 버전 승인(또는 승인 되돌리기) 시 다른 견적 버전의 열린 요청을 대체 — 이전 동의를 재사용하지 않는다."""
    n = 0
    for req in c.execute("SELECT * FROM contract_requests WHERE order_id=? AND status IN "
                         "('ISSUED','RESPONSE_RECEIVED','REVIEW_REQUIRED','ACCEPTED','REJECTED') AND quote_id IS NOT ?",
                         (order_id, keep_quote_id)).fetchall():
        _set_status(c, req, "SUPERSEDED", actor, "superseded", {"reason": reason},
                    superseded_at=clock.now_iso(), superseded_reason=reason)
        n += 1
    return n


# ---------------- 응답 수집 ----------------
def _retry(fn, sleep, attempts=3):
    """일시 오류(사용량 제한·네트워크)는 짧게 두 번 더 시도. 그래도 실패하면 커서에 기록하고 다음 주기에 다시."""
    for i in range(attempts):
        try:
            return fn()
        except SourceError as e:
            if e.kind not in ("RATE_LIMITED", "NETWORK") or i == attempts - 1:
                raise
            sleep(min(e.retry_after or 2 ** i, 10))


def parse_response(r: dict) -> dict:
    vals = {}
    for qid, a in (r.get("answers") or {}).items():
        xs = [x.get("value") for x in (a.get("textAnswers") or {}).get("answers", []) if x.get("value") is not None]
        vals[qid] = xs[0] if len(xs) == 1 else (xs or None)
    m = mapping()
    out = {k: vals.get(v["question_id"]) for k, v in m["prefill"].items()}
    out["consents"] = {c["key"]: vals.get(c["question_id"]) for c in m["consents"]}
    return out


def sync_once(ctx, sleep=time.sleep) -> dict:
    """Form B 응답 전체를 다시 읽는다(모든 페이지). 중복 기준 (form_id, response_id) — 몇 번을 돌려도 기록은 한 건.
    PC가 꺼져 있던 동안의 응답도 다음 실행에서 그대로 들어온다."""
    from . import intake
    fid = form_id(ctx)
    stats = {"ok": True, "new": 0, "revised": 0, "total": 0, "error": None, "skipped": not fid}
    if not fid:
        return stats
    rows, token = [], None
    try:
        while True:
            page = _retry(lambda: ctx.forms.list_responses(fid, page_token=token), sleep)
            rows.extend(page.responses)
            token = page.next_page_token
            if not token:
                break
        body = _form_body(ctx, sleep) if rows else None
    except SourceError as e:
        intake._record_error(ctx, fid, e.kind, e.retry_after)
        stats.update(ok=False, error=e.kind)
        return stats
    if body is not None:
        with ctx.db.tx() as c:
            _notice_id(c, fid, body)  # 지금 폼 원본도 남긴다(고지 변경 판정 근거)
    for r in rows:
        stats["total"] += 1
        res = _ingest(ctx, fid, r)
        if res in ("new", "revised"):
            stats[res] += 1
    with ctx.db.tx() as c:
        c.execute("INSERT INTO sync_cursors(provider, scope, last_success_at, last_count, status, attempts) "
                  "VALUES (?, ?, ?, ?, 'OK', 0) ON CONFLICT(provider, scope) DO UPDATE SET "
                  "last_success_at=excluded.last_success_at, last_count=excluded.last_count, status='OK', attempts=0, "
                  "last_error_kind=NULL, next_attempt_at=NULL", (PROVIDER, fid, clock.now_iso(), stats["total"]))
    return stats


def _ingest(ctx, fid, r) -> str:
    raw = jdump(r)
    h = sha256_text(raw)
    a = parse_response(r)
    code = _norm_code(a.get("code"))
    a["code"] = None  # 확인코드는 평문으로 두지 않는다(원문은 암호화된 revision에만)
    email = r.get("respondentEmail")
    now = clock.now_iso()
    with ctx.db.tx() as c:
        row = c.execute("SELECT * FROM contract_responses WHERE form_id=? AND response_id=?",
                        (fid, r["responseId"])).fetchone()
        if row is not None:
            last = c.execute("SELECT raw_sha256 FROM contract_response_revisions WHERE response_row_id=? "
                             "ORDER BY revision DESC LIMIT 1", (row["id"],)).fetchone()
            if last and last["raw_sha256"] == h:
                return "same"
            rev = row["revision"] + 1
            c.execute("INSERT INTO contract_response_revisions(response_row_id, revision, last_submitted_time, raw_enc, "
                      "raw_sha256, fetched_at) VALUES (?,?,?,?,?,?)",
                      (row["id"], rev, r.get("lastSubmittedTime"), ctx.enc(raw), h, now))
            c.execute("UPDATE contract_responses SET revision=?, last_submitted_time=?, respondent_email_enc=?, "
                      "respondent_email_hash=?, answers_json=?, last_fetched_at=? WHERE id=?",
                      (rev, r.get("lastSubmittedTime"), ctx.enc(email), email_hash(ctx, email), jdump(a), now, row["id"]))
            req_id, result = row["request_id"], "revised"
        else:
            req = c.execute("SELECT * FROM contract_requests WHERE code_hash=?",
                            (code_hash(ctx, code),)).fetchone() if code else None
            cand = None
            if req is None and a.get("order_no"):
                o = c.execute("SELECT id FROM orders WHERE order_no=?", (_norm_text(a["order_no"]),)).fetchone()
                cand = o["id"] if o else None
            rid = c.execute("INSERT INTO contract_responses(form_id, response_id, request_id, candidate_order_id, "
                            "match_status, create_time, last_submitted_time, respondent_email_enc, respondent_email_hash, "
                            "answers_json, revision, first_fetched_at, last_fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)",
                            (fid, r["responseId"], req["id"] if req else None, cand,
                             "MATCHED" if req else "CODE_UNKNOWN", r.get("createTime"),
                             r.get("lastSubmittedTime") or r.get("createTime"), ctx.enc(email), email_hash(ctx, email),
                             jdump(a), now, now)).lastrowid
            c.execute("INSERT INTO contract_response_revisions(response_row_id, revision, last_submitted_time, raw_enc, "
                      "raw_sha256, fetched_at) VALUES (?,1,?,?,?,?)", (rid, r.get("lastSubmittedTime"), ctx.enc(raw), h, now))
            req_id, result = (req["id"] if req else None), "new"
            if req is None:
                audit.record(c, "contract_response", rid, None, "contract_response_unmatched", order_id=cand)
        if req_id is not None:
            req = c.execute("SELECT * FROM contract_requests WHERE id=?", (req_id,)).fetchone()
            if req["identity_status"] == "VERIFIED" and req["identity_method"] != "verified_email":
                # 관리자 본인 확인은 그때의 응답 기준 — 새 응답·수정이 오면 다시 확인
                c.execute("UPDATE contract_requests SET identity_status='PENDING' WHERE id=?", (req_id,))
                req = c.execute("SELECT * FROM contract_requests WHERE id=?", (req_id,)).fetchone()
            if req["status"] == "ISSUED":
                _set_status(c, req, "RESPONSE_RECEIVED", None, "response_received",
                            {"response_row_id": None if result == "revised" else rid})
            else:
                _event(c, req, "response_received" if result == "new" else "response_revised", None,
                       detail={"status": req["status"]})
            evaluate(ctx, c, req_id, None)
        return result


# ---------------- 검증 ----------------
def _latest_notice(c, fid):
    return c.execute("SELECT * FROM contract_notices WHERE form_id=? ORDER BY id DESC LIMIT 1", (fid,)).fetchone()


def _consent_problems(a) -> tuple[list[dict], bool]:
    out, refused = [], False
    for c in mapping()["consents"]:
        v = (a.get("consents") or {}).get(c["key"])
        if v is None or v == "":
            out.append({"code": "CONSENT_MISSING", "title": c["title"]})
        elif v == c["accept"]:
            continue
        elif v in c.get("refuse", []):
            out.append({"code": "REFUSED", "title": c["title"]})
            refused = True
        elif v in c.get("explain", []):
            out.append({"code": "NEEDS_EXPLANATION", "title": c["title"]})
        else:
            out.append({"code": "CONSENT_UNKNOWN", "title": c["title"]})
    return out, refused


def evaluate(ctx, c, req_id, actor) -> str:
    """요청에 연결된 응답을 검증해 상태를 정한다. 반환: 새 상태."""
    req = c.execute("SELECT * FROM contract_requests WHERE id=?", (req_id,)).fetchone()
    if req["status"] == "SUPERSEDED":
        return req["status"]
    resps = c.execute("SELECT * FROM contract_responses WHERE request_id=? ORDER BY last_submitted_time, id",
                      (req_id,)).fetchall()
    if not resps:
        return req["status"]
    primary = resps[-1]
    a = jload(primary["answers_json"], {})
    o = c.execute("SELECT * FROM orders WHERE id=?", (req["order_id"],)).fetchone()
    group = customers.group_ids(c, req["customer_id"])
    cust = c.execute("SELECT * FROM customers WHERE id=?", (o["customer_id"],)).fetchone()
    q = c.execute("SELECT * FROM quotes WHERE id=?", (req["quote_id"],)).fetchone()
    probs: list[dict] = []
    if _norm_text(a.get("order_no")) != _norm_text(o["order_no"]):
        probs.append({"code": "ORDER_MISMATCH"})
    if cust is None or cust["deleted_at"] or o["customer_id"] not in group:  # 연결된 고객 묶음 안이면 같은 고객
        probs.append({"code": "CUSTOMER_MISSING"})
    if q is None or q["status"] != "APPROVED":
        probs.append({"code": "QUOTE_SUPERSEDED"})
    elif q["valid_until"] and primary["last_submitted_time"] and \
            clock.kst(clock.parse(primary["last_submitted_time"])).date().isoformat() > q["valid_until"]:
        probs.append({"code": "QUOTE_EXPIRED"})
    if _norm_text(a.get("quote_ref")) != _norm_text(req["quote_ref"]):
        probs.append({"code": "QUOTE_MISMATCH"})
    if primary["create_time"] and clock.parse(primary["create_time"]) < clock.parse(req["issued_at"]):
        probs.append({"code": "SUBMITTED_BEFORE_ISSUE"})
    latest = _latest_notice(c, req["form_id"])
    issued = c.execute("SELECT body_json FROM contract_notices WHERE id=?", (req["notice_id"],)).fetchone()
    if latest is not None and notice_sha(jload(latest["body_json"], {})) != notice_sha(jload(issued["body_json"], {})):
        probs.append({"code": "NOTICE_CHANGED"})
    if req["terms_policy_id"]:
        p, h = _terms(c, q) if q is not None else (None, None)
        if h != req["terms_sha256"]:
            probs.append({"code": "TERMS_CHANGED"})
    cons, refused = _consent_problems(a)
    probs += cons
    _record_consents(c, req, primary, latest)
    if req["contract_form_schema_version"] != mapping()["version"]:
        probs.append({"code": "SCHEMA_CHANGED"})
    if len(resps) > 1:
        sigs = {jdump({k: v for k, v in jload(r["answers_json"], {}).items()}) for r in resps}
        if len(sigs) > 1 or any(_consent_problems(jload(r["answers_json"], {}))[1] for r in resps):
            probs.append({"code": "CONFLICTING_RESPONSES"})
        elif not _reviewed(c, req_id, resps):
            probs.append({"code": "MULTIPLE_RESPONSES", "n": len(resps)})
    eh = primary["respondent_email_hash"]
    own, other = _identity_match(c, group, eh)
    if req["identity_status"] != "VERIFIED":
        if own and not other:
            c.execute("UPDATE contract_requests SET identity_status='VERIFIED', identity_method='verified_email', "
                      "identity_at=? WHERE id=?", (clock.now_iso(), req_id))
            _event(c, req, "identity_auto_verified", None, detail={"method": "verified_email"})
        elif other:
            probs.append({"code": "IDENTITY_OTHER_CUSTOMER"})
        else:
            probs.append({"code": "IDENTITY_UNVERIFIED"})
    if not _reviewed(c, req_id, resps):  # 유효 계약 조건 10: 운영자 검증 완료(D-25)
        probs.append({"code": "OPERATOR_REVIEW_PENDING"})
    hard = [p for p in probs if p["code"] not in SOFT]
    if not probs:
        status = "ACCEPTED"
    elif refused and not [p for p in hard if p["code"] not in ("REFUSED", "CONSENT_MISSING", "NEEDS_EXPLANATION")]:
        status = "REJECTED"
    else:
        status = "REVIEW_REQUIRED"
    req = c.execute("SELECT * FROM contract_requests WHERE id=?", (req_id,)).fetchone()
    q61 = next(x for x in mapping()["consents"] if x["key"] == "q6_1_refund_policy")
    ack = int((a.get("consents") or {}).get("q6_1_refund_policy") == q61["accept"])
    changed = status != req["status"] or jdump(probs) != req["problems_json"] or req["response_id"] != primary["id"] \
        or req["refund_policy_acknowledged"] != ack
    if not changed:
        return status
    extra = {"problems_json": jdump(probs), "response_id": primary["id"], "refund_policy_acknowledged": ack,
             "refund_policy_acknowledged_at": primary["last_submitted_time"] if ack else None,
             "refund_policy_review_required": int(not ack)}
    if status in ("ACCEPTED", "REJECTED"):
        extra.update(decided_at=clock.now_iso(), decided_by=getattr(actor, "id", None))
    _set_status(c, req, status, actor, "evaluated",
                {"problems": [p["code"] for p in probs], "response_row_id": primary["id"],
                 "responses": len(resps)}, **extra)
    if status == "ACCEPTED" and req["status"] != "ACCEPTED":
        _apply_to_order(ctx, c, req_id, primary, actor)
    return status


def _rev_key(resps) -> str:
    return jdump(sorted([r["id"], r["revision"]] for r in resps))


def _reviewed(c, req_id, resps) -> bool:
    """운영자가 지금 응답들(수정 포함)을 보고 검증했는가 — 새 응답·수정이 오면 다시 검증."""
    key = _rev_key(resps)
    return any((jload(e["detail_json"], {}) or {}).get("rev_key") == key for e in c.execute(
        "SELECT detail_json FROM contract_events WHERE request_id=? AND event='reviewed'", (req_id,)).fetchall())


def _record_consents(c, req, resp, notice) -> None:
    """문항별 독립 동의 기록(응답 수정마다 한 벌). Q6 청약철회 별도 동의·Q6-1·Q7은 해당 고지 섹션 해시와 함께."""
    a = jload(resp["answers_json"], {})
    body = jload(notice["body_json"], {}) if notice is not None else {}
    sec_of = {"q6_withdrawal": "withdrawal", "q6_1_refund_policy": "refund", "q7_aftercare": "aftercare"}
    for cdef in mapping()["consents"]:
        v = (a.get("consents") or {}).get(cdef["key"])
        result = ("MISSING" if not v else "AGREED" if v == cdef["accept"] else "REFUSED" if v in cdef.get("refuse", [])
                  else "NEEDS_EXPLANATION" if v in cdef.get("explain", []) else "UNKNOWN")
        sec = policy_section(body, sec_of[cdef["key"]])["sha256"] if cdef["key"] in sec_of and body else None
        c.execute("INSERT OR IGNORE INTO contract_consents(request_id, response_row_id, response_revision, consent_key, "
                  "question_id, question_title, answer, result, notice_section_sha256, submitted_at, recorded_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (req["id"], resp["id"], resp["revision"], cdef["key"],
                                                     cdef["question_id"], cdef["title"], v, result, sec,
                                                     resp["last_submitted_time"], clock.now_iso()))


def _apply_to_order(ctx, c, req_id, resp, actor) -> None:
    """계약이 유효해지면 주문의 '고객 동의'를 기록한다(견적 동의 대기 → 고객 동의 완료, 재견적이면 재동의)."""
    req = c.execute("SELECT * FROM contract_requests WHERE id=?", (req_id,)).fetchone()
    o = c.execute("SELECT * FROM orders WHERE id=?", (req["order_id"],)).fetchone()
    q = tr.latest_quote(c, o["id"], "APPROVED")
    if q is None or q["id"] != req["quote_id"]:
        return
    who = "관리자 확인" if actor else "확인된 계약 이메일로 자동 확정"
    payload = {"channel": "Form B 전자 동의", "confirm": "on", "_system_confirm": True,
               "evidence": f"계약 요청 #{req['id']} · 견적 {req['quote_ref']} · Google 응답 {resp['response_id'][:10]}… · {who}",
               "accepted_at": resp["last_submitted_time"] or resp["create_time"]}
    recorder = actor or _Recorder(req["identity_by"] or req["issued_by"])
    if o["lifecycle_state"] == S.QUOTE_APPROVED.value:
        try:
            tr.perform(ctx, o["id"], "record_acceptance", actor, payload,
                       effect=lambda cc, cur: quotes._record_accept(cc, cur, recorder, payload, "quote", q))
        except TransitionBlocked as e:  # 보류 등으로 막히면 계약은 유효로 두고, 막힘이 풀린 뒤 화면에서 반영
            _event(c, req, "order_update_blocked", actor, detail={"codes": [u.code for u in e.unmet]})
        return
    cr = quotes.change_request_open(c, o["id"])
    prior = c.execute("SELECT 1 FROM customer_acceptances WHERE order_id=? AND kind='quote' AND quote_id=?",
                      (o["id"], q["id"])).fetchone()
    if cr and not prior and q["approved_at"] >= cr["opened_at"]:
        quotes._record_accept(c, o, recorder, payload, "quote", q)
        c.execute("UPDATE issues SET state='RESOLVED', resolved_by=?, resolved_at=?, resolution=? WHERE id=?",
                  (getattr(actor, "id", None), clock.now_iso(), f"새 견적 v{q['version']} 고객 재동의(Form B)", cr["id"]))
        audit.record(c, "issue", cr["id"], actor, "issue_resolved", order_id=o["id"], prev="CHANGE_REQUEST",
                     reason=f"견적 v{q['version']} 재동의(Form B)")
        c.execute("UPDATE orders SET version=version+1, updated_at=? WHERE id=?", (clock.now_iso(), o["id"]))


@dataclasses.dataclass(frozen=True)
class _Recorder:
    """자동 확정 때 customer_acceptances.recorded_by에 남길 사람(본인 확인자, 없으면 발행자)."""
    id: int
    role: str = "system"


def review(ctx, req_id, actor, method: str, note: str, remember_email: bool = True) -> str:
    """관리자 본인 확인·검토. 본인 확인과 '같은 내용 중복 응답' 확인만 풀 수 있다.
    주문번호·견적 불일치, 동의 거부·누락, 고지 변경은 풀 수 없고 새로 발행해야 한다."""
    _admin(actor)
    if method not in IDENTITY_METHODS or not (note or "").strip():
        raise TransitionBlocked([unmet("G-CONTRACT-REVIEW")])
    if method == "verified_email" and (get(ctx, req_id) or {"identity_method": None})["identity_method"] != "verified_email":
        raise TransitionBlocked([unmet("G-CONTRACT-REVIEW")])  # 시스템이 이메일 일치를 확인했을 때만 고를 수 있다
    with ctx.db.tx() as c:
        req = c.execute("SELECT * FROM contract_requests WHERE id=?", (req_id,)).fetchone()
        if req is None or req["status"] not in ("REVIEW_REQUIRED", "RESPONSE_RECEIVED"):
            raise TransitionBlocked([unmet("G-CONTRACT-STATE",
                                           status=STATUS_LABEL.get(req["status"] if req else "", "-"))])
        resps = c.execute("SELECT * FROM contract_responses WHERE request_id=? ORDER BY last_submitted_time, id",
                          (req_id,)).fetchall()
        if not resps:
            raise TransitionBlocked([unmet("G-CONTRACT-STATE", status=STATUS_LABEL["ISSUED"])])
        primary = resps[-1]
        c.execute("UPDATE contract_requests SET identity_status='VERIFIED', identity_method=?, identity_note=?, "
                  "identity_by=?, identity_at=? WHERE id=?", (method, note.strip(), actor.id, clock.now_iso(), req_id))
        saved = False
        if remember_email and primary["respondent_email_hash"]:
            owner = c.execute("SELECT customer_id FROM orders WHERE id=?", (req["order_id"],)).fetchone()[0]  # 지금 주문의 고객
            saved = c.execute("INSERT OR IGNORE INTO customer_identities(customer_id, kind, value_hash, value_enc, method, "
                              "note, verified_by, verified_at) VALUES (?,?,?,?,?,?,?,?)",
                              (owner, "email", primary["respondent_email_hash"], primary["respondent_email_enc"], method,
                               note.strip(), actor.id, clock.now_iso())).rowcount > 0
        _event(c, req, "reviewed", actor, detail={"method": method, "note": note.strip(), "responses": len(resps),
                                                   "rev_key": _rev_key(resps), "response_row_id": primary["id"],
                                                   "email_saved": saved})
        return evaluate(ctx, c, req_id, actor)


# ---------------- 업무 진행 조건 ----------------
def gate_ok(c, order_id) -> bool:
    return tr.contract_ok(c, order_id)


def apply_pending(ctx, o, actor) -> None:
    """계약은 유효한데 주문에 아직 반영되지 않았을 때(보류가 풀린 뒤 등) 화면에서 반영."""
    _admin(actor)
    with ctx.db.tx() as c:
        q = tr.latest_quote(c, o["id"], "APPROVED")
        req = c.execute("SELECT * FROM contract_requests WHERE order_id=? AND quote_id=? AND status='ACCEPTED'",
                        (o["id"], q["id"] if q else -1)).fetchone()
        if req is None:
            raise TransitionBlocked([unmet("G-CONTRACT-REQUIRED")])
        resp = c.execute("SELECT * FROM contract_responses WHERE id=?", (req["response_id"],)).fetchone()
    payload = {"channel": "Form B 전자 동의", "confirm": "on",
               "evidence": f"계약 요청 #{req['id']} · 견적 {req['quote_ref']} · Google 응답 {resp['response_id'][:10]}… · 관리자 반영",
               "accepted_at": resp["last_submitted_time"] or resp["create_time"]}
    tr.perform(ctx, o["id"], "record_acceptance", actor, payload, expected_version=None,
               effect=lambda cc, cur: quotes._record_accept(cc, cur, actor, payload, "quote", q))


# ---------------- 증거 ----------------
def evidence(ctx, req_id) -> dict:
    """주문별 계약 증거 묶음(화면·내보내기 공용). 원문 응답·이메일은 복호화해 담는다 — 호출부가 권한을 확인한다."""
    req = get(ctx, req_id)
    o = ctx.db.one("SELECT * FROM orders WHERE id=?", (req["order_id"],))
    cust = ctx.db.one("SELECT id, display_name, deleted_at FROM customers WHERE id=?", (o["customer_id"],))
    notice = ctx.db.one("SELECT * FROM contract_notices WHERE id=?", (req["notice_id"],))
    terms = ctx.db.one("SELECT * FROM policies WHERE id=?", (req["terms_policy_id"],)) if req["terms_policy_id"] else None
    resps = []
    for r in ctx.db.all("SELECT * FROM contract_responses WHERE request_id=? ORDER BY id", (req_id,)):
        revs = ctx.db.all("SELECT * FROM contract_response_revisions WHERE response_row_id=? ORDER BY revision", (r["id"],))
        resps.append({"row": r, "email": ctx.dec(r["respondent_email_enc"]), "answers": answer_rows(r),
                      "revisions": [{"revision": v["revision"], "last_submitted_time": v["last_submitted_time"],
                                     "raw_sha256": v["raw_sha256"], "fetched_at": v["fetched_at"],
                                     "raw": jload(ctx.dec(v["raw_enc"]), {})} for v in revs]})
    assets = {k: ctx.db.one("SELECT * FROM assets WHERE id=?", (req[k],)) if req[k] else None
              for k in ("quote_html_asset_id", "quote_pdf_asset_id", "delivered_asset_id")}
    return {"req": req, "order": o, "customer": cust, "notice": notice, "notice_body": jload(notice["body_json"], {}),
            "notice_text": notice_text(jload(notice["body_json"], {})), "terms": terms,
            "quote": jload(snapshot_of(ctx, req), {}), "responses": resps, "events": events_of(ctx, req_id),
            "problems": problems_of(req), "assets": assets,
            "checks": {"quote_sha256_ok": sha256_text(snapshot_of(ctx, req)) == req["quote_sha256"],
                       "notice_sha256_ok": notice["sha256"] in (notice_sha(jload(notice["body_json"], {})),
                                                                sha256_text(notice["body_json"])),
                       "quote_html_ok": files.verify(ctx, assets["quote_html_asset_id"]) if assets["quote_html_asset_id"] else None,
                       "quote_pdf_ok": files.verify(ctx, assets["quote_pdf_asset_id"]) if assets["quote_pdf_asset_id"] else None}}


def notice_text(body: dict) -> list[dict]:
    """Form B 원본에서 사람이 읽을 고지 문구(섹션 제목·설명, 문항, 선택지)."""
    out = [{"kind": "title", "text": (body.get("info") or {}).get("title", "")},
           {"kind": "desc", "text": (body.get("info") or {}).get("description", "")}]
    for it in body.get("items", []):
        q = (it.get("questionItem") or {}).get("question")
        if q is None:
            out.append({"kind": "section", "text": it.get("title", ""), "desc": it.get("description", "")})
        else:
            out.append({"kind": "question", "text": it.get("title", ""), "desc": it.get("description", ""),
                        "options": [o.get("value") for o in (q.get("choiceQuestion") or {}).get("options", [])]})
    return [x for x in out if x.get("text") or x.get("desc")]


def export_zip(ctx, req_id, actor) -> bytes:
    """계약 증거 내보내기(운영자만 — 호출부 확인). 개인정보가 들어 있으니 내보낸 사실을 기록한다."""
    import io
    import zipfile
    ev = evidence(ctx, req_id)
    req = ev["req"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("contract.json", json.dumps({
            "order_no": ev["order"]["order_no"], "order_id": req["order_id"], "customer_id": req["customer_id"],
            "quote_id": req["quote_id"], "quote_version": req["quote_version"], "quote_ref": req["quote_ref"],
            "status": req["status"], "issued_at": req["issued_at"], "delivered_at": req["delivered_at"],
            "delivered_channel": req["delivered_channel"], "delivered_note": req["delivered_note"],
            "identity": {"status": req["identity_status"], "method": req["identity_method"], "note": req["identity_note"],
                         "by": req["identity_by"], "at": req["identity_at"]},
            "decided_at": req["decided_at"], "problems": ev["problems"], "terms_version": req["terms_version"],
            "terms_sha256": req["terms_sha256"], "quote_sha256": req["quote_sha256"],
            "notice_sha256": ev["notice"]["sha256"], "notice_revision_id": ev["notice"]["revision_id"],
            "checks": ev["checks"]}, ensure_ascii=False, indent=1, default=str))
        z.writestr("quote_snapshot.json", snapshot_of(ctx, req))
        z.writestr("form_b_notice.json", ev["notice"]["body_json"])
        if ev["terms"]:
            z.writestr("terms_policy.txt", f"{ev['terms']['title']} v{ev['terms']['version']}\n\n{ev['terms']['body']}")
        for i, r in enumerate(ev["responses"], 1):
            z.writestr(f"responses/{i}_{r['row']['response_id']}.json",
                       json.dumps({"email": r["email"], "answers": r["answers"], "revisions": r["revisions"]},
                                  ensure_ascii=False, indent=1, default=str))
        z.writestr("events.json", json.dumps([dict(e) for e in ev["events"]], ensure_ascii=False, indent=1, default=str))
        for k, a in ev["assets"].items():
            data = files.read(ctx, a) if a else None
            if data:
                z.writestr(f"files/{a['filename']}", data)
    with ctx.db.tx() as c:
        _event(c, req, "exported", actor)
        audit.record(c, "contract", req_id, actor, "contract_exported", order_id=req["order_id"], prev=audit.PII)
    return buf.getvalue()
