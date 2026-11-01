"""Wrap the report page for printing (light theme, A4, tables fit the page).

  make_pdf.py <page.html> <print.html>
"""
import sys

PRINT_CSS = """<style>
@page { size: A4; margin: 16mm 14mm; }
@media print {
  html, body { background: #ffffff !important; }
  body { -webkit-print-color-adjust: exact; print-color-adjust: exact; font-size: 10.5pt; }
  main { padding-block: 0; gap: 24px; max-width: none; }
  header { padding-bottom: 14px; }
  .week, .part, .example, .table-wrap, figure, .finding, .diagram, .panel, h2, h3, li { break-inside: avoid; }
  h2, h3 { break-after: avoid; }
  .diagram svg { min-width: 0; }
  .table-wrap { overflow: visible !important; }
  table { font-size: 8.2pt; table-layout: auto; }
  th, td { padding: 4px 6px; white-space: normal; }
  thead th { font-size: 6.6pt; letter-spacing: 0.02em; vertical-align: bottom; }
  td:first-child { white-space: nowrap; }
}
</style>"""

src, dst = sys.argv[1:3]
head, rest = open(src).read().split("<main>", 1)
open(dst, "w").write('<!doctype html><html lang="en" data-theme="light"><head><meta charset="utf-8">'
                     '<meta name="viewport" content="width=device-width, initial-scale=1">' + head
                     + PRINT_CSS + "</head><body><main>" + rest + "</body></html>")
