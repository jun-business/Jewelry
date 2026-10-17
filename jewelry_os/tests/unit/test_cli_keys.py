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
