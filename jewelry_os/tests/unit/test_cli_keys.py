"""설치 가이드용 CLI: set-secret · export-keys · import-keys · install-task(pythonw)."""
import getpass

from jewelry_os import cli
from jewelry_os.core.config import load_config


def test_set_secret_and_key_roundtrip(tmp_path, monkeypatch, capsys):
    a = tmp_path / "pcA"
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "sk-ant-test-value")
    assert cli.main(["--env", "test", "--data-dir", str(a), "set-secret", "ANTHROPIC_API_KEY"]) == 0
    assert load_config("test", a).secret("ANTHROPIC_API_KEY") == "sk-ant-test-value"
    assert "sk-ant-test-value" not in capsys.readouterr().out  # 값은 출력하지 않음
    keyfile = tmp_path / "keys.txt"
    assert cli.main(["--env", "test", "--data-dir", str(a), "export-keys", str(keyfile)]) == 0
    b = tmp_path / "pcB"
    assert cli.main(["--env", "test", "--data-dir", str(b), "import-keys", str(keyfile)]) == 0
    ca, cb = load_config("test", a), load_config("test", b)
    for k in cli.KEY_NAMES:
        assert ca.secret(k) == cb.secret(k)


def test_import_keys_refuses_when_data_exists(tmp_path, ctx, maker, operator):
    from helpers import new_order
    new_order(ctx)
    keyfile = tmp_path / "k.txt"
    keyfile.write_text("JOS_DATA_KEY=a\nJOS_BACKUP_KEY=b\nJOS_SECRET=c\n", encoding="utf-8")
    assert cli.main(["--env", "test", "--data-dir", str(ctx.cfg.data_dir), "import-keys", str(keyfile)]) == 2


def test_install_task_uses_pythonw(tmp_path):
    assert cli.main(["--env", "test", "--data-dir", str(tmp_path), "install-task", "--dry-run"]) == 0
    xml = (tmp_path / "JewelryOS-task.xml").read_text(encoding="utf-16")
    assert "pythonw.exe" in xml and "<LogonTrigger>" in xml


def test_bundled_google_client_fallback(tmp_path, monkeypatch):
    """D-21: 코드에 든 Google 클라이언트 값을 기본으로, PC별 secrets.env·환경변수가 있으면 그것이 우선."""
    import json
    from jewelry_os.core import config
    f = tmp_path / "google_client.json"
    f.write_text(json.dumps({"client_id": "bundled-id.apps.googleusercontent.com", "client_secret": "bundled-sec"}),
                 encoding="utf-8")
    monkeypatch.setattr(config, "BUNDLED_FILE", f)
    monkeypatch.delenv("JOS_GOOGLE_CLIENT_ID", raising=False)
    cfg = load_config("test", tmp_path / "d")
    assert cfg.secret("JOS_GOOGLE_CLIENT_ID") == "bundled-id.apps.googleusercontent.com"
    assert cfg.secret("JOS_GOOGLE_CLIENT_SECRET") == "bundled-sec"
    assert cfg.secret("JOS_GOOGLE_REFRESH_TOKEN") is None  # 연결 토큰은 코드에 넣지 않는다
    cfg.set_secret("JOS_GOOGLE_CLIENT_ID", "pc-id")
    assert cfg.secret("JOS_GOOGLE_CLIENT_ID") == "pc-id"
    f.write_text(json.dumps({"client_id": "", "client_secret": ""}), encoding="utf-8")
    assert load_config("test", tmp_path / "e").secret("JOS_GOOGLE_CLIENT_SECRET") is None


def test_keys_carry_google_connection(tmp_path, capsys):
    a, b = tmp_path / "pcA", tmp_path / "pcB"
    load_config("test", a).set_secret("JOS_GOOGLE_REFRESH_TOKEN", "rt-from-pcA")
    keyfile = tmp_path / "k.txt"
    assert cli.main(["--env", "test", "--data-dir", str(a), "export-keys", str(keyfile)]) == 0
    assert "Google 연결" in capsys.readouterr().out
    assert cli.main(["--env", "test", "--data-dir", str(b), "import-keys", str(keyfile)]) == 0
    assert load_config("test", b).secret("JOS_GOOGLE_REFRESH_TOKEN") == "rt-from-pcA"
    c = tmp_path / "pcC"  # 연결 전 PC에서 내보낸 키 파일(토큰 없음)도 가져올 수 있다
    k2 = tmp_path / "k2.txt"
    assert cli.main(["--env", "test", "--data-dir", str(c), "export-keys", str(k2)]) == 0
    assert "JOS_GOOGLE_REFRESH_TOKEN" not in k2.read_text(encoding="utf-8")
    assert cli.main(["--env", "test", "--data-dir", str(tmp_path / "pcD"), "import-keys", str(k2)]) == 0
