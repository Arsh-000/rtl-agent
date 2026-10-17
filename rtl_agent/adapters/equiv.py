"""Does a new design version behave exactly like the original?

The original RTL is the "golden reference". Two checks:

1. Random co-simulation (always, when possible)
   A testbench is GENERATED automatically from the port list. It drives the
   same random inputs into the golden design and the new design, cycle after
   cycle, and compares every output. With a flexible latency policy it
   also tries "the new design's outputs arrive L cycles later" for
   L = 1..max_extra_cycles, all in the same simulation run.

2. Bounded formal check (extra evidence, when latency is unchanged)
   Yosys builds a "miter" (both designs side by side, outputs compared) and
   asks a SAT solver: "is there ANY input sequence of up to N cycles after
   reset that makes them differ?" No answer within N cycles is a proof for
   those N cycles - not a full proof, and the report says so.

Limits (stated honestly in the report): designs with more than one clock
need a user testbench; random inputs only test what they happen to hit, so
we also report how many output bits ever toggled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..tools import run
from . import DesignFiles
from .inspect import DesignInfo, yosys_errors

GOLDEN_SUFFIX = "__golden"
EARLY_CHECK = 2      # also check "right outputs, but up to this many cycles too early / too late" (diagnosis only)


@dataclass
class EquivResult:
    ran: bool                        # False = check not possible for this design
    passed: bool = False
    latency: Optional[int] = None    # extra cycles of latency that matched
    compared: int = 0                # cycles compared
    toggled: str = ""                # "x of y output bits toggled"
    detail: str = ""                 # first mismatches, or why it did not run
    mismatches: dict = field(default_factory=dict)   # latency -> mismatch count


@dataclass
class FormalResult:
    status: str                      # "proven_bounded" | "counterexample" | "inconclusive" | "skipped"
    depth: int = 0
    detail: str = ""


def _counterexample_trace(log: str) -> str:
    """Turn Yosys' SAT trace table into 'cycle 1: rst=1 en=0 ...' lines, up to the failing cycle."""
    steps: dict[int, dict[str, str]] = {}
    fail_at = None
    for t, name, dec in re.findall(r"^\s+(\d+)\s+\\(\S+)\s+(-?\d+)\s", log, re.M):
        t = int(t)
        if name == "trigger":
            if dec != "0" and fail_at is None:
                fail_at = t
            continue
        if name.startswith("in_"):
            steps.setdefault(t, {})[name[3:]] = dec
    if not steps:
        return "(no trace available)"
    last = fail_at or max(steps)
    lines = [f"cycle {t}: " + " ".join(f"{k}={v}" for k, v in sorted(steps[t].items()) if k != "clk")
             for t in sorted(steps) if t <= last]
    lines.append(f"-> at cycle {last} the outputs differ from the original design."
                 " Note: behaviour must match on EVERY cycle, including the cycle right after reset.")
    return "\n".join(lines)


def make_golden(design: DesignFiles, workdir: Path, yosys: str) -> tuple[Optional[Path], str]:
    """Write the original design as one flat module renamed <top>__golden."""
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "golden.v"
    script = design.yosys_read_cmds() + [
        f"hierarchy -check -top {design.top}", "proc", "flatten", "opt_clean",
        f"rename {design.top} {design.top}{GOLDEN_SUFFIX}",
        f"write_verilog -noattr {out.name}",
    ]
    (workdir / "golden.ys").write_text("\n".join(script) + "\n")
    res = run([yosys, "-q", "-l", "golden.log", "-s", "golden.ys"], cwd=workdir, timeout=600)
    if not res.ok or not out.exists():
        log = (workdir / "golden.log").read_text(errors="replace") if (workdir / "golden.log").exists() else ""
        return None, yosys_errors(log + res.output)
    return out, ""


def can_cosimulate(info: DesignInfo) -> tuple[bool, str]:
    if info.internal_clocks:
        return False, "the design has internally generated clocks"
    if len(info.clocks) > 1:
        return False, f"the design has {len(info.clocks)} clocks ({', '.join(info.clocks)})"
    if any(p.direction == "inout" for p in info.ports):
        return False, "the design has inout ports"
    return True, ""


def _random_expr(width: int) -> str:
    words = (width + 31) // 32
    return "{" + ", ".join(["$random(seed)"] * words) + "}"


