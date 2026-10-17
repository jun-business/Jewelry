"""docs/design/design-tokens.json → web/static/css/tokens.css (PDS §4.1 — 토큰 단일 원본)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT.parent / "docs" / "design" / "design-tokens.json"
OUT = ROOT / "jewelry_os" / "web" / "static" / "css" / "tokens.css"


def build(tokens: dict) -> str:
    def block(colors):
        return "\n".join(f"  --jos-color-{k}: {v};" for k, v in colors.items())
    common = []
    f = tokens["font"]
    common.append(f"  --jos-font-sans: {f['family-sans']};")
    common.append(f"  --jos-font-mono: {f['family-mono']};")
    for k, v in f["size"].items():
        common.append(f"  --jos-font-size-{k}: {v};")
    for k, v in f["line-height"].items():
        common.append(f"  --jos-line-{k}: {v};")
    for grp in ("space", "radius", "shadow", "size", "motion", "z"):
        for k, v in tokens[grp].items():
            common.append(f"  --jos-{grp}-{k}: {v};")
    light = block(tokens["themes"]["light"]["color"])
    dark = block(tokens["themes"]["dark"]["color"])
    return ("/* 생성 파일 — 직접 고치지 말고 docs/design/design-tokens.json을 고친 뒤 tools/gen_tokens_css.py 실행 */\n"
            f":root {{\n{chr(10).join(common)}\n{light}\n}}\n"
            f"@media (prefers-color-scheme: dark) {{\n  :root:not([data-theme=\"light\"]) {{\n{dark}\n  }}\n}}\n"
            f":root[data-theme=\"dark\"] {{\n{dark}\n}}\n")


if __name__ == "__main__":
    css = build(json.loads(SRC.read_text(encoding="utf-8")))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if "--check" in sys.argv:
        sys.exit(0 if OUT.read_text(encoding="utf-8") == css else 1)
    OUT.write_text(css, encoding="utf-8")
    print(f"wrote {OUT}")
