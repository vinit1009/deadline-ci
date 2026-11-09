import os
import tomllib
import unittest

from deadline.cli import main
from deadline.compare import compare, markdown, regressed
from deadline.trace import parse, parse_file

from synth import control_loop

CFG = {"budget": [{"name": "wake-up", "type": "latency", "thread": {"process": "deadline-demo", "tid": 2},
                   "from_state": "THRECEIVE", "percentile": 99, "max_us": 150}]}


class Compare(unittest.TestCase):
    def test_a_budget_that_starts_missing_is_a_new_miss(self):
        base = parse(control_loop([10] * 200).text())
        head = parse(control_loop([10] * 190 + [2300] * 10).text())
        rows = compare(base, head, CFG)
        self.assertEqual(rows[0].status, "new miss")
        self.assertTrue(regressed(rows))
        self.assertIn("**NEW MISS**", markdown(rows, "main.txt", "pr.txt"))

    def test_fixed_and_unchanged_runs_pass(self):
        good = parse(control_loop([10] * 200).text())
        bad = parse(control_loop([10] * 190 + [2300] * 10).text())
        self.assertEqual(compare(bad, good, CFG)[0].status, "fixed")
        self.assertFalse(regressed(compare(good, good, CFG)))

    def test_slower_only_counts_with_a_limit_on_the_increase(self):
        base = parse(control_loop([10] * 200).text())
        head = parse(control_loop([40] * 200).text())
        self.assertEqual(compare(base, head, CFG)[0].status, "ok")
        rows = compare(base, head, CFG, max_increase=0.25)
        self.assertEqual(rows[0].status, "slower")
        self.assertAlmostEqual(rows[0].change, 30, places=3)
        self.assertTrue(regressed(rows))


class CompareRealTraces(unittest.TestCase):
    def setUp(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        self.budgets = os.path.join(root, "scenario", "deadlines.toml")
        self.healthy = os.path.join(root, "examples", "qnx-qemu", "healthy.txt.gz")
        self.regressed = os.path.join(root, "examples", "qnx-qemu", "regressed.txt.gz")

    def test_the_priority_mistake_shows_as_new_misses_and_names_the_stage(self):
        cfg = tomllib.load(open(self.budgets, "rb"))
        rows = {r.type: r for r in compare(parse_file(self.healthy), parse_file(self.regressed), cfg)}
        self.assertEqual(rows["latency"].status, "new miss")
        self.assertEqual(rows["chain"].status, "new miss")
        self.assertEqual(rows["roundtrip"].status, "ok")
        self.assertTrue(rows["chain"].grew.startswith("ready -> running +"))

    def test_cli_exit_codes(self):
        self.assertEqual(main(["compare", self.healthy, self.regressed, "--budgets", self.budgets, "--no-step-summary"]), 1)
        self.assertEqual(main(["compare", self.regressed, self.healthy, "--budgets", self.budgets, "--no-step-summary"]), 0)
