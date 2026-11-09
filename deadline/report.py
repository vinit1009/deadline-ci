"""Outputs: JUnit XML (so a CI check fails), a Markdown summary (for the job page) and an SVG timeline
of the worst violation (thread lanes coloured by state)."""
from __future__ import annotations

import html
import math
from xml.sax.saxutils import escape, quoteattr

from .budgets import BLOCKED, Result
from .trace import Trace

COLORS = {"THRUNNING": "#2e9e5b", "THREADY": "#f2b544"}
BLOCKED_COLOR = "#d9d4c8"


def _fmt(r: Result, v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{v * 100:.1f}%" if r.unit == "share" else f"{v:.1f} us"


def junit(results: list, trace_name: str) -> str:
    failures = sum(1 for r in results if not r.passed)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<testsuites name="deadline" tests="{len(results)}" failures="{failures}">',
           f'  <testsuite name={quoteattr("deadline: " + trace_name)} tests="{len(results)}" failures="{failures}">']
    for r in results:
        out.append(f'    <testcase classname={quoteattr("deadline." + r.type)} name={quoteattr(r.name)}>')
        if not r.passed:
            out.append(f'      <failure message={quoteattr(r.message)}>{escape(_details(r))}</failure>')
        else:
            out.append(f'      <system-out>{escape(r.message)}</system-out>')
        out.append('    </testcase>')
    out += ['  </testsuite>', '</testsuites>', '']
    return "\n".join(out)


def _details(r: Result) -> str:
    lines = [r.message]
    if r.stats:
        lines.append("  ".join(f"{k} {_fmt(r, v)}" for k, v in r.stats.items()))
    for b in r.breakdown:
        lines.append(f"  {b['stage']}: p50 {_fmt(r, b['p50'])}, p99 {_fmt(r, b['p99'])}, worst sample {_fmt(r, b['worst'])}")
    if r.worst and r.worst.get("pid") is not None:
        lines.append(f"worst sample: pid {r.worst['pid']} tid {r.worst['tid']} at {r.worst['t_us'] / 1000:.3f} ms")
    return "\n".join(lines)


def markdown(results: list, trace: Trace, trace_name: str, svg_name: str = "") -> str:
    ok = all(r.passed for r in results)
    lines = [f"### Deadline CI: {'all budgets met' if ok else str(sum(not r.passed for r in results)) + ' budget(s) missed'}", "",
             f"Trace `{trace_name}`: {len(trace.events):,} events over {trace.duration_us / 1000:.1f} ms.", "",
             "| Budget | Type | Observed | Limit | p50 | max | Samples | Result |", "|---|---|---|---|---|---|---|---|"]
    for r in results:
        label = "max" if r.percentile >= 100 else f"p{r.percentile:g}"
        lines.append(f"| {r.name} | {r.type} | {label} {_fmt(r, r.observed)} | {_fmt(r, r.limit)} | {_fmt(r, r.stats.get('p50'))} | "
                     f"{_fmt(r, r.stats.get('max'))} | {r.samples} | {'pass' if r.passed else '**FAIL**'} |")
    for r in results:
        if r.breakdown:
            lines += ["", f"#### Where the time went: {r.name}", "", "| Stage | p50 | p99 | Worst sample |", "|---|---|---|---|"]
            lines += [f"| {b['stage']} | {_fmt(r, b['p50'])} | {_fmt(r, b['p99'])} | {_fmt(r, b['worst'])} |" for b in r.breakdown]
    if svg_name:
        lines += ["", f"Worst violation timeline: `{svg_name}`."]
    return "\n".join(lines) + "\n"


# ---------- SVG timeline ----------

def _intervals(trace: Trace, t0: float, t1: float) -> dict:
    """(pid, tid) -> [(start, end, state)] clipped to [t0, t1]."""
    cur: dict = {}
    on_cpu: dict = {}
    out: dict = {}

    def close(pt, t):
        if pt in cur:
            state, since = cur[pt]
            s, e = max(since, t0), min(t, t1)
            if e > s:
                out.setdefault(pt, []).append((s, e, state))

    for ev in trace.events:
        if ev.t_us > t1:
            break
        if ev.cls != "THREAD":
            continue
        pt = (ev.pid, ev.tid)
        if ev.name == "THRUNNING":
            other = on_cpu.get(ev.cpu)
            if other and other != pt and cur.get(other, ("",))[0] == "THRUNNING":
                close(other, ev.t_us); cur[other] = ("THREADY", ev.t_us)
            on_cpu[ev.cpu] = pt
        close(pt, ev.t_us)
        cur[pt] = (ev.name, ev.t_us)
    for pt in list(cur):
        close(pt, t1)
    return out


