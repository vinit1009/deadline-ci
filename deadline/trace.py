"""Reads the text that QNX's traceprinter writes for a kernel event trace (.kev).

Recommended capture, on the target or the host:
    traceprinter -n -p "c:0x%016c CPU:%02C %-16Z:%-18z" -f trace.kev > trace.txt
The full 64-bit cycle count never wraps; the header's TRACE_CYCLES_PER_SEC turns cycles into time.
The default format ("t:0x%08c ...", 32-bit cycles) also works: wraps are undone because traceprinter
prints events in time order. Without -n, argument lines follow their event and are joined to it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

_EVENT = re.compile(
    r"^(?:e:\S+\s+)?(?:t:(?P<tus>\d+\.\d+\.\d+)us\s+)?"
    r"(?:c:0x(?P<c64>[0-9a-fA-F]+)|t:0x(?P<c32>[0-9a-fA-F]+))\s+"
    r"CPU:(?P<cpu>\d+)\s+(?P<cls>[A-Z_]+)\s*:\s*(?P<ev>[A-Za-z0-9_]+)(?:/\d+)?\s*(?P<args>.*)$"
)
_ARG = re.compile(r"([A-Za-z_][A-Za-z0-9_()]*):(\S+?),?(?=\s|$)")
_HEADER = re.compile(r"^(TRACE_[A-Z_]+)::\s*(.*)$")


@dataclass
class Event:
    """One trace event. `t_us` is microseconds since the first event."""
    t_us: float
    cpu: int
    cls: str            # event class, e.g. THREAD, KER_CALL, KER_EXIT, PROCESS, INT_ENTR
    name: str           # event, e.g. THRUNNING, MSG_SENDV, PROCCREATE_NAME
    args: dict = field(default_factory=dict)
    pid: Optional[int] = None   # the thread the event is about (THREAD events) or that ran it (kernel calls)
    tid: Optional[int] = None


@dataclass
class Trace:
    events: list
    cycles_per_sec: float
    header: dict
    process_names: dict     # pid -> name, from PROCCREATE_NAME events
    duration_us: float

    def pids_named(self, name: str) -> list:
        """PIDs whose process name ends with `name` (names are often paths like ./demo)."""
        return [pid for pid, n in self.process_names.items() if n == name or n.endswith('/' + name)]


def _int(value: str) -> Optional[int]:
    try:
        return int(value, 0)
    except (TypeError, ValueError):
        return None


def parse(lines: Iterable[str], cycles_per_sec: Optional[float] = None) -> Trace:
    header: dict = {}
    raw: list = []
    for line in lines:
        line = line.rstrip("\n")
        if not line.strip():
            continue
        h = _HEADER.match(line.strip())
        if h:
            header[h.group(1)] = h.group(2).strip()
            continue
        m = _EVENT.match(line.strip())
        if m:
            raw.append([m, dict(_ARG.findall(m.group("args") or ""))])
        elif raw and _ARG.search(line):
            # an argument line printed without -n belongs to the event above it
            raw[-1][1].update(_ARG.findall(line))
    cps = cycles_per_sec or float(header.get("TRACE_CYCLES_PER_SEC", "0") or 0)
    if not cps and not any(m.group("tus") for m, _ in raw):
        raise ValueError("No TRACE_CYCLES_PER_SEC header and no %t timestamps; pass cycles_per_sec.")

    events: list = []
    names: dict = {}
    running: dict = {}      # cpu -> (pid, tid) of the thread running there, for kernel calls
    base = None
    wrap = 0
    last32 = None
    for m, args in raw:
        if m.group("c64"):
            cycles = int(m.group("c64"), 16)
        elif m.group("c32"):
            c = int(m.group("c32"), 16)
            if m.group("cls") == "CONTROL" and m.group("ev") == "TIME" and _int("0x" + args.get("msb", "").removeprefix("0x")) is not None:
                # The kernel's time event carries the upper 32 bits of the cycle counter.
                wrap = _int("0x" + args["msb"].removeprefix("0x")) << 32
            elif last32 is not None and c < last32 and last32 - c > 0x80000000:
                wrap += 1 << 32
            last32 = c
            cycles = c + wrap
        else:
            cycles = None
        if cps and cycles is not None:
            t = cycles / cps * 1e6
        else:
            s, ms, us = (int(x) for x in m.group("tus").split("."))
            t = s * 1e6 + ms * 1e3 + us
        if base is None:
            base = t
        cpu = int(m.group("cpu"))
        ev = Event(t_us=t - base, cpu=cpu, cls=m.group("cls"), name=m.group("ev"), args=args)
        if ev.cls == "THREAD":
            ev.pid, ev.tid = _int(args.get("pid")), _int(args.get("tid"))
            if ev.name == "THRUNNING":
                running[cpu] = (ev.pid, ev.tid)
        elif ev.cls in ("KER_CALL", "KER_EXIT", "KER_UNKNOWN"):
            ev.pid, ev.tid = running.get(cpu, (None, None))
        elif ev.cls == "PROCESS" and ev.name.startswith("PROCCREATE"):
            pid = _int(args.get("pid"))
            if pid is not None and args.get("name"):
                names[pid] = args["name"]
            ev.pid = pid
        events.append(ev)
    duration = events[-1].t_us if events else 0.0
    return Trace(events=events, cycles_per_sec=cps, header=header, process_names=names, duration_us=duration)


def parse_file(path: str, cycles_per_sec: Optional[float] = None) -> Trace:
    """Reads traceprinter output; a .gz file is decompressed on the fly."""
    if str(path).endswith(".gz"):
        import gzip
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            return parse(f, cycles_per_sec)
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse(f, cycles_per_sec)
