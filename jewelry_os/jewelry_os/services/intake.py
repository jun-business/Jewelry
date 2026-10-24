"""Google 폼 동기화·매핑·수기 접수(FUN-001, DD-003)."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import timedelta
from pathlib import Path

from ..adapters.google import SourceError
from ..core import clock
from ..core.db import jdump, jload
from ..domain.guards import TransitionBlocked, unmet
from . import audit, customers, files, orders, settings

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


FORBIDDEN_FIELDS = {"address", "shipping_address"}
FIELD_LABEL = {"display_name": "고객 표시명", "contact_kind": "회신수단 종류", "contact_value": "회신수단(연락처)",
               "product_type": "제품 유형", "description": "의뢰 설명", "material": "소재", "size": "사이즈",
               "stones_engraving": "스톤·각인", "stones": "스톤 요청(스톤·각인에 합침)",
               "engraving": "각인·기타 요구(스톤·각인에 합침)", "budget_range": "예산", "desired_date": "희망 날짜",
               "reference_files": "참고 이미지", "reference_link": "참고 이미지 링크(의뢰 설명 끝에 붙임)",
               "source_channel": "유입 경로", "consent": "개인정보 동의"}
# 폼이 따로 묻지만 앱은 한 칸에 담는 질문(D-22): (보조 필드, 합칠 필드, 붙일 머리말)
MERGED_FIELDS = (("stones", "stones_engraving", "스톤"), ("engraving", "stones_engraving", "각인·기타"),
                 ("reference_link", "description", "참고 링크"))
BINDING_KEY = "intake_question_binding"     # 설정: {실제 질문 ID: 필드}(SRS-004 r2)
UNMAPPED_META = "intake_unmapped_questions"      # meta: 동기화 중 본 미인식 질문 ID와 가린 예시값(파일 문항 제외)
_QID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class MappingError(Exception):
    pass


def load_mapping(name="intake_v1", data: dict | None = None, binding: dict | None = None) -> dict:
    """번들 매핑 적재·검증. binding({질문 ID: 필드}, 운영자 질문 연결)이 있으면 그 질문 ID를 덧붙인다.
    자리표시 ID도 남겨 Mock 응답은 그대로 읽힌다(한 필드에 ID 여럿 — 답이 있는 쪽이 값)."""
    m = data if data is not None else json.loads((MAPPINGS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    if binding:
        specs = {q["field"]: q for q in m["questions"].values()}
        qs = dict(m["questions"])
        for qid, f in binding.items():
            qs[qid] = {**specs.get(f, {"required": False, "kind": "text"}), "field": f}
        m = {**m, "questions": qs}
    fields = {q["field"] for q in m["questions"].values()}
    missing = REQUIRED_FIELDS - fields
    if missing:
        raise MappingError(f"필수 필드 매핑 없음: {sorted(missing)}")
    if fields & FORBIDDEN_FIELDS:
        raise MappingError("배송 주소는 폼에서 받지 않아요(출고 단계에서만 입력)")
    return m


def current_mapping(ctx) -> dict:
    """동기화에 쓰는 매핑 = 번들 + 설정의 질문 연결."""
    return load_mapping(binding=settings.get(ctx, BINDING_KEY) or None)


def validate_binding(binding: dict) -> dict:
    """질문 연결 검사(빈 선택은 버림): 질문 ID 형식 · 주소 필드 금지 · 번들 필드만 · 항목당 질문 1개 · 필수 항목 전부."""
    b = {str(q).strip(): f for q, f in binding.items() if f}
    for qid in b:
        if not _QID.match(qid):
            raise MappingError(f"질문 ID 형식이 올바르지 않아요: {qid}")
    load_mapping(binding=b)
    unknown = sorted(set(b.values()) - set(FIELD_LABEL))
    if unknown:
        raise MappingError(f"목록에 없는 항목이에요: {', '.join(unknown)}")
    dup = [f for f in FIELD_LABEL if list(b.values()).count(f) > 1]
    if dup:
        raise MappingError("한 항목에는 질문 하나만 연결하세요: " + ", ".join(FIELD_LABEL[f] for f in dup))
    missing = [f for f in FIELD_LABEL if f in REQUIRED_FIELDS and f not in b.values()]
    if missing:
        raise MappingError("필수 항목을 모두 연결하세요: " + ", ".join(FIELD_LABEL[f] for f in missing))
    return b


def save_binding(ctx, binding: dict, actor) -> dict:
    """질문 연결 저장(operator만 — 호출부 검사, 감사 setting_changed) 후 받은 응답 전체를 다시 읽는다. 반환 = sync 통계."""
    b = validate_binding(binding)
    if settings.get(ctx, BINDING_KEY) != b:
        settings.put(ctx, BINDING_KEY, b, actor)
    stats = sync_once(ctx, full=True)
    fetch_pending_files(ctx)
    return stats


def unmapped_questions(ctx, mapping: dict | None = None) -> list[dict]:
    """현재 폼의 최근 동기화에서 본, 현재 매핑에 없는 질문 ID(질문 연결 필요). 파일 문항은 제외(모양으로 인식)."""
    mapping = mapping or current_mapping(ctx)
    raw = jload(ctx.db.scalar("SELECT value FROM meta WHERE key=?", (UNMAPPED_META,)), {})
    if raw.get("form_id") != settings.get(ctx, "google_form_id"):
        return []
    return [{"qid": q, **v} for q, v in sorted(raw.get("questions", {}).items(), key=lambda kv: kv[1]["first_seen"])
            if q not in mapping["questions"]]


def binding_rows(ctx) -> list[dict]:
    """설정 > 폼 매핑 표: 연결된 질문 + 미인식 질문. 예시값은 미인식 질문에만 있다(연결 후 버림)."""
    binding = settings.get(ctx, BINDING_KEY) or {}
    rows = [{"qid": q, "field": f, "sample": ""} for q, f in binding.items()]
    return rows + [{**q, "field": ""} for q in unmapped_questions(ctx) if q["qid"] not in binding]


def _sample(a: dict) -> str:
    """질문 구분용 예시값: 연락처·주소는 가리고 30자로 자른다(DD-006 B-11 규칙 재사용)."""
    from .analysis import redact_text
    vals = [str(x.get("value") or "") for x in a.get("textAnswers", {}).get("answers", [])]
    s = redact_text(", ".join(v for v in vals if v)) or ""
    return s if len(s) <= 30 else s[:30] + "…"


def _note_unmapped(seen: dict, mapping: dict, answers: dict) -> None:
    for qid, a in answers.items():
        if qid in mapping["questions"] or not isinstance(a, dict) or "fileUploadAnswers" in a:
            continue
        v = seen.setdefault(qid, {"sample": "", "count": 0})
        v["count"] += 1
        if not v["sample"]:
            v["sample"] = _sample(a)


def _store_unmapped(c, form_id, mapping: dict, seen: dict) -> None:
    """미인식 질문 기록(meta). 연결된 질문의 예시값은 지운다 — 개인정보 보관 최소화."""
    row = c.execute("SELECT value FROM meta WHERE key=?", (UNMAPPED_META,)).fetchone()
    prev = jload(row[0], {}) if row else {}
    qs = {q: v for q, v in (prev.get("questions", {}) if prev.get("form_id") == form_id else {}).items()
          if q not in mapping["questions"]}
    now = clock.now_iso()
    for qid, v in seen.items():
        old = qs.get(qid, {})
        qs[qid] = {"sample": v["sample"] or old.get("sample", ""), "count": v["count"],
                   "first_seen": old.get("first_seen", now), "last_seen": now}
    if not qs and not row:
        return
    c.execute("INSERT INTO meta(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (UNMAPPED_META, jdump({"form_id": form_id, "questions": qs})))


def parse_answers(mapping: dict, answers: dict) -> dict:
    out: dict = {}
    for qid, spec in mapping["questions"].items():
        a = answers.get(qid)
        f = spec["field"]
        if a is None:
            out.setdefault(f, None)  # 같은 필드의 다른 질문 ID(자리표시 ↔ 실제 폼)에 답이 있으면 그 값을 유지
            continue
        if "fileUploadAnswers" in a:
            out[f] = [{"file_id": x["fileId"], "name": x.get("fileName", "image"), "mime": x.get("mimeType")}
                      for x in a["fileUploadAnswers"].get("answers", [])]
        else:
            vals = [x.get("value") for x in a.get("textAnswers", {}).get("answers", [])]
            out[f] = vals[0] if len(vals) == 1 else (vals or None)
    # Google Forms assigns opaque question IDs when a form is created. The
    # bundled intake mapping may still contain its placeholder ID, so discover
    # file uploads by their answer shape instead of silently treating them as
    # absent. This requires no extra OAuth scope or form-body access.
    uploaded = []
    seen_file_ids = set()
    for answer in answers.values():
        if not isinstance(answer, dict) or "fileUploadAnswers" not in answer:
            continue
        for item in answer["fileUploadAnswers"].get("answers", []):
            file_id = item.get("fileId")
            if not file_id or file_id in seen_file_ids:
                continue
            seen_file_ids.add(file_id)
            uploaded.append({"file_id": file_id, "name": item.get("fileName", "image"),
                             "mime": item.get("mimeType")})
    if uploaded:
        mapped = out.get("reference_files") or []
        known = {item.get("file_id") for item in mapped}
        out["reference_files"] = mapped + [item for item in uploaded if item["file_id"] not in known]
    for extra, target, head in MERGED_FIELDS:
        v = out.pop(extra, None)
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v if x)
        if v:
            sep = "\n" if target == "description" else " / "
            out[target] = f"{out[target]}{sep}{head}: {v}" if out.get(target) else f"{head}: {v}"
    ch = mapping.get("choices", {}).get("source_channel", {})
    # 빈 답 = 미분류(None), 목록 밖 값 = 기타(SRS-124)
    out["source_channel"] = ch.get(out["source_channel"], "other") if out.get("source_channel") else None
    out["consent"] = bool(out.get("consent"))
    return out


def digest(answers: dict) -> str:
    return hashlib.sha256(jdump(answers).encode()).hexdigest()


def _cursor(ctx, form_id):
    return ctx.db.one("SELECT * FROM sync_cursors WHERE provider='google_forms' AND scope=?", (form_id,))


def sync_once(ctx, fail_after_commit_of: int | None = None, full: bool = False) -> dict:
    """한 번 동기화. 반환: {ok, new, revised, total, error}. 커서는 모든 응답 커밋 뒤에만 갱신(SRS-016).
    full: 증분 설정과 무관하게 전체 응답을 다시 읽는다(질문 연결 저장 직후 재해석)."""
    form_id = settings.get(ctx, "google_form_id")
    mapping = current_mapping(ctx)
    src = ctx.forms
    # BD-002: live + 시험 폼이면 시험 주문. Mock 모드 응답은 시험 운영 모드·시험 견적 규칙(BD §6.4)으로 표지한다.
    is_test_form = settings.effective_google_mode(ctx) == "live" and settings.get(ctx, "google_form_purpose") == "test"
    since = None
    if settings.get(ctx, "sync_incremental") and not full:
        cur = _cursor(ctx, form_id)
        if cur and cur["last_success_at"]:
            since = clock.iso(clock.parse(cur["last_success_at"]) - timedelta(days=1)).replace("+00:00", "Z")
    stats = {"ok": True, "new": 0, "revised": 0, "total": 0, "error": None}
    seen: dict = {}
    token = None
    try:
        while True:
            page = src.list_responses(form_id, page_token=token, since_rfc3339=since) if since else \
                src.list_responses(form_id, page_token=token)
            for r in page.responses:
                stats["total"] += 1
                _note_unmapped(seen, mapping, r.get("answers", {}))
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
        _store_unmapped(c, form_id, mapping, seen)
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
                cust = customers.auto_link(ctx, c, cust)  # 이름·전화번호가 같으면 기존 고객 묶음으로(D-26)
                oid = orders.create_order(ctx, c, customer_id=cust, source="form", intake_id=iid,
                                          product_type=parsed.get("product_type"),
                                          summary=(parsed.get("description") or "")[:80] or None,
                                          source_channel=parsed.get("source_channel"), is_test=is_test_form)
                _apply_flags(ctx, c, oid, parsed)
                c.execute("INSERT INTO system_jobs(kind, payload_json, status, created_at, updated_at) "
                          "VALUES ('fetch_files', ?, 'PENDING', ?, ?)", (jdump({"order_id": oid}), clock.now_iso(),
                                                                        clock.now_iso()))
                result = ("new", oid)
            else:
                parsed = parse_answers(mapping, answers)
                latest = c.execute("SELECT answers_json FROM intake_revisions WHERE intake_id=? "
                                   "ORDER BY revision DESC LIMIT 1", (row["id"],)).fetchone()
                previous = jload(latest["answers_json"], {}) if latest else {}
                files_changed = (previous.get("reference_files") or []) != (parsed.get("reference_files") or [])
                o = c.execute("SELECT o.*, cu.deleted_at AS customer_deleted_at FROM orders o JOIN customers cu "
                              "ON cu.id=o.customer_id WHERE o.intake_id=?", (row["id"],)).fetchone()
                # 원문이 같아도 해석 결과가 달라지면(질문 연결 저장 등) 새 revision(SRS-004 r2).
                # 삭제된 고객은 파일만 비교 — 파기한 표시명·연락처가 재해석으로 되살아나지 않게(SRS-084)
                reparsed = files_changed if o["customer_deleted_at"] else parsed != previous
                changed = row["last_submitted_at"] != last or row["raw_digest"] != dg or reparsed
                if not changed:
                    return "same"
                rev = row["revision"] + 1
                c.execute("UPDATE intake_responses SET last_submitted_at=?, raw_digest=?, revision=?, schema_version=? "
                          "WHERE id=?", (last, dg, rev, mapping["version"], row["id"]))
                c.execute("INSERT INTO intake_revisions(intake_id, revision, last_submitted_at, raw_digest, answers_json, "
                          "created_at) VALUES (?,?,?,?,?,?)", (row["id"], rev, last, dg, jdump(parsed), clock.now_iso()))
                c.execute("UPDATE orders SET input_revision=?, version=version+1, updated_at=? WHERE id=?",
                          (rev, clock.now_iso(), o["id"]))
                audit.record(c, "intake", rid, None, "intake_revised", order_id=o["id"], ref_version=rev)
                _fill_empty(ctx, c, o, row, parsed, mapping)
                _apply_flags(ctx, c, o["id"], parsed, revised=True)
                if files_changed:
                    c.execute("INSERT INTO system_jobs(kind, payload_json, status, created_at, updated_at) "
                              "VALUES ('fetch_files', ?, 'PENDING', ?, ?)",
                              (jdump({"order_id": o["id"]}), clock.now_iso(), clock.now_iso()))
                result = ("revised", o["id"])
    except Exception as e:  # UNIQUE 경합(다른 워커가 먼저 넣음) → 재조회 후 no-op
        import sqlite3
        if isinstance(e, sqlite3.IntegrityError):
            return "same"
        raise
    from . import analysis
    analysis.process_new(ctx, result[1])
    return result[0]


def _apply_flags(ctx, c, oid, parsed, revised=False):
    """보류 판정. revised: 새 revision에서 조건이 채워졌으면 시스템이 건 보류만 푼다(사람이 건 보류는 유지)."""
    if not parsed.get("consent"):
        orders.set_hold(c, oid, "NO_CONSENT", None, "폼 응답에 개인정보 동의가 없음")
    elif revised:
        _clear_system_hold(c, oid, "NO_CONSENT", "폼 응답에서 개인정보 동의 확인")
    missing = [f for f in ("display_name", "contact_value", "product_type", "description") if not parsed.get(f)]
    if missing:
        orders.set_hold(c, oid, "NEEDS_INFO", None, "필수 항목 비어 있음: " + ",".join(missing))
    elif revised:
        _clear_system_hold(c, oid, "NEEDS_INFO", "폼 응답에서 필수 항목 확인")


def _clear_system_hold(c, oid, reason, note):
    if c.execute("SELECT 1 FROM holds WHERE order_id=? AND reason=? AND cleared_at IS NULL AND set_by IS NULL",
                 (oid, reason)).fetchone():
        orders.clear_hold(c, oid, reason, None, note)


def _fill_empty(ctx, c, o, intake_row, parsed, mapping):
    """새 revision에서 주문·고객의 빈 값만 채운다(질문 연결 전에 비어 들어온 응답). 이미 있는 값은 덮지 않는다."""
    c.execute("UPDATE orders SET product_type=COALESCE(product_type, ?), summary=COALESCE(summary, ?), "
              "source_channel=COALESCE(source_channel, ?) WHERE id=?",
              (parsed.get("product_type"), (parsed.get("description") or "")[:80] or None, parsed.get("source_channel"),
               o["id"]))
    cust = c.execute("SELECT * FROM customers WHERE id=?", (o["customer_id"],)).fetchone()
    if cust["deleted_at"]:
        return
    consent_v = mapping["consent_version"] if parsed.get("consent") else None
    name = parsed.get("display_name") if cust["display_name"] == "(이름 없음)" else None
    contact = None if cust["contact_enc"] else ctx.enc(parsed.get("contact_value"))
    c.execute("UPDATE customers SET display_name=COALESCE(?, display_name), contact_kind=COALESCE(contact_kind, ?), "
              "contact_enc=COALESCE(contact_enc, ?), consent_version=COALESCE(consent_version, ?) WHERE id=?",
              (name, parsed.get("contact_kind"), contact, consent_v, cust["id"]))
    if consent_v and not intake_row["consent_version"]:
        c.execute("UPDATE intake_responses SET consent_version=? WHERE id=?", (consent_v, intake_row["id"]))


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
        cid = customers.auto_link(ctx, c, cid)  # 이름·전화번호가 같으면 기존 고객 묶음으로(D-26)
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
