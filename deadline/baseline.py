"""Propose budget limits from a trace of a run you know is healthy.

Each budget is measured on the trace at its own percentile, a safety margin is added, and the result
is rounded up to a readable number. The TOML file is edited line by line, so comments and layout stay.
"""
from __future__ import annotations

import math
import re

from .budgets import Result, evaluate
from .trace import Trace

LIMIT_KEY = {"latency": "max_us", "roundtrip": "max_us", "jitter": "tolerance_us", "cpu": "max_share", "chain": "max_us"}


def nice_ceil(value: float) -> float:
    """Round up to two significant figures: 109.1 -> 110, 2875 -> 2900, 7.31 -> 7.4."""
    if value <= 0 or math.isnan(value):
        return 1.0
    step = 10 ** (math.floor(math.log10(value)) - 1)
    return round(math.ceil(round(value / step, 9)) * step, 10)


def suggest(result: Result, margin: float) -> float | None:
    if result.observed is None:
        return None
    if result.type == "cpu":
        return min(1.0, max(0.01, math.ceil(round(result.observed * (1 + margin), 9) * 100) / 100))
    return nice_ceil(result.observed * (1 + margin))


def propose(trace: Trace, cfg: dict, margin: float) -> list:
    """[(budget, result, suggested limit or None)] for every budget in the config."""
    out = []
    for b in cfg["budget"]:
        r = evaluate(trace, {**b, LIMIT_KEY[b["type"]]: b.get(LIMIT_KEY[b["type"]], math.inf)})
        out.append((b, r, suggest(r, margin)))
    return out


def rewrite(text: str, proposals: list) -> str:
    """Returns the TOML text with each budget's limit replaced by its suggestion."""
    wanted = {b["name"]: (LIMIT_KEY[b["type"]], s) for b, _, s in proposals if s is not None}
    blocks = re.split(r"(?m)^(?=\[\[budget\]\]\s*$)", text)
    out = []
    for block in blocks:
        m = re.search(r'(?m)^name\s*=\s*"((?:[^"\\]|\\.)*)"', block)
        if not block.startswith("[[budget]]") or not m or m.group(1) not in wanted:
            out.append(block)
            continue
        key, value = wanted[m.group(1)]
        shown = f"{value:.2f}" if key == "max_share" else (f"{value:g}" if value != int(value) else str(int(value)))
        line = re.compile(rf"(?m)^({key}\s*=\s*)([^#\n]*?)(\s*(#.*)?)$")
        if line.search(block):
            block = line.sub(lambda mm: f"{mm.group(1)}{shown}{mm.group(3) or ''}", block, count=1)
        else:
            block = block.rstrip("\n") + f"\n{key} = {shown}\n" + ("\n" if block.endswith("\n\n") else "")
        out.append(block)
    return "".join(out)
