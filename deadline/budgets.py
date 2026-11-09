"""Timing budgets declared in deadlines.toml, checked against a parsed trace.

Budget types:
  latency    time from a thread leaving a blocked state (e.g. RECEIVE, woken by a timer pulse) to running
  roundtrip  time from a kernel call (e.g. MSG_SENDV) to its return, for one thread
  jitter     how far the time between a thread's wake-ups strays from its nominal period
  cpu        share of one CPU used by a process in each window
  chain      time from the first stage of a wake-up chain (e.g. the clock interrupt) to the thread running,
             broken down by stage: interrupt -> timer fired -> pulse sent -> ready -> running
Each budget passes when the chosen percentile (default 99; 100 means the maximum) is within its limit.
"""
from __future__ import annotations

import bisect
import math
import tomllib
from dataclasses import dataclass, field
from typing import Optional

from .trace import Trace

BLOCKED = {"THRECEIVE", "THREPLY", "THSEND", "THNANOSLEEP", "THSEM", "THCONDVAR", "THMUTEX", "THJOIN", "THINTR",
           "THSIGSUSPEND", "THSIGWAITINFO", "THWAITPAGE", "THNET_SEND", "THNET_REPLY", "THSTOPPED"}
TYPES = {"latency", "roundtrip", "jitter", "cpu", "chain"}
DEFAULT_CHAIN = ["INTERRUPT:INT_DELIVER", "SYSTEM:TIMER", "COMM:SND_PULSE_EXE"]
STAGE_NAMES = {"INTERRUPT:INT_DELIVER": "interrupt", "INTERRUPT:INT_ENTR": "interrupt", "SYSTEM:TIMER": "timer fired", "COMM:SND_PULSE_EXE": "pulse sent", "COMM:SND_PULSE": "pulse sent"}


@dataclass
class Result:
    name: str
    type: str
    passed: bool
    unit: str
    limit: float
    percentile: float
    observed: Optional[float]
    samples: int
    stats: dict = field(default_factory=dict)
    worst: Optional[dict] = None        # {"t_us", "end_us", "value", "pid", "tid"} for the timeline
    message: str = ""
    breakdown: list = field(default_factory=list)  # chain budgets: [{"stage", "p50", "p99", "worst"}]


def load(path: str) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    budgets = cfg.get("budget", [])
    if not budgets:
        raise ValueError(f"{path} declares no [[budget]] tables.")
    for b in budgets:
        if b.get("type") not in TYPES:
            raise ValueError(f"Budget {b.get('name')!r}: type must be one of {sorted(TYPES)}.")
        if "name" not in b:
            raise ValueError("Every budget needs a name.")
        for st in b.get("stages", []) if b["type"] == "chain" else []:
            _stage(str(st))     # raises on a malformed stage
    return cfg


def _as_int(value) -> Optional[int]:
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        return None


def _stage(spec: str) -> tuple:
    """'INTERRUPT:INT_DELIVER intr_id=0x80010000' -> ('INTERRUPT:INT_DELIVER', {'intr_id': '0x80010000'})."""
    event, *rest = spec.split()
    filters = {}
    for item in rest:
        key, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"Chain stage {spec!r}: filters look like key=value.")
        filters[key] = value
    return event.upper(), filters


def _arg_is(actual, wanted: str) -> bool:
    """traceprinter prints some numbers as 0x80010000(2147549184); compare numbers by value."""
    if actual is None:
        return False
    actual = str(actual).split("(")[0]
    a, w = _as_int(actual), _as_int(wanted)
    return a == w if a is not None and w is not None else actual == wanted


def percentile(values: list, p: float) -> float:
    """Nearest-rank percentile; p=100 is the maximum."""
    if not values:
        return math.nan
    s = sorted(values)
    k = max(1, math.ceil(p / 100 * len(s)))
    return s[min(k, len(s)) - 1]


def _stats(values: list) -> dict:
    return {"p50": percentile(values, 50), "p99": percentile(values, 99), "p99.9": percentile(values, 99.9), "max": max(values) if values else math.nan}


def _threads(trace: Trace, sel: dict) -> set:
    """(pid, tid) pairs matching {process|pid, tid?}."""
    pids = set()
    if "pid" in sel:
        pids.add(int(sel["pid"]))
    if "process" in sel:
        pids.update(trace.pids_named(str(sel["process"])))
    seen = {(e.pid, e.tid) for e in trace.events if e.cls == "THREAD" and e.pid in pids}
    if "tid" in sel:
        seen = {pt for pt in seen if pt[1] == int(sel["tid"])}
    return seen


