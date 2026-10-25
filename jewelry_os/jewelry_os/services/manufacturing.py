"""CAD·디자인 버전·사양서·발주 패킷·발주·수령·QC·출고(FUN-008, DD-005)."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

from ..core import clock
from ..core.db import jdump, jload
from ..domain import transitions as tr
from ..domain.guards import TransitionBlocked, unmet
from ..domain.money import parse_won
from ..domain.states import FACTORY_SPEC_FIELDS, QC_ITEMS, S, SPEC_FIELDS
from . import audit, files, settings


def _maker(actor):
    if actor is None or actor.role != "maker":
        raise TransitionBlocked([unmet("G-ROLE-MAKER")])


def designs_of(ctx, order_id):
    return ctx.db.all("SELECT * FROM design_versions WHERE order_id=? ORDER BY version DESC", (order_id,))


def upload_design(ctx, o, actor, uploads: dict, note=None) -> int:
    """uploads = {'cad': (name, bytes), 'preview': ..., 'stl': ...} 중 1개 이상. 새 디자인 버전."""
    _maker(actor)
    if not uploads:
        raise files.FileRejected("올릴 파일을 고르세요.")
    for kind, (name, data) in uploads.items():
        files.check(kind, name, data)
    with ctx.db.tx() as c:
        cur = c.execute("SELECT * FROM orders WHERE id=?", (o["id"],)).fetchone()
        prev = tr.latest_design(c, o["id"])
        v = (prev["version"] + 1) if prev else 1
        ids = {}
        for kind, (name, data) in uploads.items():
            ids[kind] = files.store(ctx, c, order_id=o["id"], order_no=o["order_no"], kind=kind, filename=name, data=data,
                                    actor=actor, version=v)
        keep = {k: (prev[f"{k}_asset_id"] if prev else None) for k in ("cad", "preview", "stl")}
        keep.update({k: v2 for k, v2 in ids.items()})
        did = c.execute("INSERT INTO design_versions(order_id, version, cad_asset_id, preview_asset_id, stl_asset_id, note, "
                        "created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (o["id"], v, keep["cad"], keep["preview"], keep["stl"], note, actor.id, clock.now_iso())).lastrowid
        audit.record(c, "design", f"{o['order_no']}:v{v}", actor, "design_uploaded", order_id=o["id"], ref_version=v)
        n = c.execute("UPDATE factory_packets SET status='VOID', void_reason='새 디자인 버전' WHERE order_id=? AND "
                      "status='VALID'", (o["id"],)).rowcount
        need_reval = cur["lifecycle_state"] == S.CUSTOMER_DESIGN_APPROVED.value
        if not need_reval:
            c.execute("UPDATE orders SET version=version+1, updated_at=? WHERE id=?", (clock.now_iso(), o["id"]))
    if need_reval:  # BD §6.6 — 주체는 업로드한 maker
        tr.perform(ctx, o["id"], "revalidate_design", actor, {"reason": f"새 디자인 v{v} 업로드"})
    return did


def record_design_ok(ctx, o, actor, payload):
    def effect(c, cur):
        d = tr.latest_design(c, o["id"])
        at, src = clock.from_kst_input(payload.get("accepted_at"))
        c.execute("INSERT INTO customer_acceptances(order_id, kind, design_version_id, channel, evidence_text, accepted_at, "
                  "accepted_at_src, recorded_by, recorded_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  (o["id"], "design", d["id"], payload["channel"], payload["evidence"], at, src, actor.id,
                   clock.now_iso()))
    return tr.perform(ctx, o["id"], "record_design_ok", actor, payload, expected_version=payload.get("version"),
                      effect=effect)


def save_factory_spec(ctx, o, actor, spec: dict) -> int:
    _maker(actor)
    with ctx.db.tx() as c:
        prev = tr.latest_factory_spec(c, o["id"])
        rev = (prev["revision"] + 1) if prev else 1
        clean = {}
        for k, _ in FACTORY_SPEC_FIELDS:
            v = spec.get(k)
            clean[k] = "해당 없음" if spec.get(f"{k}_na") else (v.strip() if isinstance(v, str) else v)
        clean["stl_asset_id"] = int(spec["stl_asset_id"]) if spec.get("stl_asset_id") else None
        c.execute("INSERT INTO factory_specs(order_id, revision, spec_json, created_by, created_at) VALUES (?,?,?,?,?)",
                  (o["id"], rev, jdump(clean), actor.id, clock.now_iso()))
        c.execute("UPDATE factory_packets SET status='VOID', void_reason='사양서 변경' WHERE order_id=? AND status='VALID'",
                  (o["id"],))
        audit.record(c, "factory_spec", f"{o['order_no']}:r{rev}", actor, "factory_spec_saved", order_id=o["id"])
        return rev


def default_factory_spec(ctx, o) -> dict:
    """공장 사양 기본값 = 승인 견적의 구조화 사양(SRS-161, 3.3.7)."""
    with ctx.db.tx() as c:
        fs = tr.latest_factory_spec(c, o["id"])
        if fs:
            return jload(fs["spec_json"], {})
        q = tr.latest_quote(c, o["id"], "APPROVED")
        return jload(q["spec_json"], {}) if q else {}


def _canon(spec: dict) -> str:
    return json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def packet_hash(design_version_id: int, stl_sha: str, spec: dict) -> str:
    """DD-005."""
    spec_sha = hashlib.sha256(_canon(spec).encode()).hexdigest()
    return hashlib.sha256(f"JOSPKT1\n{design_version_id}\n{stl_sha}\n{spec_sha}".encode()).hexdigest()


def build_packet(ctx, o, actor, payload=None) -> int:
    payload = payload or {}
    out = {}

    def effect(c, cur):
        d = tr.latest_design(c, o["id"])
        fs = tr.latest_factory_spec(c, o["id"])
        spec = jload(fs["spec_json"], {})
        stl = c.execute("SELECT * FROM assets WHERE id=?", (spec["stl_asset_id"],)).fetchone()
        data = files.read(ctx, stl)
        h = packet_hash(d["id"], files.sha256(data), spec)
        c.execute("UPDATE factory_packets SET status='VOID', void_reason='새 패킷' WHERE order_id=? AND status='VALID'",
                  (o["id"],))
        zbuf = io.BytesIO()
        manifest = {"order_no": o["order_no"], "design_version": d["version"], "stl_file": stl["filename"],
                    "stl_sha256": files.sha256(data), "spec": spec, "packet_hash": h, "created_at": clock.now_iso()}
        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            z.writestr(stl["filename"], data)
            z.writestr("spec.txt", "\n".join(f"{lab}: {spec.get(k) or ''}" for k, lab in FACTORY_SPEC_FIELDS))
            z.writestr("CHECKSUM.txt", f"packet_hash={h}\nstl_sha256={files.sha256(data)}\n")
        rel = f"{o['order_no']}/packet/{h}.zip"
        p = ctx.cfg.files_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(zbuf.getvalue())
        out["id"] = c.execute("INSERT INTO factory_packets(order_id, design_version_id, spec_id, stl_hash, packet_hash, "
                              "status, zip_ref, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                              (o["id"], d["id"], fs["id"], files.sha256(data), h, "VALID", rel, actor.id,
                               clock.now_iso())).lastrowid
    tr.perform(ctx, o["id"], "build_packet", actor, payload, expected_version=payload.get("version"), effect=effect)
    return out["id"]


def packet_hash_ok(ctx, c, pk) -> bool:
    fs = c.execute("SELECT * FROM factory_specs WHERE id=?", (pk["spec_id"],)).fetchone()
    spec = jload(fs["spec_json"], {})
    stl = c.execute("SELECT * FROM assets WHERE id=?", (spec.get("stl_asset_id"),)).fetchone()
    if stl is None:
        return False
    data = files.read(ctx, stl)
    if data is None:
        return False
    return packet_hash(pk["design_version_id"], files.sha256(data), spec) == pk["packet_hash"]


def send_factory(ctx, o, actor, payload):
    def effect(c, cur):
        pk = tr.valid_packet(c, o["id"])
        c.execute("UPDATE factory_packets SET status='SENT', sent_at=? WHERE id=?", (clock.now_iso(), pk["id"]))
        c.execute("INSERT INTO factory_jobs(packet_id, order_id, vendor_ref, expected_at, status) VALUES (?,?,?,?,?)",
                  (pk["id"], o["id"], payload.get("vendor_ref"), payload.get("expected_at"), "SENT"))
    return tr.perform(ctx, o["id"], "send_factory", actor, payload, expected_version=payload.get("version"),
                      effect=effect)


def receive(ctx, o, actor, payload):
    def effect(c, cur):
        c.execute("UPDATE factory_jobs SET received_at=?, actual_cost_krw=?, defect_cost_krw=?, logistics_cost_krw=?, "
                  "status='RECEIVED' WHERE order_id=? AND status='SENT'",
                  (payload["received_at"], parse_won(payload.get("actual_cost")) or 0,
                   parse_won(payload.get("defect_cost")) or 0, parse_won(payload.get("logistics_cost")) or 0, o["id"]))
    return tr.perform(ctx, o["id"], "receive", actor, payload, expected_version=payload.get("version"), effect=effect)


def factory_job(ctx, order_id):
    return ctx.db.one("SELECT * FROM factory_jobs WHERE order_id=? ORDER BY id DESC LIMIT 1", (order_id,))


def qc_photos(ctx, order_id):
    return ctx.db.all("SELECT * FROM assets WHERE order_id=? AND kind='qc_photo' ORDER BY id", (order_id,))


def record_qc(ctx, o, actor, results: dict, photos: list[tuple[str, bytes]] | None = None) -> str:
    """results = {item: 'pass'|'fail', item_memo: str}. 전부 기록되면 PASS/FAIL, 아니면 INCOMPLETE.
    photos = 검수 사진(각인·세팅 확대 등). 검수 통과에는 사진이 한 장 이상 있어야 한다 — 하자 분쟁 때
    공방 쪽 증거(2026-10-25 시뮬레이션 S-52)."""
    _maker(actor)
    checklist = {k: {"label": lab, "result": results.get(k), "memo": results.get(f"{k}_memo") or ""} for k, lab in QC_ITEMS}
    vals = [v["result"] for v in checklist.values()]
    result = "INCOMPLETE" if any(v not in ("pass", "fail") for v in vals) else ("FAIL" if "fail" in vals else "PASS")
    photos = [p for p in (photos or []) if p and p[1]]
    for name, data in photos:
        files.check("qc_photo", name, data)
    if result == "PASS" and not photos and not qc_photos(ctx, o["id"]):
        raise TransitionBlocked([unmet("G-QC-PHOTO")])
    with ctx.db.tx() as c:
        for name, data in photos:
            files.store(ctx, c, order_id=o["id"], order_no=o["order_no"], kind="qc_photo", filename=name, data=data,
                        actor=actor)
        c.execute("INSERT INTO quality_checks(order_id, checklist_json, result, inspected_by, inspected_at) "
                  "VALUES (?,?,?,?,?)", (o["id"], jdump(checklist), result, actor.id, clock.now_iso()))
        audit.record(c, "qc", o["order_no"], actor, "qc_recorded", order_id=o["id"], next=result)
    if result == "PASS":
        tr.perform(ctx, o["id"], "pass_qc", actor, {"confirm": results.get("confirm")})
    return result


def latest_qc(ctx, order_id):
    r = ctx.db.one("SELECT * FROM quality_checks WHERE order_id=? ORDER BY id DESC LIMIT 1", (order_id,))
    return (dict(r) | {"checklist": jload(r["checklist_json"], {})}) if r else None


def ship(ctx, o, actor, payload):
    def effect(c, cur):
        c.execute("INSERT INTO shipments(order_id, recipient_enc, method, carrier, tracking_no, platform_status, "
                  "shipped_at, created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (o["id"], ctx.enc(jdump({"name": payload.get("recipient"), "phone": payload.get("recipient_phone"),
                                           "address": payload.get("address")})), payload.get("method"),
                   payload.get("carrier"), payload.get("tracking_no"), payload.get("platform_status"), clock.now_iso(),
                   clock.now_iso()))
    return tr.perform(ctx, o["id"], "ship", actor, payload, expected_version=payload.get("version"), effect=effect)


def close(ctx, o, actor, payload):
    def effect(c, cur):
        c.execute("UPDATE shipments SET delivered_confirmed_at=?, delivery_evidence=? WHERE order_id=?",
                  (clock.now_iso(), payload.get("evidence"), o["id"]))
    return tr.perform(ctx, o["id"], "close", actor, payload, expected_version=payload.get("version"), effect=effect)


def margin(ctx, o) -> dict:
    """견적 예상 vs 실제(SRS-077·162) — 고객 합의 금액은 바뀌지 않는다."""
    q = ctx.db.one("SELECT * FROM quotes WHERE order_id=? AND status='APPROVED' ORDER BY version DESC LIMIT 1", (o["id"],))
    if q is None:
        return {}
    est = q["total_krw"] - q["cost_estimate_krw"] - q["variable_cost_krw"]
    fj = factory_job(ctx, o["id"])
    out = {"total": q["total_krw"], "cost_estimate": q["cost_estimate_krw"], "variable": q["variable_cost_krw"],
           "margin_est": est}
    if fj and fj["actual_cost_krw"] is not None:
        actual = (fj["actual_cost_krw"] or 0) + (fj["defect_cost_krw"] or 0) + (fj["logistics_cost_krw"] or 0)
        out.update(actual_cost=actual, margin_actual=q["total_krw"] - actual - q["variable_cost_krw"])
        out["diff"] = out["margin_actual"] - est
    return out


def set_cad_slot(ctx, o, actor, date_str):
    _maker(actor)
    with ctx.db.tx() as c:
        c.execute("DELETE FROM cad_slots WHERE order_id=?", (o["id"],))
        c.execute("INSERT INTO cad_slots(date, order_id, set_by, set_at) VALUES (?,?,?,?)",
                  (date_str, o["id"], actor.id, clock.now_iso()))
        audit.record(c, "cad_slot", o["order_no"], actor, "cad_slot_set", order_id=o["id"], next=date_str)
