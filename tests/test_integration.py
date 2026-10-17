"""Integration tests with the real EDA tools (skipped if they are not installed).

These run real Yosys / OpenSTA / Icarus on the example designs and exercise
the whole agent: offline scripted runs, the HTTP LLM client against a fake
server, guardrails, pausing on the daily limit and resuming.

Run:  python -m unittest tests.test_integration -v
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from rtl_agent.adapters import DesignFiles
from rtl_agent.adapters.equiv import cosimulate, formal_check, make_golden
from rtl_agent.adapters.inspect import inspect_design
from rtl_agent.config import load_config
from rtl_agent.controller import Agent
from rtl_agent.llm.base import DailyLimitReached, LLMProvider, LLMReply
from rtl_agent.tools import find_tool

ROOT = Path(__file__).resolve().parents[1]
TOOLS = {n: find_tool(n) for n in ("yosys", "sta", "iverilog", "vvp")}
HAVE_TOOLS = all(TOOLS.values()) and (ROOT / "libs" / "nangate45_typ.lib").exists()


def copy_example(name: str, tmp: Path) -> Path:
    """Copy an example (and the library) into a temp folder so tests never touch the originals."""
    shutil.copytree(ROOT / "examples" / name, tmp / "examples" / name)
    (tmp / "libs").mkdir(exist_ok=True)
    lib = tmp / "libs" / "nangate45_typ.lib"
    if not lib.exists():
        os.symlink(ROOT / "libs" / "nangate45_typ.lib", lib)
    return tmp / "examples" / name / "config.yaml"


def scripted_texts(name: str) -> list[str]:
    return [p.read_text() for p in sorted((ROOT / "examples" / name / "scripted").glob("*.md"))]


class ListProvider(LLMProvider):
    """Returns prepared replies; can raise DailyLimitReached at a chosen call."""
    def __init__(self, replies, fail_at=None, on_call=None):
        self.replies, self.fail_at, self.on_call, self.calls = list(replies), fail_at, on_call, 0

    def complete(self, messages, max_tokens=None, overrides=None):
        self.calls += 1
        self.overrides = getattr(self, "overrides", []) + [overrides]
        if self.on_call:
            self.on_call(self.calls)
        if self.fail_at == self.calls:
            raise DailyLimitReached("test: daily limit")
        text = self.replies.pop(0) if self.replies else "NO_MORE_IDEAS"
        return LLMReply(text=text, prompt_tokens=10, completion_tokens=10)


@unittest.skipUnless(HAVE_TOOLS, "EDA tools or libs/nangate45_typ.lib not installed")
class TestAdaptersOnExamples(unittest.TestCase):
    def test_fifo_inspection_finds_memory_clock_reset(self):
        with tempfile.TemporaryDirectory() as d:
            info = inspect_design(DesignFiles([ROOT / "examples/sync_fifo/rtl/sync_fifo.v"], "sync_fifo"),
                                  Path(d), TOOLS["yosys"])
        self.assertTrue(info.ok)
        self.assertEqual(info.clocks, ["clk"])
        self.assertEqual(info.memory_bits, 128)
        self.assertEqual(info.resets, [{"port": "rst_n", "active_high": False, "kind": "async"}])

    def test_latency_policy_in_cosimulation(self):
        """The pipelined dot4 matches the original only when one extra cycle is allowed."""
        import re
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            orig = DesignFiles([ROOT / "examples/dot_product/rtl/dot4.v"], "dot4")
            golden, err = make_golden(orig, d / "g", TOOLS["yosys"])
            self.assertIsNotNone(golden, err)
            text = (ROOT / "examples/dot_product/scripted/002.md").read_text()
            (d / "p").mkdir()
            (d / "p" / "dot4.v").write_text(re.search(r"```verilog\n(.*?)```", text, re.S).group(1))
            cand = DesignFiles([d / "p" / "dot4.v"], "dot4")
            info = inspect_design(cand, d / "i", TOOLS["yosys"])
            fixed = cosimulate(golden, cand, info, d / "e0", TOOLS["iverilog"], TOOLS["vvp"], 0, 500, 1)
            flexible = cosimulate(golden, cand, info, d / "e2", TOOLS["iverilog"], TOOLS["vvp"], 2, 500, 1)
        self.assertFalse(fixed.passed)
        self.assertTrue(flexible.passed)
        self.assertEqual(flexible.latency, 1)


    def test_reset_behaviour_difference_is_caught(self):
        """Real case from a Groq run: the LLM also reset the output register. Outputs then differ
        only in the cycle right after a reset. Both the co-simulation and the formal check must
        catch it, and the formal check must explain it with an input trace."""
        data = ROOT / "tests" / "data" / "gray_reset"
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            golden, err = make_golden(DesignFiles([data / "original.v"], "gray_counter"), d / "g", TOOLS["yosys"])
            self.assertIsNotNone(golden, err)
            changed = DesignFiles([data / "resets_output.v"], "gray_counter")
            info = inspect_design(changed, d / "i", TOOLS["yosys"])
            sim = cosimulate(golden, changed, info, d / "e", TOOLS["iverilog"], TOOLS["vvp"], 0, 2000, 1)
            same = cosimulate(golden, DesignFiles([data / "original.v"], "gray_counter"), info, d / "e2",
                              TOOLS["iverilog"], TOOLS["vvp"], 0, 2000, 1)
            fr = formal_check(golden, changed, info, d / "f", TOOLS["yosys"], 10, 60)
        self.assertFalse(sim.passed)
        self.assertTrue(same.passed)
        self.assertEqual(fr.status, "counterexample")
        self.assertIn("rst=1", fr.detail)
        self.assertIn("the outputs differ", fr.detail)


    def test_too_early_outputs_are_diagnosed(self):
        """Real case from a Groq run: the LLM kept removing a one-cycle output delay. The comparison
        must reject it AND say 'right outputs, but 1 cycle earlier'."""
        data = ROOT / "tests" / "data" / "gray_lag"
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            golden, err = make_golden(DesignFiles([data / "original.v"], "gray_counter"), d / "g", TOOLS["yosys"])
            early = DesignFiles([data / "one_cycle_early.v"], "gray_counter")
            info = inspect_design(early, d / "i", TOOLS["yosys"])
            res = cosimulate(golden, early, info, d / "e", TOOLS["iverilog"], TOOLS["vvp"], 2, 2000, 1)
        self.assertFalse(res.passed)                        # even flexible latency never allows EARLIER outputs
        self.assertIn("1 clock cycle(s) EARLIER", res.detail)

    def test_registered_output_must_stay_registered(self):
        """Real case from a Groq run: the LLM moved the Gray conversion AFTER the flip-flops. The values at
        every clock edge are identical (co-simulation passes), so only a structural check can catch it."""
        from rtl_agent.guardrails import registered_output_differences
        data = ROOT / "tests" / "data" / "gray_regout"
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            ref = inspect_design(DesignFiles([data / "registered.v"], "gray_counter"), d / "r", TOOLS["yosys"])
            comb_files = DesignFiles([data / "combinational.v"], "gray_counter")
            comb = inspect_design(comb_files, d / "c", TOOLS["yosys"])
            self.assertEqual(ref.registered_outputs, ["gray"])
            self.assertEqual(comb.registered_outputs, [])
            self.assertTrue(registered_output_differences(ref, comb))
            self.assertFalse(registered_output_differences(ref, ref))
            # behaviour alone would have accepted it
            golden, _ = make_golden(DesignFiles([data / "registered.v"], "gray_counter"), d / "g", TOOLS["yosys"])
            eq = cosimulate(golden, comb_files, comb, d / "e", TOOLS["iverilog"], TOOLS["vvp"], 0, 2000, 1)
            self.assertTrue(eq.passed)

    def test_too_late_outputs_are_diagnosed(self):
        """The opposite real case: the reference is on time and the LLM's change adds a cycle of delay
        (it computed the output from the register's old value). With fixed latency it must be rejected
        with 'right outputs, but 1 cycle later'."""
        data = ROOT / "tests" / "data" / "gray_lag"
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            golden, err = make_golden(DesignFiles([data / "one_cycle_early.v"], "gray_counter"), d / "g",
                                      TOOLS["yosys"])
            late = DesignFiles([data / "original.v"], "gray_counter")
            info = inspect_design(late, d / "i", TOOLS["yosys"])
            res = cosimulate(golden, late, info, d / "e", TOOLS["iverilog"], TOOLS["vvp"], 0, 2000, 1)
            self.assertFalse(res.passed)
            self.assertIn("1 clock cycle(s) LATER", res.detail)
            self.assertIn("no extra delay is allowed", res.detail)
            # with flexible latency the same design is accepted, as 1 extra cycle
            ok = cosimulate(golden, late, info, d / "e2", TOOLS["iverilog"], TOOLS["vvp"], 1, 2000, 1)
            self.assertTrue(ok.passed)
            self.assertEqual(ok.latency, 1)


@unittest.skipUnless(HAVE_TOOLS, "EDA tools or libs/nangate45_typ.lib not installed")
class TestAgentRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_agent(self, cfg_path, provider=None, resume=None, scripted=False):
        cfg = load_config(cfg_path)
        if scripted:
            cfg.llm.provider = "scripted"
        agent = Agent(cfg, resume_dir=resume)
        if provider is not None:
            agent.provider = provider
        return agent, agent.run()

    def test_offline_fsm_run(self):
        agent, state = self.run_agent(copy_example("seq_detector", self.tmp), scripted=True)
        statuses = [r["status"] for r in state["rounds"]]
        self.assertEqual(statuses, ["baseline", "kept", "kept"])
        self.assertIn("no further", state["stop_reason"])
        self.assertTrue((agent.store.dir / "report.html").exists())
        self.assertTrue((agent.store.dir / "best" / "seq_detector.v").exists())
        self.assertTrue(state["sdc_generated"])
        # every round folder has the promised files
        for n in (1, 2):
            for f in ("diff_vs_parent.patch", "metrics.json", "change_note.md", "prompt.md", "llm_reply.md"):
                self.assertTrue((agent.store.round_dir(n) / f).exists(), f)

    def test_fifo_strict_equivalence_rejects_testbench_passing_change(self):
        _, state = self.run_agent(copy_example("sync_fifo", self.tmp), scripted=True)
        r = {x["number"]: x for x in state["rounds"]}
        self.assertEqual(r[1]["status"], "invalid")
        self.assertTrue(r[1]["checks"]["testbench"]["passed"])          # the user's testbench missed it...
        self.assertFalse(r[1]["checks"]["equivalence"]["passed"])       # ...the golden comparison did not
        self.assertEqual(r[2]["status"], "kept")                        # look-ahead head register fixes timing
        self.assertEqual(state["best_round"], 2)

    def test_http_provider_drives_the_agent(self):
        """Full run through the real OpenAI-compatible client, against a fake local server."""
        replies = scripted_texts("seq_detector")

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                text = replies.pop(0) if replies else "NO_MORE_IDEAS"
                data = json.dumps({"choices": [{"message": {"content": text}}],
                                   "usage": {"prompt_tokens": 500, "completion_tokens": 300}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            cfg = load_config(copy_example("seq_detector", self.tmp))
            cfg.llm.base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
            state = Agent(cfg).run()
        finally:
            server.shutdown()
        self.assertEqual([r["status"] for r in state["rounds"]], ["baseline", "kept", "kept"])
        self.assertEqual(state["tokens"], 3 * 800)

    def test_sdc_change_stops_the_run(self):
        cfg_path = copy_example("dot_product", self.tmp)
        sdc = cfg_path.parent / "constraints" / "dot4.sdc"

        def tamper(call):
            sdc.write_text(sdc.read_text().replace("-period 1.2", "-period 5.0"))

        _, state = self.run_agent(cfg_path, provider=ListProvider(scripted_texts("dot_product"), on_call=tamper))
        self.assertEqual(state["status"], "stopped")
        self.assertIn("SDC file", state["stop_reason"])

    def test_pause_on_daily_limit_then_resume(self):
        cfg_path = copy_example("seq_detector", self.tmp)
        replies = scripted_texts("seq_detector")
        agent, state = self.run_agent(cfg_path, provider=ListProvider(replies, fail_at=2))
        self.assertEqual(state["status"], "paused")
        self.assertEqual([r["status"] for r in state["rounds"]], ["baseline", "kept"])
        _, state2 = self.run_agent(cfg_path, provider=ListProvider(replies[1:]), resume=agent.store.dir)
        self.assertEqual(state2["status"], "finished")
        self.assertEqual([r["status"] for r in state2["rounds"]], ["baseline", "kept", "kept"])
        self.assertEqual(state2["best_round"], 2)

    def test_spec_mode_replaces_a_wrong_testbench(self):
        """All RTL attempts fail against a wrong testbench -> a new testbench is requested,
        and the RTL we already have is re-checked against it (no extra RTL call needed)."""
        cfg_path = copy_example("spec_gray_counter", self.tmp)
        cfg = load_config(cfg_path)
        cfg.explore.candidates = 0
        cfg.spec.rtl_attempts = 2
        good_tb, good_rtl = scripted_texts("spec_gray_counter")[:2]
        wrong_tb = good_tb.replace("if (rst)      ref_count <= 8'd0;", "if (rst)      ref_count <= 8'd1;")
        self.assertNotEqual(wrong_tb, good_tb)
        provider = ListProvider([wrong_tb, good_rtl, good_rtl, good_tb])   # then: NO_MORE_IDEAS
        agent = Agent(cfg)
        agent.provider = provider
        state = agent.run()
        self.assertEqual(state["rounds"][0]["status"], "baseline", state.get("stop_reason"))
        # tb, rtl, rtl-fix, new tb, then two improve calls (timing is unmet, so a single
        # NO_MORE_IDEAS is not accepted - the LLM is asked once more)
        self.assertEqual(provider.calls, 6)
        self.assertTrue((agent.store.dir / "spec" / "testbench_2").is_dir())

    def test_cut_off_reply_is_asked_again_with_less_reasoning(self):
        """Real Groq runs: with reasoning_effort=medium some replies were empty because the hidden
        reasoning used the whole output limit. The same question is asked again with 'low'."""
        class CutOffFirst(ListProvider):
            def complete(self, messages, max_tokens=None, overrides=None):
                if not getattr(self, "did_cut", False):
                    self.did_cut = True
                    self.overrides = [overrides]
                    return LLMReply(text="", prompt_tokens=10, completion_tokens=10, cut_off=True)
                return super().complete(messages, max_tokens, overrides)
        cfg_path = copy_example("dot_product", self.tmp)
        provider = CutOffFirst(scripted_texts("dot_product"))
        agent, state = self.run_agent(cfg_path, provider=provider)
        self.assertEqual(provider.overrides[:2], [None, {"reasoning_effort": "low"}])
        r1 = state["rounds"][1]
        self.assertNotIn("cut off", r1["reason"])
        self.assertTrue((agent.store.round_dir(1) / "llm_reply_cut_off.md").exists())

    def test_no_more_ideas_is_not_accepted_while_targets_unmet(self):
        """One NO_MORE_IDEAS while timing is unmet -> recorded, asked again; twice in a row -> stop."""
        _, state = self.run_agent(copy_example("dot_product", self.tmp),
                                  provider=ListProvider(["NO_MORE_IDEAS", scripted_texts("dot_product")[1]]))
        self.assertEqual([r["status"] for r in state["rounds"]], ["baseline", "invalid", "kept"])
        self.assertIn("success", state["stop_reason"])
        shutil.rmtree(self.tmp / "runs", ignore_errors=True)
        _, state = self.run_agent(self.tmp / "examples" / "dot_product" / "config.yaml",
                                  provider=ListProvider(["NO_MORE_IDEAS", "NO_MORE_IDEAS"]))
        self.assertEqual([r["status"] for r in state["rounds"]], ["baseline", "invalid", "invalid"])
        self.assertIn("twice in a row", state["stop_reason"])

    def test_custom_sta_command(self):
        cfg_path = copy_example("seq_detector", self.tmp)
        cfg = load_config(cfg_path)
        cfg.custom_commands.sta = ('echo \'{"setup_ws": 0.5, "setup_tns": 0, "hold_ws": 0.1, "power_mw": 1.25}\''
                                   ' > "$OUT_DIR/sta_metrics.json"')
        state = Agent(cfg, analyze_only=True).run()
        m = state["rounds"][0]["metrics"]
        self.assertEqual((m["setup_ws"], m["power_mw"]), (0.5, 1.25))


if __name__ == "__main__":
    unittest.main()