def _states(trace: Trace, threads: set) -> dict:
    """(pid, tid) -> [(t_us, state, cpu)] in time order."""
    out: dict = {pt: [] for pt in threads}
    for e in trace.events:
        if e.cls == "THREAD" and (e.pid, e.tid) in out:
            out[(e.pid, e.tid)].append((e.t_us, e.name, e.cpu))
    return out


def _wakeups(trace: Trace, threads: set, from_state: Optional[str]) -> list:
    """[(left_blocked_at, running_at, pid, tid)]: each time a thread leaves a blocked state and then runs."""
    wanted = {from_state} if from_state else BLOCKED
    found = []
    for (pid, tid), seq in _states(trace, threads).items():
        prev = None
        woke_at = None
        for t, state, _ in seq:
            if prev in wanted and state not in BLOCKED:
                woke_at = t
            if state == "THRUNNING" and woke_at is not None:
                found.append((woke_at, t, pid, tid))
                woke_at = None
            if state in BLOCKED:
                woke_at = None
            prev = state
    return sorted(found)


def _running_intervals(trace: Trace, pids: set) -> list:
    """[(start, end, pid, tid)] while threads of these processes were running."""
    open_: dict = {}        # (pid, tid) -> (start, cpu)
    on_cpu: dict = {}       # cpu -> (pid, tid)
    out = []

    def close(pt, t):
        if pt in open_:
            start, _ = open_.pop(pt)
            if t > start:
                out.append((start, t, pt[0], pt[1]))

    for e in trace.events:
        if e.cls != "THREAD":
            continue
        pt = (e.pid, e.tid)
        if e.name == "THRUNNING":
            other = on_cpu.get(e.cpu)
            if other and other != pt:
                close(other, e.t_us)
            on_cpu[e.cpu] = pt
            if e.pid in pids and pt not in open_:
                open_[pt] = (e.t_us, e.cpu)
        else:
            close(pt, e.t_us)
            for cpu, who in list(on_cpu.items()):
                if who == pt:
                    del on_cpu[cpu]
    for pt in list(open_):
        close(pt, trace.duration_us)
    return out


