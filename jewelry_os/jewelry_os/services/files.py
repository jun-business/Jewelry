"""파일 저장(FR-MFG-002, SRS-009·070). 데이터 폴더 안에만 두고 인증 라우트로만 제공한다."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..core import clock

LIMITS = {  # kind → (허용 확장자, 최대 MB)
    "reference": ({".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf"}, 30),
    "concept": ({".jpg", ".jpeg", ".png", ".webp", ".pdf"}, 30),
    "preview": ({".jpg", ".jpeg", ".png", ".webp", ".pdf"}, 30),
    "cad": ({".3dm"}, 200),
    "stl": ({".stl"}, 200),
    "qc_photo": ({".jpg", ".jpeg", ".png", ".webp"}, 30),
    "evidence": ({".jpg", ".jpeg", ".png", ".webp", ".pdf"}, 30),
}
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".pdf": "application/pdf",
        ".stl": "model/stl", ".3dm": "application/octet-stream", ".heic": "image/heic", ".zip": "application/zip"}


class FileRejected(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe(name: str) -> str:
    name = Path(name).name
    return re.sub(r"[^\w.\-가-힣 ]", "_", name)[:120] or "file"


def check(kind: str, filename: str, data: bytes) -> None:
    exts, mb = LIMITS[kind]
    ext = Path(filename).suffix.lower()
    if ext not in exts:
        raise FileRejected(f"이 파일은 올릴 수 없어요. {'·'.join(sorted(e.lstrip('.').upper() for e in exts))}만, "
                           f"최대 {mb}MB예요.")
    if len(data) > mb * 1024 * 1024:
        raise FileRejected(f"파일이 너무 커요. 최대 {mb}MB예요.")
    if not data:
        raise FileRejected("빈 파일이에요.")


def store(ctx, c, *, order_id, order_no, kind, filename, data: bytes, actor=None, source_file_id=None,
          version=None, validate=True) -> int:
    if validate:
        check(kind, filename, data)
    h = sha256(data)
    ext = Path(filename).suffix.lower()
    rel = Path(order_no) / kind / f"{h}{ext}"
    full = ctx.cfg.files_dir / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    if not full.exists():
        full.write_bytes(data)
    if version is None:
        version = (c.execute("SELECT COALESCE(MAX(version),0) FROM assets WHERE order_id=? AND kind=?",
                             (order_id, kind)).fetchone()[0]) + 1
    cur = c.execute("INSERT INTO assets(order_id, kind, version, filename, mime, size, file_hash, storage_ref, "
                    "source_file_id, uploaded_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (order_id, kind, version, _safe(filename), MIME.get(ext), len(data), h, str(rel).replace("\\", "/"),
                     source_file_id, getattr(actor, "id", None), clock.now_iso()))
    return cur.lastrowid


def placeholder(c, *, order_id, kind, filename, source_file_id, error) -> int:
    cur = c.execute("INSERT INTO assets(order_id, kind, version, filename, source_file_id, fetch_status, fetch_error, "
                    "created_at) VALUES (?,?,?,?,?,?,?,?)", (order_id, kind, 1, _safe(filename), source_file_id,
                                                              "FAILED", error, clock.now_iso()))
    return cur.lastrowid


def read(ctx, asset) -> bytes | None:
    if not asset["storage_ref"]:
        return None
    p = ctx.cfg.files_dir / asset["storage_ref"]
    return p.read_bytes() if p.exists() else None


def verify(ctx, asset) -> bool:
    data = read(ctx, asset)
    return data is not None and sha256(data) == asset["file_hash"]
