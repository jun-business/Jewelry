"""명령줄(PBS-006): serve · backup · restore · install-task · google-auth · seed-demo."""
from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path


def _task_xml(python: str, workdir: str, env: str) -> str:
    """Windows 작업 스케줄러 — 로그온 시 실행(SRS-011)."""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Jewelry OS 자동 시작</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled><Delay>PT30S</Delay></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure></Settings>
  <Actions Context="Author"><Exec><Command>{python}</Command><Arguments>-m jewelry_os serve --env {env}</Arguments><WorkingDirectory>{workdir}</WorkingDirectory></Exec></Actions>
</Task>
"""


SETTABLE = {"ANTHROPIC_API_KEY", "JOS_GOOGLE_CLIENT_ID", "JOS_GOOGLE_CLIENT_SECRET"}
KEY_NAMES = ("JOS_DATA_KEY", "JOS_BACKUP_KEY", "JOS_SECRET")


def _keys_cmd(a, cfg) -> int:
    """비밀값 관리(설치 가이드 §5·§6). 값은 화면·로그에 출력하지 않는다."""
    if a.cmd == "set-secret":
        import getpass
        v = getpass.getpass(f"{a.name} 값을 붙여넣고 Enter(화면에 보이지 않아요): ").strip()
        if not v:
            print("입력이 없어 저장하지 않았어요.", file=sys.stderr)
            return 2
        cfg.set_secret(a.name, v)
        print(f"{a.name}을(를) 저장했어요. 앱을 다시 시작하면 적용돼요.")
        return 0
    if a.cmd == "export-keys":
        out = Path(a.file)
        out.write_text("\n".join(f"{k}={cfg.secret(k)}" for k in KEY_NAMES) + "\n", encoding="utf-8")
        print(f"키를 {out}에 내보냈어요. 이 파일이 있어야 백업을 복원하고 고객 연락처를 읽을 수 있어요 — "
              "USB나 비밀번호 관리자에 따로 보관하고, 백업 파일과 같은 곳에 두지 마세요.")
        return 0
    if a.cmd == "import-keys":
        vals = {}
        for line in Path(a.file).read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() in KEY_NAMES and v.strip():
                    vals[k.strip()] = v.strip()
        if set(vals) != set(KEY_NAMES):
            print("키 파일에 필요한 값이 모두 있지 않아요(JOS_DATA_KEY·JOS_BACKUP_KEY·JOS_SECRET).", file=sys.stderr)
            return 2
        if cfg.db_path.exists() and not a.force:
            import sqlite3
            con = sqlite3.connect(cfg.db_path)
            try:
                n = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            except sqlite3.Error:
                n = 0
            con.close()
            if n:
                print(f"이 PC 데이터에 주문 {n}건이 있어요. 키를 바꾸면 읽을 수 없게 돼요. 정말이면 --force를 붙이세요.",
                      file=sys.stderr)
                return 2
        for k, v in vals.items():
            cfg.set_secret(k, v)
        print("키를 가져왔어요. 이제 restore 명령으로 백업을 복원하세요.")
        return 0
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jewelry_os")
    ap.add_argument("--env", default=os.environ.get("JOS_ENV", "prod"))
    ap.add_argument("--data-dir", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve")
    sv.add_argument("--no-browser", action="store_true")
    sv.add_argument("--env", dest="env2")
    sub.add_parser("backup")
    rs = sub.add_parser("restore")
    rs.add_argument("file")
    it = sub.add_parser("install-task")
    it.add_argument("--dry-run", action="store_true")
    sub.add_parser("google-auth")
    sub.add_parser("seed-demo")
    ss = sub.add_parser("set-secret", help="API 키 등 비밀값을 화면에 보이지 않게 입력해 secrets.env에 저장")
    ss.add_argument("name", choices=sorted(SETTABLE))
    ek = sub.add_parser("export-keys", help="암호화 키를 파일로 내보내기(PC 교체·복원 대비, USB 등에 보관)")
    ek.add_argument("file")
    ik = sub.add_parser("import-keys", help="내보낸 키 파일을 새 PC에 가져오기(복원 전에)")
    ik.add_argument("file")
    ik.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    env = getattr(a, "env2", None) or a.env

    from .core.config import HOST, load_config
    cfg = load_config(env, a.data_dir)

    if a.cmd == "serve":
        from .core.singleton import AlreadyRunning, InstanceLock
        try:
            lock = InstanceLock(cfg.data_dir).acquire()
        except AlreadyRunning:
            print("Jewelry OS가 이미 실행 중이에요.", file=sys.stderr)
            return 3
        import uvicorn

        from .bootstrap import build_ctx
        from .core.logging_setup import setup
        from .web.app import create_app
        (cfg.data_dir / "logs").mkdir(exist_ok=True)
        setup(cfg.data_dir / "logs" / "jos.log")
        ctx = build_ctx(cfg)
        app = create_app(ctx, run_scheduler=True)
        if not a.no_browser:
            webbrowser.open(f"http://{HOST}:{cfg.port}/")
        try:
            uvicorn.run(app, host=HOST, port=cfg.port, log_level="warning")  # SRS-145: 127.0.0.1 고정
        except SystemExit:
            return 3
        finally:
            lock.release()
        return 0
    if a.cmd == "backup":
        from .bootstrap import build_ctx
        from .services import backup
        print(backup.make_backup(build_ctx(cfg)))
        return 0
    if a.cmd == "restore":
        from .core.singleton import AlreadyRunning, InstanceLock
        try:
            lock = InstanceLock(cfg.data_dir).acquire()
        except AlreadyRunning:
            print("앱을 먼저 끄세요. 실행 중에는 복원할 수 없어요.", file=sys.stderr)
            return 3
        from .services import backup
        try:
            s = backup.restore(cfg, Path(a.file))
        except backup.RestoreError as e:
            print(f"복원 실패: {e}", file=sys.stderr)
            return 2
        finally:
            lock.release()
        print(f"복원 완료 — 주문 {s['orders']}건 · 첨부 {s['assets']}건 · 감사 {s['audit']}건 · 동의 {s['acceptances']}건")
        return 0
    if a.cmd in ("set-secret", "export-keys", "import-keys"):
        return _keys_cmd(a, cfg)
    if a.cmd == "install-task":
        pyw = Path(sys.executable).with_name("pythonw.exe")  # 로그온 시 검은 창 없이 실행
        xml = _task_xml(str(pyw if pyw.exists() else sys.executable), str(Path(__file__).resolve().parents[1]), env)
        out = cfg.data_dir / "JewelryOS-task.xml"
        out.write_text(xml, encoding="utf-16")
        if a.dry_run:
            print(out)
            return 0
        import subprocess
        r = subprocess.run(["schtasks", "/Create", "/TN", "JewelryOS", "/XML", str(out), "/F"], capture_output=True,
                           text=True)
        print(r.stdout or r.stderr)
        return r.returncode
    if a.cmd == "google-auth":
        from .adapters.google import GoogleOAuth, run_loopback_flow
        run_loopback_flow(GoogleOAuth(cfg), webbrowser.open)
        print("Google 계정을 연결했어요. 앱이 켜져 있으면 다시 시작하세요(자동 모드면 그때부터 실제 폼에서 가져와요).")
        return 0
    if a.cmd == "seed-demo":
        from .bootstrap import build_ctx
        from .devtools import seed_demo
        seed_demo(build_ctx(cfg))
        print("시험 데이터를 넣었어요.")
        return 0
    return 1
