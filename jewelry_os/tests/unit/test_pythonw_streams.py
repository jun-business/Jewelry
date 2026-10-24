"""start.bat·자동 시작은 pythonw로 띄운다 — stdout·stderr가 None이어도 serve가 죽지 않아야 한다."""
import sys

from jewelry_os.cli import _ensure_std_streams


def test_missing_std_streams_are_replaced(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    _ensure_std_streams()
    assert sys.stdout is not None and sys.stderr is not None
    sys.stdout.write("x")
    sys.stdout.isatty()  # uvicorn 로그 설정이 부르는 호출 — None이면 여기서 죽었다
