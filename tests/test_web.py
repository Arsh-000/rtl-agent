"""Tests for the browser interface (rtl_agent/web).

Unit tests need nothing installed. The end-to-end tests start the real web server on a free
port and run the agent through it with real tools (skipped when the tools are missing).
"""

import json
import os
import socket
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from rtl_agent.web.jobs import JobError, JobManager, build_rtl_config, build_spec_config, detect_modules, safe_name
from rtl_agent.config import load_config
from rtl_agent.tools import find_tool

TOOLS = all(find_tool(t) for t in ("yosys", "sta", "iverilog", "vvp"))
LIB = Path(__file__).resolve().parent.parent / "libs" / "nangate45_typ.lib"

MAX4 = """// largest of four
module max4 (input wire clk, input wire [15:0] a, b, c, d, output reg [15:0] biggest);
    wire [15:0] m1 = (a > b) ? a : b;
    wire [15:0] m2 = (m1 > c) ? m1 : c;
    always @(posedge clk) biggest <= (m2 > d) ? m2 : d;
endmodule
"""
HIER = """module leaf(input wire x, output wire y); assign y = ~x; endmodule
module top_level(input wire clk, input wire i, output reg o);
    wire w;
    leaf u_leaf (.x(i), .y(w));
    always @(posedge clk) o <= w;
endmodule
"""