def generate_cosim_tb(info: DesignInfo, max_latency: int, cycles: int, seed: int) -> str:
    top = info.top
    clk = info.clocks[0] if info.clocks else None
    resets = {r["port"]: r for r in info.resets}
    inputs = [p for p in info.ports if p.direction == "input" and p.name != clk]
    outputs = [p for p in info.ports if p.direction == "output"]
    out_w = sum(p.width for p in outputs)
    L = max_latency if clk else 0
    H = L + EARLY_CHECK if clk else L   # history depth: allowed latencies + "too late" diagnosis
    lines = ["`timescale 1ns/1ps", "module rtl_agent_equiv_tb;", f"    integer seed = {seed};"]
    if clk:
        lines.append(f"    reg {clk} = 1'b0;")
    for p in inputs:
        lines.append(f"    reg [{p.width - 1}:0] {p.name} = 0;")
    for p in outputs:
        lines.append(f"    wire [{p.width - 1}:0] gold_{p.name}, cand_{p.name};")
    conn_g = ", ".join([f".{p.name}({p.name})" for p in info.ports if p.direction == "input"] +
                       [f".{p.name}(gold_{p.name})" for p in outputs])
    conn_c = ", ".join([f".{p.name}({p.name})" for p in info.ports if p.direction == "input"] +
                       [f".{p.name}(cand_{p.name})" for p in outputs])
    gold_cat = "{" + ", ".join(f"gold_{p.name}" for p in outputs) + "}"
    cand_cat = "{" + ", ".join(f"cand_{p.name}" for p in outputs) + "}"
    lines += [
        f"    {top}{GOLDEN_SUFFIX} gold ({conn_g});",
        f"    {top} cand ({conn_c});",
        f"    wire [{out_w - 1}:0] gold_all = {gold_cat};",
        f"    wire [{out_w - 1}:0] cand_all = {cand_cat};",
        f"    reg  [{out_w - 1}:0] hist [0:{H}];       // golden outputs of the last {H + 1} cycles",
        f"    integer mism [0:{L}];",
        f"    integer first_shown [0:{L}];",
        f"    reg  [{out_w - 1}:0] seen0, seen1;      // which golden output bits were ever 0 / 1",
        "    integer cyc, k, compared, last_reset, toggled;",
        "    // Diagnosis only (never a pass): does the candidate produce the right outputs too EARLY?",
        f"    reg  [{out_w - 1}:0] chist [0:{EARLY_CHECK}];   // candidate outputs of the last cycles",
        f"    integer early [0:{EARLY_CHECK}];",
        "    // Diagnosis only: does the candidate produce the right outputs too LATE (more delay than allowed)?",
        f"    integer late [0:{H}];",
    ]
    if clk:
        lines.append(f"    always #5 {clk} = ~{clk};")

    def reset_assign(active: bool) -> list[str]:
        out = []
        for name, r in resets.items():
            if any(p.name == name for p in inputs):
                level = "1'b1" if (r["active_high"] == active) else "1'b0"
                out.append(f"{name} = {level};")
        return out

    def randomize() -> list[str]:
        return [f"{p.name} = {_random_expr(p.width)};" for p in inputs if p.name not in resets]

    body = [
        "    initial begin",
        f"        for (k = 0; k <= {L}; k = k + 1) begin mism[k] = 0; first_shown[k] = 0; end",
        f"        for (k = 0; k <= {EARLY_CHECK}; k = k + 1) early[k] = 0;",
        f"        for (k = 0; k <= {H}; k = k + 1) late[k] = 0;",
        "        seen0 = 0; seen1 = 0; compared = 0; last_reset = 0;",
        *["        " + s for s in randomize()],
        *["        " + s for s in reset_assign(True)],
    ]
    if clk:
        # Reset is pulsed several times during the run (not just once), so behaviour right after a
        # reset is compared in different states - a single pulse can easily hit a lucky state.
        pulses = sorted({max(6, int(cycles * f)) for f in (0.15, 0.33, 0.5, 0.62, 0.8, 0.93)})
        assert_at = " || ".join(f"cyc == {p}" for p in pulses)
        release_at = " || ".join(["cyc == 3"] + [f"cyc == {p + 1}" for p in pulses])
        body += [
            f"        for (cyc = 0; cyc < {cycles}; cyc = cyc + 1) begin",
            f"            @(negedge {clk});",
            "            // 1) remember the golden outputs, then compare for every allowed latency",
            f"            for (k = {H}; k > 0; k = k - 1) hist[k] = hist[k-1];",
            "            hist[0] = gold_all;",
            f"            for (k = {EARLY_CHECK}; k > 0; k = k - 1) chist[k] = chist[k-1];",
            "            chist[0] = cand_all;",
            "            // latency 0 is compared on every cycle, including right after a reset;",
            "            // shifted latencies skip the few cycles around a reset (pipelines restart there)",
            "            if (cyc > 4) begin",
            "                compared = compared + 1;",
            "                if (^gold_all !== 1'bx) begin seen0 = seen0 | ~gold_all; seen1 = seen1 | gold_all; end",
            f"                for (k = 0; k <= {L}; k = k + 1) begin",
            f"                    if ((k == 0 || cyc > last_reset + k + 2) && (^hist[k] !== 1'bx) && (cand_all !== hist[k])) begin",
            "                        mism[k] = mism[k] + 1;",
            "                        if (first_shown[k] < 3) begin",
            "                            first_shown[k] = first_shown[k] + 1;",
            '                            $display("@@MISMATCH latency=%0d cycle=%0d expected=%h got=%h", k, cyc, hist[k], cand_all);',
            "                        end",
            "                    end",
            "                end",
            f"                for (k = 1; k <= {EARLY_CHECK}; k = k + 1)",
            "                    if (cyc > last_reset + k + 2 && (^gold_all !== 1'bx) && (chist[k] !== gold_all))",
            "                        early[k] = early[k] + 1;",
            f"                for (k = {L + 1}; k <= {H}; k = k + 1)",
            "                    if (cyc > last_reset + k + 2 && (^hist[k] !== 1'bx) && (cand_all !== hist[k]))",
            "                        late[k] = late[k] + 1;",
            "            end",
            "            // 2) apply new random inputs (reset held at the start, then pulsed several times)",
            *["            " + s for s in randomize()],
            f"            if ({release_at}) begin {' '.join(reset_assign(False))} end",
            f"            if ({assert_at}) begin {' '.join(reset_assign(True))} last_reset = cyc; end",
            "        end",
        ]
    else:   # purely combinational design
        body += [
            f"        for (cyc = 0; cyc < {cycles}; cyc = cyc + 1) begin",
            *["            " + s for s in randomize()],
            "            #5;",
            "            compared = compared + 1;",
            "            seen0 = seen0 | ~gold_all; seen1 = seen1 | gold_all;",
            "            if ((^gold_all !== 1'bx) && (cand_all !== gold_all)) begin",
            "                mism[0] = mism[0] + 1;",
            "                if (first_shown[0] < 3) begin first_shown[0] = first_shown[0] + 1;",
            '                    $display("@@MISMATCH latency=0 cycle=%0d expected=%h got=%h", cyc, gold_all, cand_all); end',
            "            end",
            "            #5;",
            "        end",
        ]
    body += [
        f"        toggled = 0; for (k = 0; k < {out_w}; k = k + 1) if (seen0[k] && seen1[k]) toggled = toggled + 1;",
        f'        $display("@@COVERAGE compared=%0d toggled=%0d of {out_w}", compared, toggled);',
        f'        for (k = 0; k <= {L}; k = k + 1) $display("@@RESULT latency=%0d mismatches=%0d", k, mism[k]);',
        *([f'        for (k = 1; k <= {EARLY_CHECK}; k = k + 1) $display("@@EARLY cycles=%0d mismatches=%0d", k, early[k]);',
           f'        for (k = {L + 1}; k <= {H}; k = k + 1) $display("@@LATE cycles=%0d mismatches=%0d", k, late[k]);']
          if clk else []),
        "        $finish;",
        "    end",
        "endmodule",
    ]
    order = "    // output order in messages: " + ", ".join(f"{p.name}[{p.width}]" for p in outputs)
    return "\n".join(lines + [order] + body) + "\n"


