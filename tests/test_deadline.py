import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

from deadline.budgets import check, evaluate, percentile
from deadline.cli import main
from deadline.report import junit, markdown, svg_timeline
from deadline.trace import parse

from synth import Trace, control_loop


def lat_budget(max_us=150, p=99, **extra):
    return {"name": "wake-up", "type": "latency", "thread": {"process": "deadline-demo", "tid": 2}, "from_state": "THRECEIVE", "max_us": max_us, "percentile": p, **extra}


class TraceParsing(unittest.TestCase):
    def test_reads_header_events_and_process_names(self):
        tr = parse(control_loop([10, 20]).text())
        self.assertEqual(tr.cycles_per_sec, 1e9)
        self.assertEqual(tr.process_names[20], "./deadline-demo")
        self.assertEqual(tr.pids_named("deadline-demo"), [20])
        running = [e for e in tr.events if e.name == "THRUNNING"]
        self.assertAlmostEqual(running[0].t_us, 1010.0, places=3)
        self.assertEqual((running[0].pid, running[0].tid), (20, 2))

    def test_joins_argument_lines_printed_without_n(self):
        t = Trace(joined=False).proc(0, 7, "./a").th(5, 1, "THRUNNING", 7, 1)
        tr = parse(t.text())
        self.assertEqual(tr.process_names[7], "./a")
        self.assertEqual((tr.events[-1].pid, tr.events[-1].tid), (7, 1))

    def test_undoes_32_bit_cycle_wraps_in_the_default_format(self):
        t = Trace(fmt="c32")
        for s in range(7):  # an event a second for 6 s at 1 GHz: the 32-bit counter wraps
            t.th(s * 1_000_000, 0, "THRUNNING" if s % 2 == 0 else "THREADY", 1, 1)
        tr = parse(t.text())
        self.assertAlmostEqual(tr.events[-1].t_us, 6_000_000, delta=1)

    def test_control_time_events_give_the_upper_32_bits(self):
        lines = ["TRACE_CYCLES_PER_SEC:: 1000000000\n",
                 "t:0xfffffff0 CPU:00 CONTROL         :TIME               msb:0x00000000, lsb(offset):0xfffffff0\n",
                 "t:0xfffffff8 CPU:00 THREAD          :THRUNNING          pid:1 tid:1\n",
                 "t:0x00000010 CPU:00 CONTROL         :TIME               msb:0x00000003, lsb(offset):0x00000010\n",
                 "t:0x00000020 CPU:00 THREAD          :THREADY            pid:1 tid:1\n"]
        tr = parse(lines)
        # msb jumped from 0 to 3: 3 * 2^32 cycles later, plus the low bits
        self.assertAlmostEqual(tr.events[-1].t_us, ((3 << 32) + 0x20 - 0xfffffff0) / 1000, places=3)

    def test_kernel_calls_belong_to_the_thread_running_on_that_cpu(self):
        t = Trace().proc(0, 30, "./client").th(1, 1, "THRUNNING", 30, 1)
        t.ev(2, 1, "KER_CALL", "MSG_SENDV", coid="0x40000002").ev(300, 1, "KER_EXIT", "MSG_SENDV", status="0")
        tr = parse(t.text())
        call = [e for e in tr.events if e.cls == "KER_CALL"][0]
        self.assertEqual((call.pid, call.tid, call.name), (30, 1, "MSG_SENDV"))

    def test_needs_a_clock(self):
        with self.assertRaises(ValueError):
            parse(["c:0x0000000000000001 CPU:00 THREAD          :THRUNNING pid:1 tid:1\n"])


