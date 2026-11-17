"""Render docs/report.md to a PDF (figures embedded).

  analysis/.venv/bin/pip install markdown weasyprint   # once
  analysis/.venv/bin/python docs/build_report_pdf.py docs/report.md /tmp/report.html
  analysis/.venv/bin/python -c "import weasyprint; weasyprint.HTML('/tmp/report.html').write_pdf('docs/Team7-final-report.pdf')"
"""
import base64, re, sys
from pathlib import Path
import markdown
src = Path(sys.argv[1]); out = Path(sys.argv[2])
html = markdown.markdown(src.read_text(), extensions=["tables"])
def emb(m):
    p = (src.parent / m.group(1)).resolve()
    return 'src="data:image/png;base64,' + base64.b64encode(p.read_bytes()).decode() + '"'
html = re.sub(r'src="([^"]+\.png)"', emb, html)
css = """
@page { size: A4; margin: 18mm 17mm 18mm 17mm }
body { font-family: 'Source Serif 4', Georgia, serif; color:#14213D; font-size: 10.6pt; line-height: 1.45 }
h1 { font-size: 21pt; line-height:1.15; margin: 0 0 6pt; color:#14213D }
h2 { font-family:'IBM Plex Sans', Arial, sans-serif; font-size: 13.5pt; color:#9A4D12; border-bottom: 1.5px solid #E8D5C2; padding-bottom: 3pt; margin-top: 18pt; break-after: avoid }
h3 { font-family:'IBM Plex Sans', Arial, sans-serif; font-size: 11pt; margin-top: 12pt; break-after: avoid }
p, li { text-align: left }
code { font-family: 'IBM Plex Mono', Consolas, monospace; font-size: 8.8pt; background:#F3F1EC; padding: 0 2px; border-radius:3px }
table { border-collapse: collapse; width: 100%; font-family:'IBM Plex Sans', Arial, sans-serif; font-size: 8.4pt; margin: 6pt 0 10pt; break-inside: avoid; font-variant-numeric: tabular-nums }
th { text-align: left; border-bottom: 1.5px solid #14213D; padding: 3pt 4pt }
td { border-bottom: 1px solid #DCE1E8; padding: 3pt 4pt }
img { width: 100%; margin: 6pt 0; break-inside: avoid }
hr { border: 0; border-top: 1px solid #DCE1E8 }
strong { color: #0F1B33 }
"""
fonts = '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400..700&family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono&display=swap">'
out.write_text(f'<!doctype html><html><head><meta charset="utf-8"><title>Team 7 final report</title>{fonts}<style>{css}</style></head><body>{html}</body></html>')