class TestWebUnits(unittest.TestCase):
    def test_detect_modules_and_top(self):
        d = detect_modules([{"name": "a.v", "text": HIER}])
        self.assertEqual(d["modules"], ["leaf", "top_level"])
        self.assertEqual(d["top"], "top_level")          # leaf is instantiated, so it is not the top
        self.assertEqual(detect_modules([{"name": "m.v", "text": MAX4}])["top"], "max4")
        self.assertIsNone(detect_modules([{"name": "e.v", "text": "// nothing here"}])["top"])

    def test_safe_names(self):
        self.assertEqual(safe_name("max4.v", (".v",)), "max4.v")
        self.assertEqual(safe_name("C:\\work\\max4.v", (".v",)), "max4.v")     # folders are dropped
        for bad in ("..v", "evil.sh", ".hidden.v", "a b.v", ""):
            with self.assertRaises(JobError):
                safe_name(bad, (".v",))

    def test_rtl_form_becomes_a_valid_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            cfg = build_rtl_config({"files": [{"name": "max4.v", "text": MAX4}], "top": "max4",
                                    "timing": {"clock_period_ns": "1.2"}, "description": "largest of four",
                                    "targets": {"min_setup_slack_ns": "0", "max_area_um2": "400"},
                                    "priority": ["area", "timing", "power"],
                                    "latency": {"policy": "flexible", "max_extra_cycles": "1"},
                                    "loop": {"max_rounds": "4"}, "llm": {"reasoning_effort": "low"}}, job)
            self.assertEqual(cfg["design"]["rtl_files"], ["inputs/max4.v"])
            self.assertEqual(cfg["clock_period_ns"], 1.2)
            self.assertEqual(cfg["targets"]["max_area_um2"], 400.0)
            self.assertEqual(cfg["latency"], {"policy": "flexible", "max_extra_cycles": 1})
            (job / "config.yaml").write_text(yaml.safe_dump(cfg))
            if LIB.exists():
                c = load_config(job / "config.yaml")      # the agent's own validation accepts it
                self.assertEqual(c.priority, ["area", "timing", "power"])
                self.assertEqual(c.llm.extra_params, {"reasoning_effort": "low"})

    def test_rtl_form_with_sdc_and_testbench(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            cfg = build_rtl_config({"files": [{"name": "max4.v", "text": MAX4}], "top": "max4",
                                    "timing": {"sdc_name": "max4.sdc", "sdc_text": "create_clock -period 1.2 [get_ports clk]\n"},
                                    "testbench": {"files": [{"name": "tb.v", "text": "module tb; endmodule"}], "top": "tb"}}, job)
            self.assertEqual(cfg["sdc"], "inputs/max4.sdc")
            self.assertNotIn("clock_period_ns", cfg)
            self.assertEqual(cfg["testbench"]["files"], ["inputs/tb.v"])
            self.assertTrue((job / "inputs" / "max4.sdc").exists())

    def test_form_mistakes_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            good = {"files": [{"name": "max4.v", "text": MAX4}], "top": "max4", "timing": {"clock_period_ns": "1"}}
            for change, words in [({"top": "bad name"}, "Verilog name"), ({"files": []}, "at least one"),
                                  ({"timing": {}}, "clock period"), ({"timing": {"clock_period_ns": "-1"}}, "greater than 0"),
                                  ({"priority": ["timing", "timing", "area"]}, "priority"),
                                  ({"latency": {"policy": "sometimes"}}, "latency")]:
                with self.assertRaises(JobError) as ctx:
                    build_rtl_config(dict(good, **change), job / words.replace(" ", "_"))
                self.assertIn(words, str(ctx.exception))

    def test_spec_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = build_spec_config({"top": "acc", "spec": "A 16-bit accumulator that adds din when valid is 1.",
                                     "interface": "module acc(input clk, ...);", "reset_port": "rst",
                                     "clock_period_ns": "1.0", "candidates": "1"}, Path(tmp))
            self.assertEqual(cfg["mode"], "spec")
            self.assertEqual(cfg["spec"]["reset_port"], "rst")
            self.assertEqual(cfg["explore"], {"candidates": 1})
            with self.assertRaises(JobError):
                build_spec_config({"top": "acc", "spec": "too short", "clock_period_ns": "1"}, Path(tmp) / "x")

    def test_real_run_needs_a_key_and_bad_ids_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = JobManager(Path(tmp))
            old = os.environ.pop("GROQ_API_KEY", None)
            try:
                if LIB.exists():
                    with self.assertRaises(JobError) as ctx:
                        jobs.create({"kind": "rtl", "action": "run", "files": [{"name": "max4.v", "text": MAX4}],
                                     "top": "max4", "timing": {"clock_period_ns": "1.2"}})
                    self.assertIn("key", str(ctx.exception))
                    self.assertEqual(list(jobs.root.iterdir()), [])     # nothing left behind
            finally:
                if old is not None:
                    os.environ["GROQ_API_KEY"] = old
            for bad in ("../x", "a/b", "", "X"):
                with self.assertRaises(JobError):
                    jobs.job_dir(bad)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(TOOLS and LIB.exists(), "needs Yosys, OpenSTA, Icarus and libs/nangate45_typ.lib")
class TestWebEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rtl_agent.web.server import serve_in_thread
        cls.tmp = tempfile.TemporaryDirectory()
        cls.port = _free_port()
        cls.srv = serve_in_thread("127.0.0.1", cls.port, Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def call(self, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                return r.status, (json.loads(data) if r.headers.get_content_type() == "application/json" else data)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait(self, job_id, until=lambda d: d["status"] != "running", timeout=300):
        end = time.time() + timeout
        while time.time() < end:
            _, d = self.call(f"/api/runs/{job_id}")
            if until(d):
                return d
            time.sleep(1)
        self.fail(f"run {job_id} did not reach the expected state in {timeout}s")

    def test_page_status_and_inspect(self):
        code, page = self.call("/")
        self.assertEqual(code, 200)
        self.assertIn(b"RTL Agent", page)
        _, st = self.call("/api/status")
        self.assertTrue(st["tools_ok"])
        self.assertEqual(sorted(e["name"] for e in st["examples"]), ["dot_product", "spec_gray_counter"])
        _, ins = self.call("/api/inspect", {"files": [{"name": "max4.v", "text": MAX4}]})
        self.assertEqual(ins["top"], "max4")
        self.assertEqual(ins["info"]["clocks"], ["clk"])
        self.assertEqual(len(ins["info"]["ports"]), 6)
        _, bad = self.call("/api/inspect", {"files": [{"name": "x.v", "text": "module x(input a; endmodule"}]})
        self.assertTrue(bad["error"])

    def test_measure_only_run(self):
        code, r = self.call("/api/runs", {"kind": "rtl", "action": "analyze", "files": [{"name": "max4.v", "text": MAX4}],
                                          "top": "max4", "timing": {"clock_period_ns": "1.2"}})
        self.assertEqual(code, 200, r)
        d = self.wait(r["id"])
        self.assertEqual(d["status"], "finished")
        self.assertLess(d["baseline"]["setup_ws"], 0)
        self.assertTrue(d["has_report"])
        code, html = self.call(f"/api/runs/{r['id']}/report")
        self.assertEqual(code, 200)
        self.assertIn(b"max4", html)

    def test_offline_example_pause_and_continue(self):
        code, r = self.call("/api/runs", {"kind": "example", "action": "run", "example": "dot_product", "offline": True})
        self.assertEqual(code, 200, r)
        job = r["id"]
        self.wait(job, until=lambda d: d["rounds_done"] >= 1 or d["status"] != "running", timeout=180)
        code, _ = self.call(f"/api/runs/{job}/stop", {})
        self.assertEqual(code, 200)
        d = self.wait(job, timeout=120)
        self.assertEqual(d["status"], "paused", d["stop_reason"])
        code, _ = self.call(f"/api/runs/{job}/resume", {})
        self.assertEqual(code, 200)
        d = self.wait(job, timeout=300)
        self.assertEqual(d["status"], "finished", d["stop_reason"])
        self.assertGreaterEqual(d["best"]["setup_ws"], 0)              # the example meets its target
        self.assertEqual([x["status"] for x in d["rounds"]], ["baseline", "invalid", "kept"])
        code, z = self.call(f"/api/runs/{job}/best.zip")
        self.assertEqual(code, 200)
        self.assertEqual(z[:2], b"PK")
        _, lst = self.call("/api/runs")
        self.assertIn(job, [x["id"] for x in lst["runs"]])

    def test_errors_come_back_as_messages(self):
        code, r = self.call("/api/runs", {"kind": "rtl", "action": "analyze", "files": [], "top": "x",
                                          "timing": {"clock_period_ns": "1"}})
        self.assertEqual(code, 400)
        self.assertIn("Verilog file", r["error"])
        code, r = self.call("/api/runs/does_not_exist")
        self.assertEqual(code, 400)
        code, r = self.call("/api/runs", {"kind": "example", "example": "../../etc", "offline": True})
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
