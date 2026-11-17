"""Refresh the figures embedded in the slide HTML.

  python3 docs/slides/embed_figures.py

Slides embed their images as base64 (so the deck is self-contained). Every
<img> that should show a figure carries data-fig="<file>"; this script replaces
its src with the current docs/slides/deck/project/slides/assets/<file>, which
analysis/generate_report_artifacts.py writes. Run it after regenerating the
figures, then rebuild the PDF (deck_to_pdf.sh Team7-final.pdf).
"""
import base64
import re
from pathlib import Path

SLIDES = Path(__file__).resolve().parent / "deck/project/slides"
IMG = re.compile(r'<img([^>]*?)data-fig="([^"]+)"([^>]*?)src="[^"]*"', re.S)


def main():
    for f in sorted(SLIDES.glob("*.html")):
        s = f.read_text()
        n = 0

        def sub(m):
            nonlocal n
            data = base64.b64encode((SLIDES / "assets" / m.group(2)).read_bytes()).decode()
            n += 1
            return f'<img{m.group(1)}data-fig="{m.group(2)}"{m.group(3)}src="data:image/png;base64,{data}"'

        s = IMG.sub(sub, s)
        if n:
            f.write_text(s)
            print(f"{f.name}: {n} figure(s)")


if __name__ == "__main__":
    main()
