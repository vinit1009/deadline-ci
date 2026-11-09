"""deadline: turn a QNX kernel trace into pass/fail timing tests.

    deadline check trace.txt --budgets deadlines.toml [--junit report.xml] [--summary summary.md] [--svg worst.svg]
    deadline threads trace.txt           # list processes and threads seen, to write selectors

Exit status: 0 when every budget is met, 1 when any is missed, 2 on bad input.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .budgets import check, load, _threads
from .report import junit, markdown, svg_timeline
from .trace import parse_file


def _write(path: str, text: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def cmd_check(a) -> int:
    trace = parse_file(a.trace, a.cycles_per_sec)
    cfg = load(a.budgets)
    results = check(trace, cfg)
    name = os.path.basename(a.trace)
    for r in results:
        print(f"{'PASS' if r.passed else 'FAIL'}  {r.name}: {r.message}")
    if a.junit:
        _write(a.junit, junit(results, name))
    svg_name = ""
    if a.svg:
        failing = [r for r in results if not r.passed and r.worst]
        target = failing[0] if failing else next((r for r in results if r.worst), None)
        if target:
            budget = next(b for b in cfg["budget"] if b["name"] == target.name)
            focus = _threads(trace, budget.get("thread", {})) if "thread" in budget else set()
            _write(a.svg, svg_timeline(trace, target, focus))
            svg_name = os.path.basename(a.svg)
    summary = markdown(results, trace, name, svg_name)
    if a.summary:
        _write(a.summary, summary)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step and not a.no_step_summary:
        with open(step, "a", encoding="utf-8") as f:
            f.write(summary)
    return 0 if all(r.passed for r in results) else 1


def cmd_threads(a) -> int:
    trace = parse_file(a.trace, a.cycles_per_sec)
    seen: dict = {}
    for e in trace.events:
        if e.cls == "THREAD" and e.pid is not None:
            seen.setdefault(e.pid, set()).add(e.tid)
    for pid in sorted(seen):
        print(f"{pid:>8}  {trace.process_names.get(pid, '?'):<40} tids {', '.join(str(t) for t in sorted(seen[pid]))}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="deadline", description="Turn a QNX kernel trace into pass/fail timing tests.")
    p.add_argument("--version", action="version", version=f"deadline {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="check a trace against the budgets in deadlines.toml")
    c.add_argument("trace", help="traceprinter output (text)")
    c.add_argument("--budgets", default="deadlines.toml")
    c.add_argument("--junit", help="write JUnit XML here")
    c.add_argument("--summary", help="write a Markdown summary here")
    c.add_argument("--svg", help="write an SVG timeline of the worst violation here")
    c.add_argument("--cycles-per-sec", type=float, help="override the trace header's TRACE_CYCLES_PER_SEC")
    c.add_argument("--no-step-summary", action="store_true", help="do not append to $GITHUB_STEP_SUMMARY")
    c.set_defaults(func=cmd_check)
    t = sub.add_parser("threads", help="list the processes and threads in a trace")
    t.add_argument("trace")
    t.add_argument("--cycles-per-sec", type=float)
    t.set_defaults(func=cmd_threads)
    a = p.parse_args(argv)
    try:
        return a.func(a)
    except (OSError, ValueError, KeyError) as e:
        print(f"deadline: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
