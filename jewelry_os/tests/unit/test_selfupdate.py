"""D-23 운영 PC 자동 업데이트 — 새 커밋이 있으면 백업 뒤 받고, 없거나 받을 수 없으면 그대로 켠다."""
import subprocess
from types import SimpleNamespace

import pytest

from jewelry_os.core import selfupdate


def git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    origin, dev, shop = tmp_path / "origin.git", tmp_path / "dev", tmp_path / "shop"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", "-q", str(origin), str(dev))
    for k, v in (("user.name", "t"), ("user.email", "t@t")):
        git(dev, "config", k, v)
    (dev / "a.txt").write_text("1")
    git(dev, "add", ".")
    git(dev, "commit", "-q", "-m", "1")
    git(dev, "push", "-q", "origin", "main")
    git(tmp_path, "clone", "-q", str(origin), str(shop))
    monkeypatch.setattr(selfupdate, "REPO", shop)
    monkeypatch.delenv(selfupdate.SKIP_ENV, raising=False)
    return dev, shop


def cfg(tmp_path, env="prod"):
    return SimpleNamespace(env=env, data_dir=tmp_path / "data")


def push_new(dev):
    (dev / "a.txt").write_text("2")
    git(dev, "commit", "-q", "-am", "2")
    git(dev, "push", "-q", "origin", "main")


def test_no_update_when_current(repos, tmp_path):
    assert selfupdate.update_available() is None
    assert selfupdate.run(cfg(tmp_path)) is False


def test_pulls_new_version_after_backup(repos, tmp_path, monkeypatch):
    dev, shop = repos
    push_new(dev)
    calls = []
    real = subprocess.run
    monkeypatch.setattr(selfupdate.subprocess, "run",
                        lambda a, **k: calls.append(a) or SimpleNamespace(returncode=0) if "backup" in a else real(a, **k))
    assert selfupdate.run(cfg(tmp_path)) is True
    assert (shop / "a.txt").read_text() == "2"
    assert any("backup" in c for c in calls)


def test_backup_failure_keeps_current_version(repos, tmp_path, monkeypatch):
    dev, shop = repos
    push_new(dev)
    real = subprocess.run
    monkeypatch.setattr(selfupdate.subprocess, "run",
                        lambda a, **k: SimpleNamespace(returncode=1) if "backup" in a else real(a, **k))
    assert selfupdate.run(cfg(tmp_path)) is False
    assert (shop / "a.txt").read_text() == "1"


def test_only_prod_and_not_after_restart(repos, tmp_path, monkeypatch):
    dev, _ = repos
    push_new(dev)
    assert selfupdate.run(cfg(tmp_path, env="dev")) is False
    monkeypatch.setenv(selfupdate.SKIP_ENV, "1")
    assert selfupdate.run(cfg(tmp_path)) is False


def test_offline_or_no_repo_is_quiet(tmp_path, monkeypatch):
    monkeypatch.setattr(selfupdate, "REPO", tmp_path / "nope")
    monkeypatch.delenv(selfupdate.SKIP_ENV, raising=False)
    assert selfupdate.run(cfg(tmp_path)) is False