class Budgets(unittest.TestCase):
    def test_percentile_is_nearest_rank(self):
        v = list(range(1, 101))
        self.assertEqual(percentile(v, 50), 50)
        self.assertEqual(percentile(v, 99), 99)
        self.assertEqual(percentile(v, 100), 100)

    def test_latency_passes_and_fails_on_the_percentile(self):
        tr = parse(control_loop([10] * 199 + [2300]).text())
        self.assertTrue(evaluate(tr, lat_budget(150, 99)).passed)       # one outlier in 200 is beyond p99
        bad = evaluate(tr, lat_budget(150, 100))
        self.assertFalse(bad.passed)
        self.assertEqual(bad.samples, 200)
        self.assertAlmostEqual(bad.worst["value"], 2300, places=3)
        self.assertIn("max 2300.0 us > 150.0 us", bad.message)

    def test_jitter_measures_the_period_between_runs(self):
        tr = parse(control_loop([10, 10, 90, 10]).text())
        r = evaluate(tr, {"name": "period", "type": "jitter", "thread": {"process": "deadline-demo", "tid": 2}, "from_state": "THRECEIVE", "period_us": 1000, "tolerance_us": 50, "percentile": 100})
        self.assertFalse(r.passed)
        self.assertAlmostEqual(r.observed, 80, places=3)    # 1080 us then 920 us between runs

    def test_roundtrip_pairs_calls_with_their_return(self):
        t = Trace().proc(0, 30, "./client")
        for i in range(10):
            base = i * 1000
            t.th(base + 1, 1, "THRUNNING", 30, 1).ev(base + 2, 1, "KER_CALL", "MSG_SENDV").th(base + 3, 1, "THREPLY", 30, 1)
            t.th(base + 3 + (100 if i < 9 else 900), 1, "THRUNNING", 30, 1).ev(base + 4 + (100 if i < 9 else 900), 1, "KER_EXIT", "MSG_SENDV")
        tr = parse(t.text())
        r = evaluate(tr, {"name": "rt", "type": "roundtrip", "thread": {"process": "client"}, "call": "MSG_SENDV", "max_us": 400, "percentile": 99.9})
        self.assertEqual(r.samples, 10)
        self.assertFalse(r.passed)
        self.assertAlmostEqual(r.observed, 902, places=3)

    def test_cpu_share_per_window(self):
        t = Trace().proc(0, 40, "./ai-worker").proc(0, 41, "./other")
        # 4 windows of 100 ms: the worker runs 30, 30, 60 and 30 ms of each.
        for k, busy_ms in enumerate([30, 30, 60, 30]):
            start = k * 100_000
            t.th(start + 1, 0, "THRUNNING", 40, 1).th(start + 1 + busy_ms * 1000, 0, "THREADY", 40, 1)
            t.th(start + 2 + busy_ms * 1000, 0, "THRUNNING", 41, 1).th(start + 99_999, 0, "THREADY", 41, 1)
        t.th(400_000, 0, "THRUNNING", 41, 1)
        tr = parse(t.text())
        r = evaluate(tr, {"name": "cpu", "type": "cpu", "process": "ai-worker", "window_ms": 100, "max_share": 0.40, "percentile": 100})
        self.assertFalse(r.passed)
        self.assertAlmostEqual(r.observed, 0.60, places=2)
        self.assertTrue(evaluate(tr, {"name": "cpu", "type": "cpu", "process": "ai-worker", "window_ms": 100, "max_share": 0.40, "percentile": 75}).passed)

    def test_a_selector_that_matches_nothing_fails_unless_allowed(self):
        tr = parse(control_loop([10]).text())
        r = evaluate(tr, {**lat_budget(), "thread": {"process": "nope"}})
        self.assertFalse(r.passed)
        self.assertIn("No samples", r.message)
        self.assertTrue(evaluate(tr, {**lat_budget(), "thread": {"process": "nope"}, "allow_empty": True}).passed)

    def test_preemption_counts_from_ready_to_running(self):
        t = Trace().proc(0, 20, "./deadline-demo").proc(0, 50, "./hog")
        t.th(1, 0, "THRECEIVE", 20, 2).th(2, 0, "THRUNNING", 50, 1)
        t.th(1000, 0, "THREADY", 20, 2).th(3300, 0, "THREADY", 50, 1).th(3300, 0, "THRUNNING", 20, 2)
        tr = parse(t.text())
        r = evaluate(tr, lat_budget(150, 100))
        self.assertAlmostEqual(r.observed, 2300, places=3)


class Reports(unittest.TestCase):
    def setUp(self):
        self.trace = parse(control_loop([10] * 50 + [2300]).text())
        self.results = check(self.trace, {"budget": [lat_budget(150, 100), {**lat_budget(5000, 100), "name": "loose"}]})

    def test_junit_marks_failures(self):
        root = ET.fromstring(junit(self.results, "trace.txt"))
        self.assertEqual(root.get("failures"), "1")
        cases = root.findall(".//testcase")
        self.assertEqual(len(cases), 2)
        self.assertIsNotNone(cases[0].find("failure"))
        self.assertIsNone(cases[1].find("failure"))

    def test_markdown_and_svg(self):
        md = markdown(self.results, self.trace, "trace.txt", "worst.svg")
        self.assertIn("1 budget(s) missed", md)
        self.assertIn("**FAIL**", md)
        svg = svg_timeline(self.trace, self.results[0], {(20, 2)})
        ET.fromstring(svg)  # well-formed
        self.assertIn("deadline-demo (20) tid 2", svg)
        self.assertIn("worst sample: 2300.0 us", svg)

    def test_cli_writes_outputs_and_exits_1_on_a_miss(self):
        with tempfile.TemporaryDirectory() as d:
            trace = os.path.join(d, "trace.txt")
            with open(trace, "w") as f:
                f.writelines(control_loop([10] * 50 + [2300]).text())
            toml = os.path.join(d, "deadlines.toml")
            with open(toml, "w") as f:
                f.write('[[budget]]\nname = "wake-up"\ntype = "latency"\nthread = { process = "deadline-demo", tid = 2 }\nfrom_state = "THRECEIVE"\npercentile = 100\nmax_us = 150\n')
            code = main(["check", trace, "--budgets", toml, "--junit", os.path.join(d, "out", "junit.xml"), "--summary", os.path.join(d, "out", "summary.md"), "--svg", os.path.join(d, "out", "worst.svg"), "--no-step-summary"])
            self.assertEqual(code, 1)
            for f in ("junit.xml", "summary.md", "worst.svg"):
                self.assertTrue(os.path.getsize(os.path.join(d, "out", f)) > 100)
            self.assertEqual(main(["check", os.path.join(d, "missing.txt"), "--budgets", toml]), 2)


if __name__ == "__main__":
    unittest.main()
