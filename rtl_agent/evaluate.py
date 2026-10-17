"""Evaluate one design version: is it correct, and how good is it?

This is the "judge" part of the agent. The same pipeline runs for the
baseline, for every architecture candidate and for every improvement
proposal, so all versions are measured in exactly the same way:

    1. SDC unchanged?            (guardrail)
    2. compiles?                 Icarus Verilog
    3. same interface?           Yosys inspection vs. the original
    4. still correct?            testbench and/or co-simulation vs. golden,
                                 plus a bounded formal check when possible
    5. synthesize                Yosys  -> area, cells
    6. timing + power            OpenSTA -> slack, critical paths, power

A version that fails steps 1-4 is never measured or kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .adapters import DesignFiles
from .adapters.equiv import cosimulate, formal_check
from .adapters.inspect import DesignInfo, inspect_design
from .adapters.simulate import compile_check, run_testbench
from .adapters.sta import StaResult, run_sta
from .adapters.synth import SynthResult, synthesize
from .guardrails import check_sdc_unchanged, interface_differences, registered_output_differences
from .scoring import Metrics


@dataclass
class Evaluation:
    valid: bool
    reason: str = ""                 # short reason (shown in the history table)
    detail: str = ""                 # longer detail (fed back to the LLM when it failed)
    metrics: Optional[Metrics] = None
    checks: dict = field(default_factory=dict)
    analysis: dict = field(default_factory=dict)
    info: Optional[DesignInfo] = None
    synth: Optional[SynthResult] = None
    sta: Optional[StaResult] = None


class Evaluator:
    def __init__(self, cfg, tools: dict, sdc: Path, sdc_hash: str, clock_period_ns: Optional[float],
                 testbench: Optional[dict] = None):
        self.cfg = cfg
        self.tools = tools                    # name -> full path of the program
        self.sdc, self.sdc_hash = sdc, sdc_hash
        self.clock_period_ns = clock_period_ns
        self.testbench = testbench            # {"files": [...], "top": ..., "pass_pattern": ..., ...}
        self.reference: Optional[DesignInfo] = None   # interface of the original design
        self.golden: Optional[Path] = None            # flattened original design for equivalence

    def design(self, files: list[Path]) -> DesignFiles:
        d = self.cfg.design
        return DesignFiles(files=files, top=d.top, include_dirs=d.include_dirs, defines=d.defines)

    def evaluate(self, files: list[Path], workdir: Path, is_reference: bool = False) -> Evaluation:
        cfg, t = self.cfg, self.tools
        workdir.mkdir(parents=True, exist_ok=True)
        design = self.design(files)
        checks: dict = {}

        # 1. guardrail: the timing rules must be exactly the ones we started with
        check_sdc_unchanged(self.sdc, self.sdc_hash)

        # 2. does it compile?
        comp = compile_check(design, workdir / "compile", t["iverilog"])
        if not comp.ok:
            return Evaluation(False, "does not compile", "Compiler errors:\n" + comp.detail)

        # 3. what is in it, and is the interface unchanged?
        info = inspect_design(design, workdir / "inspect", t["yosys"])
        if not info.ok:
            return Evaluation(False, "Yosys could not read the design", info.error)
        if self.reference is not None and not is_reference:
            diffs = interface_differences(self.reference, info)
            if diffs:
                return Evaluation(False, "interface changed", "The top-level ports must not change:\n- "
                                  + "\n- ".join(diffs), info=info)
            if cfg.correctness.keep_registered_outputs:
                regs = registered_output_differences(self.reference, info)
                if regs:
                    return Evaluation(False, "registered output became combinational",
                                      "- " + "\n- ".join(regs) + "\nKeep these outputs registered: each output bit "
                                      "must come straight from a flip-flop (`always @(posedge clk) out <= ...`), with "
                                      "no logic after it. A registered output is glitch-free and gives the next block "
                                      "a full clock cycle, so it is part of the interface. Do not move logic after the "
                                      "registers; shorten the logic INTO them instead (for example compute the next "
                                      "output value from the next state, one cycle ahead).", info=info)

        # 4. is it still correct?
        latency = 0
        verified_by = []
        # The comparison with the original runs FIRST: when it fails it can say precisely what is
        # wrong (e.g. "right outputs, but one cycle early"), which helps the LLM far more than raw
        # testbench errors. The testbench runs after it.
        use_equiv = (cfg.correctness.equivalence != "off" and self.golden is not None and not is_reference)
        if use_equiv:
            max_lat = cfg.latency.max_extra_cycles if cfg.latency.policy == "flexible" else 0
            eq = cosimulate(self.golden, design, info, workdir / "equiv", t["iverilog"], t["vvp"],
                            max_lat, cfg.correctness.cycles, cfg.correctness.seed)
            checks["equivalence"] = {"ran": eq.ran, "passed": eq.passed, "latency": eq.latency,
                                     "compared_cycles": eq.compared, "outputs_toggled": eq.toggled,
                                     "detail": eq.detail[-1500:]}
            if eq.ran and not eq.passed:
                if self.testbench:        # for the record: would the user's testbench have noticed?
                    tb = self.testbench
                    sim = run_testbench(design, tb["files"], tb["top"], workdir / "testbench", t["iverilog"],
                                        t["vvp"], tb["pass_pattern"], tb["fail_pattern"], tb["timeout_s"])
                    checks["testbench"] = {"passed": sim.passed, "detail": sim.detail[-1500:]}
                return Evaluation(False, "behaviour differs from the original", eq.detail, checks=checks, info=info)
            if not eq.ran and cfg.correctness.equivalence == "on":
                return Evaluation(False, "equivalence check could not run", eq.detail, checks=checks, info=info)
            if eq.ran:
                latency = eq.latency or 0
                verified_by.append("co-simulation vs original")

            if cfg.correctness.formal != "off" and latency == 0:
                fr = formal_check(self.golden, design, info, workdir / "formal", t["yosys"],
                                  cfg.correctness.formal_depth, cfg.correctness.formal_timeout_s)
                checks["formal"] = {"status": fr.status, "depth": fr.depth, "detail": fr.detail}
                if fr.status == "counterexample":
                    return Evaluation(False, "formal check found a difference",
                                      "A bounded formal check (starting from reset) found an input sequence "
                                      f"where the outputs differ. {fr.detail}", checks=checks, info=info)
                if fr.status == "proven_bounded":
                    verified_by.append("bounded formal")

        if self.testbench:
            tb = self.testbench
            sim = run_testbench(design, tb["files"], tb["top"], workdir / "testbench", t["iverilog"], t["vvp"],
                                tb["pass_pattern"], tb["fail_pattern"], tb["timeout_s"])
            checks["testbench"] = {"passed": sim.passed, "detail": sim.detail[-1500:]}
            if not sim.passed:
                return Evaluation(False, "testbench failed", "Testbench output:\n" + sim.detail, checks=checks,
                                  info=info)
            verified_by.append("testbench")

        if not is_reference and not verified_by:
            return Evaluation(False, "no correctness check possible",
                              "Neither a testbench nor the automatic equivalence check could run for this design, "
                              "so the change cannot be trusted. Provide a testbench in the config.",
                              checks=checks, info=info)
        checks["verified_by"] = verified_by

        # 5. synthesis
        syn = synthesize(design, cfg.library.liberty, workdir / "synth", t["yosys"], self.clock_period_ns,
                         cfg.library.tie_hi, cfg.library.tie_lo, cfg.custom_commands.synth)
        if not syn.ok:
            return Evaluation(False, "synthesis failed", syn.error, checks=checks, info=info)

        # 6. timing and power
        sta = run_sta(syn.netlist, cfg.design.top, cfg.library.liberty, self.sdc, workdir / "sta", t["sta"],
                      custom_cmd=cfg.custom_commands.sta)
        if not sta.ok:
            return Evaluation(False, "timing analysis failed", sta.error, checks=checks, info=info, synth=syn)

        metrics = Metrics(setup_ws=sta.setup_ws, setup_tns=sta.setup_tns, hold_ws=sta.hold_ws,
                          area=syn.area, power_mw=sta.power_mw, flop_bits=info.flop_bits,
                          cell_count=syn.cell_count, latency=latency)
        analysis = {"paths": [p.__dict__ for p in sta.paths], "cell_types": syn.cell_types,
                    "sta_warnings": sta.warnings, "clocks": sta.clocks,
                    "memory_bits": info.memory_bits}
        return Evaluation(True, "measured", metrics=metrics, checks=checks, analysis=analysis,
                          info=info, synth=syn, sta=sta)
