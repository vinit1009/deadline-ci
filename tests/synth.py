"""Builds traceprinter-style text for tests, in the documented formats (QNX 8.0 Utilities Reference,
traceprinter): the header block, then one line per event; -n puts arguments on the event's line."""
from __future__ import annotations

CPS = 1_000_000_000  # 1 GHz, so 1 us = 1000 cycles


class Trace:
    def __init__(self, fmt: str = "c64", joined: bool = True):
        self.fmt, self.joined = fmt, joined
        self.lines = ["TRACEPRINTER version 1.02", "-- HEADER FILE INFORMATION --", f"TRACE_CYCLES_PER_SEC:: {CPS}",
                      "TRACE_CPU_NUM:: 2", "TRACE_SYSNAME:: QNX", "-- KERNEL EVENTS --"]

    def ev(self, t_us: float, cpu: int, cls: str, event: str, **args) -> "Trace":
        cycles = int(t_us * CPS / 1_000_000) + 0x4BE9A514
        stamp = f"c:0x{cycles:016x}" if self.fmt == "c64" else f"t:0x{cycles & 0xFFFFFFFF:08x}"
        head = f"{stamp} CPU:{cpu:02d} {cls:<16}:{event:<18}"
        parts = [f"{k}:{v}" for k, v in args.items()]
        if self.joined:
            self.lines.append((head + " " + " ".join(parts)).rstrip())
        else:
            self.lines.append(head.rstrip())
            self.lines += [f"                      {p}" for p in parts]
        return self

    def proc(self, t_us: float, pid: int, name: str) -> "Trace":
        return self.ev(t_us, 0, "PROCESS", "PROCCREATE_NAME", ppid=1, pid=pid, name=name)

    def th(self, t_us: float, cpu: int, state: str, pid: int, tid: int) -> "Trace":
        return self.ev(t_us, cpu, "THREAD", state, pid=pid, tid=tid)

    def text(self) -> list:
        return [line + "\n" for line in self.lines]


def control_loop(latencies_us: list, period_us: float = 1000.0, pid: int = 20, tid: int = 2) -> Trace:
    """A control thread blocked in MsgReceive, woken every period by a timer pulse, runs 50 us."""
    t = Trace().proc(0, pid, "./deadline-demo")
    t.th(1, 0, "THRECEIVE", pid, tid)
    for i, lat in enumerate(latencies_us):
        wake = period_us * (i + 1)
        t.th(wake, 0, "THREADY", pid, tid)
        t.th(wake + lat, 0, "THRUNNING", pid, tid)
        t.th(wake + lat + 50, 0, "THRECEIVE", pid, tid)
    return t
