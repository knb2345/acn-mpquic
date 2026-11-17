"""Fill the results table on slide 8 from analysis/tables/main_2600.csv.

  python3 docs/slides/make_results_table.py

Replaces everything between <!--TABLE--> and <!--/TABLE--> in results.html,
so no number in the table is typed by hand. Cells: mean frames on time at
D = 250 ms over completed runs; "stalled" when 4+ of 8 runs stalled; a dash
where the configuration is not shown (R1 under outage).
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SLIDE = ROOT / "docs/slides/deck/project/slides/results.html"
COLS = [("S0", "Single path<br>(S0)"), ("S1", "MinRTT<br>(S1)"), ("S4", "ECF<br>(S4)"),
        ("S2", "Round-robin<br>(S2)"), ("F2", "Frame split<br>(F2, ours)"), ("A1", "F2 over two<br>conns (A1)"),
        ("R1", "Ideal link<br>(R1)")]
ROWS = [("symmetric", "Symmetric (1.6 + 1.6, 40 ms)"), ("bw_asym", "Bandwidth asym. (2.0 + 1.2, 40/60 ms)"),
        ("rtt_asym", "RTT asym. (1.6 + 1.6, 30/150 ms)"), ("outage", "2-s outages (1.8 + 1.8, every 15 s)")]
TH = "font-weight:600;padding:8px 6px;border-bottom:2px solid #14213D"


def main():
    t = {(r["scenario"], r["config"]): r for r in csv.DictReader(open(ROOT / "analysis/tables/main_2600.csv"))}
    head = "".join(f'<th style="{TH}{";color:#2F6FB0" if c == "F2" else ""}">{n}</th>' for c, n in COLS)
    body = []
    for k, (scen, label) in enumerate(ROWS):
        last = k == len(ROWS) - 1
        b = "" if last else ";border-bottom:1px solid #DCE1E8"
        cells = []
        for c, _ in COLS:
            r = t.get((scen, c))
            if r is None:
                v = "–"
            elif r["on_time_250"] == "":
                v = "stalled"
            else:
                v = f'{float(r["on_time_250"]) * 100:.1f}%'
            style = f"padding:10px 6px{b}"
            if c == "F2":
                style += ";font-weight:600;color:#2F6FB0"
            cells.append(f'<td style="{style}">{v}</td>')
        body.append(f'<tr><td style="text-align:left;padding:10px 6px{b}">{label}</td>{"".join(cells)}</tr>')
    table = (f'<table style="border-collapse:collapse;font-size:22px;width:100%">\n'
             f'<thead><tr style="color:#4A5568;text-align:right"><th style="text-align:left;{TH}">Scenario</th>{head}</tr></thead>\n'
             f'<tbody style="text-align:right;font-variant-numeric:tabular-nums">\n' + "\n".join(body) + "\n</tbody>\n</table>")
    s = SLIDE.read_text()
    i, j = s.index("<!--TABLE-->"), s.index("<!--/TABLE-->")
    SLIDE.write_text(s[: i + len("<!--TABLE-->")] + "\n" + table + "\n" + s[j:])
    print("results table updated")


if __name__ == "__main__":
    main()
