"""브랜드 이미지 생성(D-19 틴틴골드). 원본: docs/design/brand/tntngold_logo_source.webp(1300×1300).
사용: .venv/Scripts/python tools/make_brand_assets.py   (Pillow 필요 — requirements-dev.txt)
출력: jewelry_os/web/static/img/ — logo.webp(전체, 고객 문서·로그인), mark.png(TN 모노그램, 사이드바),
      favicon.png·favicon.ico(브라우저 탭), apple-touch-icon.png
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT.parent / "docs" / "design" / "brand" / "tntngold_logo_source.webp"
OUT = ROOT / "jewelry_os" / "web" / "static" / "img"
MARK_BOX = (380, 195, 1000, 760)  # 원본에서 TN 모노그램 + 별 영역


def square(img: Image.Image, bg) -> Image.Image:
    w, h = img.size
    s = max(w, h)
    canvas = Image.new("RGB", (s, s), bg)
    canvas.paste(img, ((s - w) // 2, (s - h) // 2))
    return canvas


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    src = Image.open(SRC).convert("RGB")
    bg = src.getpixel((40, 650))  # 크림색 바탕
    # 테두리 선(가장자리 몇 px)을 잘라 낸 전체 로고
    full = src.crop((14, 14, src.width - 14, src.height - 14)).resize((480, 480), Image.LANCZOS)
    full.save(OUT / "logo.webp", "WEBP", quality=90, method=6)
    mark = square(src.crop(MARK_BOX), bg)
    mark.resize((128, 128), Image.LANCZOS).save(OUT / "mark.png", optimize=True)
    mark.resize((32, 32), Image.LANCZOS).save(OUT / "favicon.png", optimize=True)
    mark.resize((180, 180), Image.LANCZOS).save(OUT / "apple-touch-icon.png", optimize=True)
    mark.save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    for p in sorted(OUT.iterdir()):
        print(p.name, p.stat().st_size)


if __name__ == "__main__":
    main()