def cosimulate(golden: Path, candidate: DesignFiles, info: DesignInfo, workdir: Path,
               iverilog: str, vvp: str, max_latency: int, cycles: int, seed: int) -> EquivResult:
    ok, why = can_cosimulate(info)
    if not ok:
        return EquivResult(ran=False, detail=f"co-simulation skipped: {why}")
    workdir.mkdir(parents=True, exist_ok=True)
    tb = workdir / "equiv_tb.v"
    tb.write_text(generate_cosim_tb(info, max_latency, cycles, seed))
    cmd = [iverilog, "-g2012", "-o", "equiv.vvp", "-s", "rtl_agent_equiv_tb"] + \
        candidate.iverilog_flags() + [str(golden)] + [str(f) for f in candidate.files] + [str(tb)]
    res = run(cmd, cwd=workdir, timeout=300, log_file=workdir / "equiv_compile.log")
    if not res.ok:
        return EquivResult(ran=True, passed=False, detail="equivalence testbench did not compile:\n"
                           + "\n".join(res.output.strip().splitlines()[:15]))
    sim = run([vvp, "-n", "equiv.vvp"], cwd=workdir, timeout=900, log_file=workdir / "equiv.log")
    out = sim.output
    mism = {int(a): int(b) for a, b in re.findall(r"@@RESULT latency=(\d+) mismatches=(\d+)", out)}
    if sim.timed_out or not mism:
        return EquivResult(ran=True, passed=False, detail="equivalence simulation did not finish:\n" + out[-800:])
    cov = re.search(r"@@COVERAGE compared=(\d+) toggled=(\d+) of (\d+)", out)
    result = EquivResult(ran=True, mismatches=mism,
                         compared=int(cov.group(1)) if cov else 0,
                         toggled=f"{cov.group(2)} of {cov.group(3)}" if cov else "")
    matching = sorted(l for l, n in mism.items() if n == 0)
    if matching:
        result.passed, result.latency = True, matching[0]
    else:
        order = ", ".join(f"{p.name}[{p.width - 1}:0]" if p.width > 1 else p.name
                          for p in info.ports if p.direction == "output")
        counts = ", ".join(f"{n} with {lat} extra cycle(s)" for lat, n in sorted(mism.items()))
        shown = [l.replace("latency=0 ", "") for l in re.findall(r"@@MISMATCH (latency=0 .*)", out)]
        early = sorted(int(e) for e, n in re.findall(r"@@EARLY cycles=(\d+) mismatches=(\d+)", out) if n == "0")
        hint = ""
        if early:
            # The most useful thing we can tell the LLM: its logic is right, only the timing is off.
            e = early[0]
            hint = (f"DIAGNOSIS: your design produces exactly the original outputs, but {e} clock cycle(s) "
                    f"EARLIER. The original design's outputs appear {e} cycle(s) later (for example a registered "
                    f"output computed from the previous value of a register). Keep that delay exactly - "
                    f"do not 'fix' it; behaviour must match cycle for cycle.\n")
        late = sorted(int(e) for e, n in re.findall(r"@@LATE cycles=(\d+) mismatches=(\d+)", out) if n == "0")
        if late and not hint:
            d = late[0]
            allowed = ("no extra delay is allowed" if max_latency == 0
                       else f"at most {max_latency} extra cycle(s) are allowed")
            hint = (f"DIAGNOSIS: your design produces exactly the original outputs, but {d} clock cycle(s) "
                    f"LATER: your change added {d} cycle(s) of delay (for example an output now computed from "
                    f"the previous value of a register, or an extra register stage), and {allowed}. "
                    f"Find a different way to shorten the critical path that keeps the original timing.\n")
        result.detail = (hint + f"Outputs differ from the original design. Mismatching cycles out of "
                         f"{result.compared}: {counts}.\nFirst differences at the original timing (hex values of "
                         f"all outputs concatenated as {{{order}}}):\n" + "\n".join(shown[:4]))
    return result


