"""The agent loop. Start reading here.

    A. Setup         check tools, create the run folder
    B. Start design  spec mode: LLM writes testbench + RTL;  rtl mode: user's RTL
    C. Understand    inspect ports/clocks/flip-flops, check the SDC against the design
    D. Baseline      round 0: measure everything, make the golden reference
    E. Explore       (optional) LLM proposes different architectures
    F. Improve       diagnose -> propose -> check -> measure -> keep or reject -> repeat
    G. Finish        write the report, the best RTL is in best/

The LLM only ever *proposes*. Every decision below is made by plain Python
code from measured numbers.
"""

from __future__ import annotations

import datetime as _dt
import time
from pathlib import Path
from typing import Optional

from . import prompts
from .adapters.equiv import make_golden
from .config import Config
from .evaluate import Evaluation, Evaluator
from .guardrails import GuardrailViolation
from .history import RoundRecord, RunStore, record_from_dict, record_to_dict, rtl_hash
from .llm import DailyLimitReached, LLMError, RequestTooLarge, estimate_tokens, make_provider
from .reply_parser import Proposal, parse_reply
from .scoring import Metrics, all_targets_met, compare, judge_claims, targets_status
from .sdc import check_against_design, fingerprint, generate_basic_sdc, read_clocks
from .tools import require_tool, tool_versions


class StopRun(Exception):
    """Raised to end the run early with a reason (e.g. daily LLM limit reached)."""

    def __init__(self, reason: str, status: str = "stopped"):
        super().__init__(reason)
        self.reason, self.status = reason, status


