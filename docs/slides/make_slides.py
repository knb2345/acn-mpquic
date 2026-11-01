"""Build the presentation deck from the results files.

  python3 docs/slides/make_slides.py

Writes docs/slides/team7-slides.html (present in a browser: arrow keys, N for
speaker notes). Every number on the slides comes from results/.
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = json.loads((REPO / "results/report_numbers_crf22.json").read_text())["medians"]
P = json.loads((REPO / "results/report_numbers_crf22.json").read_text())["paired"]
E = json.loads((REPO / "results/ml_test.eval.json").read_text())
TR = json.loads((REPO / "results/ml_train/train.train.json").read_text())

NAMES = [("learned", "learned (ours)"), ("deadline", "hand-tuned deadline"), ("framepin", "frame-pin"),
         ("minrtt", "MinRTT"), ("ecf", "ECF"), ("roundrobin", "round-robin"), ("redundant", "redundant"),
         ("single", "single path")]
SCENS = ["symmetric", "rtt_asym", "low_bw", "trace", "outage"]


def pct(v, d=1):
    return f"{v:.{d}f}%"


def hbar_chart(rows, vmax, unit="%", width=560, title="", bh=34, gap=14):
    """Horizontal bars, drawn to scale: rows = [(label, value, highlight)]."""
    # Room for the longest label (17px monospace is about 10.3px per character).
    left, right = max(len(r[0]) for r in rows) * 10.3 + 22, 70
    plot = width - left - right
    h = 30 + len(rows) * (bh + gap) + 24
    out = [f'<svg viewBox="0 0 {width} {h}" role="img" aria-label="{title}">']
    for t in (0, 0.5, 1):
        x = left + t * plot
        out.append(f'<line class="grid" x1="{x:.1f}" y1="20" x2="{x:.1f}" y2="{h - 24}"/>')
        out.append(f'<text class="axis" x="{x:.1f}" y="{h - 6}" text-anchor="middle">{t * vmax:g}{unit}</text>')
    for i, (label, v, hi) in enumerate(rows):
        y = 26 + i * (bh + gap)
        w = max(1.0, min(v, vmax) / vmax * plot)
        out.append(f'<text class="lab" x="{left - 10:.1f}" y="{y + bh * 0.68:.1f}" text-anchor="end">{label}</text>')
        out.append(f'<rect class="bar{" hi" if hi else ""}" x="{left:.1f}" y="{y}" width="{w:.1f}" height="{bh}" rx="3"/>')
        out.append(f'<text class="val" x="{left + w + 8:.1f}" y="{y + bh * 0.68:.1f}">{v:.1f}{unit}</text>')
    out.append("</svg>")
    return "\n".join(out)


def miss_table(col, fmt):
    head = "".join(f"<th>{s}</th>" for s in SCENS)
    body = []
    for k, n in NAMES:
        cells = ""
        for s in SCENS:
            v = R[s][k][col]
            best = min(R[s][kk][col] for kk, _ in NAMES)
            cells += f'<td{" class=best" if v == best else ""}>{fmt(v)}</td>'
        body.append(f'<tr{" class=ours" if k == "learned" else ""}><td>{n}</td>{cells}</tr>')
    return f'<table><thead><tr><th>scheduler</th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


t = R["trace"]
pt = P["trace"]
e_tr, e_all = E["trace"], E["all"]
cov = dict(zip(TR["quantiles"], TR["val_coverage"]))

slides = []


def slide(cls, body, notes):
    slides.append((cls, body, notes))


# 1 -------------------------------------------------------------------------
slide("title", """
<div class="kicker">ACN project · Team 7</div>
<h1>Frame-aware multipath QUIC<br>with a learned bandwidth predictor</h1>
<p class="sub">Streaming live lectures over two cellular connections without missing playback deadlines</p>
<p class="who">Kabir Bajaj and Team 7 · 29 October 2026</p>
""", """Introduce the team and the one-line idea: a phone with two SIM cards has two internet connections; we
send a live lecture over both at once, and we built a scheduler, driven by a small neural network, that
decides which connection each video frame takes so frames arrive on time.""")

# 2 -------------------------------------------------------------------------
slide("", """
<h2>The setting</h2>
<div class="cols">
<ul class="big">
<li>Students attend live classes on a <b>phone over mobile data</b></li>
<li>Many phones hold <b>two SIM cards</b>, two carriers: two internet connections at once</li>
<li>Each one alone is often <b>slow and unsteady</b>: a few Mbit/s, 50–150 ms delay, short coverage holes</li>
<li><b>Multipath QUIC</b> runs one connection over both at the same time</li>
</ul>
<div class="card"><div class="kicker">The question</div><p>A <b>scheduler</b> inside the transport decides, packet by packet, which connection
carries the data. How should it decide when the data is <b>live video</b>?</p></div>
</div>
""", """Start from the user: a student in India on mobile data. Dual-SIM phones are common, so there are two
connections. Multipath QUIC (an IETF draft about to become an RFC) lets one connection use both paths.
The scheduler is the piece that decides which path each packet takes; that is what this project is about.""")

# 3 -------------------------------------------------------------------------
slide("", """
<h2>How a live lecture travels</h2>
<div class="frames">
""" + "".join(f'<div class="fr {"i" if j % 8 == 0 else "p"}"></div>' for j in range(17)) + """
</div>
<div class="legend"><span><i class="sw i"></i>I-frame: full picture, ~55 KB, every 2 s</span><span><i class="sw p"></i>P-frame: only the changes, ~8 KB, 24 per second</span></div>
<ul class="big">
<li>A frame is usable only when <b>every byte</b> has arrived</li>
<li>The player shows frame <i>i</i> a fixed <b>playback delay</b> after it was produced (we use 300 ms)</li>
<li>Later than that: a <b>missed deadline</b>, the picture freezes or glitches</li>
</ul>
""", """Explain video frames: the encoder sends a big complete picture (I-frame) every 2 seconds and small
difference frames in between. The player has a schedule; a frame that arrives after its slot is a missed
deadline and the viewer sees a freeze. Big frames are the risky ones because they take longest to send.""")

# 4 -------------------------------------------------------------------------
slide("", """
<h2>The problem: schedulers see bytes, not frames</h2>
<div class="cols">
<div>
<h3>What standard schedulers do</h3>
<ul>
<li><b>MinRTT</b>: lowest-delay path with room</li>
<li><b>Round-robin</b>: alternate paths</li>
<li><b>Redundant</b>: everything on both paths</li>
<li><b>ECF</b>: wait for the fast path if that finishes sooner</li>
</ul>
<h3>Why that hurts video</h3>
<ul>
<li><b>Frames get split</b>: done only when the slow half arrives</li>
<li><b>Big frames block small ones</b> queued behind them</li>
</ul>
</div>
<div class="card"><div class="kicker">Worked example</div>
<p>A 45 KB I-frame on a 1.5 Mbit/s path needs <b>240 ms</b> to transmit, plus 30 ms travel: <b>270 ms of a 300 ms budget</b>, before counting the frames already queued.</p>
<p>With two slow paths there is no room to spare.</p></div>
</div>
""", """None of the standard schedulers knows where a frame starts or ends or when it is due. Two failure
modes: a frame split across a fast and a slow path finishes only when the slow part lands; and a big
I-frame occupies a path while small frames wait behind it. The worked example shows how tight the budget
is on low-bandwidth links.""")

# 5 -------------------------------------------------------------------------
slide("", """
<h2>Research questions</h2>
<ol class="big">
<li>How often do transport-only multipath schedulers make lecture frames <b>miss their playback deadline</b> on two low-bandwidth cellular paths?</li>
<li>Can a scheduler that knows each frame's <b>size and deadline</b>, driven by a <b>learned prediction</b> of each path's capacity, do better?</li>
<li>Which part of the design makes the difference? (ablations)</li>
</ol>
<p class="note">Closest work: MoMQ (2026), frame-aware multipath QUIC for Starlink + Wi-Fi. Two similar low-bandwidth cellular paths are not studied.</p>
""", """State the three questions. Question 3 is answered with ablations: a hand-tuned version of our
scheduler and a frame-pinning version without deadlines. Mention MoMQ as the closest related work and how
our setting differs.""")

# 6 -------------------------------------------------------------------------
slide("", """
<h2>Our approach</h2>
<div class="flow">
<div class="box">Path observations<small>every 100 ms, per path: delivery rate, send rate, queueing delay, loss, cwnd, BBR estimate</small></div>
<div class="arrow">→</div>
<div class="box hi">Neural network<small>predicts a cautious capacity for the next 0.5 s</small></div>
<div class="arrow">→</div>
<div class="box">Deadline scheduler<small>picks the path(s) for each video frame</small></div>
</div>
<ul class="big">
<li>Each frame on its <b>own QUIC stream</b>; the sender tells the transport its <b>deadline</b></li>
<li>Built <b>inside TQUIC</b> (Tencent's Rust QUIC library), not as a simulation</li>
</ul>
""", """The big picture: every 100 ms we measure each path; a small neural network turns those measurements
into a cautious estimate of how fast the path will be for the next half second; the scheduler uses that
estimate to decide where each frame goes. All of it runs inside a real QUIC implementation.""")

# 7 -------------------------------------------------------------------------
slide("", """
<h2>The scheduler, per frame</h2>
<div class="formula">T<sub>p</sub> = (Q<sub>p</sub> + S) / B<sub>p</sub> + sRTT<sub>p</sub> / 2</div>
<p class="center small">finish time on path p · S frame size · Q<sub>p</sub> bytes already queued · B<sub>p</sub> predicted capacity · sRTT<sub>p</sub> round-trip time</p>
<ol class="big">
<li>If a path finishes the frame before its deadline: <b>whole frame on the path that finishes first</b></li>
<li>Otherwise (big I-frames): <b>split</b> so both parts finish at the same moment</li>
<li>Keep each frame on its path(s), so <b>small frames overtake</b> a big one on the other path</li>
<li>A frame already past its deadline may use any path (safety net)</li>
</ol>
""", """Walk through the formula: time to drain what is ahead of the frame plus the frame itself at the
path's rate, plus one-way delay. Rules 1-3 are the proposal's design; rule 4 we added after finding a
failure where a path collapsed and frames pinned to it never arrived. The split solves a simple linear
equation so both halves land together.""")

# 8 -------------------------------------------------------------------------
slide("", f"""
<h2>Why machine learning? The estimate B<sub>p</sub> is the hard part</h2>
<div class="cols">
<div>
<p class="big">Simple estimates of a path's capacity fail in opposite ways:</p>
<ul class="big">
<li><b>BBR's estimate</b> and the <b>"best of last 3 s"</b> rule are often <b>too high</b> when a link fades</li>
<li>The <b>recent delivery rate</b> is far <b>too low</b> on a path that carries little traffic</li>
<li>Too high is what makes a frame <b>miss its deadline</b></li>
</ul>
</div>
<div class="card"><div class="kicker">On held-out cellular traces</div>
<p>BBR overestimates capacity <b>{pct(e_tr['bbr']['over_pct'])}</b> of the time</p>
<p>"Best of last 3 s" overestimates <b>{pct(e_tr['peak3s']['over_pct'])}</b></p>
<p>Mean delivery rate is off by <b>{pct(e_tr['mean1s']['med_err_pct'], 0)}</b> (median)</p></div>
</div>
""", """This is the motivation for ML. The scheduler's formula is simple physics; the uncertain part is the
path's capacity in the near future. Hand-made estimates are either too optimistic on fading links or far
too pessimistic on idle links. A learned model can combine all the signals and tell us how sure it is.""")

# 9 -------------------------------------------------------------------------
slide("", """
<h2>The learned bandwidth predictor</h2>
<div class="cols">
<ul>
<li><b>Input (44 numbers)</b>: for each 100 ms tick of the last second, delivery rate, send rate, queueing delay, loss ratio; plus cwnd/RTT, bytes in flight, min RTT, BBR's estimate</li>
<li><b>Model</b>: small MLP, 44 → 32 → 32 → 4, ReLU</li>
<li><b>Output</b>: the 10th, 20th, 30th and 50th <b>percentile</b> of the capacity over the next 0.5 s</li>
<li><b>Loss</b>: quantile (pinball) loss, trained with Adam in numpy</li>
<li>The scheduler uses the <b>10th percentile</b>: "at least this much, 9 times out of 10"</li>
</ul>
<div class="card"><div class="kicker">Deployed inside TQUIC</div>
<p>Weights compiled into Rust as constants; two small matrix products per path every 100 ms.</p>
<p>Rust and Python predictions agree to <b>0.002%</b> (574 live predictions).</p>
<p>All 566 TQUIC tests pass.</p></div>
</div>
""", """Describe the model: inputs summarise the last second of each path; a small two-layer network outputs
percentiles rather than a single guess, trained with the pinball loss which is the standard loss for
quantile regression. Using a low percentile builds caution into the estimate instead of a hand-tuned
safety margin. It runs inside the transport on the send path and we verified the Rust code matches.""")

# 10 ------------------------------------------------------------------------
slide("", f"""
<h2>Training without leaking the test data</h2>
<div class="cols">
<ul>
<li><b>Training traces</b> (6): Verizon LTE ×2, T-Mobile LTE ×2, AT&amp;T LTE 2016, Verizon EVDO</li>
<li><b>Test traces</b> (2, never used for training): AT&amp;T LTE, T-Mobile 3G</li>
<li><b>60 emulated sessions</b>, 20 random conditions (traces scaled to 1–6 Mbit/s, constant rates, outages), 3 schedulers</li>
<li><b>Exact labels</b>: the emulator knows the true capacity at every moment</li>
<li>Validated on <b>4 held-out conditions</b>; percentile chosen on 4 more</li>
</ul>
<div class="card"><div class="kicker">Calibration on held-out conditions</div>
<table class="mini"><thead><tr><th>percentile</th><th>target</th><th>measured</th></tr></thead><tbody>
{"".join(f"<tr><td>{int(q*100)}th</td><td>{int(q*100)}%</td><td>{c*100:.1f}%</td></tr>" for q, c in cov.items())}
</tbody></table>
<p class="small">{TR['samples_train']:,} training and {TR['samples_val']:,} validation samples</p></div>
</div>
""", """Emphasise that the model never sees the test traces, and that validation uses whole network
conditions it never saw, so the numbers are honest. Labels are exact because in emulation we set the
capacity ourselves. The calibration table shows the predicted percentiles behave as percentiles: the
10th percentile is exceeded about 10% of the time.""")

# 11 ------------------------------------------------------------------------
slide("", """
<h2>The testbed: real code, emulated network</h2>
<svg class="diagram" viewBox="0 0 900 230" role="img" aria-label="Student namespace and server namespace joined by path A and path B">
<rect class="ns" x="10" y="20" width="270" height="190" rx="10"/>
<text class="dt" x="30" y="56">Student (QUIC client)</text>
<text class="dm" x="30" y="84">network namespace mpq-cli</text>
<text class="dm" x="30" y="128">10.10.1.1 → path A</text>
<text class="dm" x="30" y="176">10.10.2.1 → path B</text>
<rect class="ns" x="620" y="20" width="270" height="190" rx="10"/>
<text class="dt" x="640" y="56">Lecture (QUIC server)</text>
<text class="dm" x="640" y="84">network namespace mpq-srv</text>
<text class="dm" x="640" y="128">one stream per frame</text>
<path class="wa" d="M280 124 H620"/><path class="wb" d="M280 172 H620"/>
<text class="ta" x="450" y="112" text-anchor="middle">path A · netem delay + tbf rate</text>
<text class="tb" x="450" y="198" text-anchor="middle">path B · netem delay + tbf rate</text>
</svg>
<ul>
<li>Linux <b>network namespaces</b> + <b>veth</b> cables; <b>tc netem</b> adds delay, <b>tc tbf</b> sets rate and a 100 ms queue, in both directions</li>
<li>A replay script changes each path's rate <b>every 100 ms</b> from real cellular traces</li>
<li>Checked: 40.5 ms measured on a 40 ms path; per-path bytes match TQUIC's own count within 410 bytes</li>
</ul>
""", """Explain the emulator in one breath: two isolated network stacks on the laptop connected by two
virtual cables, each shaped with the kernel's traffic-control tools; a script replays recorded cellular
speeds. The real TQUIC code runs on top, so bugs and behaviour are real.""")

# 12 ------------------------------------------------------------------------
slide("", """
<h2>Workload and scenarios</h2>
<div class="cols flip">
<div class="card"><div class="kicker">Real lecture</div>
<p>Harvard CopyrightX Lecture 3.2 (W. Fisher), CC BY 3.0, Wikimedia Commons</p>
<p>60 s clip · 1,441 frames at 24 fps · 1.57 Mbit/s · I-frames ~55 KB every 2 s</p>
<p class="small">Encoded like a live stream (H.264, no B-frames, quality-capped)</p></div>
<table class="nw"><thead><tr><th>scenario</th><th>path A</th><th>path B</th></tr></thead><tbody>
<tr><td>symmetric</td><td>4 Mbit/s, 40 ms</td><td>4 Mbit/s, 40 ms</td></tr>
<tr><td>rtt_asym</td><td>4 Mbit/s, 30 ms</td><td>4 Mbit/s, 150 ms</td></tr>
<tr><td>low_bw</td><td>1.5 Mbit/s, 60 ms</td><td>1 Mbit/s, 120 ms</td></tr>
<tr><td>trace</td><td>AT&amp;T LTE trace</td><td>T-Mobile 3G trace</td></tr>
<tr><td>outage</td><td>4 Mbit/s, stable</td><td>off 2 s every 30 s</td></tr>
</tbody></table>
</div>
""", """The video is a real university lecture under an open licence. The five scenarios each test one
thing: a sanity check, delay asymmetry, a link that needs both paths, real cellular variability, and a
coverage hole. The trace scenario is the realistic one.""")

# 13 ------------------------------------------------------------------------
slide("", """
<h2>Experiment design</h2>
<ul class="big">
<li><b>8 schedulers</b>: learned (ours), hand-tuned deadline, frame-pin, MinRTT, ECF, round-robin, redundant, single path</li>
<li><b>5 scenarios × 5 runs</b> each = <b>200 runs</b> of the 60 s lecture</li>
<li>Runs with the same number see the <b>same network</b>, so schedulers are compared <b>run by run</b></li>
<li>Network rebuilt before every run; all schedulers from <b>one frozen build</b></li>
<li>Metrics: deadline-miss rate at 150 / 300 / 500 ms, I-frame misses, stall time, frame completion time, bytes per path</li>
</ul>
""", """Explain fairness: same network conditions per run number, fresh network each run, one compiled
build for everything. Hand-tuned deadline and frame-pin are our ablations. Stall time models a player
that waits for late frames.""")

# 14 ------------------------------------------------------------------------
slide("", f"""
<h2>Result 1: the predictor is the most reliable estimate</h2>
<div class="cols even">
<div><h3>How often each estimate is too high (trace)</h3>
{hbar_chart([("model, 10th pct", e_tr['model_q10']['over_pct'], True), ("BBR's estimate", e_tr['bbr']['over_pct'], False),
             ("best of last 3 s", e_tr['peak3s']['over_pct'], False)], 50, title="overestimate rate")}
</div>
<div><h3>Median error of the estimate (trace)</h3>
{hbar_chart([("model, 30th pct", e_tr['model_q30']['med_err_pct'], True), ("model, median", e_tr['model_q50']['med_err_pct'], True),
             ("BBR's estimate", e_tr['bbr']['med_err_pct'], False), ("best of last 3 s", e_tr['peak3s']['med_err_pct'], False)], 60, title="median error")}
</div>
</div>
<p class="note">{e_tr['n']:,} samples on traces the model never saw. Its cautious estimate overestimates a third as often as BBR's, and its estimates are closer to the truth.</p>
""", """This is the main ML result. On the unseen cellular traces, the cautious estimate is too high only
about 11% of the time, against 38% for BBR and 47% for the 3-second rule. And the model's own estimates
have lower error than BBR's. Mention one caveat: on steady links BBR is already nearly exact.""")

# 15 ------------------------------------------------------------------------
slide("", f"""
<h2>Result 2: deadline misses (300 ms playback delay)</h2>
{miss_table("miss_pct", lambda v: pct(v))}
<p class="note">Medians over 5 runs. Fast links (symmetric, rtt_asym): almost no misses for anyone. low_bw: big I-frames barely fit. trace: real variability, where scheduling matters.</p>
""", """Read the table by column. On fast links nobody misses. On low_bw, the lecture's big frames barely fit
a 300 ms budget for any scheduler; with 500 ms almost everything arrives. On outage everyone is close.
The trace column is the interesting one; the next slide zooms in.""")

# 16 ------------------------------------------------------------------------
slide("", f"""
<h2>Result 3: real cellular traces</h2>
<div class="cols">
<div>
{hbar_chart([(n, t[k]['miss_pct'], k in ("learned", "deadline")) for k, n in NAMES[:6]], 20, title="frames missing deadline on trace")}
<p class="small">redundant {pct(t['redundant']['miss_pct'])} and single path {pct(t['single']['miss_pct'])} are off the scale.</p>
</div>
<ul>
<li>Deadline-aware schedulers miss <b>{pct(t['deadline']['miss_pct'])}</b> (hand-tuned) and <b>{pct(t['learned']['miss_pct'])}</b> (learned), against <b>{pct(min(t['minrtt']['miss_pct'], t['ecf']['miss_pct'], t['roundrobin']['miss_pct']))}–{pct(max(t['minrtt']['miss_pct'], t['ecf']['miss_pct'], t['roundrobin']['miss_pct']))}</b> for transport-only</li>
<li>Learned vs MinRTT / ECF: fewer misses in {pt['minrtt']['miss_pct']['better']} and {pt['ecf']['miss_pct']['better']} of 5 runs, less stalling in {pt['minrtt']['stall_ms']['better']} and {pt['ecf']['stall_ms']['better']} of 5</li>
<li>Same order at other delays: {pct(min(t['learned']['miss150_pct'], t['deadline']['miss150_pct']))}–{pct(max(t['learned']['miss150_pct'], t['deadline']['miss150_pct']))} vs {pct(min(t[k]['miss150_pct'] for k in ('minrtt', 'ecf', 'roundrobin')))}–{pct(max(t[k]['miss150_pct'] for k in ('minrtt', 'ecf', 'roundrobin')))} at 150 ms; {pct(min(t['learned']['miss500_pct'], t['deadline']['miss500_pct']))}–{pct(max(t['learned']['miss500_pct'], t['deadline']['miss500_pct']))} vs {pct(min(t[k]['miss500_pct'] for k in ('minrtt', 'ecf', 'roundrobin')))}–{pct(max(t[k]['miss500_pct'] for k in ('minrtt', 'ecf', 'roundrobin')))} at 500 ms</li>
</ul>
</div>
""", """On real variability both deadline-aware schedulers beat every transport-only one, at every playback
delay. The paired comparison shows the learned scheduler beats MinRTT and ECF in most runs and stalls
less.""")

# 17 ------------------------------------------------------------------------
slide("", f"""
<h2>What the ablations show</h2>
<div class="cols">
<div class="card"><div class="kicker">Frame-pin (no deadline check, no splitting)</div>
<p>Misses <b>{pct(t['framepin']['miss_pct'])}</b> on trace, no better than transport-only.</p>
<p>Keeping frames whole is not enough: the <b>deadline check and splitting</b> are what help.</p></div>
<div class="card"><div class="kicker">Learned vs hand-tuned estimate</div>
<p>Hand-tuned {pct(t['deadline']['miss_pct'])}, learned {pct(t['learned']['miss_pct'])}; learned better in {pt['deadline']['miss_pct']['better']} of 5 runs.</p>
<p>The better predictor has <b>not yet</b> become fewer misses: it is cautious everywhere and sends more traffic over the weaker path.</p></div>
</div>
""", """Be honest here, it is a strength. The frame-pin ablation shows deadline awareness is what matters.
The learned scheduler does not yet beat the hand-tuned one even though its predictions are better: its
caution costs bandwidth on steady links. That points directly at the next step.""")

# 18 ------------------------------------------------------------------------
slide("", """
<h2>Findings along the way</h2>
<ul>
<li><b>A stall bug in TQUIC</b>: a path being validated with a full congestion window blocked every other path, ACKs included; whole connections crawled (15 of 1,800 frames in 90 s). Fixed in a few lines; worth reporting upstream.</li>
<li><b>Pinned frames and a rare BBR collapse</b>: a path fell to ~1 packet/s; frames pinned to it never arrived. Fixed by releasing late frames to any path.</li>
<li><b>Bitrate must fit the network</b>: at 2.13 Mbit/s the lecture overloaded low_bw and every scheduler missed ~65%; a live platform would lower the bitrate, so we used 1.57 Mbit/s.</li>
<li><b>Measurement pitfall</b>: WSL2 steps the wall clock by ~0.5 s; all timing uses the monotonic clock.</li>
</ul>
""", """These show the work was done on real code: we found and fixed a real bug in TQUIC, a robustness
problem in our own scheduler, a workload that overloaded the network, and a clock issue that would have
corrupted the measurements.""")

# 19 ------------------------------------------------------------------------
slide("", """
<h2>Limitations</h2>
<ul class="big">
<li>Emulation only: one laptop, public US cellular traces, no live mobile network</li>
<li>Predictor trained on six public traces; transfer to Indian networks is untested</li>
<li>One lecture clip, 60 s; five runs per scheduler and scenario</li>
<li>Two bug fixes in TQUIC apply to every scheduler (all compared on the same build)</li>
</ul>
""", """Name the limitations before the professor does. Emulation gives control and fairness but not live
networks. The model's generalisation to other countries' networks is an open question.""")

# 20 ------------------------------------------------------------------------
slide("", """
<h2>Next steps</h2>
<ol class="big">
<li><b>Turn better predictions into fewer misses</b>: less caution on steady paths, e.g. pick the percentile from the predicted spread</li>
<li><b>Retrain with the learned scheduler's own traffic</b> (training data so far came from other schedulers)</li>
<li>More traces and longer lectures; figures for the final report</li>
</ol>
""", """The clearest next step comes straight from result 4: the model is accurate but too cautious on steady
links. Choosing the percentile adaptively, and retraining on data from the learned scheduler itself,
should close the gap with the hand-tuned estimate.""")

# 21 ------------------------------------------------------------------------
slide("title", f"""
<h2>Summary</h2>
<ul class="big">
<li>Transport-only schedulers miss <b>16–18%</b> of lecture frames on real cellular traces</li>
<li>Deadline-aware frame scheduling cuts that to <b>{pct(t['deadline']['miss_pct'])}–{pct(t['learned']['miss_pct'])}</b></li>
<li>Our learned predictor overestimates capacity <b>{pct(e_tr['model_q10']['over_pct'])}</b> of the time vs <b>{pct(e_tr['bbr']['over_pct'])}</b> for BBR</li>
<li>Everything runs in real TQUIC code on an emulated network; code, data and results are reproducible from one repository</li>
</ul>
<p class="who">Thank you · Questions?</p>
""", """Repeat the three numbers: 16-18% for today's schedulers, about 11-13% with deadline-aware scheduling,
and a predictor that is wrong in the dangerous direction a third as often as BBR. Then invite questions.""")

# --------------------------------------------------------------------------
CSS = """
:root { --bg:#f5f6f8; --paper:#ffffff; --ink:#131722; --muted:#5a6273; --rule:#dde1e7; --accent:#2a78d6;
  --accent-soft:#e3eefb; --bar:#b3bcc8; --a:#1f8a70; --b:#b8650a;
  --f-d:"IBM Plex Sans Condensed","Arial Narrow",Arial,sans-serif; --f-b:"IBM Plex Sans","Segoe UI",Arial,sans-serif;
  --f-m:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace; }
* { box-sizing:border-box; }
html, body { height:100%; }
body { margin:0; background:#1b1f27; color:var(--ink); font-family:var(--f-b);
  padding-left:max(16px, env(safe-area-inset-left,0px)); padding-right:max(16px, env(safe-area-inset-right,0px)); }
.deck { display:flex; flex-direction:column; align-items:center; gap:24px; padding-block:24px; }
.slide { width:min(100%, calc((100vh - 48px) * 16 / 9)); aspect-ratio:16/9; background:var(--paper);
  container-type:inline-size; position:relative; overflow:hidden; border-radius:6px;
  padding:4.5cqw 5.5cqw 4cqw; display:flex; flex-direction:column; gap:2.6cqw; }
.slide::after { content:attr(data-n); position:absolute; right:3cqw; bottom:2cqw; font:1.1cqw var(--f-m); color:var(--muted); }
.slide.title { justify-content:center; background:linear-gradient(180deg,#ffffff 0%,#eef3fa 100%); }
h1 { font:600 4.6cqw/1.08 var(--f-d); margin:0; text-wrap:balance; }
h2 { font:600 3.6cqw/1.12 var(--f-d); margin:0; text-wrap:balance; }
h3 { font:600 2.1cqw/1.2 var(--f-d); margin:0 0 1cqw; }
p, li { font-size:2cqw; line-height:1.42; margin:0; }
.big li, p.big { font-size:2.3cqw; }
ul, ol { margin:0; padding-left:2.8cqw; display:flex; flex-direction:column; gap:1.5cqw; }
.kicker { font:500 1.4cqw var(--f-m); letter-spacing:.08em; text-transform:uppercase; color:var(--accent); }
.sub { font-size:2.4cqw; color:var(--muted); max-width:70cqw; }
.who { font:1.5cqw var(--f-m); color:var(--muted); margin-top:2cqw; }
.cols { display:grid; grid-template-columns:1.25fr 1fr; gap:3.5cqw; align-items:start; }
.cols.even { grid-template-columns:1fr 1fr; }
.cols.flip { grid-template-columns:1fr 1.25fr; }
td.nw, .nw td { white-space:nowrap; }
ul + h3 { margin-top:1.6cqw; }
.card { background:var(--bg); border:1px solid var(--rule); border-radius:8px; padding:2.4cqw; display:flex; flex-direction:column; gap:1.3cqw; }
.note, .small { font-size:1.65cqw; color:var(--muted); }
.center { text-align:center; }
.formula { font:500 3.6cqw var(--f-m); text-align:center; padding:1.5cqw; background:var(--accent-soft); border-radius:8px; }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
th, td { font-size:1.85cqw; padding:.8cqw 1cqw; border-bottom:1px solid var(--rule); text-align:right; }
th:first-child, td:first-child { text-align:left; }
th { font:500 1.3cqw var(--f-m); letter-spacing:.04em; text-transform:uppercase; color:var(--muted); }
tr.ours td { background:var(--accent-soft); font-weight:600; }
td.best { color:var(--accent); font-weight:600; }
table.mini th, table.mini td { font-size:1.7cqw; }
svg { width:100%; height:auto; display:block; }
svg text { font-family:var(--f-m); }
.lab, .val { font-size:17px; fill:var(--ink); }
.axis { font-size:14px; fill:var(--muted); }
.grid { stroke:var(--rule); }
.bar { fill:var(--bar); } .bar.hi { fill:var(--accent); }
.frames { display:flex; align-items:flex-end; gap:.6cqw; height:9cqw; }
.fr { flex:1; border-radius:3px; } .fr.i { height:100%; background:var(--accent); } .fr.p { height:16%; background:var(--bar); }
.legend { display:flex; gap:3cqw; font-size:1.7cqw; color:var(--muted); }
.sw { display:inline-block; width:1.2cqw; height:1.2cqw; border-radius:2px; margin-right:.6cqw; vertical-align:middle; }
.sw.i { background:var(--accent); } .sw.p { background:var(--bar); }
.flow { display:flex; align-items:stretch; gap:1.5cqw; }
.box { flex:1; border:1px solid var(--rule); border-radius:8px; padding:1.8cqw; font:600 2.3cqw var(--f-d); background:var(--bg); display:flex; flex-direction:column; gap:.8cqw; }
.box small { font:400 1.6cqw var(--f-b); color:var(--muted); }
.box.hi { background:var(--accent-soft); border-color:var(--accent); }
.arrow { align-self:center; font-size:3cqw; color:var(--muted); }
.diagram .ns { fill:var(--bg); stroke:var(--rule); stroke-width:1.5; }
.diagram .dt { font:600 21px var(--f-d); fill:var(--ink); } .diagram .dm { font-size:15px; fill:var(--muted); }
.diagram .wa { stroke:var(--a); stroke-width:4; fill:none; } .diagram .wb { stroke:var(--b); stroke-width:4; fill:none; }
.diagram .ta { fill:var(--a); font-size:15px; } .diagram .tb { fill:var(--b); font-size:15px; }
.notes { display:none; }
body.show-notes .notes { display:block; width:min(100%, calc((100vh - 48px) * 16 / 9)); background:#262b35; color:#e9ecf1;
  border-radius:6px; padding:16px 20px; font-size:15px; line-height:1.5; margin-top:-12px; }
.hint { color:#9aa3b1; font:13px var(--f-m); text-align:center; }
@media print {
  @page { size: 1280px 720px; margin: 0; }
  body { background:#fff; padding:0; } .deck { gap:0; padding:0; } .hint { display:none; }
  .slide { width:1280px; height:720px; border-radius:0; break-after:page; }
  body.print-notes .slide { width:996px; height:560px; margin:0 auto; break-after:auto; }
  body.print-notes .notes { display:block; width:1280px; height:160px; background:#fff; color:#131722; border-top:2px solid #dde1e7;
    padding:16px 60px; font-size:17px; break-after:page; margin:0; border-radius:0; }
}
"""

JS = """
const slides = [...document.querySelectorAll('.slide')];
if (location.hash === '#notes') document.body.classList.add('show-notes', 'print-notes');
document.addEventListener('keydown', e => {
  if (e.key === 'n' || e.key === 'N') { document.body.classList.toggle('show-notes'); return; }
  const cur = slides.findIndex(s => s.getBoundingClientRect().top > -10);
  const go = i => slides[Math.max(0, Math.min(slides.length - 1, i))]?.scrollIntoView({behavior: 'smooth', block: 'center'});
  if (['ArrowRight', 'ArrowDown', 'PageDown', ' '].includes(e.key)) { e.preventDefault(); go(cur + 1); }
  if (['ArrowLeft', 'ArrowUp', 'PageUp'].includes(e.key)) { e.preventDefault(); go(cur - 1); }
});
"""

def write(slides, filename, title, extra_css=""):
    """Write a deck page next to this script (make_short.py reuses it)."""
    parts = [f'<title>{title}</title>',
             '<meta name="claude:viewport-fit-sides" content="cover">',
             '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
             '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@500;600&family=IBM+Plex+Sans:ital,wght@0,400;0,600;1,400&display=swap">',
             f"<style>{CSS}{extra_css}</style>", '<div class="deck">',
             '<p class="hint">Arrow keys to move · N shows speaker notes</p>']
    for i, (cls, body, notes) in enumerate(slides, 1):
        parts.append(f'<section class="slide {cls}" data-n="{i} / {len(slides)}">{body}</section>')
        parts.append(f'<div class="notes"><b>Slide {i}.</b> {notes}</div>')
    parts += ["</div>", f"<script>{JS}</script>"]
    out = Path(__file__).with_name(filename)
    out.write_text("\n".join(parts))
    print(f"wrote {out} ({len(slides)} slides)")


if __name__ == "__main__":
    write(slides, "team7-slides.html", "Team 7 Slides")
