"""Render the public site from the published data repository.

The layout rule: white ground, thin type, almost no borders — contrast carried
by weight and colour rather than by boxes. So there are no cards. Every block is
a two-column section with a heading rail on the left and one hairline above it,
and the page is held together by spacing.

Output is static, generated in the same run that writes the data, so the page
and the record it renders cannot drift apart.

The hard rule, and it is the important one:

    NO METRIC IS COMPUTED IN THIS FILE.

Sharpe, drawdown, monthly returns and the rest arrive already calculated by
bese.metrics. The single exception is rebasing NAV onto its own first point for
the chart axis, which is the definition of the axis rather than a statistic. If
you find yourself about to write `math.sqrt(252)` here, stop.

It follows that None is never zero: a withheld value renders as absence, and a
gated one says why.
"""

from __future__ import annotations

#: Where the record lives, and who answers for it. Named on the page because a
#: verification document that cannot be argued with is not doing its job: the
#: reader needs a route to say "this number is wrong" that does not depend on
#: the operator choosing to listen.
REPO_SLUG = "bese-am/track-record"
REPO_URL = "https://github.com/bese-am/track-record"
OWNER = "kkacajj"
OWNER_URL = "https://github.com/kkacajj"

import csv
import html
import math
import json
import shutil
from datetime import datetime
from pathlib import Path

MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]

W = 900
PAD_L, PAD_R, PAD_T, PAD_B = 58, 6, 10, 26

#: An SVG scales as one piece: shrink it to a phone and the type shrinks with
#: it. A 900-unit chart rendered into 354 CSS pixels puts 10.5px labels on
#: screen at 4px, which is not small type, it is no type. So every chart is
#: emitted twice -- once at 900 units for wide screens, once at 430 for narrow
#: -- and CSS shows whichever fits. The alternative, one drawing that stretches,
#: cannot work: nothing in CSS reaches inside a viewBox to resize the text.
W_NARROW = 430
PAD_L_NARROW = 46
PAD_R_NARROW = 22


def label_every(n, usable, label_px=44):
    """How many x labels to skip so they do not collide.

    Matters beyond phones. At 60 sessions even a wide chart cannot fit 60 dates
    across, so this thins them everywhere rather than letting the axis turn to
    mush as the record grows. With today's handful of sessions it returns 1 and
    changes nothing.
    """
    if n <= 1:
        return 1
    per = usable / n
    return max(1, math.ceil(label_px / per)) if per > 0 else 1


def dual(fn, *args, cid=None):
    """Render a chart at both widths; CSS picks one."""
    wide, narrow = {}, {"w": W_NARROW, "pad_l": PAD_L_NARROW,
                       "pad_r": PAD_R_NARROW}
    if cid is not None:
        wide["cid"] = cid
        narrow["cid"] = cid + "-m"      # ids must stay unique across both
    return (f'<div class="c-wide">{fn(*args, **wide)}</div>'
            f'<div class="c-narrow">{fn(*args, **narrow)}</div>')


# ------------------------------------------------------------- formatting ---

def esc(v) -> str:
    """Escape anything that came from a data file before it enters the page.

    Every string here originates in a broker or firm export -- symbol, trade
    id, flags. None of it is written by us, so none of it is trusted markup.
    """
    return html.escape("" if v is None else str(v), quote=True)


def money(v, dp=2):
    return "—" if v is None else f"${v:,.{dp}f}"


def pct(v, dp=2):
    return "—" if v is None else f"{v * 100:.{dp}f}%"


def spct(v, dp=2):
    if v is None:
        return "—"
    return f"{'+' if v > 0 else ''}{v * 100:.{dp}f}%"


def ratio(v, dp=2):
    return "—" if v is None else f"{v:.{dp}f}"


def cls(v):
    if v is None or v == 0:
        return ""
    return "up" if v > 0 else "down"


def scale(v, lo, hi, a, b):
    return (a + b) / 2 if hi == lo else a + (v - lo) * (b - a) / (hi - lo)


