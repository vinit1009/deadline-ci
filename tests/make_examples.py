"""Writes a demo budgets file and two synthetic traces (healthy and regressed) into a folder, in the
traceprinter format the tests use. They stand in for real target traces in CI and the README demo.

    PYTHONPATH=tests python3 tests/make_examples.py demo
"""
import os
import sys

from synth import control_loop

BUDGETS = """# Demo budgets for the synthetic control loop (pid 20, tid 2, woken by a 1 ms timer pulse).
[[budget]]
name = "Control loop wakes up within 150 us of its timer pulse"
type = "latency"
thread = { process = "deadline-demo", tid = 2 }
from_state = "THRECEIVE"
percentile = 99
max_us = 150

[[budget]]
name = "Control loop period stays within 200 us of 1 ms"
type = "jitter"
thread = { process = "deadline-demo", tid = 2 }
from_state = "THRECEIVE"
period_us = 1000
tolerance_us = 200
percentile = 100
"""


def main(out: str) -> None:
    os.makedirs(out, exist_ok=True)
    healthy = [20 + (i % 9) * 6 for i in range(500)]                    # 20 to 68 us
    regressed = [2300 if i % 25 == 24 else v for i, v in enumerate(healthy)]  # a busy thread steals the CPU now and then
    with open(os.path.join(out, "deadlines.toml"), "w") as f:
        f.write(BUDGETS)
    for name, lats in (("healthy", healthy), ("regressed", regressed)):
        with open(os.path.join(out, f"{name}.txt"), "w") as f:
            f.writelines(control_loop(lats).text())
    print(f"wrote {out}/deadlines.toml, {out}/healthy.txt, {out}/regressed.txt")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo")
