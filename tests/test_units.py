"""Fast unit tests - no EDA tools and no LLM needed.

Run:  python -m unittest discover -s tests -v     (or: pytest tests)
"""

import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace

from rtl_agent.adapters.inspect import DesignInfo, Port
from rtl_agent.adapters.sta import parse_paths, _parse_power_mw
from rtl_agent.adapters.synth import _parse_stat
from rtl_agent.config import ConfigError, load_config
from rtl_agent.guardrails import GuardrailViolation, check_sdc_unchanged, interface_differences, safe_filename
from rtl_agent.history import RoundRecord, RunStore, rtl_hash
from rtl_agent.reply_parser import parse_reply
from rtl_agent.scoring import Metrics, all_targets_met, compare, judge_claims
from rtl_agent.sdc import check_against_design, fingerprint, generate_basic_sdc, read_clocks, referenced_ports

ROOT = Path(__file__).resolve().parents[1]
TARGETS = SimpleNamespace(min_setup_slack_ns=0.0, min_hold_slack_ns=0.0, max_area_um2=None, max_power_mw=None)


def m(ws=0.1, area=100.0, power=1.0, hold=0.05):
    return Metrics(setup_ws=ws, setup_tns=min(ws, 0), hold_ws=hold, area=area, power_mw=power)


class TestReplyParser(unittest.TestCase):
    def test_full_format(self):
        reply = textwrap.dedent("""\
            ### PLAN
            The adder chain is the critical path. Use a tree.
            ### CHANGE_NOTE
            {"idea": "adder tree", "expected": {"setup_slack": "improve", "area": "same", "power": "same"}, "latency_change": 0}
            ### FILE: dot4.v
            ```verilog
            module dot4(); endmodule
            ```
            """)
        p = parse_reply(reply, ["dot4.v"])
        self.assertEqual(p.idea, "adder tree")
        self.assertEqual(p.expected["setup_slack"], "improve")
        self.assertIn("module dot4", p.files["dot4.v"])
        self.assertFalse(p.problem)

    def test_unlabeled_single_block_single_file(self):
        p = parse_reply("Here you go:\n```verilog\nmodule x(); endmodule\n```\n", ["x.v"])
        self.assertIn("x.v", p.files)

    def test_unlabeled_block_but_many_files_is_a_problem(self):
        p = parse_reply("```verilog\nmodule x(); endmodule\n```", ["a.v", "b.v"])
        self.assertFalse(p.files)
        self.assertTrue(p.problem)

    def test_bold_headings_and_empty_reply(self):
        p = parse_reply("**PLAN**\nshorter path\n**FILE: a.v**\n```systemverilog\nmodule a(); endmodule\n```", ["a.v", "b.v"])
        self.assertEqual(list(p.files), ["a.v"])
        self.assertEqual(p.plan, "shorter path")
        empty = parse_reply("   ", ["a.v"])
        self.assertFalse(empty.no_more_ideas)
        self.assertTrue(empty.problem)

    def test_no_more_ideas(self):
        self.assertTrue(parse_reply("NO_MORE_IDEAS", ["a.v"]).no_more_ideas)

    def test_think_tags_removed_and_bad_json_tolerated(self):
        reply = "<think>module fake(); endmodule</think>\n### PLAN\nx\n### CHANGE_NOTE\n{not json}\n" \
                "### FILE: a.v\n```verilog\nmodule a(); endmodule\n```"
        p = parse_reply(reply, ["a.v"])
        self.assertEqual(list(p.files), ["a.v"])
        self.assertNotIn("fake", p.files["a.v"])


class TestScoring(unittest.TestCase):
    def test_timing_first(self):
        better, obj = compare(m(ws=-0.1, area=200), m(ws=-0.5, area=100), ["timing", "area", "power"], TARGETS, 1.0)
        self.assertEqual((better, obj), (1, "timing"))

    def test_met_timing_then_area_decides(self):
        better, obj = compare(m(ws=0.05, area=90), m(ws=0.4, area=100), ["timing", "area", "power"], TARGETS, 1.0)
        self.assertEqual((better, obj), (1, "area"))

    def test_small_change_is_tie(self):
        better, _ = compare(m(area=99.7), m(area=100.0), ["area"], TARGETS, 1.0)
        self.assertEqual(better, 0)

    def test_targets(self):
        t = SimpleNamespace(min_setup_slack_ns=0.0, min_hold_slack_ns=0.0, max_area_um2=95.0, max_power_mw=None)
        self.assertFalse(all_targets_met(m(area=100), t))
        self.assertTrue(all_targets_met(m(area=90), t))
        self.assertFalse(all_targets_met(m(ws=-0.01, area=90), t))

    def test_claims(self):
        c = judge_claims({"setup_slack": "improve", "area": "improve"}, m(ws=0.3, area=120), m(ws=0.1, area=100))
        self.assertTrue(c["setup_slack"]["correct"])
        self.assertFalse(c["area"]["correct"])
        self.assertEqual(c["area"]["measured"], "worsen")
        self.assertIsNone(c["power"]["correct"])


