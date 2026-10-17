"""디자인 토큰 대비비 검사 — PDS §2 색상 토큰의 WCAG 2.2 AA 충족을 계산으로 확인한다.
사용: python check_contrast.py design-tokens.json  (종료코드 0 = 모든 쌍 통과)
"""
import json
import sys


def lum(hex_):
    hex_ = hex_.lstrip("#")
    rgb = [int(hex_[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def main(path):
    tokens = json.load(open(path, encoding="utf-8"))
    failed = 0
    for theme, spec in tokens["themes"].items():
        colors = spec["color"]
        for fg, bg, minimum, use in tokens["pairs"]:
            r = ratio(colors[fg], colors[bg])
            ok = r >= minimum
            failed += not ok
            print(f"{theme:5} {'PASS' if ok else 'FAIL'} {r:5.2f} >= {minimum:<3} {fg} on {bg} ({use})")
    print(f"\nfailed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "design-tokens.json"))
