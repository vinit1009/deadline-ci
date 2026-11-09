"""Compare two runs budget by budget: the base (for example main) and the head (a pull request).

Each budget is judged on both traces at its own percentile. A budget that passed on the base and
fails on the head is a new miss; one that got worse by more than --max-increase while still passing
is reported as slower. For chain budgets the stage that grew the most is named, so a review comment
says where the extra time went.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .budgets import Result, evaluate
from .trace import Trace


@dataclass
class Row:
    name: str
    type: str
    unit: str
    limit: float
    base: Result
    head: Result
    change: Optional[float]         # head - base, in the budget's unit
    change_pct: Optional[float]     # relative to the base
    status: str                     # "new miss", "still missing", "fixed", "slower", "ok", "no samples"
    grew: str = ""                  # chain budgets: the stage that grew the most, e.g. "ready -> running +9002.6 us"


def _fmt(unit: str, v: Optional[float]) -> str:
    if v is None:
        return "-"
    return f"{v * 100:.1f}%" if unit == "share" else f"{v:,.1f} us"


def _signed(unit: str, v: Optional[float]) -> str:
    if v is None:
        return "-"
    if abs(v) < (0.0005 if unit == "share" else 0.05):
        return "no change"
    sign = "+" if v >= 0 else "-"
    return sign + (f"{abs(v) * 100:.1f} pts" if unit == "share" else f"{abs(v):,.1f} us")


def _ratio(r: "Row") -> str:
    """ (+12%) for small changes, (x5,629) when the head is ten or more times the base."""
    if r.change_pct is None or r.change is None or abs(r.change) < (0.0005 if r.unit == "share" else 0.05):
        return ""
    if r.change_pct >= 9:
        return f" (x{r.head.observed / r.base.observed:,.0f})"
    return f" ({r.change_pct:+.0%})"


def compare(base: Trace, head: Trace, cfg: dict, max_increase: Optional[float] = None) -> list:
    rows = []
    for b in cfg["budget"]:
        rb, rh = evaluate(base, b), evaluate(head, b)
        change = pct = None
        if rb.observed is not None and rh.observed is not None:
            change = rh.observed - rb.observed
            pct = change / rb.observed if rb.observed else None
        if rh.observed is None or rb.observed is None:
            status = "no samples"
        elif rb.passed and not rh.passed:
            status = "new miss"
        elif not rb.passed and not rh.passed:
            status = "still missing"
        elif not rb.passed and rh.passed:
            status = "fixed"
        elif max_increase is not None and pct is not None and pct > max_increase:
            status = "slower"
        else:
            status = "ok"
        grew = ""
        if rb.breakdown and rh.breakdown:
            deltas = [(h["p99"] - s["p99"], h["stage"]) for s, h in zip(rb.breakdown, rh.breakdown)]
            d, stage = max(deltas)
            if d > 0:
                grew = f"{stage} {_signed(rh.unit, d)} at p99"
        rows.append(Row(b["name"], b["type"], rh.unit, rh.limit, rb, rh, change, pct, status, grew))
    return rows


def regressed(rows: list) -> bool:
    return any(r.status in ("new miss", "slower") for r in rows)


def text(rows: list) -> str:
    out = []
    for r in rows:
        pct = _ratio(r)
        extra = f"; {r.grew}" if r.grew else ""
        out.append(f"{r.status.upper():<13} {r.name}: {_fmt(r.unit, r.base.observed)} -> {_fmt(r.unit, r.head.observed)},"
                   f" {_signed(r.unit, r.change)}{pct}, limit {_fmt(r.unit, r.limit)}{extra}")
    return "\n".join(out)


def markdown(rows: list, base_name: str, head_name: str) -> str:
    new = sum(r.status == "new miss" for r in rows)
    slower = sum(r.status == "slower" for r in rows)
    fixed = sum(r.status == "fixed" for r in rows)
    verdict = (f"{new} new budget miss(es)" if new else "no new budget misses") + (f", {slower} slower" if slower else "") + (f", {fixed} fixed" if fixed else "")
    lines = [f"### Deadline CI: {head_name} against {base_name}: {verdict}", "",
             "| Budget | Base | Head | Change | Limit | Result |", "|---|---|---|---|---|---|"]
    for r in rows:
        label = "max" if r.head.percentile >= 100 else f"p{r.head.percentile:g}"
        pct = _ratio(r)
        result = {"new miss": "**NEW MISS**", "still missing": "still missing", "fixed": "fixed", "slower": "**slower**", "ok": "ok", "no samples": "no samples"}[r.status]
        lines.append(f"| {r.name} ({label}) | {_fmt(r.unit, r.base.observed)} | {_fmt(r.unit, r.head.observed)} | {_signed(r.unit, r.change)}{pct} | {_fmt(r.unit, r.limit)} | {result} |")
    grown = [r for r in rows if r.grew]
    if grown:
        lines += [""] + [f"Where the extra time went, {r.name}: {r.grew}." for r in grown]
    return "\n".join(lines) + "\n"
