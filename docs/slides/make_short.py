"""Build the short (9-slide) deck from the results files.

  python3 docs/slides/make_short.py

Writes docs/slides/team7-short.html. Same style, data and helpers as the full
deck (make_slides.py): plain wording plus the equations the scheduler and the
predictor actually use (scheduler_deadline.rs, analysis/ml/train.py,
analysis/ml/eval_predictor.py, analysis/frames.py). Details are in the
speaker notes (N).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_slides import E, P, R, hbar_chart, pct, write  # noqa: E402

t, pt, e_tr = R["trace"], P["trace"], E["trace"]
DEADLINE_AWARE = ("learned", "deadline")
TRANSPORT = ("minrtt", "ecf", "roundrobin")


def span(scen, keys, col="miss_pct"):
    vals = [R[scen][k][col] for k in keys]
    return f"{pct(min(vals))}–{pct(max(vals))}"


def frac(num, den):
    return f'<span class="frac"><span>{num}</span><span>{den}</span></span>'


EXTRA_CSS = """
.m { gap:1.7cqw; }
.m p, .m li { font-size:1.75cqw; }
.m ul { gap:.9cqw; }
.oneliner { font-size:2cqw; max-width:80cqw; padding:1.4cqw 1.8cqw; background:var(--paper); border:1px solid var(--rule); border-radius:8px; }
.frames.thin { height:6cqw; }
.cols3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:2cqw; align-items:stretch; }
.caption { font-size:1.55cqw; color:var(--muted); }
.eq { font-family:Cambria,"STIX Two Text","Times New Roman",serif; font-size:2.3cqw;
  background:var(--accent-soft); border-radius:8px; padding:.9cqw 1.4cqw; text-align:center; line-height:1.5; }
