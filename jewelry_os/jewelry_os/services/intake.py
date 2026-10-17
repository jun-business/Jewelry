"""Google 폼 동기화·매핑·수기 접수(FUN-001, DD-003)."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from pathlib import Path

from ..adapters.google import SourceError
from ..core import clock
from ..core.db import jdump, jload
from ..domain.guards import TransitionBlocked, unmet
from . import audit, files, orders, settings

log = logging.getLogger("jos.intake")
MAPPINGS_DIR = Path(__file__).resolve().parent.parent / "mappings"
REQUIRED_FIELDS = {"display_name", "contact_kind", "contact_value", "product_type", "description", "consent"}
ERROR_TEXT = {
    "AUTH_EXPIRED": "Google 연결이 만료됐어요. 다시 연결하면 밀린 응답을 모두 가져와요.",
    "FORBIDDEN": "폼에 접근할 수 없어요. 연결한 Google 계정에 이 폼 권한이 있는지 확인하세요.",
    "RATE_LIMITED": "Google 요청 한도에 걸려 잠시 쉬어 가는 중이에요.",
    "NETWORK": "인터넷 연결을 확인하세요. Google에 연결하지 못했어요.",
    "NOT_FOUND": "폼을 찾을 수 없어요. 설정의 폼 ID를 확인하세요.",
}
ERROR_CODE = {"AUTH_EXPIRED": "SYNC-401", "FORBIDDEN": "SYNC-403", "RATE_LIMITED": "SYNC-429", "NETWORK": "SYNC-NET",
              "NOT_FOUND": "SYNC-404"}


class MappingError(Exception):
    pass


def load_mapping(name="intake_v1", data: dict | None = None) -> dict:
    m = data if data is not None else json.loads((MAPPINGS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    fields = {q["field"] for q in m["questions"].values()}
    missing = REQUIRED_FIELDS - fields
    if missing:
        raise MappingError(f"필수 필드 매핑 없음: {sorted(missing)}")
    if "address" in fields or "shipping_address" in fields:
        raise MappingError("배송 주소는 폼에서 받지 않아요(출고 단계에서만 입력)")
    return m


def parse_answers(mapping: dict, answers: dict) -> dict:
    out: dict = {}
    for qid, spec in mapping["questions"].items():
        a = answers.get(qid)
        f = spec["field"]
        if a is None:
            out[f] = None
            continue
        if "fileUploadAnswers" in a:
            out[f] = [{"file_id": x["fileId"], "name": x.get("fileName", "image"), "mime": x.get("mimeType")}
                      for x in a["fileUploadAnswers"].get("answers", [])]
        else:
            vals = [x.get("value") for x in a.get("textAnswers", {}).get("answers", [])]
            out[f] = vals[0] if len(vals) == 1 else (vals or None)
    ch = mapping.get("choices", {}).get("source_channel", {})
    # 빈 답 = 미분류(None), 목록 밖 값 = 기타(SRS-124)
    out["source_channel"] = ch.get(out["source_channel"], "other") if out.get("source_channel") else None
    out["consent"] = bool(out.get("consent"))
    return out


def digest(answers: dict) -> str:
    return hashlib.sha256(jdump(answers).encode()).hexdigest()


def _cursor(ctx, form_id):
    return ctx.db.one("SELECT * FROM sync_cursors WHERE provider='google_forms' AND scope=?", (form_id,))


def sync_once(ctx, fail_after_commit_of: int | None = None) -> dict:
    """한 번 동기화. 반환: {ok, new, revised, total, error}. 커서는 모든 응답 커밋 뒤에만 갱신(SRS-016)."""
    form_id = settings.get(ctx, "google_form_id")
    mapping = load_mapping()
    src = ctx.forms
    # BD-002: live + 시험 폼이면 시험 주문. Mock 모드 응답은 시험 운영 모드·시험 견적 규칙(BD §6.4)으로 표지한다.
    is_test_form = settings.get(ctx, "google_mode") == "live" and settings.get(ctx, "google_form_purpose") == "test"
    since = None
    if settings.get(ctx, "sync_incremental"):
        cur = _cursor(ctx, form_id)
        if cur and cur["last_success_at"]:
            since = clock.iso(clock.parse(cur["last_success_at"]) - timedelta(days=1)).replace("+00:00", "Z")
    stats = {"ok": True, "new": 0, "revised": 0, "total": 0, "error": None}
    new_orders: list[int] = []
    token = None
    try:
        while True:
            page = src.list_responses(form_id, page_token=token, since_rfc3339=since) if since else \
                src.list_responses(form_id, page_token=token)
            for r in page.responses:
                stats["total"] += 1
                res = _ingest_one(ctx, form_id, mapping, r, is_test_form)
                if res == "new":
                    stats["new"] += 1
                elif res == "revised":
                    stats["revised"] += 1
                if fail_after_commit_of is not None and stats["total"] == fail_after_commit_of:
                    raise RuntimeError("injected failure")  # 시험용 장애 주입(TC-S-020)
            token = page.next_page_token
            if not token:
                break
    except SourceError as e:
        _record_error(ctx, form_id, e.kind, e.retry_after)
        stats.update(ok=False, error=e.kind)
        return stats
    with ctx.db.tx() as c:
        c.execute("INSERT INTO sync_cursors(provider, scope, last_success_at, last_count, status, attempts) "
                  "VALUES ('google_forms', ?, ?, ?, 'OK', 0) ON CONFLICT(provider, scope) DO UPDATE SET "
                  "last_success_at=excluded.last_success_at, last_count=excluded.last_count, status='OK', attempts=0, "
                  "last_error_kind=NULL, next_attempt_at=NULL", (form_id, clock.now_iso(), stats["total"]))
    return stats


def _record_error(ctx, form_id, kind, retry_after):
    cur = _cursor(ctx, form_id)
    attempts = (cur["attempts"] if cur else 0) + 1
    if retry_after:
        wait = retry_after
    elif kind == "RATE_LIMITED":
        wait = min(60, 2 ** attempts) * 60
    elif kind == "NETWORK":
        wait = min(30, 2 ** attempts) * 60
    else:
        wait = None
    nxt = clock.iso(clock.now() + timedelta(seconds=wait)) if wait else None
    with ctx.db.tx() as c:
        c.execute("INSERT INTO sync_cursors(provider, scope, status, last_error_kind, last_error_at, attempts, "
                  "next_attempt_at) VALUES ('google_forms', ?, 'ERROR', ?, ?, ?, ?) ON CONFLICT(provider, scope) DO UPDATE "
                  "SET status='ERROR', last_error_kind=excluded.last_error_kind, last_error_at=excluded.last_error_at, "
                  "attempts=excluded.attempts, next_attempt_at=excluded.next_attempt_at",
                  (form_id, kind, clock.now_iso(), attempts, nxt))
    log.warning("google sync error %s attempts=%s", kind, attempts)


def _ingest_one(ctx, form_id, mapping, r, is_test_form) -> str:
    answers = r.get("answers", {})
    dg = digest(answers)
    rid = r["responseId"]
    last = r.get("lastSubmittedTime") or r.get("createTime")
    try:
        with ctx.db.tx() as c:
            row = c.execute("SELECT * FROM intake_responses WHERE form_id=? AND response_id=?", (form_id, rid)).fetchone()
            if row is None:
                parsed = parse_answers(mapping, answers)
                cur = c.execute("INSERT INTO intake_responses(form_id, response_id, schema_version, submitted_at, "
                                "last_submitted_at, raw_digest, revision, consent_version, is_test, created_at) "
                                "VALUES (?,?,?,?,?,?,1,?,?,?)", (form_id, rid, mapping["version"], r.get("createTime"),
                                                                last, dg, mapping["consent_version"] if parsed["consent"]
                                                                else None, int(is_test_form), clock.now_iso()))
                iid = cur.lastrowid
                c.execute("INSERT INTO intake_revisions(intake_id, revision, last_submitted_at, raw_digest, answers_json, "
                          "created_at) VALUES (?,1,?,?,?,?)", (iid, last, dg, jdump(parsed), clock.now_iso()))
                cust = orders.create_customer(ctx, c, parsed.get("display_name") or "(이름 없음)", parsed.get("contact_kind"),
                                              parsed.get("contact_value"), mapping["consent_version"] if parsed["consent"] else None)
                oid = orders.create_order(ctx, c, customer_id=cust, source="form", intake_id=iid,
                                          product_type=parsed.get("product_type"),
                                          summary=(parsed.get("description") or "")[:80] or None,
                                          source_channel=parsed.get("source_channel"), is_test=is_test_form)
                _apply_flags(ctx, c, oid, parsed)
                c.execute("INSERT INTO system_jobs(kind, payload_json, status, created_at, updated_at) "
                          "VALUES ('fetch_files', ?, 'PENDING', ?, ?)", (jdump({"order_id": oid}), clock.now_iso(),
                                                                        clock.now_iso()))
                result = ("new", oid)
            elif row["last_submitted_at"] != last or row["raw_digest"] != dg:
                parsed = parse_answers(mapping, answers)
                rev = row["revision"] + 1
                c.execute("UPDATE intake_responses SET last_submitted_at=?, raw_digest=?, revision=? WHERE id=?",
                          (last, dg, rev, row["id"]))
                c.execute("INSERT INTO intake_revisions(intake_id, revision, last_submitted_at, raw_digest, answers_json, "
                          "created_at) VALUES (?,?,?,?,?,?)", (row["id"], rev, last, dg, jdump(parsed), clock.now_iso()))
                o = c.execute("SELECT * FROM orders WHERE intake_id=?", (row["id"],)).fetchone()
                c.execute("UPDATE orders SET input_revision=?, version=version+1, updated_at=? WHERE id=?",
                          (rev, clock.now_iso(), o["id"]))
                audit.record(c, "intake", rid, None, "intake_revised", order_id=o["id"], ref_version=rev)
                _apply_flags(ctx, c, o["id"], parsed)
                result = ("revised", o["id"])
            else:
                return "same"
    except Exception as e:  # UNIQUE 경합(다른 워커가 먼저 넣음) → 재조회 후 no-op
        import sqlite3
        if isinstance(e, sqlite3.IntegrityError):
            return "same"
        raise
    from . import analysis
    analysis.process_new(ctx, result[1])
    return result[0]


def _apply_flags(ctx, c, oid, parsed):
    if not parsed.get("consent"):
        orders.set_hold(c, oid, "NO_CONSENT", None, "폼 응답에 개인정보 동의가 없음")
    missing = [f for f in ("display_name", "contact_value", "product_type", "description") if not parsed.get(f)]
    if missing:
        orders.set_hold(c, oid, "NEEDS_INFO", None, "필수 항목 비어 있음: " + ",".join(missing))


def answers_of(ctx, order) -> tuple[dict, int]:
    if order["intake_id"] is None:  # 수기 접수 원문은 order_specs revision 0
        r = ctx.db.one("SELECT spec_json FROM order_specs WHERE order_id=? AND revision=0", (order["id"],))
        d = jload(r["spec_json"], {}) if r else {}
        d.setdefault("product_type", order["product_type"])
        return d, 1
    r = ctx.db.one("SELECT * FROM intake_revisions WHERE intake_id=? ORDER BY revision DESC LIMIT 1", (order["intake_id"],))
    return (jload(r["answers_json"], {}), r["revision"]) if r else ({}, 1)


def revisions_of(ctx, order):
    if order["intake_id"] is None:
        return []
    return ctx.db.all("SELECT * FROM intake_revisions WHERE intake_id=? ORDER BY revision", (order["intake_id"],))


def fetch_pending_files(ctx) -> int:
    """동기화가 등록한 파일 가져오기 작업 처리(SRS-018). 실패는 자리표시 자산 + 예외 큐."""
    n = 0
    for job in ctx.db.all("SELECT * FROM system_jobs WHERE kind='fetch_files' AND status IN ('PENDING','RETRY') ORDER BY id"):
        n += run_file_job(ctx, job)
    return n


def run_file_job(ctx, job) -> int:
    oid = jload(job["payload_json"])["order_id"]
    o = ctx.db.one("SELECT * FROM orders WHERE id=?", (oid,))
    parsed, _ = answers_of(ctx, o)
    failed = False
    for f in parsed.get("reference_files") or []:
        exists = ctx.db.one("SELECT * FROM assets WHERE order_id=? AND source_file_id=?", (oid, f["file_id"]))
        if exists and exists["fetch_status"] == "OK":
            continue
        try:
            data = ctx.files.fetch(f["file_id"])
            with ctx.db.tx() as c:
                if exists:
                    c.execute("DELETE FROM assets WHERE id=?", (exists["id"],))
                files.store(ctx, c, order_id=oid, order_no=o["order_no"], kind="reference", filename=f["name"], data=data,
                            source_file_id=f["file_id"], validate=False)
        except SourceError as e:
            failed = True
            if not exists:
                with ctx.db.tx() as c:
                    files.placeholder(c, order_id=oid, kind="reference", filename=f["name"], source_file_id=f["file_id"],
                                      error=e.kind)
    with ctx.db.tx() as c:
        c.execute("UPDATE system_jobs SET status=?, attempts=attempts+1, last_error=?, updated_at=? WHERE id=?",
                  ("FAILED" if failed else "DONE", "파일을 가져오지 못했어요" if failed else None, clock.now_iso(), job["id"]))
    return 0 if failed else 1


def manual_intake(ctx, actor, form: dict) -> str:
    """수기 접수(SRS-155). 필수: 표시명·회신수단·제품유형·설명·동의 증빙."""
    errors = {}
    for k, lab in (("display_name", "고객 표시명"), ("contact_value", "회신수단"), ("product_type", "제품 유형"),
                   ("description", "설명")):
        if not (form.get(k) or "").strip():
            errors[k] = f"{lab}을(를) 입력하세요."
    if not ((form.get("consent_channel") or "").strip() and (form.get("consent_evidence") or "").strip()):
        errors["consent_evidence"] = "개인정보 동의를 어떻게 받았는지 증빙을 입력하세요."
    if errors:
        raise ValidationError(errors)
    with ctx.db.tx() as c:
        cid = orders.create_customer(ctx, c, form["display_name"].strip(), form.get("contact_kind") or "kakao",
                                     form["contact_value"].strip(), settings.get(ctx, "consent_version"))
        oid = orders.create_order(ctx, c, customer_id=cid, source="manual", product_type=form["product_type"],
                                  summary=form["description"][:80], source_channel="manual",
                                  is_test=settings.test_mode(ctx), actor=actor)
        c.execute("INSERT INTO customer_acceptances(order_id, kind, channel, evidence_text, accepted_at, recorded_by, "
                  "recorded_at) VALUES (?,?,?,?,?,?,?)", (oid, "consent", form["consent_channel"], form["consent_evidence"],
                                                        clock.now_iso(), actor.id, clock.now_iso()))
        c.execute("INSERT INTO order_specs(order_id, revision, spec_json, change_reason, created_by, created_at) "
                  "VALUES (?,0,?,?,?,?)", (oid, jdump({"description": form["description"], "material": form.get("material"),
                                                      "size": form.get("size"), "intake_channel": form.get("intake_channel")}),
                                          "수기 접수 원문", actor.id, clock.now_iso()))
        no = c.execute("SELECT order_no FROM orders WHERE id=?", (oid,)).fetchone()[0]
    from . import analysis
    analysis.process_new(ctx, oid)
    return no


class ValidationError(Exception):
    def __init__(self, errors: dict):
        super().__init__("; ".join(errors.values()))
        self.errors = errors
