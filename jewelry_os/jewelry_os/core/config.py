"""환경 설정(TRD-001·006·010, SRS-107·160). 업무 설정은 DB settings 테이블(services/settings.py)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

HOST = "127.0.0.1"  # SRS-145: R1은 루프백만. 설정으로 바꿀 수 없다.
DEFAULT_PORT = 8770
DEV_PORT = 8771  # 개발용 앱은 다른 주소 — 운영 앱(8770)과 헷갈리지 않게


def _default_data_dir(env: str) -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "JewelryOS" / env


BUNDLED_GOOGLE = {"JOS_GOOGLE_CLIENT_ID": "client_id", "JOS_GOOGLE_CLIENT_SECRET": "client_secret"}
BUNDLED_FILE = Path(__file__).resolve().parents[1] / "google_client.json"


def bundled_secret(name: str) -> str | None:
    """코드에 들어 있는 Google 데스크톱 앱 클라이언트 값(jewelry_os/google_client.json, D-21). 비어 있으면 None."""
    key = BUNDLED_GOOGLE.get(name)
    if key is None:
        return None
    import json
    try:
        v = (json.loads(BUNDLED_FILE.read_text(encoding="utf-8")).get(key) or "").strip()
    except (OSError, ValueError):
        return None
    return v or None


@dataclass
class Config:
    env: str = "prod"
    data_dir: Path = field(default_factory=lambda: _default_data_dir("prod"))
    port: int = DEFAULT_PORT
    secrets: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jewelry_os.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def secrets_path(self) -> Path:
        return self.data_dir / "secrets.env"

    @property
    def is_test(self) -> bool:
        return self.env == "test"

    def secret(self, name: str) -> str | None:
        return os.environ.get(name) or self.secrets.get(name) or bundled_secret(name)

    def set_secret(self, name: str, value: str) -> None:
        self.secrets[name] = value
        lines = [f"{k}={v}" for k, v in sorted(self.secrets.items())]
        self.secrets_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _restrict(self.secrets_path)


def _restrict(path: Path) -> None:
    """비밀 파일을 현재 사용자 전용으로(가능한 범위에서)."""
    try:
        if os.name == "nt":
            import subprocess
            user = os.environ.get("USERNAME", "")
            if user:
                subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                               capture_output=True, check=False)
        else:
            os.chmod(path, 0o600)
    except Exception:
        pass


def load_config(env: str | None = None, data_dir: str | os.PathLike | None = None) -> Config:
    env = env or os.environ.get("JOS_ENV", "prod")
    if env not in ("test", "dev", "prod"):
        raise ValueError(f"JOS_ENV must be test|dev|prod, got {env}")
    d = Path(data_dir or os.environ.get("JOS_DATA_DIR") or _default_data_dir(env))
    cfg = Config(env=env, data_dir=d, port=int(os.environ.get("JOS_PORT", DEV_PORT if env == "dev" else DEFAULT_PORT)))
    for sub in (cfg.data_dir, cfg.files_dir, cfg.backups_dir):
        sub.mkdir(parents=True, exist_ok=True)
    if cfg.secrets_path.exists():
        for line in cfg.secrets_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg.secrets[k.strip()] = v.strip()
    if not cfg.secret("JOS_BACKUP_KEY"):
        from cryptography.fernet import Fernet
        cfg.set_secret("JOS_BACKUP_KEY", Fernet.generate_key().decode())
    if not cfg.secret("JOS_DATA_KEY"):
        from cryptography.fernet import Fernet
        cfg.set_secret("JOS_DATA_KEY", Fernet.generate_key().decode())
    if not cfg.secret("JOS_SECRET"):
        cfg.set_secret("JOS_SECRET", secrets.token_urlsafe(32))
    return cfg
