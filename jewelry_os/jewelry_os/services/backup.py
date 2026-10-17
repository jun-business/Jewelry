"""암호화 백업·복원(DD-008, SRS-083·100·109)."""
from __future__ import annotations

import io
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ..core import clock


class RestoreError(Exception):
    pass


def _fernet(cfg) -> Fernet:
    return Fernet(cfg.secret("JOS_BACKUP_KEY").encode())


def make_backup(ctx) -> Path:
    cfg = ctx.cfg
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "jewelry_os.db"
        src = sqlite3.connect(cfg.db_path)
        dst = sqlite3.connect(snap)
        src.backup(dst)
        dst.close()
        src.close()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "jewelry_os.db")
            for p in cfg.files_dir.rglob("*"):
                if p.is_file():
                    z.write(p, "files/" + p.relative_to(cfg.files_dir).as_posix())
    token = _fernet(cfg).encrypt(buf.getvalue())
    name = f"jos-{clock.kst(clock.now()):%Y%m%d-%H%M%S}.josbak"
    out = cfg.backups_dir / name
    out.write_bytes(token)
    with ctx.db.tx() as c:
        c.execute("INSERT INTO backups(path, size, status, created_at) VALUES (?,?,?,?)",
                  (str(out), len(token), "OK", clock.now_iso()))
    prune(ctx)
    return out


def prune(ctx) -> None:
    from . import settings
    keep = timedelta(days=int(settings.get(ctx, "backup_keep_days")))
    for r in ctx.db.all("SELECT * FROM backups WHERE status='OK'"):
        if clock.now() - clock.parse(r["created_at"]) > keep:
            Path(r["path"]).unlink(missing_ok=True)
            with ctx.db.tx() as c:
                c.execute("UPDATE backups SET status='PRUNED' WHERE id=?", (r["id"],))


def due(ctx) -> bool:
    r = ctx.db.one("SELECT created_at FROM backups WHERE status='OK' ORDER BY id DESC LIMIT 1")
    if r is None:
        return True
    from . import settings
    return clock.now() - clock.parse(r["created_at"]) > timedelta(hours=int(settings.get(ctx, "alert_backup_hours")))


def restore(cfg, backup_file: Path, key: str | None = None) -> dict:
    """앱이 꺼진 상태에서 실행. 실패 시 원본 데이터 무변경."""
    f = Fernet((key or cfg.secret("JOS_BACKUP_KEY")).encode())
    try:
        raw = f.decrypt(Path(backup_file).read_bytes())
    except InvalidToken:
        raise RestoreError("백업 키가 맞지 않거나 파일이 손상됐어요.")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            z.extractall(tdp)
        con = sqlite3.connect(tdp / "jewelry_os.db")
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            con.close()
            raise RestoreError(f"DB 무결성 검사 실패: {ok}")
        import hashlib
        bad = []
        for aid, ref, h in con.execute("SELECT id, storage_ref, file_hash FROM assets WHERE storage_ref IS NOT NULL"):
            p = tdp / "files" / ref
            if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != h:
                bad.append(aid)
        summary = {"orders": con.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
                   "assets": con.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
                   "audit": con.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0],
                   "acceptances": con.execute("SELECT COUNT(*) FROM customer_acceptances").fetchone()[0],
                   "bad_files": bad}
        con.close()
        if bad:
            raise RestoreError(f"파일 해시가 맞지 않아요: {bad}")
        # 원자적 교체: 기존 것을 옆으로 옮기고 새 것을 놓는다
        stamp = f"{clock.kst(clock.now()):%Y%m%d%H%M%S}"
        old_db = cfg.db_path.with_name(f"jewelry_os.db.before-restore-{stamp}")
        if cfg.db_path.exists():
            cfg.db_path.replace(old_db)
        for suffix in ("-wal", "-shm"):
            Path(str(cfg.db_path) + suffix).unlink(missing_ok=True)
        shutil.copy2(tdp / "jewelry_os.db", cfg.db_path)
        if cfg.files_dir.exists():
            cfg.files_dir.replace(cfg.files_dir.with_name(f"files.before-restore-{stamp}"))
        shutil.copytree(tdp / "files", cfg.files_dir) if (tdp / "files").exists() else cfg.files_dir.mkdir()
    return summary
