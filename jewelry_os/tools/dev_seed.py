"""개발용 데이터 폴더 준비(JOS_ENV=dev). 시험 계정은 이 파일이 원본이다(실제 고객·실제 비밀번호 아님).
사용: python tools/dev_seed.py <데이터 폴더>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jewelry_os.bootstrap import build_ctx, needs_setup, run_setup  # noqa: E402
from jewelry_os.core.config import load_config  # noqa: E402
from jewelry_os.devtools import seed_demo  # noqa: E402

DEV_ACCOUNTS = {"maker": ("father", "김장인", "maker-pass-123"), "operator": ("ops", "김운영", "oper-pass-1234")}

if __name__ == "__main__":
    cfg = load_config("dev", sys.argv[1] if len(sys.argv) > 1 else None)
    ctx = build_ctx(cfg)
    if needs_setup(ctx):
        m, o = DEV_ACCOUNTS["maker"], DEV_ACCOUNTS["operator"]
        run_setup(ctx, {"username": m[0], "display_name": m[1], "password": m[2]},
                  {"username": o[0], "display_name": o[1], "password": o[2]})
        seed_demo(ctx)
        print("seeded", cfg.data_dir)
    else:
        print("already set up", cfg.data_dir)
