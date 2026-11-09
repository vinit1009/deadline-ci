import os
import tempfile
import tomllib
import unittest

from deadline.baseline import nice_ceil, propose, rewrite
from deadline.budgets import check, evaluate
from deadline.cli import main
from deadline.trace import parse, parse_file

from synth import control_loop

TOML = '''# demo budgets
[[budget]]
name = "wake-up"
type = "latency"
thread = { process = "deadline-demo", tid = 2 }
from_state = "THRECEIVE"   # blocked in MsgReceive
percentile = 99
max_us = 5                 # too tight on purpose

[[budget]]
name = "period"
type = "jitter"
thread = { process = "deadline-demo", tid = 2 }
from_state = "THRECEIVE"
period_us = 1000
percentile = 100
'''


def healthy_trace():
    return parse(control_loop([20 + (i % 7) * 5 for i in range(200)]).text())


class NiceCeil(unittest.TestCase):
    def test_rounds_up_to_two_significant_figures(self):
        self.assertEqual(nice_ceil(109.1), 110)
        self.assertEqual(nice_ceil(2875), 2900)
        self.assertEqual(nice_ceil(7.31), 7.4)
        self.assertEqual(nice_ceil(100), 100)
        self.assertEqual(nice_ceil(0), 1.0)


class Baseline(unittest.TestCase):
    def test_suggestions_cover_the_healthy_run_with_margin(self):
        tr = healthy_trace()
        cfg = tomllib.loads(TOML)
        rows = {b["name"]: (r, s) for b, r, s in propose(tr, cfg, 0.25)}
        r, s = rows["wake-up"]
        self.assertEqual(r.observed, 50)  # p99 of 20..50 us
        self.assertEqual(s, nice_ceil(50 * 1.25))
        self.assertGreaterEqual(rows["period"][1], rows["period"][0].observed * 1.25)

    def test_rewrite_keeps_comments_and_passes_on_the_same_run(self):
        tr = healthy_trace()
        cfg = tomllib.loads(TOML)
        text = rewrite(TOML, propose(tr, cfg, 0.25))
        self.assertIn("# too tight on purpose", text)
        self.assertIn("max_us = 63", text)
        self.assertIn("tolerance_us = ", text)  # added where the budget had none
        new = tomllib.loads(text)
        self.assertTrue(all(r.passed for r in check(tr, new)))

    def test_cli_writes_a_budgets_file_that_passes(self):
        with tempfile.TemporaryDirectory() as d:
            trace, budgets, out = (os.path.join(d, n) for n in ("trace.txt", "deadlines.toml", "suggested.toml"))
            with open(trace, "w") as f:
                f.writelines(control_loop([20 + (i % 7) * 5 for i in range(200)]).text())
            with open(budgets, "w") as f:
                f.write(TOML)
            # the hand-written file is too tight and the jitter budget has no limit yet: not a pass
            self.assertNotEqual(main(["check", trace, "--budgets", budgets, "--no-step-summary"]), 0)
            self.assertEqual(main(["baseline", trace, "--budgets", budgets, "--write", out]), 0)
            self.assertEqual(main(["check", trace, "--budgets", out, "--no-step-summary"]), 0)


class RealTraces(unittest.TestCase):
    """Traces captured from QNX OS 8.0 under QEMU (examples/qnx-qemu), checked with the demo budgets."""

    def setUp(self):
        self.root = os.path.join(os.path.dirname(__file__), "..")
        self.budgets = os.path.join(self.root, "scenario", "deadlines.toml")

    def test_healthy_run_passes_every_budget(self):
        trace = os.path.join(self.root, "examples", "qnx-qemu", "healthy.txt.gz")
        self.assertEqual(main(["check", trace, "--budgets", self.budgets, "--no-step-summary"]), 0)

    def test_regressed_run_fails(self):
        trace = os.path.join(self.root, "examples", "qnx-qemu", "regressed.txt.gz")
        self.assertEqual(main(["check", trace, "--budgets", self.budgets, "--no-step-summary"]), 1)

    def test_the_chain_puts_the_regressed_delay_in_scheduling(self):
        cfg = tomllib.load(open(self.budgets, "rb"))
        chain = next(b for b in cfg["budget"] if b["type"] == "chain")
        results = {}
        for run in ("healthy", "regressed"):
            tr = parse_file(os.path.join(self.root, "examples", "qnx-qemu", f"{run}.txt.gz"))
            results[run] = evaluate(tr, chain)
        self.assertTrue(results["healthy"].passed)
        bad = results["regressed"]
        self.assertFalse(bad.passed)
        stages = {b["stage"]: b["worst"] for b in bad.breakdown}
        self.assertGreater(stages["ready -> running"], 8000)        # the loop sat ready while the worker ran
        self.assertLess(sum(v for k, v in stages.items() if k != "ready -> running"), 20)