def evaluate(trace: Trace, budget: dict) -> Result:
    kind = budget["type"]
    p = float(budget.get("percentile", 99))
    unit = "share" if kind == "cpu" else "us"
    limit = float(budget.get("max_share" if kind == "cpu" else ("tolerance_us" if kind == "jitter" else "max_us"), math.nan))
    if math.isnan(limit):
        key = {"cpu": "max_share", "jitter": "tolerance_us"}.get(kind, "max_us")
        raise ValueError(f"Budget {budget['name']!r} needs {key}.")
    samples: list = []      # (value, worst-context)
    if kind == "latency":
        threads = _threads(trace, budget.get("thread", {}))
        for woke, ran, pid, tid in _wakeups(trace, threads, budget.get("from_state")):
            samples.append((ran - woke, {"t_us": woke, "end_us": ran, "pid": pid, "tid": tid}))
    elif kind == "jitter":
        threads = _threads(trace, budget.get("thread", {}))
        period = float(budget["period_us"])
        by_thread: dict = {}
        for woke, ran, pid, tid in _wakeups(trace, threads, budget.get("from_state")):
            by_thread.setdefault((pid, tid), []).append(ran)
        for (pid, tid), runs in by_thread.items():
            for a, b in zip(runs, runs[1:]):
                samples.append((abs((b - a) - period), {"t_us": a, "end_us": b, "pid": pid, "tid": tid}))
    elif kind == "roundtrip":
        threads = _threads(trace, budget.get("thread", {}))
        call = str(budget["call"]).upper()
        pending: dict = {}
        for e in trace.events:
            if (e.pid, e.tid) not in threads or e.name != call:
                continue
            if e.cls == "KER_CALL":
                pending[(e.pid, e.tid)] = e.t_us
            elif e.cls == "KER_EXIT" and (e.pid, e.tid) in pending:
                start = pending.pop((e.pid, e.tid))
                samples.append((e.t_us - start, {"t_us": start, "end_us": e.t_us, "pid": e.pid, "tid": e.tid}))
    elif kind == "chain":
        threads = _threads(trace, budget.get("thread", {}))
        stages = [str(x) for x in budget.get("stages", DEFAULT_CHAIN)]
        if not stages:
            raise ValueError(f"Budget {budget['name']!r}: stages must list at least one CLASS:EVENT.")
        window = float(budget.get("window_us", 1000))  # longest gap allowed between consecutive stages
        pids = {pid for pid, _ in threads}
        index = []
        for st in stages:
            event, filters = _stage(st)
            cls, _, name = event.partition(":")
            index.append([e.t_us for e in trace.events if e.cls == cls and e.name == name
                          and (e.args.get("pid") is None or _as_int(e.args.get("pid")) in pids)
                          and all(_arg_is(e.args.get(k), v) for k, v in filters.items())])
        for woke, ran, pid, tid in _wakeups(trace, threads, budget.get("from_state")):
            cur, marks = woke, []
            for ts in reversed(index):
                k = bisect.bisect_right(ts, cur) - 1
                if k < 0 or cur - ts[k] > window:
                    break
                marks.append(ts[k])
                cur = ts[k]
            if len(marks) != len(stages):
                continue  # this wake-up was not caused by the chain (e.g. a message, not a timer pulse)
            marks.reverse()
            points = marks + [woke, ran]
            parts = [b - a for a, b in zip(points, points[1:])]
            samples.append((ran - marks[0], {"t_us": marks[0], "end_us": ran, "pid": pid, "tid": tid, "parts": parts}))
        labels = [STAGE_NAMES.get(_stage(x)[0], _stage(x)[0].split(":")[-1].lower()) for x in stages] + ["ready", "running"]
        chain_labels = [f"{a} -> {b}" for a, b in zip(labels, labels[1:])]
    elif kind == "cpu":
        sel = budget.get("process")
        pids = set(trace.pids_named(str(sel))) if sel else set()
        if "pid" in budget:
            pids.add(int(budget["pid"]))
        window = float(budget.get("window_ms", 100)) * 1000
        intervals = _running_intervals(trace, pids)
        n = max(1, math.ceil(trace.duration_us / window))
        busy = [0.0] * n
        for start, end, _, _ in intervals:
            k = int(start // window)
            while k < n and k * window < end:
                busy[k] += max(0.0, min(end, (k + 1) * window) - max(start, k * window))
                k += 1
        # The last window is usually partial; judge it on its real length.
        for k in range(n):
            length = min(window, trace.duration_us - k * window)
            if length >= window * 0.5:
                samples.append((busy[k] / length, {"t_us": k * window, "end_us": k * window + length, "pid": None, "tid": None}))
        unit = "share"

    values = [v for v, _ in samples]
    if not values:
        empty_ok = bool(budget.get("allow_empty", False))
        return Result(budget["name"], kind, empty_ok, unit, limit, p, None, 0, {}, None,
                      "No samples matched this budget; check its thread or process selector." if not empty_ok else "No samples (allowed).")
    observed = percentile(values, p)
    worst_value, worst_ctx = max(samples, key=lambda s: s[0])
    passed = observed <= limit
    breakdown = []
    if kind == "chain":
        for i, stage in enumerate(chain_labels):
            vals = [ctx["parts"][i] for _, ctx in samples]
            breakdown.append({"stage": stage, "p50": percentile(vals, 50), "p99": percentile(vals, 99), "worst": worst_ctx["parts"][i]})
    label = "max" if p >= 100 else f"p{p:g}"
    fmt = (lambda v: f"{v * 100:.1f}%") if unit == "share" else (lambda v: f"{v:.1f} us")
    message = f"{label} {fmt(observed)} {'<=' if passed else '>'} {fmt(limit)} over {len(values)} samples (worst {fmt(worst_value)} at {worst_ctx['t_us'] / 1000:.3f} ms)"
    if breakdown:
        message += "; worst sample: " + ", ".join(f"{b['stage']} {fmt(b['worst'])}" for b in breakdown)
    worst = {k: v for k, v in worst_ctx.items() if k != "parts"}
    return Result(budget["name"], kind, passed, unit, limit, p, observed, len(values), _stats(values),
                  {**worst, "value": worst_value}, message, breakdown)


def check(trace: Trace, cfg: dict) -> list:
    return [evaluate(trace, b) for b in cfg["budget"]]