def formal_check(golden: Path, candidate: DesignFiles, info: DesignInfo, workdir: Path, yosys: str,
                 depth: int, timeout_s: int) -> FormalResult:
    """Bounded equivalence proof with Yosys' built-in SAT solver."""
    workdir.mkdir(parents=True, exist_ok=True)
    top = info.top
    sat = ["sat -verify -prove-asserts -show-ports"]
    if info.is_sequential:
        sat.append(f"-seq {depth} -set-init-zero")
        # Force reset during the first cycle so the proof starts from the reset state.
        for r in info.resets:
            sat.append(f"-set-at 1 in_{r['port']} {1 if r['active_high'] else 0}")
    else:
        sat.append("-seq 1")
    sat.append(f"-timeout {timeout_s} rtl_agent_miter")
    script = [f"read_verilog -sv {golden}"] + candidate.yosys_read_cmds() + [
        "hierarchy -check",          # no -top here: it would delete the golden module
        "proc", "memory", "flatten", "opt_clean",
        f"miter -equiv -flatten -make_assert -ignore_gold_x {top}{GOLDEN_SUFFIX} {top} rtl_agent_miter",
        "hierarchy -top rtl_agent_miter",
        "async2sync", "opt -fast",
        " ".join(sat),
    ]
    (workdir / "formal.ys").write_text("\n".join(script) + "\n")
    res = run([yosys, "-l", "formal.log", "-s", "formal.ys"], cwd=workdir, timeout=timeout_s + 120)
    log = (workdir / "formal.log").read_text(errors="replace") if (workdir / "formal.log").exists() else res.output
    d = depth if info.is_sequential else 1
    if "SUCCESS!" in log:
        return FormalResult("proven_bounded", d, f"no difference possible in the first {d} cycle(s) after reset"
                            if info.is_sequential else "proven equivalent for all inputs (combinational)")
    if "TIMEOUT" in log or res.timed_out:
        return FormalResult("inconclusive", d, f"SAT solver gave up after {timeout_s}s")
    if "FAIL!" in log:
        if info.is_sequential and not info.resets:
            return FormalResult("inconclusive", d, "difference found from an all-zero start state, but the "
                                "design has no reset, so that state may be unreachable")
        return FormalResult("counterexample", d, "the solver found an input sequence where the outputs differ "
                            "from the original design:\n" + _counterexample_trace(log))
    return FormalResult("inconclusive", d, "formal check could not run:\n" + yosys_errors(log)[:600])