.eq.big { font-size:3cqw; padding:1.1cqw; }
.eq.plain { background:none; padding:.2cqw 0; }
.eq i { font-style:italic; }
.frac { display:inline-flex; flex-direction:column; vertical-align:middle; text-align:center; margin:0 .15em; line-height:1.2; }
.frac > span { padding:0 .25em; }
.frac > span + span { border-top:.08em solid currentColor; }
.card .eq { background:var(--paper); font-size:1.9cqw; white-space:nowrap; }
.eq.nw { white-space:nowrap; }
"""

slides = []


def slide(cls, body, notes):
    slides.append((cls, body, notes))


# 1 -------------------------------------------------------------------------
slide("title", f"""
<div class="kicker">ACN project · Team 7</div>
<h1>Frame-aware multipath QUIC<br>with a learned bandwidth predictor</h1>
<p class="sub">Streaming live lectures over two mobile connections without the video freezing</p>
<p class="oneliner">On real cellular networks, standard schedulers deliver <b>{span('trace', TRANSPORT)}</b> of video frames too late.
Ours: <b>{span('trace', DEADLINE_AWARE)}</b>.</p>
<p class="who">Kabir Bajaj and Team 7 · 29 October 2026</p>
""", """A phone with two SIM cards has two internet connections. We send a live lecture over both at once,
and we built a scheduler, helped by a small neural network, that decides which connection each video frame
takes so it arrives in time. The box is the headline result.""")

# 2 -------------------------------------------------------------------------
slide("", """
<h2>The setting</h2>
<ul class="big">
<li>Students watch live lectures on a <b>phone with mobile data</b></li>
<li>Many phones have <b>two SIM cards</b>: two connections, each slow and unsteady (a few Mbit/s, 50–150 ms delay)</li>
<li><b>Multipath QUIC</b> uses both at once; a <b>scheduler</b> picks the path for every packet</li>
</ul>
<div class="frames thin">
""" + "".join(f'<div class="fr {"i" if j % 8 == 0 else "p"}"></div>' for j in range(17)) + """
</div>
<div class="legend"><span><i class="sw i"></i>I-frame: full picture, ~55 KB, every 2 s</span><span><i class="sw p"></i>P-frame: only the changes, ~8 KB, 24 per second</span></div>
<p class="big">Frame <i>i</i>, produced at time <i>s<sub>i</sub></i>, must arrive completely by <i>s<sub>i</sub></i> + <i>D</i>, with playback delay <b><i>D</i> = 300 ms</b>.
If not, it is a <b>deadline miss</b> and the video freezes.</p>
""", """QUIC is the modern transport protocol behind YouTube and Google; multipath QUIC lets one connection
use two networks. The scheduler is the part that decides which network each packet goes on. Video is a
sequence of frames: a big full picture (I-frame) every 2 seconds and small difference frames (P-frames) in
between. The player shows each frame D = 300 ms after it was made; a frame that is late or incomplete causes
a freeze. The big I-frames are the risky ones.""")

# 3 -------------------------------------------------------------------------
slide("m", f"""
<h2>The problem: schedulers see bytes, not frames</h2>
<div class="cols">
<ul>
<li><b>MinRTT</b> (TQUIC's default): next packet on the lowest-delay path with room</li>
<li><b>Round-robin</b>: alternate between paths</li>
<li><b>ECF</b> (earliest completion first): wait for the fast path if that finishes sooner</li>
<li><b>Redundant</b>: every packet on both paths (half the capacity spent on copies)</li>
<li>None knows where a frame starts or ends, or when it is due: a <b>split frame waits for its slower half</b>,
and <b>small frames get stuck behind big ones</b></li>
</ul>
<div class="card"><div class="kicker">Example: one I-frame on a slow path</div>
<div class="eq">{frac('45 KB × 8', '1.5 Mbit/s')} + 30 ms = <b>270 ms</b></div>
<p>That is 270 of the 300 ms budget, before counting frames already queued.</p></div>
</div>
<p class="big"><b>Research questions:</b> (1) how often do standard schedulers miss deadlines? (2) can a scheduler that knows each
frame's size and deadline, with a learned speed prediction, do better? (3) which part of the design matters?</p>
""", """MinRTT sends on the lowest-delay path that has room; round-robin alternates; ECF waits for the fast
path if that would finish sooner; redundant sends everything twice. None of them knows about frames. The
example: 45 kilobytes is 360 kilobits; at 1.5 megabits per second that is 240 ms of sending, plus 30 ms of
travel. On slow links there is no room for a bad choice. Question 3 is answered with ablations: versions of
our scheduler with one part removed.""")

# 4 -------------------------------------------------------------------------
slide("m", f"""
<h2>Our scheduler: predict when each frame will arrive</h2>
<div class="eq big"><i>T<sub>p</sub></i> = {frac('<i>Q<sub>p</sub></i> + <i>S</i>', '<i>B<sub>p</sub></i>')} + <i>d<sub>p</sub></i></div>
<p class="caption" style="text-align:center"><i>T<sub>p</sub></i> arrival time on path <i>p</i> · <i>S</i> frame size ·
<i>Q<sub>p</sub></i> bytes waiting ahead of it · <i>B<sub>p</sub></i> path speed · <i>d<sub>p</sub></i> = sRTT<sub><i>p</i></sub>/2 one-way delay</p>
<div class="cols even">
<div class="card"><div class="kicker">Rule 1: whole frame if a path makes it</div>
<div class="eq"><i>p</i>* = argmin<sub><i>p</i></sub> <i>T<sub>p</sub></i><br>use <i>p</i>* if &nbsp;<i>T<sub>p*</sub></i> ≤ <i>h</i> · (deadline − now)</div>
<p>Then all <i>S</i> bytes go on <i>p</i>*. Safety margin <i>h</i> = 0.85 for the hand-tuned version, <i>h</i> = 1 for the learned one (its prediction is already cautious).</p></div>
<div class="card"><div class="kicker">Rule 2: otherwise split, both parts finish together</div>
<div class="eq"><i>T</i>* = {frac('<i>S</i> + <i>Q<sub>A</sub></i> + <i>Q<sub>B</sub></i> + <i>B<sub>A</sub>d<sub>A</sub></i> + <i>B<sub>B</sub>d<sub>B</sub></i>', '<i>B<sub>A</sub></i> + <i>B<sub>B</sub></i>')}<br><i>s<sub>p</sub></i> = <i>B<sub>p</sub></i>(<i>T</i>* − <i>d<sub>p</sub></i>) − <i>Q<sub>p</sub></i></div>
<p><i>s<sub>A</sub></i> + <i>s<sub>B</sub></i> = <i>S</i>, and both parts arrive at the same time <i>T</i>*.</p></div>
</div>
<p class="caption"><i>Q<sub>p</sub></i> = bytes of earlier frames planned for <i>p</i> + max(0, in-flight<sub><i>p</i></sub> − <i>B<sub>p</sub></i>·minRTT<sub><i>p</i></sub>).
Each path sends only its own frames, so small frames overtake big ones. A frame already past its deadline may use any path.</p>
""", """The formula: to know when a frame arrives on a path, add up everything waiting ahead of it plus the
frame itself, divide by the path's speed, and add the one-way delay. Rule 1: pick the path with the earliest
arrival; if it makes the deadline, the whole frame goes there. The hand-tuned version keeps a 15% safety margin.
Rule 2, mostly for big I-frames: split the frame so both parts land at the same time. Setting the two arrival
times equal and the two parts summing to S gives T-star and each part's size. Q_p also counts data stuck in the
network's queue: bytes in flight beyond what the path holds at its minimum RTT. Everything depends on B_p, the
speed, which is what the neural network predicts.""")

# 5 -------------------------------------------------------------------------
slide("m", f"""
<h2>The ML part: predicting the speed <i>B<sub>p</sub></i></h2>
<div class="cols flip">
<div style="display:flex; flex-direction:column; gap:1.3cqw">
<ul>
<li>If <i>B<sub>p</sub></i> is <b>too high</b>, rule 1 trusts a path that will be late. BBR's own estimate is too high
<b>{pct(e_tr['bbr']['over_pct'])}</b> of the time on our test recordings.</li>
<li><b>Input</b> <i>x</i>: 44 numbers per path from the last second (delivery rate, send rate, queueing delay, loss, cwnd, BBR's estimate, ...)</li>
<li><b>Target</b>: <i>y</i> = ln(1 + <i>C</i>/1000), <i>C</i> = true average speed over the next 0.5 s (bytes/s)</li>
<li><b>Data</b>: 6 recorded cellular networks for training, 2 others for testing</li>
</ul>
<div class="kicker">What the scheduler uses</div>
<div class="eq nw"><i>B<sub>p</sub></i> = 1000 (e<sup><i>ŷ</i><sub>0.1</sub></sup> − 1) &nbsp;⇒&nbsp; P(<i>C</i> &lt; <i>B<sub>p</sub></i>) ≈ 10%</div>
</div>
<div class="card"><div class="kicker">Small neural network (MLP): 44 → 32 → 32 → 4</div>
<div class="eq"><i>h</i><sub>1</sub> = ReLU(<i>W</i><sub>1</sub><i>x</i> + <i>b</i><sub>1</sub>)<br><i>h</i><sub>2</sub> = ReLU(<i>W</i><sub>2</sub><i>h</i><sub>1</sub> + <i>b</i><sub>2</sub>)<br><i>ŷ</i> = <i>W</i><sub>3</sub><i>h</i><sub>2</sub> + <i>b</i><sub>3</sub></div>
<p>Four outputs <i>ŷ<sub>τ</sub></i>: the 10th, 20th, 30th and 50th percentile (<i>τ</i> = 0.1, 0.2, 0.3, 0.5).</p>
<div class="kicker">Pinball loss</div>
<div class="eq"><i>L<sub>τ</sub></i>(<i>y</i>, <i>ŷ</i>) = max(<i>τ</i>(<i>y</i> − <i>ŷ</i>), (<i>τ</i> − 1)(<i>y</i> − <i>ŷ</i>))</div>
<p>For <i>τ</i> = 0.1, too high costs 0.9, too low 0.1: <b>9× penalty for overestimates</b>.</p>
</div>
</div>
""", """Motivation first: the formula is only as good as the speed estimate, and a high estimate is the
dangerous error. The model reads the last second of each path and predicts the speed for the next half second,
in a log scale so that slow and fast links are treated alike. It is a two-layer network; ReLU just sets
negative values to zero. It outputs four percentiles instead of one guess. The pinball loss is the standard loss
for percentiles: for the 10th percentile, an overestimate is penalised nine times more than an underestimate, so
the model learns a value the real speed stays above 90% of the time. That cautious value is B_p. The weights are
compiled into TQUIC's Rust code, and Rust and Python agree to 0.002%.""")

# 6 -------------------------------------------------------------------------
slide("m", """
<h2>How we test: real QUIC code on an emulated network</h2>
<svg class="diagram" viewBox="0 0 900 150" role="img" aria-label="Student and lecture server joined by path A and path B">
<rect class="ns" x="10" y="5" width="270" height="140" rx="10"/>
<text class="dt" x="30" y="42">Student (QUIC client)</text>
<text class="dm" x="30" y="84">10.10.1.1 → path A</text>
<text class="dm" x="30" y="122">10.10.2.1 → path B</text>
<rect class="ns" x="620" y="5" width="270" height="140" rx="10"/>
<text class="dt" x="640" y="42">Lecture (QUIC server)</text>
<text class="dm" x="640" y="84">one stream per frame</text>
<path class="wa" d="M280 80 H620"/><path class="wb" d="M280 118 H620"/>
<text class="ta" x="450" y="68" text-anchor="middle">path A · netem delay + tbf speed</text>
<text class="tb" x="450" y="140" text-anchor="middle">path B · netem delay + tbf speed</text>
</svg>
<div class="cols">
<ul>
<li>One laptop: Linux <b>network namespaces</b> joined by two virtual links (<b>veth</b>)</li>
<li>Linux <b>tc</b>: netem adds delay, tbf sets speed with a 100 ms queue; every 100 ms the speed is replayed from <b>real cellular recordings</b></li>
<li>A real 60 s lecture (1,441 frames, 1.57 Mbit/s); 8 schedulers × 5 scenarios × 5 runs = <b>200 runs</b>, same network for the same run number</li>
</ul>
<table class="mini nw"><thead><tr><th>scenario</th><th>path A</th><th>path B</th></tr></thead><tbody>
<tr><td>symmetric</td><td>4 Mbit/s, 40 ms</td><td>4 Mbit/s, 40 ms</td></tr>
<tr><td>rtt_asym</td><td>4 Mbit/s, 30 ms</td><td>4 Mbit/s, 150 ms</td></tr>
<tr><td>low_bw</td><td>1.5 Mbit/s, 60 ms</td><td>1 Mbit/s, 120 ms</td></tr>
<tr><td><b>trace</b></td><td>AT&amp;T LTE</td><td>T-Mobile 3G</td></tr>
<tr><td>outage</td><td>4 Mbit/s</td><td>off 2 s / 30 s</td></tr>
</tbody></table>
</div>
""", """The emulator is two isolated network stacks on one laptop, connected by two virtual cables, each
shaped with Linux traffic control. A script replays recorded cellular speeds every 100 ms. The real TQUIC code
runs on top, so behaviour and bugs are real. The trace scenario uses the two recordings the model never saw;
it is the realistic one. Runs with the same number see exactly the same network, so schedulers are compared
fairly, run by run.""")

# 7 -------------------------------------------------------------------------
over = [("our model (10th pct)", e_tr["model_q10"]["over_pct"], True),
        ("BBR (built-in)", e_tr["bbr"]["over_pct"], False),
        ("best of last 3 s", e_tr["peak3s"]["over_pct"], False)]
err = [("our model (median)", e_tr["model_q50"]["med_err_pct"], True),
       ("BBR (built-in)", e_tr["bbr"]["med_err_pct"], False),
       ("best of last 3 s", e_tr["peak3s"]["med_err_pct"], False)]
slide("m", f"""
<h2>Result 1: the model predicts speed better</h2>
<div class="cols even">
<div><h3>How often the guess is too high</h3>
<div class="eq plain">P(<i>B̂</i> &gt; <i>C</i>)</div>
{hbar_chart(over, 50, title="overestimate rate")}</div>
<div><h3>Typical error</h3>
<div class="eq plain">median |<i>B̂</i> − <i>C</i>| / <i>C</i></div>
{hbar_chart(err, 60, title="median error")}</div>
</div>
<p class="caption"><i>B̂</i> = estimate, <i>C</i> = true speed over the next 0.5 s, at {e_tr['n']:,} moments on the 2 recordings the model never saw.
Blue = our model. <b>Shorter bars are better.</b></p>
<p class="big">The 10th percentile is too high <b>{pct(e_tr['model_q10']['over_pct'])}</b> of the time, close to the 10% it promises
(<b>calibrated</b>), against <b>{pct(e_tr['bbr']['over_pct'])}</b> for BBR.</p>
""", """Left chart: how often each estimate was above the true speed, the dangerous mistake. Our cautious
estimate is too high about 11% of the time, which is what a 10th percentile should do; BBR is too high 38% of
the time, and the best-of-last-3-seconds rule (what our hand-tuned version starts from) 47%. Right chart: the
typical relative error. Our median prediction is closer to the truth than both rules.""")

# 8 -------------------------------------------------------------------------
slide("m", f"""
<h2>Result 2: fewer late frames on real cellular networks</h2>
<div class="cols even">
<div>
{hbar_chart([(n, t[k]['miss_pct'], k in DEADLINE_AWARE) for k, n in
             [("learned", "ours (learned)"), ("deadline", "ours (hand-tuned)"), ("framepin", "frame-pin"),
              ("minrtt", "MinRTT"), ("ecf", "ECF"), ("roundrobin", "round-robin")]], 20,
            title="frames missing deadline on trace", bh=30, gap=12)}
<p class="caption">Trace scenario, median of 5 runs. Blue = ours. <b>Shorter is better.</b>
Redundant {pct(t['redundant']['miss_pct'])} and single path {pct(t['single']['miss_pct'])} are off the scale.</p>
</div>
<div style="display:flex; flex-direction:column; gap:1.3cqw">
<div class="eq">miss rate = {frac('1', '<i>N</i>')} Σ<sub><i>i</i></sub> 1[<i>c<sub>i</sub></i> &gt; <i>s<sub>i</sub></i> + <i>D</i>]</div>
<p class="caption"><i>c<sub>i</sub></i> = when frame <i>i</i> is complete, <i>N</i> = 1,441 frames, <i>D</i> = 300 ms</p>
<ul>
<li>Late frames: <b>{span('trace', DEADLINE_AWARE)}</b> with ours vs <b>{span('trace', TRANSPORT)}</b> standard</li>
<li>Run by run, learned beats MinRTT and ECF in {pt['minrtt']['miss_pct']['better']} and {pt['ecf']['miss_pct']['better']} of 5 runs; freezes less in {pt['minrtt']['stall_ms']['better']} and {pt['ecf']['stall_ms']['better']} of 5
({t['learned']['stall_ms'] / 1000:.1f} s vs {t['minrtt']['stall_ms'] / 1000:.1f}–{t['ecf']['stall_ms'] / 1000:.1f} s per minute)</li>
<li><b>Ablation</b>: frame-pin keeps frames whole but has no deadline check or split: {pct(t['framepin']['miss_pct'])}, no better than standard</li>
</ul>
</div>
</div>
""", f"""The chart shows, for each scheduler, the share of the lecture's frames that arrived after their
deadline on the real recordings. Both of our versions beat every standard scheduler, and the order is the same
with 150 ms and 500 ms deadlines. Freezing time models a player that waits for late frames: it shows frame i at
max(previous + frame interval, completion time), and every extra wait counts. The frame-pin ablation shows the
deadline check and the split are what help, not just keeping frames whole. On fast links every scheduler is
fine; on the slow constant links ({span('low_bw', ('learned', 'deadline', 'minrtt', 'ecf', 'roundrobin'))}
missed) the big I-frames barely fit a 300 ms budget for anyone.""")

# 9 -------------------------------------------------------------------------
slide("", f"""
<h2>Conclusions</h2>
<div class="cols3">
<div class="card"><div class="kicker">What we found</div>
<ul>
<li>Planning frames against deadlines cuts late frames from <b>{span('trace', TRANSPORT)}</b> to <b>{span('trace', DEADLINE_AWARE)}</b></li>
<li>Our ML model is the <b>most accurate</b> speed estimate</li>
<li>But it is too cautious: the hand-tuned version is still a bit better ({pct(t['deadline']['miss_pct'])} vs {pct(t['learned']['miss_pct'])}; learned better in {pt['deadline']['miss_pct']['better']} of 5 runs)</li>
</ul></div>
<div class="card"><div class="kicker">Limitations</div>
<ul>
<li>Emulated network, not a live one</li>
<li>Recordings from US networks</li>
<li>One 60 s lecture, 5 runs each</li>
</ul></div>
<div class="card"><div class="kicker">Next steps</div>
<ul>
<li>Choose <i>τ</i> from the predicted spread: <b>less caution</b> on steady links</li>
<li>Retrain on the learned scheduler's own traffic</li>
<li>More recordings, longer lectures</li>
</ul></div>
</div>
<p class="who">Thank you · Questions?</p>
""", """Say the honest result clearly: deadline-aware scheduling works, and the model predicts speed better
than anything else, but it does not yet beat the hand-tuned version because the 10th percentile is cautious
even when the link is steady. Choosing the percentile from how spread out the four predictions are is the first
next step. Along the way we also found and fixed a stall bug in TQUIC itself. Then invite questions.""")

if __name__ == "__main__":
    write(slides, "team7-short.html", "Team 7 Short Slides", EXTRA_CSS)