class TestSdc(unittest.TestCase):
    SDC = "create_clock -name clk -period 1.25 [get_ports clk]\n" \
          "set_input_delay 0.1 -clock clk [get_ports {a b}]\nset_output_delay 0.1 -clock clk [all_outputs]\n"

    def info(self):
        return DesignInfo(ok=True, top="t", ports=[Port("clk", "input", 1), Port("a", "input", 4),
                                                    Port("y", "output", 4)], clocks=["clk"], flop_bits=4)

    def test_read_clocks(self):
        self.assertEqual(read_clocks(self.SDC), [{"name": "clk", "period": 1.25, "port": "clk"}])

    def test_ports(self):
        self.assertEqual(referenced_ports(self.SDC), {"clk", "a", "b"})
        problems = check_against_design(self.SDC, self.info())
        self.assertTrue(any("'b'" in p or "b" in p for p in problems if "do not exist" in p))

    def test_generate(self):
        text = generate_basic_sdc(self.info(), 2.0)
        self.assertIn("create_clock -name clk -period 2.0", text)
        self.assertEqual(read_clocks(text)[0]["period"], 2.0)


class TestGuardrails(unittest.TestCase):
    def test_file_names(self):
        self.assertEqual(safe_filename("fifo.v"), "fifo.v")
        for bad in ("../x.v", "/etc/passwd", "a/b.v", "x.py", "..v"):
            with self.assertRaises(GuardrailViolation):
                safe_filename(bad)

    def test_interface(self):
        a = DesignInfo(ok=True, ports=[Port("clk", "input", 1), Port("y", "output", 8)])
        b = DesignInfo(ok=True, ports=[Port("clk", "input", 1), Port("y", "output", 9), Port("z", "output", 1)])
        diffs = interface_differences(a, b)
        self.assertEqual(len(diffs), 2)

    def test_sdc_fingerprint(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.sdc"
            f.write_text("create_clock -period 1 [get_ports clk]\n")
            h = fingerprint(f)
            check_sdc_unchanged(f, h)
            f.write_text("create_clock -period 2 [get_ports clk]\n")
            with self.assertRaises(GuardrailViolation):
                check_sdc_unchanged(f, h)


class TestHistory(unittest.TestCase):
    def test_round_folder_and_diff(self):
        with tempfile.TemporaryDirectory() as d:
            store = RunStore(Path(d) / "run")
            store.write_rtl(0, {"a.v": "module a();\nendmodule\n"})
            store.write_rtl(1, {"a.v": "module a();\n wire x;\nendmodule\n"})
            diff = store.write_diff(1, store.read_rtl(0), store.read_rtl(1), "round_00")
            self.assertIn("+ wire x;", diff)
            with self.assertRaises(GuardrailViolation):
                store.write_rtl(2, {"../../evil.v": "x"})
            rec = RoundRecord(number=1, phase="improve", parent=0, status="kept", reason="r", idea="i",
                              metrics=m().to_dict())
            store.write_record(rec)
            self.assertTrue((store.round_dir(1) / "change_note.md").exists())
            self.assertEqual(json.loads((store.round_dir(1) / "metrics.json").read_text())["status"], "kept")

    def test_hash_ignores_whitespace(self):
        self.assertEqual(rtl_hash({"a.v": "module a ( );"}), rtl_hash({"a.v": "module   a ( ) ;"[:-2] + ";"}))


class TestParsers(unittest.TestCase):
    def test_stat_old_and_new_layouts(self):
        old = "   Number of cells:                 12\n     DFF_X1          4\n     NAND2_X1        8\n\n" \
              "   Chip area for module '\\dot4': 123.456000\n"
        area, cells, types = _parse_stat(old)
        self.assertEqual((area, cells, types["DFF_X1"]), (123.456, 12, 4))

    def test_paths_and_power(self):
        rpt = textwrap.dedent("""\
            Startpoint: a_r[0]$_DFF_PN0_ (rising edge-triggered flip-flop clocked by clk)
            Endpoint: y[17]$_DFF_PN0_ (rising edge-triggered flip-flop clocked by clk)
            Path Group: clk
            Path Type: max

              Delay    Time   Description
            ---------------------------------------------------------
               0.0000   0.0000 ^ a_r[0]$_DFF_PN0_/CK (DFFR_X1)
               0.1000   0.1000 v a_r[0]$_DFF_PN0_/Q (DFFR_X1)  dot4.v:25.9-25.20
               0.0500   0.1500 ^ _1_/ZN (NAND2_X1)  dot4.v:40.19-40.70
                        0.1500   data arrival time

              -0.2000   slack (VIOLATED)
            """)
        p = parse_paths(rpt)[0]
        self.assertAlmostEqual(p.slack, -0.2)
        self.assertEqual(p.rtl_lines, ["dot4.v:25", "dot4.v:40"])
        self.assertEqual(_parse_power_mw("Total                  1.0e-04   2.0e-04   1.0e-06   3.01e-04  100.0%\n"),
                         0.301)


class TestFormalTrace(unittest.TestCase):
    def test_trace_stops_at_failing_cycle(self):
        from rtl_agent.adapters.equiv import _counterexample_trace
        log = "\n".join([
            "     1 \\in_clk                    0         0             0",
            "     1 \\in_rst                    1         1             1",
            "     1 \\trigger                   0         0             0",
            "     2 \\in_rst                    0         0             0",
            "     2 \\in_en                     1         1             1",
            "     2 \\trigger                   1         1             1",
            "     3 \\in_rst                    1         1             1",
            "     3 \\trigger                   0         0             0"])
        trace = _counterexample_trace(log)
        self.assertIn("cycle 1: rst=1", trace)
        self.assertIn("cycle 2: en=1 rst=0", trace)
        self.assertNotIn("cycle 3", trace)
        self.assertNotIn("clk", trace)
        self.assertIn("at cycle 2", trace)


class TestPrompts(unittest.TestCase):
    def test_testbench_prompt_guards_against_lagging_reference(self):
        # Real Groq runs: the LLM's reference model wrote `ref_gray <= f(ref_count)` (one cycle late)
        # and poked `ref_count = ...` from the stimulus. The prompt must warn against both.
        from rtl_agent.prompts import spec_testbench_prompt
        p = spec_testbench_prompt("a counter", "c", None, "clk", "rst")
        self.assertIn("continuous assignment", p)
        self.assertIn("uses the OLD state", p)
        self.assertIn("Never assign to reference-model variables", p)
        retry = spec_testbench_prompt("a counter", "c", None, "clk", "rst", previous_failure="ERROR x")
        self.assertIn("one cycle BEFORE", retry)


class TestPowerSetup(unittest.TestCase):
    def test_sta_script_sets_one_switching_activity_for_all_versions(self):
        # Real run (Yosys 0.52 netlist): without this, combinational power came out as 0 for the
        # original but not for the AI's version, and a smaller design was rejected for "worse power".
        from rtl_agent.adapters.sta import _tcl_script
        tcl = _tcl_script(Path("x.lib"), Path("n.v"), "top", Path("c.sdc"), 3)
        self.assertIn("set_power_activity -global -activity 0.1", tcl)
        self.assertLess(tcl.index("set_power_activity"), tcl.index("report_power"))


class TestSameCircuit(unittest.TestCase):
    def test_identical_measurements_point_to_earlier_round(self):
        # Real Groq run: three rounds with different Verilog text gave exactly the same circuit.
        from rtl_agent.controller import _same_circuit
        r0 = RoundRecord(number=0, phase="spec", parent=None, status="baseline", reason="", idea="",
                         metrics=dict(m(ws=-0.161, area=133.0, power=0.4635).to_dict(), flop_bits=16))
        r1 = RoundRecord(number=1, phase="improve", parent=0, status="invalid", reason="", idea="", metrics=None)
        same = dict(m(ws=-0.161, area=133.0, power=0.4635).to_dict(), flop_bits=16)
        other = dict(m(ws=-0.150, area=133.0, power=0.4635).to_dict(), flop_bits=16)
        self.assertEqual(_same_circuit(same, [r0, r1]), 0)
        self.assertIsNone(_same_circuit(other, [r0, r1]))


class TestSameFailure(unittest.TestCase):
    def test_identical_wrong_behaviour_points_to_earlier_round(self):
        # Real Groq run (a state-machine design): rounds 2, 4 and 5 made the same mistake in different words.
        from rtl_agent.controller import _same_failure
        bad = {"equivalence": {"ran": True, "passed": False, "detail": "59 mismatches; cycle=32 expected=0 got=1"}}
        other = {"equivalence": {"ran": True, "passed": False, "detail": "3 mismatches; cycle=7 expected=1 got=0"}}
        r2 = RoundRecord(number=2, phase="improve", parent=1, status="invalid", reason="", idea="", checks=bad)
        self.assertEqual(_same_failure(bad, [r2]), 2)
        self.assertIsNone(_same_failure(other, [r2]))
        self.assertIsNone(_same_failure({"equivalence": {"ran": True, "passed": True}}, [r2]))


class TestReportDesign(unittest.TestCase):
    def test_description_comes_from_comment_above_module(self):
        from rtl_agent.report import _header_comment
        text = "`timescale 1ns/1ps\n// line one\n//   line two\n\nmodule foo (input a);\nendmodule\n"
        self.assertEqual(_header_comment(text, "foo"), "line one\nline two")
        self.assertEqual(_header_comment("module foo(); endmodule", "foo"), "")
        self.assertEqual(_header_comment("// x\nmodule bar(); endmodule", "foo"), "")


class TestConfig(unittest.TestCase):
    def test_examples_load(self):
        for name in ("dot_product", "spec_gray_counter"):
            cfg = load_config(ROOT / "examples" / name / "config.yaml")
            self.assertTrue(cfg.library.liberty.is_absolute())

    def test_typo_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.yaml"
            lib = Path(d) / "x.lib"
            lib.write_text("library(x){}")
            p.write_text(f"project_name: t\ndesign: {{top: t, rtl_files: [{lib}]}}\nclock_period_ns: 1\n"
                         f"library: {{liberty: {lib}}}\ntargets: {{max_aera_um2: 3}}\n")
            with self.assertRaises(ConfigError):
                load_config(p)

    def test_library_found_through_rtl_agent_lib_dir(self):
        # The Docker image sets RTL_AGENT_LIB_DIR, so a config written for another machine still finds
        # its Liberty file (same file name) in the image's bundled libs folder.
        import os
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "libs").mkdir()
            (d / "libs" / "cells.lib").write_text("library(x){}")
            (d / "t.v").write_text("module t(); endmodule")
            p = d / "c.yaml"
            p.write_text("project_name: t\ndesign: {top: t, rtl_files: [t.v]}\nclock_period_ns: 1\n"
                         "library: {liberty: /somewhere/else/cells.lib}\n")
            old = os.environ.get("RTL_AGENT_LIB_DIR")
            try:
                os.environ.pop("RTL_AGENT_LIB_DIR", None)
                with self.assertRaises(ConfigError):
                    load_config(p)
                os.environ["RTL_AGENT_LIB_DIR"] = str(d / "libs")
                self.assertEqual(load_config(p).library.liberty, d / "libs" / "cells.lib")
            finally:
                if old is None:
                    os.environ.pop("RTL_AGENT_LIB_DIR", None)
                else:
                    os.environ["RTL_AGENT_LIB_DIR"] = old

    def test_library_found_in_rtl_agent_libs_folder(self):
        # Without Docker (no RTL_AGENT_LIB_DIR), "liberty: nangate45_typ.lib" in a template still finds
        # the example library downloaded into rtl-agent's own libs/ folder.
        import os
        bundled = Path(__file__).resolve().parent.parent / "libs" / "nangate45_typ.lib"
        if not bundled.exists():
            self.skipTest("run scripts/get_library.sh first")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "t.v").write_text("module t(); endmodule")
            p = d / "c.yaml"
            p.write_text("project_name: t\ndesign: {top: t, rtl_files: [t.v]}\nclock_period_ns: 1\n"
                         "library: {liberty: nangate45_typ.lib}\n")
            old = os.environ.pop("RTL_AGENT_LIB_DIR", None)
            try:
                self.assertEqual(load_config(p).library.liberty, bundled)
            finally:
                if old is not None:
                    os.environ["RTL_AGENT_LIB_DIR"] = old

    def test_missing_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.yaml"
            p.write_text("project_name: t\ndesign: {top: t, rtl_files: [nope.v]}\nclock_period_ns: 1\n"
                         "library: {liberty: nope.lib}\n")
            with self.assertRaises(ConfigError):
                load_config(p)


if __name__ == "__main__":
    unittest.main()
