#!/usr/bin/env bash
# Usage: deck_to_pdf.sh [out.pdf]  (final deck: deck_to_pdf.sh Team7-final.pdf;
# default Team7-midterm.pdf). Print the slide deck (docs/slides/deck/project, the same files as
# the claude.ai Slides artifact) to docs/slides/Team7-midterm.pdf with the
# Windows Chrome in headless mode: one 1920x1080 page per slide, deck order,
# speaker notes left out. Needs internet for the Google Fonts.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
tmp=/mnt/c/Users/$(cmd.exe /c echo %USERNAME% 2>/dev/null | tr -d '\r')/AppData/Local/Temp/team7deck
mkdir -p "$tmp"
python3 -I - "$here/deck" "$tmp/deck.html" <<'PY'
import json, sys
from pathlib import Path
d, out = Path(sys.argv[1]), sys.argv[2]
deck = json.loads((d / "project/deck.json").read_text())
links = "\n".join(f'<link rel="stylesheet" href="{f["href"]}">' for f in deck["faces"].values() if "href" in f)
pages = [(d / f"project/slides/{s}.html").read_text() for s in deck["order"]
         if (d / f"project/slides/{s}.html").exists()]
css = """@page { size: 1920px 1080px; margin: 0 }
html, body { margin: 0; padding: 0 }
section { width: 1920px; height: 1080px; box-sizing: border-box; position: relative; overflow: hidden; break-after: page }
section * { box-sizing: border-box }
h1, h2, h3, p, ul, ol { margin: 0 }
ul, ol { padding-left: 1.2em }
hr { border: 0; margin: 0 }
aside { display: none }"""
open(out, "w").write(f'<!doctype html><html><head><meta charset="utf-8"><title>{deck["title"]}</title>\n{links}\n<style>{css}</style></head><body>{"".join(pages)}</body></html>')
print(len(pages), "slides")
PY
win=$(wslpath -w "$tmp")
"/mnt/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu \
    --no-pdf-header-footer --virtual-time-budget=10000 \
    --print-to-pdf="$win\\deck.pdf" "file:///$(echo "$win" | tr '\\' '/')/deck.html" 2>/dev/null
out=${1:-Team7-midterm.pdf}
cp "$tmp/deck.pdf" "$here/$out"
echo "wrote $here/$out"
