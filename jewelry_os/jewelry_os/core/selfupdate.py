"""운영 PC 자동 업데이트(D-23): 앱을 켤 때 GitHub에 새 버전이 있으면 백업 → 받기 → 다시 시작.

DB는 데이터 폴더에 그대로 남고, 구조 변경은 기동 시 자동 이행된다. 어느 단계든 실패하면
지금 버전으로 그냥 켠다 — 인터넷이 끊겨도 앱은 열려야 한다.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]  # Jewelry/ (git 저장소 루트)
APP_DIR = Path(__file__).resolve().parents[2]  # Jewelry/jewelry_os/
SKIP_ENV = "JOS_SKIP_UPDATE"  # 다시 시작한 프로세스·개발자가 끌 때
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _git(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=timeout,
                          creationflags=NO_WINDOW)


def _log(cfg, msg: str) -> None:
    try:
        (cfg.data_dir / "logs").mkdir(exist_ok=True)
        with open(cfg.data_dir / "logs" / "update.log", "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


def update_available() -> tuple[str, str] | None:
    """(지금 커밋, 새 커밋) — 새 버전이 없거나 확인할 수 없으면 None."""
    if not (REPO / ".git").exists():
        return None
    if _git("fetch", "-q", timeout=20).returncode != 0:
        return None
    head, up = _git("rev-parse", "HEAD"), _git("rev-parse", "@{u}")
    if head.returncode or up.returncode:
        return None
    old, new = head.stdout.strip(), up.stdout.strip()
    if old == new or _git("merge-base", "--is-ancestor", old, new).returncode != 0:
        return None  # 최신이거나, 이 PC에 따로 커밋이 있어 빨리감기할 수 없음
    return old, new


def run(cfg) -> bool:
    """새 버전을 받았으면 True — 호출자는 잠금을 풀고 restart()로 새 코드를 띄운 뒤 종료한다."""
    if cfg.env != "prod" or os.environ.get(SKIP_ENV):
        return False
    try:
        found = update_available()
        if not found:
            return False
        old, new = found
        py = [sys.executable, "-m", "jewelry_os"]
        if subprocess.run([*py, "--env", cfg.env, "--data-dir", str(cfg.data_dir), "backup"], cwd=APP_DIR, capture_output=True, timeout=300,
                          creationflags=NO_WINDOW).returncode != 0:
            _log(cfg, "백업 실패 — 업데이트하지 않고 지금 버전으로 켬")
            return False
        r = _git("pull", "--ff-only", "-q", timeout=120)
        if r.returncode != 0:
            _log(cfg, "git pull 실패 — 지금 버전으로 켬: " + r.stderr.strip()[:300])
            return False
        if _git("diff", "--quiet", old, new, "--", "jewelry_os/requirements.txt").returncode != 0:
            subprocess.run([*py[:1], "-m", "pip", "install", "-q", "-r", "requirements.txt"], cwd=APP_DIR,
                           capture_output=True, timeout=600, creationflags=NO_WINDOW)
        _log(cfg, f"업데이트 {old[:7]} → {new[:7]}")
        return True
    except (OSError, subprocess.SubprocessError) as e:
        _log(cfg, f"업데이트 확인 실패 — 지금 버전으로 켬: {e}")
        return False


def restart(argv: list[str]) -> None:
    subprocess.Popen([sys.executable, "-m", "jewelry_os", *argv], cwd=APP_DIR, env={**os.environ, SKIP_ENV: "1"},
                     creationflags=NO_WINDOW)
