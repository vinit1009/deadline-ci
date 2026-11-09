# Deadline CI

Real-time code fails quietly: a priority change or a new busy thread adds a few milliseconds of latency, nothing crashes, and the regression ships. QNX's System Profiler shows it beautifully, but only when someone opens it.

**Deadline CI turns QNX kernel traces into pass/fail tests.** Declare timing budgets for your threads, capture a trace with `tracelogger` during a scenario, and `deadline check` fails the build when a budget is missed, with JUnit output for any CI system, a Markdown summary for the job page and an SVG timeline of the worst violation.

```
$ deadline check trace.txt --budgets deadlines.toml --junit report.xml --svg worst.svg
FAIL  Control loop wakes up within 150 us of its timer pulse: p99 2300.0 us > 150.0 us over 59 samples (worst 2300.0 us at 40.000 ms)
```

Status: work in progress for the QNX Global Hackathon 2026 (category Developer Tooling & Host Tools). The checker, reports and tests are written against the trace format documented for QNX SDP 8.0; the QEMU target scenario and the GitHub Action come next.

## Budgets

| Type | Measures | Typical use |
|---|---|---|
| `latency` | From a thread leaving a blocked state (for example RECEIVE, woken by a timer pulse) to running | Control loop wake-up latency |
| `roundtrip` | From a kernel call (for example `MSG_SENDV`) to its return, per thread | Client/server message round trip |
| `jitter` | How far the time between a thread's wake-ups strays from its period | 1 kHz loop period stability |
| `cpu` | Share of one CPU a process uses in each window | Keep a best-effort AI worker in its lane |

Each budget passes when its percentile (default 99; 100 is the maximum) is within the limit. See [`examples/deadlines.toml`](examples/deadlines.toml).

## Capturing a trace

On the QNX target (the instrumented kernel is standard in QNX SDP 8.0):

```sh
tracelogger -n 0 -s 2 -f /tmp/trace.kev &   # 2 seconds of events
./run-scenario
traceprinter -n -p "c:0x%016c CPU:%02C %-16Z:%-18z" -f /tmp/trace.kev > trace.txt
```

The custom format keeps the full 64-bit cycle count; the default format works too (32-bit wraps are undone with the kernel's CONTROL TIME events). Kernel calls are attributed to the thread running on that CPU.

## Running the tests

```sh
PYTHONPATH=.:tests python3 -m unittest discover -s tests
```

No dependencies beyond Python 3.11+.

## License

[MIT](LICENSE)