def log(msg: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


class Agent:
    def __init__(self, cfg: Config, resume_dir: Optional[Path] = None, analyze_only: bool = False):
        self.cfg = cfg
        self.analyze_only = analyze_only
        if resume_dir:
            # full path: the tools run from other folders, so a relative path would break them
            self.store = RunStore(Path(resume_dir).resolve())
            self.state = self.store.load_state()
            log(f"resuming run in {self.store.dir}")
        else:
            stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.store = RunStore(cfg.output_dir / f"{cfg.project_name}_{stamp}")
            self.state = {"status": "running", "rounds": [], "best_round": None, "llm_calls": 0,
                          "tokens": 0, "consecutive_failures": 0, "no_improve": 0, "explored": 0,
                          "last_failure": "", "stop_reason": "", "started": time.time(),
                          "elapsed_before_resume": 0.0}
        self.started = time.time()
        self.evaluator: Optional[Evaluator] = None
        self.provider = None

    # ------------------------------------------------------------------ helpers
    @property
    def rounds(self) -> list[RoundRecord]:
        return [record_from_dict(r) for r in self.state["rounds"]]

    def _save_round(self, rec: RoundRecord) -> None:
        rounds = [r for r in self.state["rounds"] if r["number"] != rec.number]
        rounds.append(record_to_dict(rec))
        self.state["rounds"] = sorted(rounds, key=lambda r: r["number"])
        self.state["llm_calls_saved"] = self.state["llm_calls"]   # calls that belong to finished rounds
        self.store.write_record(rec)
        self.store.save_state(self.state)

    def _best(self) -> Optional[RoundRecord]:
        b = self.state.get("best_round")
        return next((r for r in self.rounds if r.number == b), None) if b is not None else None

    def _next_round_number(self) -> int:
        return max([r["number"] for r in self.state["rounds"]], default=-1) + 1

    def _elapsed_min(self) -> float:
        return (self.state.get("elapsed_before_resume", 0.0) + time.time() - self.started) / 60.0

    def _llm(self):
        if self.provider is None:
            self.provider = make_provider(self.cfg.llm)
            if hasattr(self.provider, "index"):          # scripted provider: continue where we left off
                # a round interrupted half-way is thrown away and asked again, so replay from the
                # last finished round, not from the last call made
                self.provider.index = self.state.get("llm_calls_saved", self.state["llm_calls"])
        return self.provider

    def ask_llm(self, system: str, user: str, round_no: int, label: str = "") -> str:
        """One LLM call. Prompt and reply are saved in the round folder."""
        suffix = f"_{label}" if label else ""
        self.store.write_text(round_no, f"prompt{suffix}.md", f"# SYSTEM\n\n{system}\n\n# USER\n\n{user}\n")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        reply = self._call_llm(messages)
        effort = str(self.cfg.llm.extra_params.get("reasoning_effort", "")).lower()
        if reply.cut_off and effort in ("medium", "high"):
            # The hidden reasoning used up the reply's token limit before (or while) the code was
            # written. Ask the same question once more with less reasoning, so the round is not wasted.
            self.store.write_text(round_no, f"llm_reply{suffix}_cut_off.md", reply.text)
            log("    reply was cut off at the output length limit - asking again with reasoning_effort=low")
            reply = self._call_llm(messages, overrides={"reasoning_effort": "low"})
        self.store.write_text(round_no, f"llm_reply{suffix}.md", reply.text)
        self.last_reply_cut_off = reply.cut_off
        return reply.text

    def _call_llm(self, messages: list[dict], overrides: dict | None = None):
        try:
            reply = self._llm().complete(messages, overrides=overrides)
        except DailyLimitReached as exc:
            raise StopRun(f"LLM daily limit reached ({exc}). Resume tomorrow with --resume.", "paused")
        except RequestTooLarge as exc:
            raise StopRun(f"LLM request too large: {exc}")
        except LLMError as exc:
            raise StopRun(f"LLM unavailable: {exc}", "paused")
        self.state["llm_calls"] += 1
        self.state["tokens"] += reply.total_tokens
        return reply

    # ------------------------------------------------------------------ phase A
    def _setup(self) -> None:
        t = self.cfg.tools
        self.tools = {"yosys": require_tool(t.yosys), "sta": require_tool(t.sta),
                      "iverilog": require_tool(t.iverilog), "vvp": require_tool(t.vvp)}
        if "tool_versions" not in self.state:
            self.state["tool_versions"] = tool_versions(t)
            self.state["llm_provider"] = (f"{self.cfg.llm.provider}: {self.cfg.llm.model}"
                                          if self.cfg.llm.provider != "scripted"
                                          else "scripted (hand-written responses, NOT an LLM)")
        cfg_copy = self.store.dir / "config.yaml"
        if not cfg_copy.exists():
            cfg_copy.write_text(_config_as_yaml(self.cfg))

    # ------------------------------------------------------------------ phases B-D
    def _baseline(self) -> None:
        cfg = self.cfg
        n = 0
        testbench = None
        if cfg.testbench:
            testbench = {"files": [str(f) for f in cfg.testbench.files], "top": cfg.testbench.top,
                         "pass_pattern": cfg.testbench.pass_pattern, "fail_pattern": cfg.testbench.fail_pattern,
                         "timeout_s": cfg.testbench.timeout_s}

        if cfg.mode == "spec":
            from .spec_mode import build_from_spec
            log("spec mode: asking the LLM for a testbench and a first RTL version")
            files, tb_info = build_from_spec(self, testbench)
            testbench = testbench or tb_info
        else:
            files = {p.name: p.read_text() for p in cfg.design.rtl_files}
        self.state["testbench"] = testbench

        paths = self.store.write_rtl(n, files)
        # The SDC: the user's file, or a generated basic one (clearly marked).
        if cfg.sdc:
            sdc = cfg.sdc
            self.state["sdc_generated"] = False
        else:
            from .adapters import DesignFiles
            from .adapters.inspect import inspect_design
            info = inspect_design(DesignFiles(paths, cfg.design.top, cfg.design.include_dirs, cfg.design.defines),
                                  self.store.round_dir(n) / "work" / "pre_inspect", self.tools["yosys"])
            if not info.ok:
                raise StopRun(f"could not read the design: {info.error}")
            clock_port = cfg.spec.clock_port if cfg.spec else None
            sdc = self.store.dir / "generated.sdc"
            sdc.write_text(generate_basic_sdc(info, cfg.clock_period_ns, clock_port))
            self.state["sdc_generated"] = True
            log(f"no SDC given: generated a basic one at {sdc} (review it!)")
        self.state["sdc"] = str(sdc)
        self.state["sdc_hash"] = fingerprint(sdc)
        sdc_clocks = read_clocks(sdc.read_text())
        period = next((c["period"] for c in sdc_clocks if c["period"]), cfg.clock_period_ns)
        self.state["clock_period_ns"] = period

        self._make_evaluator()
        log("round 0: measuring the starting design (baseline)")
        ev = self.evaluator.evaluate(paths, self.store.round_dir(n) / "work", is_reference=True)
        rec = RoundRecord(number=n, phase="baseline" if cfg.mode == "rtl" else "spec", parent=None,
                          idea="original design" if cfg.mode == "rtl" else "first RTL generated from the spec",
                          files=sorted(files), rtl_hash=rtl_hash(files))
        if not ev.valid:
            rec.status, rec.reason = "invalid", f"{ev.reason}: {ev.detail[:500]}"
            self._save_round(rec)
            raise StopRun(f"the starting design does not pass its own checks - {ev.reason}:\n{ev.detail}")
        rec.status, rec.reason = "baseline", "starting point"
        rec.metrics, rec.checks, rec.analysis = ev.metrics.to_dict(), ev.checks, ev.analysis
        self.state["reference_info"] = ev.info.to_dict()
        self.state["sdc_problems"] = check_against_design(sdc.read_text(), ev.info)
        for p in self.state["sdc_problems"]:
            log(f"SDC warning: {p}")
        golden, err = make_golden(self.evaluator.design(paths), self.store.dir / "golden", self.tools["yosys"])
        if golden is None:
            log(f"could not create the golden reference ({err}); equivalence checks disabled")
        self.state["golden"] = str(golden) if golden else None
        self.state["best_round"] = n
        self.store.save_best(n)
        self._save_round(rec)
        self._make_evaluator()
        log("baseline: " + _metrics_line(ev.metrics))

    def _make_evaluator(self) -> None:
        tb = self.state.get("testbench")
        if tb:
            tb = dict(tb, files=[Path(f) for f in tb["files"]])
        ev = Evaluator(self.cfg, self.tools, Path(self.state["sdc"]), self.state["sdc_hash"],
                       self.state.get("clock_period_ns"), tb)
        if self.state.get("reference_info"):
            from .adapters.inspect import DesignInfo, Port
            ri = dict(self.state["reference_info"])
            ri.pop("is_sequential", None)
            ri["ports"] = [Port(**p) for p in ri["ports"]]
            ev.reference = DesignInfo(**ri)
        if self.state.get("golden"):
            ev.golden = Path(self.state["golden"])
        self.evaluator = ev

    # ------------------------------------------------------------------ prompt context
    def _context(self, task: str) -> tuple[str, str]:
        cfg, best = self.cfg, self._best()
        base = next(r for r in self.rounds if r.number == 0)
        best_files = self.store.read_rtl(best.number)
        info = self.state["reference_info"]
        clocks = best.analysis.get("clocks", [])
        status = targets_status(Metrics.from_dict(best.metrics), cfg.targets)
        unmet = [k for k, ok in status.items() if not ok]
        if "timing" in unmet:
            def ns(v):
                return "n/a" if v is None else f"{v:.3f} ns"
            unmet[unmet.index("timing")] = (f"timing (setup slack {ns(best.metrics.get('setup_ws'))}, "
                                            f"hold slack {ns(best.metrics.get('hold_ws'))})")
        rows = [("original (round 0)", base.metrics)]
        if best.number != 0:
            rows.append((f"current best (round {best.number})", best.metrics))
        history = [r.one_liner() for r in self.rounds if r.number != 0]
        ctx = {
            "design_summary": prompts.design_summary(info, best_files, clocks),
            "targets_text": prompts.targets_text(cfg.targets, cfg.priority),
            "unmet": ", ".join(unmet),
            "metrics_table": prompts.metrics_table(rows),
            "best_round": best.number,
            "paths_text": prompts.paths_text(best.analysis.get("paths", [])),
            "cells_text": prompts.cells_text(best.analysis.get("cell_types", {})),
            "warnings": self.state.get("sdc_problems", []),
            "history": history,
            "last_failure": self.state.get("last_failure", ""),
            "files_text": prompts.files_text(best_files),
            "task": task,
        }
        system = prompts.system_prompt(cfg.latency.policy, cfg.latency.max_extra_cycles)
        user = prompts.improve_prompt(ctx)
        # Keep the request inside the per-minute token limit: drop the least important parts first.
        budget = (cfg.llm.tokens_per_minute or 10 ** 9) - cfg.llm.max_output_tokens - 200
        shrink_steps = [("history", lambda c: c.update(history=c["history"][-6:])),
                        ("cells", lambda c: c.update(cells_text="(omitted to save tokens)")),
                        ("warnings", lambda c: c.update(warnings=[])),
                        ("paths", lambda c: c.update(paths_text=prompts.paths_text(
                            best.analysis.get("paths", []), limit=1))),
                        ("last_failure", lambda c: c.update(last_failure=c["last_failure"][:400]))]
        for _, step in shrink_steps:
            if estimate_tokens(system + user) <= budget:
                break
            step(ctx)
            user = prompts.improve_prompt(ctx)
        if estimate_tokens(system + user) > budget:
            raise StopRun(f"the prompt for this design needs about {estimate_tokens(system + user)} tokens, "
                          f"more than fits in llm.tokens_per_minute={cfg.llm.tokens_per_minute} with "
                          f"max_output_tokens={cfg.llm.max_output_tokens}. Use a provider/plan with higher limits.")
        return system, user

    # ------------------------------------------------------------------ one proposal
    def _try_proposal(self, phase: str, task: str) -> Optional[RoundRecord]:
        cfg, best = self.cfg, self._best()
        n = self._next_round_number()
        system, user = self._context(task)
        log(f"round {n} [{phase}]: asking the LLM for a proposal")
        reply = self.ask_llm(system, user, n)
        best_files = self.store.read_rtl(best.number)
        prop: Proposal = parse_reply(reply, sorted(best_files))
        rec = RoundRecord(number=n, phase=phase, parent=best.number, idea=prop.idea, plan=prop.plan,
                          expected=prop.expected)
        if prop.no_more_ideas:
            self.store.write_text(n, "NO_MORE_IDEAS.txt", "The LLM said it has no further ideas.\n")
            met = all_targets_met(Metrics.from_dict(best.metrics), cfg.targets)
            # Weak models sometimes give up early. While targets are unmet, one "no ideas" is not
            # accepted: the round is recorded and the LLM is asked again. Twice in a row ends the run.
            if met or self.state.get("no_ideas_streak", 0) >= 1:
                if not met:   # the run ends without reaching the targets: show that last round too
                    rec.idea = "(no proposal - the LLM said it had no more ideas)"
                    rec.status, rec.reason = "invalid", "no proposal"
                    self._save_round(rec)
                raise StopRun("the LLM has no further improvement ideas"
                              + ("" if met else " (said twice in a row while targets were unmet)"), "finished")
            self.state["no_ideas_streak"] = 1
            rec.idea = "(no proposal - the LLM said it had no more ideas)"
            unmet = [k for k, ok in targets_status(Metrics.from_dict(best.metrics), cfg.targets).items() if not ok]
            return self._reject(rec, "invalid", "no proposal",
                                f"You answered NO_MORE_IDEAS, but these targets are NOT met yet: {', '.join(unmet)}. "
                                "Do not give up. Propose one concrete, correct change - for example restructure the "
                                "logic on the critical path, precompute values one cycle early (look-ahead "
                                "registers), simplify or re-encode logic, or pipeline if the latency policy allows.")
        self.state["no_ideas_streak"] = 0
        if prop.problem:
            if getattr(self, "last_reply_cut_off", False):
                return self._reject(rec, "invalid", "reply cut off (output length limit)",
                                    "Your previous reply was cut off before it was complete: it reached the output "
                                    "length limit. Keep the next reply short: compact code, few comments, a short PLAN, "
                                    "and only the files you change.")
            return self._reject(rec, "invalid", "reply not usable", prop.problem)

        new_files = dict(best_files)
        new_files.update(prop.files)
        try:
            paths = self.store.write_rtl(n, new_files)
        except Exception as exc:                       # guardrail: bad file name
            return self._reject(rec, "invalid", "unsafe file name", str(exc))
        rec.files, rec.rtl_hash = sorted(new_files), rtl_hash(new_files)
        self.store.write_diff(n, best_files, new_files, f"round_{best.number:02d}")
        earlier = {r.rtl_hash: r.number for r in self.rounds}
        if rec.rtl_hash in earlier:
            return self._reject(rec, "duplicate", f"identical to round {earlier[rec.rtl_hash]}",
                                f"This proposal is identical to round {earlier[rec.rtl_hash]}. Try a different idea.")

        start = time.time()
        ev: Evaluation = self.evaluator.evaluate(paths, self.store.round_dir(n) / "work")
        rec.seconds = time.time() - start
        rec.checks = ev.checks
        if not ev.valid:
            twin = _same_failure(ev.checks, self.rounds)
            if twin is not None:
                # Different Verilog, but exactly the same wrong behaviour as an earlier rejected round.
                return self._reject(rec, "invalid", f"{ev.reason} - the same mistake as round {twin}",
                                    f"{ev.reason}. This is EXACTLY the same wrong behaviour as round {twin} (same "
                                    f"mismatches at the same cycles), so the idea behind it does not work. Do not try "
                                    f"it again in any form; find a different idea.\n{ev.detail}")
            return self._reject(rec, "invalid", ev.reason, f"{ev.reason}. {ev.detail}")

        rec.metrics, rec.analysis = ev.metrics.to_dict(), ev.analysis
        best_m = Metrics.from_dict(best.metrics)
        rec.claims = judge_claims(prop.expected, ev.metrics, best_m)
        verdict, objective = compare(ev.metrics, best_m, cfg.priority, cfg.targets, cfg.loop.min_improvement_pct)
        if verdict > 0:
            rec.status, rec.reason = "kept", f"better {objective} than round {best.number}"
            self.state["best_round"] = n
            self.store.save_best(n)
            self.state["no_improve"] = 0
        else:
            why = f"worse {objective}" if verdict < 0 else "no meaningful improvement"
            rec.status, rec.reason = "rejected", f"{why} than round {best.number}"
            self.state["no_improve"] += 1
        self.state["consecutive_failures"] = 0
        twin = _same_circuit(rec.metrics, self.rounds)
        if verdict <= 0 and twin is not None:
            # Different Verilog text, identical measurements: synthesis built the same circuit.
            rec.reason += f" - same results as round {twin} (most likely the same circuit)"
        self.state["last_failure"] = "" if verdict > 0 else (
            f"Round {n} ({prop.idea}) was correct but not better: {rec.reason}. Measured: "
            + _metrics_line(ev.metrics)
            + ("" if twin is None else
               f"\nIt measured EXACTLY the same as round {twin}: the synthesis tool turned both into the same "
               "circuit. Writing the same logic with different Verilog syntax does not change the circuit, "
               "because the tool already optimizes it. Change the STRUCTURE instead: what is computed, in which "
               "clock cycle, and where the registers are."))
        self._save_round(rec)
        log(f"round {n}: {rec.status.upper()} ({rec.reason}) - " + _metrics_line(ev.metrics))
        return rec

    def _reject(self, rec: RoundRecord, status: str, reason: str, detail: str) -> RoundRecord:
        rec.status, rec.reason = status, reason
        # Broken proposals count toward the "stuck" rule, not the "plateau" rule:
        # plateau means "correct designs that stopped getting better".
        self.state["consecutive_failures"] += 1
        self.state["last_failure"] = f"Round {rec.number} ({rec.idea}) was rejected: {detail[:1500]}"
        self._save_round(rec)
        log(f"round {rec.number}: {status.upper()} - {reason}")
        return rec

    # ------------------------------------------------------------------ stop rules
    def _stop_reason(self) -> Optional[str]:
        cfg, loop = self.cfg, self.cfg.loop
        best = self._best()
        if all_targets_met(Metrics.from_dict(best.metrics), cfg.targets) and not loop.optimize_beyond_targets:
            return f"success: all targets met (best = round {best.number})"
        if self.state["no_improve"] >= loop.patience:
            return f"plateau: {loop.patience} correct proposals in a row brought no meaningful improvement"
        if self.state["consecutive_failures"] >= loop.max_consecutive_failures:
            return f"stuck: {loop.max_consecutive_failures} proposals in a row were broken or duplicates"
        improve_rounds = len([r for r in self.rounds if r.phase == "improve"])
        if improve_rounds >= loop.max_rounds:
            return f"budget: reached max_rounds={loop.max_rounds}"
        if self._elapsed_min() >= loop.max_minutes:
            return f"budget: reached max_minutes={loop.max_minutes}"
        return None

    # ------------------------------------------------------------------ main entry
    def run(self) -> dict:
        from .report import write_reports
        try:
            self._setup()
            if self.state.get("best_round") is None:
                self._baseline()
            else:
                self._make_evaluator()
            if self.analyze_only:
                raise StopRun("analysis only: the design was measured, no LLM rounds were requested", "finished")

            # E. architecture exploration
            n_explore = self.cfg.explore_candidates
            while self.state["explored"] < n_explore:
                i = self.state["explored"] + 1
                ideas = [r.idea for r in self.rounds if r.phase == "explore"]
                self.state["explored"] = i
                self._try_proposal("explore", prompts.explore_task(i, n_explore, ideas))
                write_reports(self.store, self.state, self.cfg)
            if n_explore:      # exploration failures should not count against the improvement loop
                self.state["consecutive_failures"] = 0
                self.state["no_improve"] = 0

            # F. improvement loop - no fixed number of rounds; the stop rules decide.
            while True:
                reason = self._stop_reason()
                if reason:
                    self.state["stop_reason"], self.state["status"] = reason, "finished"
                    break
                self._try_proposal("improve", "Propose the next improvement toward the targets.")
                write_reports(self.store, self.state, self.cfg)
        except StopRun as stop:
            self.state["stop_reason"], self.state["status"] = stop.reason, stop.status
        except GuardrailViolation as exc:
            self.state["stop_reason"], self.state["status"] = f"guardrail: {exc}", "stopped"
        except KeyboardInterrupt:
            self.state["stop_reason"], self.state["status"] = "interrupted by the user", "paused"
        finally:
            self.state["elapsed_before_resume"] = self.state.get("elapsed_before_resume", 0.0) + \
                time.time() - self.started
            self.started = time.time()
            self.store.save_state(self.state)
            if self.state.get("rounds"):
                write_reports(self.store, self.state, self.cfg)
        log(f"stopped - {self.state['stop_reason']}")
        log(f"report: {self.store.dir / 'report.html'}")
        return self.state


def _same_circuit(metrics: dict, rounds: list) -> Optional[int]:
    """Earliest round whose measurements are identical (timing, area, power, flip-flops), or None."""
    keys = ("setup_ws", "area", "power_mw", "flop_bits")
    for r in rounds:
        if r.metrics and all(r.metrics.get(k) == metrics.get(k) for k in keys):
            return r.number
    return None

def _same_failure(checks: dict, rounds: list) -> Optional[int]:
    """Earliest rejected round whose behaviour check failed in exactly the same way, or None."""
    def signature(c: dict):
        eq = (c or {}).get("equivalence") or {}
        fr = (c or {}).get("formal") or {}
        if eq.get("ran") and eq.get("passed") is False and eq.get("detail"):
            return ("equivalence", eq["detail"])
        if fr.get("status") == "counterexample" and fr.get("detail"):
            return ("formal", fr["detail"])
        return None
    mine = signature(checks)
    if mine is None:
        return None
    for r in rounds:
        if r.status == "invalid" and signature(r.checks) == mine:
            return r.number
    return None

def _metrics_line(m: Metrics) -> str:
    def f(v, d=3):
        return "n/a" if v is None else f"{v:.{d}f}"
    return (f"setup slack {f(m.setup_ws)} ns, hold slack {f(m.hold_ws)} ns, area {f(m.area, 1)} um2, "
            f"power {f(m.power_mw, 4)} mW, flops {m.flop_bits}, extra latency {m.latency}")


def _config_as_yaml(cfg: Config) -> str:
    import yaml
    data = cfg.model_dump(mode="json")
    return "# Copy of the configuration used for this run (paths resolved).\n" + yaml.safe_dump(data, sort_keys=False)