def day(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d %b")


def rebase(nav):
    """equity / equity[0] - 1. A rebase, not a metric: it is the definition of
    the axis the chart draws, and it reconciles exactly with the published
    cumulative return."""
    base = nav[0]["equity"]
    return [p["equity"] / base - 1 for p in nav]


# ----------------------------------------------------------------- charts ---

def cumulative_chart(nav, cid="nav", w=W, pad_l=PAD_L, pad_r=PAD_R):
    """Cumulative return. The axis is percent, not currency.

    A dollar axis on a nominal base invites the reader to think the number is a
    balance somewhere, which is the single most likely way this record gets
    misread. Percent asks the question the record actually answers.
    """
    h = 300
    ys = rebase(nav)
    lo, hi = min([*ys, 0.0]), max([*ys, 0.0])
    pad = (hi - lo) * 0.30 or 0.001
    lo, hi = lo - pad, hi + pad
    n = len(nav)

    def px(i):
        return scale(i, 0, max(n - 1, 1), pad_l, w - pad_r)

    def py(v):
        return scale(v, lo, hi, h - PAD_B, PAD_T)

    every = label_every(n, w - pad_l - PAD_R)
    tvals = [lo + (hi - lo) * k / 4 for k in range(5)]
    # The zero line is the reference the whole chart is read against, so it gets
    # a label rather than being an unexplained rule near an arbitrary tick.
    tvals[min(range(5), key=lambda k: abs(tvals[k]))] = 0.0
    ticks = "".join(
        f'<text class="ax" x="{pad_l-12}" y="{py(t)+3.5:.1f}" text-anchor="end">'
        f'{t*100:.2f}%</text>' for t in tvals)
    zero = py(0.0)
    ticks += (f'<line class="zero" x1="{pad_l}" x2="{w-pad_r}" '
              f'y1="{zero:.1f}" y2="{zero:.1f}"/>')

    pts = " L".join(f"{px(i):.1f} {py(v):.1f}" for i, v in enumerate(ys))
    area = f"M{px(0):.1f} {zero:.1f} L{pts} L{px(n-1):.1f} {zero:.1f} Z"
    dots = "".join(f'<circle class="mark" cx="{px(i):.1f}" cy="{py(v):.1f}" r="2.6"/>'
                   for i, v in enumerate(ys))
    labels = "".join(
        f'<text class="ax" x="{px(i):.1f}" y="{h-PAD_B+17}" text-anchor="middle">'
        f'{day(p["date"])}</text>' for i, p in enumerate(nav)
        if i % every == 0 or i == n - 1)
    hits = "".join(
        f'<rect class="hit" x="{px(i)-18:.1f}" y="{PAD_T}" width="36" '
        f'height="{h-PAD_T-PAD_B}" data-c="{cid}" data-x="{px(i):.1f}" '
        f'data-y="{py(v):.1f}" '
        f'data-title="{datetime.fromisoformat(p["date"]).strftime("%d %b %Y")}" '
        f'data-body="Cumulative {spct(v, 3)}'
        + (f' &#183; session {spct(p["ret"], 3)}' if p["ret"] is not None
           else " &#183; inception") + '"/>'
        for i, (p, v) in enumerate(zip(nav, ys, strict=True)))

    return f'''<svg viewBox="0 0 {w} {h}" class="chart" id="{cid}">
<defs><linearGradient id="g{cid}" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="var(--accent)" stop-opacity=".16"/>
<stop offset="100%" stop-color="var(--accent)" stop-opacity=".01"/></linearGradient></defs>
{ticks}<path d="{area}" fill="url(#g{cid})"/><path class="line" d="M{pts}"/>{dots}{labels}
<line class="cross" id="c-{cid}" y1="{PAD_T}" y2="{h-PAD_B}" style="display:none"/>
<circle class="focus" id="f-{cid}" r="4" style="display:none"/>{hits}</svg>'''


def returns_chart(daily, w=W, pad_l=PAD_L, pad_r=PAD_R):
    """Session returns.

    Colour carries the sign, and so does the signed label above every bar.
    Measured, a conventional green/red pair separates by only dE 5.5 under deuteranopia,
    so colour is never the only channel here.
    """
    vals = [d["return"] for d in daily if d["return"] is not None]
    if not vals:
        return '<p class="empty">No sessions yet.</p>'
    h, pt, pb = 190, 14, 26
    m = max(abs(min(vals)), abs(max(vals))) * 1.35 or 1
    zero = scale(0, -m, m, h - pb, pt)
    n = len(daily)
    bw = min(30, (w - pad_l - PAD_R) / max(n, 1) * 0.5)

    def px(i):
        return scale(i, 0, max(n - 1, 1), pad_l + 18, w - pad_r - 18)

    every = label_every(n, w - pad_l - PAD_R, 52)
    out = (f'<line class="zero" x1="{pad_l}" x2="{w-pad_r}" y1="{zero:.1f}" '
           f'y2="{zero:.1f}"/>')
    for i, d in enumerate(daily):
        v = d["return"]
        if v is None:
            continue
        y = scale(v, -m, m, h - pb, pt)
        top, hh = min(y, zero), abs(y - zero)
        c = "up" if v >= 0 else "down"
        out += (f'<rect class="bar {c}" x="{px(i)-bw/2:.1f}" y="{top:.1f}" '
                f'width="{bw:.1f}" height="{max(hh,1.2):.1f}"/>'
                f'<text class="val {c}" x="{px(i):.1f}" '
                f'y="{(top-6) if v>=0 else (top+hh+13):.1f}" text-anchor="middle">'
                f'{v*100:+.2f}%</text>')
        if i % every == 0 or i == n - 1:
            out += (f'<text class="ax" x="{px(i):.1f}" y="{h-pb+17}" '
                    f'text-anchor="middle">{day(d["date"])}</text>')
    return f'<svg viewBox="0 0 {w} {h}" class="chart">{out}</svg>'


def drawdown_chart(dd, w=W, pad_l=PAD_L, pad_r=PAD_R):
    h, pt, pb = 180, 12, 26
    vals = [d["drawdown"] for d in dd]
    lo = min(min(vals) * 1.45, -0.0005)
    n = len(dd)

    every = label_every(n, w - pad_l - PAD_R)

    def px(i):
        return scale(i, 0, max(n - 1, 1), pad_l, w - pad_r)

    def py(v):
        return scale(v, lo, 0, h - pb, pt)

    ticks = "".join(
        f'<text class="ax" x="{pad_l-12}" y="{py(t)+3.5:.1f}" text-anchor="end">'
        f'{t*100:.2f}%</text>' for t in [lo * k / 3 for k in range(4)])
    pts = " L".join(f"{px(i):.1f} {py(v):.1f}" for i, v in enumerate(vals))
    area = f"M{px(0):.1f} {py(0):.1f} L{pts} L{px(n-1):.1f} {py(0):.1f} Z"
    trough = min(range(n), key=lambda i: vals[i])
    lbl = (f'<text class="val down" x="{px(trough):.1f}" '
           f'y="{py(vals[trough])+15:.1f}" text-anchor="middle">'
           f'{vals[trough]*100:.2f}%</text>')
    labels = "".join(
        f'<text class="ax" x="{px(i):.1f}" y="{h-pb+17}" text-anchor="middle">'
        f'{day(d["date"])}</text>' for i, d in enumerate(dd)
        if i % every == 0 or i == n - 1)
    return (f'<svg viewBox="0 0 {w} {h}" class="chart">'
            f'<line class="zero" x1="{pad_l}" x2="{w-pad_r}" y1="{py(0):.1f}" '
            f'y2="{py(0):.1f}"/>{ticks}'
            f'<path d="{area}" class="dd-fill"/><path class="dd-line" d="M{pts}"/>'
            f'{lbl}{labels}</svg>')


def distribution_chart(bins, w=W, pad_l=PAD_L, pad_r=PAD_R):
    if not bins:
        return '<p class="empty">Not enough sessions to bin.</p>'
    h, pt, pb = 160, 14, 30
    top = max(b["count"] for b in bins) or 1
    n = len(bins)
    bw = (w - pad_l - PAD_R) / n * 0.72
    every = label_every(n, w - pad_l - PAD_R, 46)
    out = ""
    for i, b in enumerate(bins):
        x = scale(i, 0, max(n - 1, 1), pad_l + bw / 2, w - pad_r - bw / 2)
        hh = (b["count"] / top) * (h - pt - pb)
        mid = (b["from"] + b["to"]) / 2
        out += (f'<rect class="bar {"up" if mid >= 0 else "down"}" '
                f'x="{x-bw/2:.1f}" y="{h-pb-hh:.1f}" width="{bw:.1f}" '
                f'height="{max(hh,1):.1f}"/>'
                f'<text class="val" x="{x:.1f}" y="{h-pb-hh-6:.1f}" '
                f'text-anchor="middle">{b["count"]}</text>')
        if i % every == 0 or i == n - 1:
            out += (f'<text class="ax" x="{x:.1f}" y="{h-pb+17}" '
                    f'text-anchor="middle">{mid*100:+.2f}%</text>')
    return (f'<svg viewBox="0 0 {w} {h}" class="chart">'
            f'<line class="zero" x1="{pad_l}" x2="{w-pad_r}" y1="{h-pb}" '
            f'y2="{h-pb}"/>{out}</svg>')


# ------------------------------------------------------------------ shell ---

#: The palette. One comment states the rule the whole look
#: depends on: white ground, thin type, almost no borders; contrast carried by
#: weight and colour, not by boxes. Green and red mean exactly one thing -- the
#: sign of a return -- and nothing decorative is allowed to use them, or they
#: stop reading as data. The portfolio line is the one accent colour.
CSS = """
:root{color-scheme:light dark;
--bg:#ffffff;--bg-subtle:#fafafa;--fg:#111318;--fg-muted:#5b6270;--fg-faint:#8b93a1;
--hairline:#ececf0;--up:#10814a;--down:#c0392f;--accent:#1b3a6b;--bench:#9aa3b2;
--warn-bg:#f6f6f7;--warn-fg:#3f4450}
@media(prefers-color-scheme:dark){:root{
--bg:#0d0f12;--bg-subtle:#121519;--fg:#eef1f5;--fg-muted:#a2abba;--fg-faint:#6f7889;
--hairline:#23272e;--up:#3ddc97;--down:#ff7a6e;--accent:#9dc0f5;--bench:#616b7c;
--warn-bg:#16181c;--warn-fg:#c7cdd8}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;
-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
a{color:inherit;text-decoration:none;border-bottom:1px solid var(--hairline)}
a:hover{border-bottom-color:var(--fg-muted)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px}

.top{border-bottom:1px solid var(--hairline);background:var(--bg);
position:sticky;top:0;z-index:5}
.top .in{max-width:1000px;margin:0 auto;padding:0 28px;display:flex;
align-items:center;gap:28px;height:58px}
.brand{font-size:14.5px;font-weight:600;letter-spacing:-.01em;border:0}
.brand span{color:var(--fg-faint);font-weight:400}
.top nav{margin-left:auto;display:flex;gap:24px}
.top nav a{font-size:12.5px;color:var(--fg-muted);border:0;padding:3px 0;
border-bottom:1.5px solid transparent}
.top nav a:hover{color:var(--fg)}
.top nav a.on{color:var(--fg);border-bottom-color:var(--fg)}

.wrap{max-width:1000px;margin:0 auto;padding:44px 28px 80px}
.eyebrow{font-size:11px;letter-spacing:.09em;text-transform:uppercase;
color:var(--fg-faint);margin:0}
h1{font-size:29px;font-weight:600;letter-spacing:-.024em;line-height:1.12;
margin:.4rem 0 .35rem}
.lede{color:var(--fg-muted);font-size:14px;margin:0;max-width:62ch}

/* Two-column section: a heading rail, then the content. One hairline above and
   no box around it. Every block aligns to the same two vertical rules. */
section.blk{display:grid;grid-template-columns:180px 1fr;gap:0 40px;
margin-top:44px;padding-top:26px;border-top:1px solid var(--hairline)}
section.blk.first{border-top:0;margin-top:34px;padding-top:0}
section.blk>.rail h2{font-size:13.5px;font-weight:600;letter-spacing:-.01em;margin:0}
section.blk>.rail .note{margin-top:9px;font-size:11.5px;color:var(--fg-muted);
line-height:1.6}
section.blk>.body{min-width:0}
@media(max-width:800px){section.blk{grid-template-columns:1fr;gap:14px}}

/* KPI: label, big number, note. No border, no ground. */
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:30px 26px}
@media(max-width:800px){.kpis{grid-template-columns:repeat(2,1fr)}}
.kpi .k{font-size:11px;color:var(--fg-faint)}
.kpi .v{margin-top:7px;font-size:27px;font-weight:600;letter-spacing:-.025em;
line-height:1;font-variant-numeric:tabular-nums}
.kpi .n{margin-top:7px;font-size:12px;color:var(--fg-faint)}

.chart{width:100%;height:auto;display:block;overflow:visible}
.c-narrow{display:none}
.ax{fill:var(--fg-faint);font-size:10.5px;font-variant-numeric:tabular-nums}
.zero{stroke:var(--hairline);stroke-width:1}
.line{fill:none;stroke:var(--accent);stroke-width:1.6;stroke-linejoin:round;
stroke-linecap:round}
.mark{fill:var(--accent)}
.focus{fill:var(--accent)}
.cross{stroke:var(--hairline);stroke-width:1}
.hit{fill:transparent;cursor:crosshair}
.bar.up{fill:var(--up)}.bar.down{fill:var(--down)}
.val{font-size:10.5px;font-weight:500;font-variant-numeric:tabular-nums;
fill:var(--fg-faint)}
.val.up{fill:var(--up)}.val.down{fill:var(--down)}
.dd-fill{fill:var(--down);fill-opacity:.10}
.dd-line{fill:none;stroke:var(--down);stroke-width:1.4;stroke-linejoin:round}

.ledger{display:grid;grid-template-columns:repeat(2,1fr);gap:2px 44px}
@media(max-width:800px){.ledger{grid-template-columns:1fr}}
.ledger h3{font-size:10px;letter-spacing:.07em;text-transform:uppercase;
color:var(--fg-faint);font-weight:600;margin:18px 0 4px}
.row{display:flex;align-items:baseline;gap:14px;padding:7px 0;
border-bottom:1px solid var(--hairline)}
.row dt{font-size:12.5px;color:var(--fg-muted)}
.row dt small{color:var(--fg-faint);font-size:11px}
.row dd{margin:0 0 0 auto;text-align:right;white-space:nowrap;
font-size:13px;font-variant-numeric:tabular-nums}
.held-inline{font-size:11.5px;color:var(--fg-faint)}
.up{color:var(--up)}.down{color:var(--down)}
svg .up,svg .down{color:inherit}

table{width:100%;border-collapse:collapse;font-size:12px}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch}
.tw table{min-width:max-content}
th{text-align:left;font-weight:600;font-size:10px;letter-spacing:.07em;
text-transform:uppercase;color:var(--fg-faint);padding:0 10px 7px 0;
border-bottom:1px solid var(--hairline);white-space:nowrap}
td{padding:7px 10px 7px 0;border-bottom:1px solid var(--hairline);
vertical-align:baseline}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
td.dim{color:var(--fg-faint)}td.strong{font-weight:600}
tr.merged td{background:var(--bg-subtle)}
.tag{font-size:10px;color:var(--fg-faint);white-space:nowrap;margin-right:6px}
/* A withheld statistic is not a warning, it is an absence: set apart by ground
   and weight, never by hue. */
.notice{background:var(--warn-bg);color:var(--warn-fg);padding:11px 14px;
font-size:12.5px;line-height:1.6;margin:18px 0 0}
.prose p{color:var(--fg-muted);font-size:13px;max-width:72ch;margin:0 0 11px}
.prose p:last-child{margin-bottom:0}
.prose b,.prose strong{color:var(--fg)}
/* The only outbound links on the site. Given a hairline underline like every
   other link, plus a little more air, so they read as actions rather than as
   running text. */
.prose a[href^="http"]{color:var(--accent);
  border-bottom-color:color-mix(in srgb,var(--accent) 35%,transparent)}
.prose p.links{display:flex;flex-wrap:wrap;gap:18px;margin-top:16px}
.prose p.links a{font-size:12.5px;letter-spacing:.01em}
/* ---------------------------------------------------------------- phones ---
   Below 640px the two-column section grid has already collapsed; what is left
   is the header, which ran off the side, and the charts, whose type was being
   scaled into illegibility. */
@media(max-width:640px){
  .wrap{padding:26px 18px 56px}
  /* Brand on its own line, navigation on the next, scrolled sideways if it
     does not fit. Wrapping it to two rows instead would push the content of
     every page down by another line on the smallest screens. */
  .top .in{flex-direction:column;align-items:stretch;gap:0;height:auto;
    padding:10px 0 0}
  .brand{padding:0 18px 8px}
  .top nav{margin-left:0;flex-wrap:wrap;gap:6px 18px;padding:0 18px 9px}
  .top nav a{white-space:nowrap;font-size:12px}
  .c-wide{display:none}
  .c-narrow{display:block}
  /* The pointer tooltip is a hover affordance. On a touch screen a tap fires
     it, then nothing dismisses it, and it covers the very point it describes.
     The value it carries is already in the tables below. */
  .hit{display:none}
  .tip{display:none}
  .kpi .v{font-size:23px}
  h1{font-size:29px}
  pre{font-size:11px;padding:11px 12px}
  section.blk{padding-top:22px;margin-top:22px}
}
@media(max-width:380px){
  .kpis{grid-template-columns:1fr}
}
pre{background:var(--bg-subtle);padding:13px 15px;overflow-x:auto;
font-size:11px;line-height:1.55;
font-family:ui-monospace,SFMono-Regular,Menlo,monospace;margin:0 0 11px}
.empty{color:var(--fg-faint);font-size:12.5px;padding:16px 0}
.foot{border-top:1px solid var(--hairline);margin-top:52px;padding:22px 0 0;
font-size:11px;color:var(--fg-faint);max-width:82ch;line-height:1.65}
.foot p{margin:0 0 8px}
.tip{position:fixed;pointer-events:none;opacity:0;transition:opacity .1s;
background:var(--bg);border:1px solid var(--hairline);padding:8px 11px;
font-size:12px;z-index:9}
.tip b{display:block;font-size:10.5px;color:var(--fg-faint);font-weight:400;
margin-bottom:3px}
"""

JS = """
const tip=document.getElementById('tip');
document.querySelectorAll('.hit').forEach(el=>{
 el.addEventListener('mouseenter',()=>{
  const c=el.dataset.c,x=+el.dataset.x,y=+el.dataset.y;
  const cr=document.getElementById('c-'+c),fo=document.getElementById('f-'+c);
  if(cr){cr.setAttribute('x1',x);cr.setAttribute('x2',x);cr.style.display='';}
  if(fo){fo.setAttribute('cx',x);fo.setAttribute('cy',y);fo.style.display='';}
  tip.replaceChildren(Object.assign(document.createElement('b'),
  {textContent:el.dataset.title}),document.createTextNode(el.dataset.body));tip.style.opacity='1';});
 el.addEventListener('mousemove',e=>{
  const tw=tip.offsetWidth||230;
  tip.style.left=Math.max(8,Math.min(e.clientX+14,innerWidth-tw-8))+'px';
  tip.style.top=(e.clientY-12)+'px';});
 el.addEventListener('mouseleave',()=>{tip.style.opacity='0';
  document.querySelectorAll('.cross,.focus').forEach(n=>n.style.display='none');});
});
"""

PAGES = [("index.html", "Track record"), ("portfolio.html", "Portfolio"),
         ("verify.html", "Verify"), ("methodology.html", "Methodology"),
         ("disclosures.html", "Disclosures")]


def sec(title, body, note="", first=False):
    n = f'<div class="note">{note}</div>' if note else ""
    return (f'<section class="blk{" first" if first else ""}">'
            f'<div class="rail"><h2>{title}</h2>{n}</div>'
            f'<div class="body">{body}</div></section>')


def kpi(label, value, note=""):
    n = f'<div class="n">{note}</div>' if note else ""
    return (f'<div class="kpi"><div class="k">{label}</div>'
            f'<div class="v">{value}</div>{n}</div>')


def scrollable_tables(body: str) -> str:
    """Wrap every table so it scrolls inside its own box.

    Done centrally rather than at each call site: a table added later would
    otherwise silently reintroduce the bug, and the failure is invisible on a
    desktop where the author is looking.
    """
    return body.replace("<table", '<div class="tw"><table', -1) \
               .replace("</table>", "</table></div>", -1)


def shell(title: str, active: str, body: str, published: str) -> str:
    nav = "".join(f'<a href="{h}"{" class=on" if h == active else ""}>{t}</a>'
                  for h, t in PAGES)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · Besë Asset Management</title>
<link rel="stylesheet" href="style.css"></head><body>
<header class="top"><div class="in">
<a class="brand" href="index.html">Besë <span>Asset Management</span></a>
<nav>{nav}</nav></div></header>
<div class="wrap">{scrollable_tables(body)}
<div class="foot">
<p><b>Nominal capital, not assets under management.</b> $100,000 is a stated
normalisation base. It is not client money, and no prop firm's advertised
account size is treated as capital under management.</p>
<p>Past performance is not indicative of future results. Nothing here is
investment advice, an offer, or a solicitation. Futures trading carries
substantial risk of loss. See <a href="disclosures.html">disclosures</a>.</p>
<p>Published {published} · every figure computed by
<span class="mono">bese.metrics</span>, not by the browser.</p>
</div></div><div class="tip" id="tip"></div><script src="app.js"></script></body></html>"""


# ------------------------------------------------------------------- data ---

def read_nav(book_dir: Path):
    out = []
    with open(book_dir / "nav.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.append({"date": r["date"], "equity": float(r["equity"]),
                        "pnl": float(r["pnl"]) if r["pnl"] else None,
                        "ret": float(r["daily_return"]) if r["daily_return"] else None,
                        "trades": int(r["trades"])})
    return out


def read_trades(book_dir: Path):
    with open(book_dir / "trades.csv", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ------------------------------------------------------------------ pages ---

def landing(idx, meta, metrics, nav):
    v = metrics["values"]
    kpis = ('<div class="kpis">'
            + kpi("Cumulative return", spct(v["cumulative_return"], 3),
                  f'{day(meta["inception"])} &#8594; '
                  f'{datetime.fromisoformat(meta["last_session"]).strftime("%d %b %Y")}')
            + kpi("Nominal NAV", money(nav[-1]["equity"]),
                  f'from {money(meta["nominal_capital"], 0)}')
            + kpi("Sessions", esc(meta["sessions"]),
                  f'{esc(meta["trades"])} strategy trades')
            + kpi("Chained records", esc(idx["chain"]["entries"]),
                  "each hashed to the one before")
            + "</div>")

    chart = (dual(cumulative_chart, nav, cid="nav")
             + '<p class="notice">Cumulative return since inception. Exposure is '
               'held at 1 NQ-equivalent regardless of NAV, so the strategy is '
               'constant-notional while the return series compounds exactly. '
               '<a href="portfolio.html">Full statistics and trade ledger &#8594;</a>'
               '</p>')

    about = '''<div class="prose">
<p><b>It is</b> a record of a strategy: every completed trade, taken from the
broker's and the firm's own records including the commission actually charged,
scaled to one unit of exposure so that sessions are comparable with each other.</p>
<p><b>It is not</b> a record of managed money. Trading is done through a
proprietary-trading firm's funded-account programme. A prop firm's advertised
account size is a risk limit, not capital under management, and this record does
not present it as one.</p>
<p><b>The NAV is constructed, not quoted.</b> It is computed from fills by
published code rather than read from an account balance. That is a weaker claim
than quoting a broker's equity endpoint, and it is why the
<a href="verify.html">verification</a> is built the way it is.</p></div>'''

    return f"""
<p class="eyebrow">Live track record</p>
<h1>A futures strategy, measured honestly.</h1>
<p class="lede">Every trade Besë takes on the Nasdaq-100 futures, normalised to a
constant 1 NQ-equivalent of exposure and applied to a $100,000 nominal base. The
inputs are archived, the arithmetic is open source, and every session is chained
the day it happens.</p>
{sec("Position", kpis, first=True)}
{sec(esc(meta["label"]), chart, note=esc(meta["tagline_en"]) + ".")}
{sec("What this is", about, note="And what it is not.")}"""


def portfolio(meta, metrics, analytics, nav, trades):
    v = metrics["values"]
    gate = metrics.get("insufficient_history")
    held = f"withheld &#183; {esc(gate['have'])}/{esc(gate['need'])}" if gate else None

    def row(label, value, raw=None, note=None, gated=False):
        cell = (f'<span class="held-inline">{held}</span>' if (gated and held)
                else f'<span class="{cls(raw)}">{value}</span>')
        n = f' <small>&#183; {note}</small>' if note else ""
        return f'<div class="row"><dt>{label}{n}</dt><dd>{cell}</dd></div>'

    blocks = [
        ("Position", [
            row("Net asset value", money(nav[-1]["equity"]), note="nominal basis"),
            row("Cumulative return", spct(v["cumulative_return"], 3),
                v["cumulative_return"]),
            row("Sessions published", esc(meta["sessions"])),
            row("Observations", esc(v["n_obs"])),
        ]),
        ("Return", [
            row("Annualised return (CAGR)", pct(v["cagr"]), gated=v["cagr"] is None),
            row("Expected excess return", pct(v["ev_excess_annual"]),
                gated=v["ev_excess_annual"] is None),
            row("Best session", spct(v["best_day"], 3), v["best_day"]),
            row("Worst session", spct(v["worst_day"], 3), v["worst_day"]),
            row("Winning sessions",
                f'{esc(v["positive_days"])} of '
                f'{esc(v["positive_days"] + v["negative_days"])}'),
            row("Win rate", pct(v["win_rate"]), gated=v["win_rate"] is None),
        ]),
        ("Risk", [
            row("Volatility, annualised", pct(v["volatility"]),
                gated=v["volatility"] is None),
            row("Maximum drawdown", pct(v["max_drawdown"]), v["max_drawdown"],
                gated=v["max_drawdown"] is None),
            row("Value at risk, 95% daily", pct(v["var_normal_95"]),
                gated=v["var_normal_95"] is None),
            row("Skew", ratio(v["skew"]), gated=v["skew"] is None),
            row("Excess kurtosis", ratio(v["kurtosis"]), gated=v["kurtosis"] is None),
        ]),
        ("Risk-adjusted", [
            row("Sharpe, excess of cash", ratio(v["sharpe"]),
                note=f'risk-free {pct(metrics["risk_free_annual"])}',
                gated=v["sharpe"] is None),
            row("Sharpe, gross", ratio(v["sharpe_gross"]),
                note="before subtracting cash", gated=v["sharpe_gross"] is None),
            row("Sharpe, autocorrelation-adjusted", ratio(v["sharpe_autocorr_adj"]),
                note="Lo (2002)", gated=v["sharpe_autocorr_adj"] is None),
            row("Sortino", ratio(v["sortino"]), gated=v["sortino"] is None),
            row("Calmar", ratio(v["calmar"]), gated=v["calmar"] is None),
        ]),
    ]
    ledger = "".join(f'<section><h3>{t}</h3><dl>{"".join(rs)}</dl></section>'
                     for t, rs in blocks)

    trows = ""
    for t in trades:
        merged = int(t["legs"]) > 1
        tags = ""
        if merged:
            tags += '<span class="tag">merged</span>'
        if t["flags"]:
            tags += '<span class="tag">review</span>'
        if t["cost_basis"] == "modelled":
            tags += '<span class="tag">est. cost</span>'
        if t["override"]:
            tags += '<span class="tag">override</span>'
        sp = float(t["standardised_pnl"])
        trows += (
            f'<tr{" class=merged" if merged else ""}>'
            f'<td class="mono dim">{esc(t["trade_id"])}</td>'
            f'<td>{esc(t["session"])}</td><td>{esc(t["symbol"])}</td>'
            f'<td>{esc(t["direction"])}</td>'
            f'<td class="n">{esc(t["qty"])}</td><td class="n">{esc(t["nq_equiv"])}</td>'
            f'<td class="n">{float(t["gross_pnl"]):,.2f}</td>'
            f'<td class="n dim">{float(t["costs"]):,.2f}</td>'
            f'<td class="n strong {cls(sp)}">{sp:+,.2f}</td>'
            f'<td class="dim">{tags}</td></tr>')

    mrows = "".join(
        f'<tr><td>{esc(m["year"])}</td><td>{esc(MONTHS[m["month"]])}</td>'
        f'<td class="n">{esc(m["sessions"])}</td>'
        f'<td class="n strong {cls(m["return"])}">{spct(m["return"], 2)}</td>'
        f'<td class="dim">{"partial" if m["partial"] else ""}</td></tr>'
        for m in analytics["monthly_returns"])

    erows = "".join(
        f'<tr><td>{esc(e["start"])}</td><td>{esc(e["trough"])}</td>'
        f'<td>{esc(e["recovered"] or "—")}</td><td class="n">{esc(e["sessions"])}</td>'
        f'<td class="n strong down">{pct(e["depth"], 3)}</td>'
        f'<td class="dim">{"ongoing" if e["ongoing"] else "recovered"}</td></tr>'
        for e in analytics["drawdown_episodes"]
    ) or '<tr><td colspan="6" class="dim">No drawdown recorded.</td></tr>'

    q = analytics["quantiles"][0]
    inst = "".join(
        f'<tr><td>{esc(k)}</td><td class="n">{esc(d["trades"])}</td>'
        f'<td class="n">{d["nq_equiv"]:g}</td>'
        f'<td class="n strong {cls(d["standardised_pnl"])}">'
        f'{d["standardised_pnl"]:+,.2f}</td></tr>'
        for k, d in sorted(meta["instruments"].items()))

    gate_note = ""
    if gate:
        gate_note = (
            f'<p class="notice">Annualised statistics are withheld until '
            f'{esc(gate["need"])} sessions; this book has {esc(gate["have"])}. On a handful '
            f'of sessions they are not imprecise estimates, they are meaningless '
            f'ones. What is shown from the first day &#8212; cumulative return, the '
            f'curve, best and worst session &#8212; are statements of what '
            f'happened.</p>')

    kpis = ('<div class="kpis">'
            + kpi("Cumulative return", spct(v["cumulative_return"], 3),
                  f'since {datetime.fromisoformat(meta["inception"]).strftime("%d %b %Y")}')
            + kpi("Net asset value", money(nav[-1]["equity"]),
                  f'nominal, from {money(meta["nominal_capital"], 0)}')
            + kpi("Sessions &#183; trades",
                  f'{esc(meta["sessions"])} &#183; {esc(meta["trades"])}',
                  f'{esc(meta["min_sessions_for_annualised"] - meta["sessions"])} '
                  f'more to ungate')
            + kpi("Exposure", "1 NQ-eq", "constant-notional")
            + "</div>")

    dd = (dual(drawdown_chart, analytics["drawdown"])
          + f'<table style="margin-top:20px"><thead><tr><th>Start</th>'
            f'<th>Trough</th><th>Recovered</th><th class="n">Sessions</th>'
            f'<th class="n">Depth</th><th>State</th></tr></thead>'
            f'<tbody>{erows}</tbody></table>')

    comp = (f'<table><thead><tr><th>Contract</th><th class="n">Trades</th>'
            f'<th class="n">NQ-equivalent</th>'
            f'<th class="n">Standardised P&amp;L</th></tr></thead>'
            f'<tbody>{inst}</tbody></table>'
            f'<div class="row" style="margin-top:20px">'
            f'<dt>Round-turn cost, per contract</dt>'
            f'<dd>NQ {money(meta["rate_card"]["NQ"])} &#183; '
            f'MNQ {money(meta["rate_card"]["MNQ"])}</dd></div>'
            f'<div class="row"><dt>Per NQ-equivalent of exposure <small>&#183; a '
            f'micro costs '
            f'{meta["cost_per_nq_equivalent"]["MNQ"] / meta["cost_per_nq_equivalent"]["NQ"]:.2f}'
            f'&#215; as much to carry the same risk</small></dt>'
            f'<dd>NQ {money(meta["cost_per_nq_equivalent"]["NQ"])} &#183; '
            f'MNQ {money(meta["cost_per_nq_equivalent"]["MNQ"])}</dd></div>'
            f'<div class="row"><dt>Costs from the firm&#39;s own figures</dt>'
            f'<dd>{esc(meta["cost_basis"]["reported"])} of {esc(meta["trades"])}'
            f' trades</dd></div>')

    return f"""
<p class="eyebrow">Portfolio</p>
<h1>{esc(meta['label'])}</h1>
<p class="lede">{esc(meta['tagline_en'])}.</p>
{sec("Position", kpis, first=True)}
{sec("Cumulative return", dual(cumulative_chart, nav, cid="nav"),
     note="Previous nominal NAV plus standardised 1&#8209;NQ profit and loss, "
          "rebased on inception.")}
{sec("Statistics", f'<div class="ledger">{ledger}</div>{gate_note}',
     note="Withheld is not zero. Every figure is computed by "
          "<span class='mono'>bese.metrics</span> and read from "
          "<span class='mono'>metrics.json</span>.")}
{sec("Session returns", dual(returns_chart, analytics["daily_returns"]),
     note="Each session's return on the nominal base.")}
{sec("Drawdown", dd,
     note="From the peak of the nominal NAV. Maximum drawdown as a published "
          f"statistic stays withheld until {esc(meta['min_sessions_for_annualised'])} "
          "sessions.")}
{sec("Monthly returns",
     f'<table><thead><tr><th>Year</th><th>Month</th><th class="n">Sessions</th>'
     f'<th class="n">Return</th><th></th></tr></thead><tbody>{mrows}</tbody></table>',
     note="Daily returns compounded within each calendar month. A month still "
          "running is labelled partial.")}
{sec("Distribution", dual(distribution_chart, analytics["distribution"]["bins"]),
     note=f'Min {spct(q["min"], 3)}, 25th {spct(q["q25"], 3)}, median '
          f'{spct(q["median"], 3)}, 75th {spct(q["q75"], 3)}, max '
          f'{spct(q["max"], 3)} over {esc(q["n"])} sessions.')}
{sec("Trade ledger",
     f'<table><thead><tr><th>Trade</th><th>Session</th><th>Symbol</th>'
     f'<th>Side</th><th class="n">Qty</th><th class="n">NQ eq.</th>'
     f'<th class="n">Gross</th><th class="n">Costs</th>'
     f'<th class="n">Standardised</th><th></th></tr></thead>'
     f'<tbody>{trows}</tbody></table>',
     note="Every strategy trade behind the curve. Shaded rows were assembled "
          "from several broker rows reporting one position. The record is of "
          "completed trades, so there is nothing open to show.")}
{sec("Composition", comp,
     note="By instrument, in NQ-equivalent exposure and standardised "
          "contribution.")}"""


def verify_page(idx, meta, chain):
    ts = meta.get("timestamping") or {}
    ts_ok = bool(ts.get("available"))

    ts_intro = ("Each snapshot carries an OpenTimestamps proof beside it, anchoring "
                "its hash into a Bitcoin block."
                if ts_ok else
                "<b>Not yet in place.</b> The chain shows the series is internally "
                "complete and unedited; on its own it does not stop a whole history "
                "being assembled in one afternoon. Until proofs are attached, treat "
                "the dates as claimed rather than proven &#8212; which is why this "
                "section says so instead of staying quiet.")
    ts_block = ""
    if ts_ok:
        snap_rel = (f"books/{meta['book']}/snapshots/"
                    f"{meta['last_session']}.json")
        cmd = (f"pip install opentimestamps-client\n"
               f"ots info   {snap_rel}.ots      # read the proof, offline\n"
               f"ots verify {snap_rel}.ots      # check it against Bitcoin")
        ts_block = (
            f"<pre>{esc(cmd)}</pre><div class='prose'>"
            "<p>A fresh proof commits to a calendar server and is <em>incomplete</em> "
            "until the aggregating Bitcoin transaction confirms, normally within a "
            "few hours. <span class=\"mono\">ots upgrade</span> completes it; the "
            "publisher does this on every run. Incomplete means &#8220;not yet "
            "confirmed&#8221;, not &#8220;invalid&#8221;.</p>"
            "<p><span class=\"mono\">ots verify</span> checks the proof against "
            "the block chain itself, so it needs a Bitcoin Core node (a pruned "
            "one is fine, and it costs nothing but a one-off sync). That is the "
            "design working as intended rather than an obstacle: the whole "
            "point is that checking this record asks you to trust no third "
            "party. If you would rather not run one, "
            "<span class=\"mono\">ots info</span> reads the proof offline, and "
            "the verifier at opentimestamps.org will check it in your browser "
            "&#8212; at the cost of trusting that site and its block "
            "explorers.</p></div>")

    rows = "".join(
        f'<tr><td>{esc(e["session_date"])}</td>'
        f'<td class="mono">{esc(e["hash"][:16])}…</td>'
        f'<td class="mono dim">{esc(e["prev_hash"][:16])}…</td>'
        f'<td class="mono dim">{esc(e["sha256"][:16])}…</td></tr>' for e in chain)

    snap = f"books/{meta['book']}/snapshots/{meta['last_session']}.json"
    hash_code = esc(
        'import hashlib, json, pathlib\n\n'
        'def canonical(payload):\n'
        '    return json.dumps(payload, sort_keys=True, indent=2,\n'
        '                      ensure_ascii=False, allow_nan=False,\n'
        '                      default=str) + "\\n"\n\n'
        f'rec  = json.loads(pathlib.Path("{snap}")\n'
        '                  .read_text(encoding="utf-8"))\n'
        'body = {k: v for k, v in rec.items() if k != "hash"}\n'
        'assert hashlib.sha256(canonical(body).encode()).hexdigest() == rec["hash"]')
    nav_code = esc(
        'import csv\n'
        f'nav = list(csv.DictReader(open("books/{meta["book"]}/nav.csv")))\n'
        'print(float(nav[-1]["equity"]) / float(nav[0]["equity"]) - 1)')

    return f"""
<p class="eyebrow">Verify</p>
<h1>How to check this record</h1>
<p class="lede">Everything below can be done by a stranger with a copy of the data
directory and no cooperation from us.</p>

{sec("Why it matters here", '''<div class="prose">
<p>A track record quoting a broker's equity endpoint is attested by the broker
whether or not you trust the arithmetic. Besë's NAV is <b>constructed</b> from
fills, so the chain and the open calculation are not a nice extra &#8212; they are
the whole of the evidence. That is why the metric code is public.</p></div>''',
     first=True)}

{sec("1. Self-hashing", '''<div class="prose">
<p>Every file in <span class="mono">snapshots/</span> carries a
<span class="mono">hash</span>: the SHA-256 of the record's canonical JSON with
the hash field removed. Change any published number and this fails.</p></div>'''
     + f"<pre>{hash_code}</pre>",
     note="Each record commits to its own content.")}

{sec("2. Chaining", '''<div class="prose">
<p>Each snapshot's <span class="mono">prev_hash</span> is the hash of the
previous session; the first is 64 zeroes. A timestamp proves a file existed;
only the chain proves the <b>series</b> is complete &#8212; so a losing day
cannot be quietly removed later without breaking every record after it.</p>
<p>Verification walks the snapshot directory rather than trusting
<span class="mono">CHAIN.jsonl</span> to list its own contents, and each
snapshot's filename must match the session date inside it, which must in turn
follow the one before. Together these mean a session cannot be removed,
reordered or invented: not the most recent ones, which is where a bad run would
sit, and not an older one dropped in under a chosen date.</p>
<p>From the project root: <span class="mono">python3 -m bese.verify</span></p>
</div>''', note="No session can be silently dropped, or invented.")}

{sec("3. Coverage", '''<div class="prose">
<p>A hash chain over the session records protects the session records. It does
not, by itself, protect <span class="mono">nav.csv</span>,
<span class="mono">trades.csv</span>, the metric files, the archive manifest or
the corrections file &#8212; and those are what a reader actually reads. So each
snapshot also pins their SHA-256 digests, and
<span class="mono">meta.json</span> is pinned too, excluding the two fields
derived from the chain itself.</p>
<p>So altering a figure in <span class="mono">nav.csv</span>, or removing rows
from <span class="mono">trades.csv</span>, fails verification. The same command
also checks that the copy served by this website is byte-identical to the copy
in the repository.</p>
</div>''', note="Every published file is covered, not just the snapshots.")}

{sec("4. Recomputation", '''<div class="prose">
<p><span class="mono">nav.csv</span> is the whole equity curve. Every published
metric is computed from it by
<span class="mono">bese.metrics.compute_core_metrics</span>, which is published
in this repository. Recompute and compare.</p></div>'''
     + f"<pre>{nav_code}</pre>",
     note="The numbers follow from the inputs.")}

{sec("5. Dating", f'''<div class="prose"><p>{ts_intro}</p></div>{ts_block}
<div class="prose">
<p>What a timestamp proves is narrow and worth stating exactly: the file existed
<em>at or before</em> that block. It does not prove the file did not exist
earlier &#8212; which is the right way round, because the claim being defended is
that a session's record was fixed on the day and not rewritten afterwards to suit
what happened next. The chain stops deletion, the timestamp stops back-dating,
and neither is sufficient alone.</p>
<p><b>The trades themselves are timestamped by someone else.</b> Every entry and
exit instant was written by the exchange and the firm, to the millisecond, in
UTC. That is third-party attestation of when a trade happened, and nothing here
improves on it.</p></div>''', note="The records were not back-dated.")}

{sec("6. The exports", '''<div class="prose">
<p>The NAV is constructed from raw broker and firm exports, so those files are
load-bearing evidence &#8212; but the firm's completed-trade export carries its
account identifier, and a track record is a document strangers are invited to
read closely, so the raw files are not published. <span class="mono">archive_manifest.json</span> is the compromise:
it records the SHA-256 of every raw export the published record was built from.
The files stay private, and if the record is ever challenged any one of them can
be produced and shown to be the file held on the day. A hash costs nothing and
forecloses &#8220;you edited the source&#8221;.</p></div>''',
     note="Fixed in time, without being published.")}

{sec("The chain",
     f'<table><thead><tr><th>Session</th><th>Hash</th><th>Previous</th>'
     f'<th>Bytes on disk</th></tr></thead><tbody>{rows}</tbody></table>',
     note=f'{esc(idx["chain"]["entries"])} records. Head '
          f'<span class="mono">{esc((meta.get("chain_head") or "—")[:20])}…</span>')}

{sec("What it proves", f'''<div class="prose">
<p><b>It proves</b> no published number has been edited after the fact; no
session has been quietly dropped; the metrics follow from the equity curve by
open code; and the equity curve follows from archived broker records by open
code.</p>
<p><b>It does not prove</b> the trading was skilful, that the same strategy would
survive a different market, or that no other unpublished account exists. Git
history can be rewritten by whoever controls the repository &#8212; which is why
the hash chain is used alongside branch protection and external timestamps
rather than instead of them. Whether those two are in force is stated above
rather than assumed here.</p>
{"" if ts_ok else "<p><b>Still outstanding.</b> Timestamp proofs are not attached yet, so the dates on this record are claimed rather than proven. That gap is stated here rather than left to be discovered.</p>"}
</div>''', note="And what it does not.")}

{sec("Where the data is", f'''<div class="prose">
<p>Everything above &#8212; the record, the site that renders it, and the code
that computes every number &#8212; is in
<a href="{REPO_URL}">{esc(REPO_SLUG)}</a>. It is public. The
<span class="mono">main</span> branch is protected against force-push and
deletion, so the append-only history cannot be rewritten without leaving a
trace.</p>
<p>Publication runs on the trading machine itself. GitHub Actions is not
involved in producing this data and holds no broker credential.</p>
<p><b>Found something wrong?</b> If a check fails, a number does not reconcile,
or something here is unclear, please say so. Open an issue and tag
<a href="{OWNER_URL}">@{esc(OWNER)}</a>. A track record nobody can question is
not one worth publishing.</p>
<p class="links">
<a href="{OWNER_URL}">@{esc(OWNER)} maintains this record</a>
<a href="{REPO_URL}/issues/new">Open an issue</a>
</p></div>''', note="Public, and open to challenge.")}"""


def methodology_page(meta, metrics):
    norm_code = esc("nq_equivalent    = contracts × point_value / 20\n"
                    "standardised P&L = (gross P&L − costs) / nq_equivalent")
    nav_code = esc("NAV(t) = NAV(t−1) + standardised P&L(t)\n"
                   "daily return = NAV(t) / NAV(t−1) − 1")

    return f"""
<p class="eyebrow">Methodology</p>
<h1>How every number is produced</h1>
<p class="lede">If something here is unclear or looks wrong, the inputs are
archived and the calculation code is named below. Check it.</p>

{sec("Sources", '''<div class="prose">
<p>Trading is discretionary, on Nasdaq-100 futures, through a proprietary-trading
firm's funded-account programme. After the session the firm's completed-trade
export is dropped into the publisher, which archives it and rebuilds the entire
record from the whole archive &#8212; there is no incremental state to drift.</p>
<p><b>Fills are exact. NAV is constructed.</b> The trades, prices, sizes and the
commission actually charged are the firm's own records. The NAV series is not
read from any account balance: it is computed from those fills by
<span class="mono">bese.normalize</span> and <span class="mono">bese.nav</span>.
This is a weaker claim than quoting a broker equity endpoint and is stated as
such.</p>
<p>Where the same trade appears in both the firm's export and the broker's, the
two are matched on their economics and the firm's figures win, because they carry
the commission actually charged rather than one modelled from a rate card. Both
linkages were run over the same period and produce identical trades.</p>
</div>''', first=True, note="Where the numbers come from.")}

{sec("Normalisation", '''<div class="prose">
<p>Positions vary in size, so raw profit and loss is not comparable across
sessions. Every trade is scaled to one NQ-equivalent of exposure:</p></div>'''
     + f"<pre>{norm_code}</pre>"
     + f'''<div class="prose">
<p>NQ is $20 per index point and MNQ $2, so one E-mini is 1.0, two are 2.0, five
micros are 0.5 and ten micros are 1.0.</p>
<p><b>Costs come off before the division</b>, so they scale with the position the
way the profit does. At the firm's rates one NQ-equivalent costs
{money(meta["cost_per_nq_equivalent"]["NQ"])} to round-trip in the E-mini against
{money(meta["cost_per_nq_equivalent"]["MNQ"])} in the micro &#8212; over three
times as much for the same risk. That asymmetry is real and the record carries it
rather than smoothing it away.</p>
<p><b>Grouping.</b> Brokers report one position as several rows when an order
fills against several on the other side. Rows are joined into one strategy trade
by linkage the source can prove: a shared fill id, or a shared entry or exit
instant to the millisecond within one account, contract and direction. Nothing is
merged on a similarity guess. Cases no linkage can see &#8212; a scale-in placed as
two independent orders &#8212; are flagged for review and merged only by a
published override.</p></div>''',
     note="To 1 NQ-equivalent of exposure.")}

{sec("The nominal account", f'''<div class="prose">
<p>The book starts at {money(meta["nominal_capital"], 0)} of nominal capital on
the session before the first trade, so the first session's result is inside the
record rather than behind it.</p></div>'''
     + f"<pre>{nav_code}</pre>"
     + '''<div class="prose">
<p>Returns are time-weighted; with no external cash flows this reduces exactly to
compounding the daily returns, and the publisher asserts that identity on every
run before writing anything.</p>
<p><b>Exposure does not compound.</b> The strategy holds one NQ-equivalent
regardless of NAV, so it is constant-notional. The return series compounds
correctly; the position size does not. A normal convention for a futures record,
stated rather than left to be inferred.</p></div>''',
     note="$100,000, and what happens to it.")}

{sec("Sessions", '''<div class="prose">
<p>A futures trading day is not a calendar day: the CME equity-index session runs
17:00 to 16:00 New York time. A trade is dated by when its profit was realised
&#8212; when the position went flat &#8212; using the firm's own session label where
it publishes one, and the CME rule otherwise.</p>
<p>The firm requires every position closed by 5:00 PM New York time and permits
no overnight holding, which is exactly the session boundary. So no trade can
straddle one, and the publisher asserts it: a position closing at or after 17:00
New York aborts the run, because it means either the declared source timezone is
wrong or a firm rule was breached, and both need a human.</p></div>''',
     note="A trading day is not a calendar day.")}

{sec("Metrics", f'''<div class="prose">
<p>Every metric is computed by <span class="mono">compute_core_metrics</span> in
<span class="mono">bese.metrics</span> and by nothing else. None is recomputed in
the publisher and none is computed in the browser: the site renders values that
were calculated there. That is the only way &#8220;our calculation source is
public&#8221; is a fact rather than a claim.</p>
<p>Annualisation basis is 252. Sharpe, Sortino and Calmar are excess of the
risk-free rate, with gross variants published alongside; the daily rate is
geometric, <span class="mono">(1 + annual) ** (1/252) − 1</span>. The rate in use
is echoed in every payload &#8212; currently
<span class="mono">{esc(metrics["risk_free_source"])}</span>, so the ratios it
feeds are explicitly gross rather than quietly assuming a rate of zero.</p>
<p><b>Annualised statistics are withheld below
{esc(meta["min_sessions_for_annualised"])} sessions.</b> On a handful of sessions they
are not imprecise estimates, they are meaningless ones. Cumulative return, the
curve and the best and worst session are published from the first day, because
those are statements of what happened.</p></div>''',
     note="Computed once, upstream, by open code.")}

{sec("Known limits", '''<div class="prose">
<p><b>One account is the source.</b> Trades are placed on a leader account and
copied to others; the record is built from the leader. A copy that filled at a
different price or failed to fill is not reflected. One trade copied to five
accounts is one trade here, not five.</p>
<p><b>Account changes are not record changes.</b> Prop accounts begin, end and
are replaced. Because the series is built from trades rather than account equity,
no account event resets it.</p>
<p><b>Gaps are gaps.</b> A session with no trades has no row. Nothing is
interpolated and no value is carried forward.</p>
<p><b>No benchmark yet.</b> A Nasdaq-100 total-return line is the right
comparison and is not yet wired in. An empty series is shown as empty rather than
filled with something convenient.</p>
<p><b>GIPS-informed, not GIPS-compliant.</b> Compliance requires third-party
verification, which has not been performed. No such claim is made anywhere.</p>
</div>''', note="Stated, not discovered.")}"""


def disclosures_page(idx):
    order = {"critical": 0, "important": 1, "note": 2}
    out = ""
    for i, d in enumerate(sorted(idx["disclosures"],
                                 key=lambda d: order.get(d["severity"], 9))):
        # A disclosure long enough to need paragraphs should get them. Splitting
        # on a blank line keeps every single-paragraph disclosure byte-identical
        # while letting the longer ones breathe.
        body = "".join(f"<p>{esc(para.strip())}</p>"
                       for para in d["body_en"].split("\n\n") if para.strip())
        out += sec(esc(d["title_en"]), f'<div class="prose">{body}</div>',
                   note=esc(d["severity"]).upper(), first=(i == 0))
    return f"""
<p class="eyebrow">Disclosures</p>
<h1>What you should know before reading any of it</h1>
<p class="lede">Each of these is stamped into every record in the data directory,
not merely rendered here. They are ordered by how much they should change how the
record is read.</p>
{out}"""


# ------------------------------------------------------------------- build ---

def build(repo: Path, out: Path) -> list[Path]:
    idx = json.loads((repo / "index.json").read_text(encoding="utf-8"))
    book = idx["books"][0]["book"]
    book_dir = repo / "books" / book
    meta = json.loads((book_dir / "meta.json").read_text(encoding="utf-8"))
    metrics = json.loads((book_dir / "metrics.json").read_text(encoding="utf-8"))
    analytics = json.loads((book_dir / "analytics.json").read_text(encoding="utf-8"))
    nav = read_nav(book_dir)
    trades = read_trades(book_dir)
    chain = [json.loads(ln) for ln
             in (repo / "CHAIN.jsonl").read_text(encoding="utf-8").splitlines()
             if ln.strip()]

    published = datetime.fromisoformat(idx["published_at"]).strftime(
        "%d %B %Y, %H:%M UTC")

    out.mkdir(parents=True, exist_ok=True)

    # GitHub Pages runs Jekyll over the directory unless told not to, which
    # silently drops any path beginning with an underscore. The record must be
    # served byte-for-byte or the hashes on the Verify page stop matching.
    (out / ".nojekyll").write_text("", encoding="utf-8", newline="\n")
    (out / "style.css").write_text(CSS.strip() + "\n", encoding="utf-8", newline="\n")
    (out / "app.js").write_text(JS.strip() + "\n", encoding="utf-8", newline="\n")

    cname = repo.parent.parent / "CNAME"
    if cname.exists():
        (out / "CNAME").write_text(
            cname.read_text(encoding="utf-8").strip() + "\n",
            encoding="utf-8", newline="\n")

    written = []
    for name, body in [
        ("index.html", landing(idx, meta, metrics, nav)),
        ("portfolio.html", portfolio(meta, metrics, analytics, nav, trades)),
        ("verify.html", verify_page(idx, meta, chain)),
        ("methodology.html", methodology_page(meta, metrics)),
        ("disclosures.html", disclosures_page(idx)),
    ]:
        (out / name).write_text(
            shell(dict(PAGES)[name], name, body, published),
            encoding="utf-8", newline="\n")
        written.append(out / name)

    data_out = out / "data"
    if data_out.exists():
        shutil.rmtree(data_out)
    shutil.copytree(repo, data_out)
    return written