def svg_timeline(trace: Trace, result: Result, focus: set, max_lanes: int = 8) -> str:
    w = result.worst or {}
    start, end = w.get("t_us", 0.0), w.get("end_us", 0.0)
    span = max(end - start, 50.0)
    t0, t1 = max(0.0, start - span * 1.5), min(trace.duration_us, end + span * 1.5)
    if t1 <= t0:
        t1 = t0 + 1.0
    lanes = _intervals(trace, t0, t1)
    busy = sorted(lanes, key=lambda pt: (pt not in focus, -sum(e - s for s, e, st in lanes[pt] if st == "THRUNNING")))
    shown = busy[:max_lanes]
    W, left, lane_h, top = 980, 220, 26, 64
    H = top + lane_h * len(shown) + 56 + (20 if result.breakdown else 0)
    x = lambda t: left + (t - t0) / (t1 - t0) * (W - left - 20)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Helvetica, Arial, sans-serif" font-size="12">',
             f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
             f'<text x="16" y="24" font-size="15" font-weight="bold" fill="#14213d">{html.escape(result.name)}: {"pass" if result.passed else "FAIL"}</text>',
             f'<text x="16" y="44" fill="#44506b">{html.escape(result.message.split("; worst sample: ")[0])}</text>']
    for i, pt in enumerate(shown):
        y = top + i * lane_h
        name = trace.process_names.get(pt[0], f"pid {pt[0]}")
        label = f"{name.split('/')[-1]} ({pt[0]}) tid {pt[1]}"
        weight = ' font-weight="bold"' if pt in focus else ''
        parts.append(f'<text x="16" y="{y + 17}" fill="#14213d"{weight}>{html.escape(label)}</text>')
        parts.append(f'<rect x="{left}" y="{y + 4}" width="{W - left - 20}" height="{lane_h - 8}" fill="#f7f3ec"/>')
        for s, e, st in lanes[pt]:
            color = COLORS.get(st, BLOCKED_COLOR if st in BLOCKED else "#ece7dc")
            parts.append(f'<rect x="{x(s):.1f}" y="{y + 4}" width="{max(0.8, x(e) - x(s)):.1f}" height="{lane_h - 8}" fill="{color}"><title>{st} {s:.1f}-{e:.1f} us</title></rect>')
    if w:
        xa, xb = x(start), x(end)
        yb = top + lane_h * len(shown) + 6
        parts.append(f'<rect x="{xa:.1f}" y="{top - 4}" width="{max(1.5, xb - xa):.1f}" height="{lane_h * len(shown) + 8}" fill="none" stroke="#b42318" stroke-width="2" stroke-dasharray="4 3"/>')
        parts.append(f'<text x="{xa:.1f}" y="{yb + 14}" fill="#b42318">worst sample: {_fmt(result, w.get("value"))}</text>')
        if result.breakdown:
            steps = ", ".join(f"{b['stage']} {_fmt(result, b['worst'])}" for b in result.breakdown)
            parts.append(f'<text x="16" y="{yb + 34}" fill="#14213d">Where the time went: {html.escape(steps)}</text>')
    ya = H - 14
    for k in range(6):
        t = t0 + (t1 - t0) * k / 5
        anchor = "start" if k == 0 else ("end" if k == 5 else "middle")    # keep the end labels inside the image
        parts.append(f'<text x="{x(t):.1f}" y="{ya}" fill="#6b7489" text-anchor="{anchor}">{t / 1000:.3f} ms</text>')
    legend = [("running", COLORS["THRUNNING"]), ("ready", COLORS["THREADY"]), ("blocked", BLOCKED_COLOR)]
    for i, (n, c) in enumerate(legend):
        parts.append(f'<rect x="{W - 300 + i * 95}" y="14" width="12" height="12" fill="{c}"/><text x="{W - 284 + i * 95}" y="24" fill="#44506b">{n}</text>')
    parts.append("</svg>")
    return "\n".join(parts)
