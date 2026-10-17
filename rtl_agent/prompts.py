"""Every text the LLM ever sees is built here.

Keeping all prompts in one file makes it easy to read exactly what the
model is told, and to tune it. The prompts are compact on purpose: free
LLM tiers count every token (see "Using Groq's free LLM" in the design doc).
"""

from __future__ import annotations

REPLY_FORMAT = """Reply format (use exactly these headings):
### PLAN
2-5 sentences: what limits the design now, and what you will change.
### CHANGE_NOTE
{"idea": "<one line>", "expected": {"setup_slack": "improve|worsen|same", "area": "improve|worsen|same", "power": "improve|worsen|same"}, "latency_change": <extra clock cycles, 0 if none>}
### FILE: <file name>
```verilog
<the COMPLETE new content of that file>
```
Repeat a FILE section for each file you change (whole file, not a diff). Omit files you do not change.
Keep the existing file names; use a new file name only for a new module.
Keep the reply short (it has a length limit): no header comment blocks, only brief comments in the code.
If you are confident no further useful improvement exists, reply with only: NO_MORE_IDEAS"""


def latency_rule(policy: str, max_extra: int) -> str:
    if policy == "fixed":
        return ("Latency is FIXED: outputs must match the original design on every clock cycle. "
                "Do NOT add pipeline stages or otherwise delay any output.")
    return (f"Latency is FLEXIBLE: you may add up to {max_extra} clock cycle(s) of latency (for example "
            "pipeline registers). If you do, delay ALL outputs (including valid/flag outputs) by the same "
            "number of cycles, and keep reset behaviour the same.")


def system_prompt(policy: str, max_extra: int) -> str:
    return f"""You are an expert digital design (RTL) engineer working inside an automated optimization loop.

How your proposals are judged (you cannot influence this):
- Each proposal is compiled, checked for identical behaviour (testbench and/or comparison with the original design),
  synthesized with Yosys to a standard-cell library, and timed with OpenSTA against a FIXED SDC.
- Only the tools' measurements count. Broken or worse proposals are rejected and recorded.

Hard rules:
1. Keep the top module name and its ports exactly the same (names, directions, widths).
   The CURRENT design's cycle-by-cycle behaviour is the reference: keep it exactly, even where it looks unusual
   (for example an output that appears one cycle later than you might expect). Do not "fix" behaviour.
   Outputs that are registered (driven directly by flip-flops) must stay registered: no logic after those flip-flops.
2. Write synthesizable Verilog-2005 (simple SystemVerilog is fine). No delays (#), no $display, no initial blocks in RTL.
3. Keep the reset behaviour.
4. {latency_rule(policy, max_extra)}
5. The clock period and SDC cannot change. Improve the logic itself.
6. Do not repeat an idea from the history unless you say what is different this time.
7. Rewriting the same logic in different Verilog syntax usually gives the SAME circuit after synthesis (the tool
   already optimizes it). Change the structure instead: what is computed, in which clock cycle, where registers sit.

{REPLY_FORMAT}"""


SPEC_SYSTEM = """You are an expert digital design engineer. You write clear, correct, synthesizable Verilog and
careful self-checking testbenches. Your output is compiled and simulated automatically, so follow the
requested reply format exactly."""


def _fmt(v, digits=3):
    return "n/a" if v is None else f"{v:.{digits}f}"


def metrics_table(rows: list[tuple[str, dict]]) -> str:
    out = ["| version | setup WS (ns) | setup TNS (ns) | hold WS (ns) | area (um2) | power (mW) | flop bits | extra latency |",
           "|---|---|---|---|---|---|---|---|"]
    for label, m in rows:
        out.append(f"| {label} | {_fmt(m.get('setup_ws'))} | {_fmt(m.get('setup_tns'))} | {_fmt(m.get('hold_ws'))} | "
                   f"{_fmt(m.get('area'), 1)} | {_fmt(m.get('power_mw'), 4)} | {m.get('flop_bits', 'n/a')} | "
                   f"{m.get('latency', 0)} |")
    return "\n".join(out)


def design_summary(info: dict, files: dict[str, str], clocks: list[dict]) -> str:
    ports = ", ".join(f"{p['name']}({p['direction'][:3]},{p['width']})" for p in info["ports"])
    file_list = ", ".join(f"{n} ({len(t.splitlines())} lines)" for n, t in files.items())
    clk = ", ".join(f"{c['name']} period {c['period']} ns" for c in clocks) or "none"
    resets = ", ".join(f"{r['port']} ({'active-high' if r['active_high'] else 'active-low'}, {r['kind']})"
                       for r in info.get("resets", [])) or "none detected"
    return (f"Top module: {info['top']}\nFiles: {file_list}\nPorts: {ports}\n"
            f"Clocks (from the SDC): {clk}\nResets: {resets}\n"
            f"Flip-flop bits: {info.get('flop_bits', 0)}, memory bits: {info.get('memory_bits', 0)}")


def targets_text(targets, priority: list[str]) -> str:
    t = [f"setup slack >= {targets.min_setup_slack_ns} ns"]
    if targets.min_hold_slack_ns is not None:
        t.append(f"hold slack >= {targets.min_hold_slack_ns} ns")
    if targets.max_area_um2 is not None:
        t.append(f"area <= {targets.max_area_um2} um2")
    if targets.max_power_mw is not None:
        t.append(f"power <= {targets.max_power_mw} mW")
    return f"Targets: {', '.join(t)}\nPriority when comparing versions: {' > '.join(priority)}"


def paths_text(paths: list[dict], limit: int = 3) -> str:
    if not paths:
        return "(no constrained setup paths reported)"
    out = []
    for i, p in enumerate(paths[:limit], 1):
        src = ", ".join(p.get("rtl_lines", [])[:8]) or "n/a"
        out.append(f"{i}. slack {_fmt(p.get('slack'))} ns, {p.get('cells', 0)} gates: "
                   f"{p['startpoint']} -> {p['endpoint']}\n   RTL lines on this path: {src}")
    return "\n".join(out)


def cells_text(cell_types: dict, limit: int = 10) -> str:
    top = sorted(cell_types.items(), key=lambda kv: -kv[1])[:limit]
    return ", ".join(f"{n} x{c}" for n, c in top) or "n/a"


def files_text(files: dict[str, str]) -> str:
    return "\n".join(f"### FILE: {name}\n```verilog\n{text.rstrip()}\n```" for name, text in files.items())


def improve_prompt(ctx: dict) -> str:
    """ctx is assembled by the controller; every value is already measured."""
    parts = [
        "## Design", ctx["design_summary"], "",
        "## " + ctx["targets_text"], f"Unmet targets right now: {ctx['unmet'] or 'none'}", "",
        "## Measurements", ctx["metrics_table"], "",
        f"## Worst setup paths of the current best version (round {ctx['best_round']})", ctx["paths_text"], "",
        "## Gates used by the current best version", ctx["cells_text"], "",
    ]
    if ctx.get("warnings"):
        parts += ["## Constraint / timing-check warnings", "\n".join(f"- {w}" for w in ctx["warnings"]), ""]
    if ctx.get("history"):
        parts += ["## History of this run (do not repeat failed ideas)", "\n".join(f"- {h}" for h in ctx["history"]), ""]
    if ctx.get("last_failure"):
        parts += ["## Why the previous proposal was rejected", ctx["last_failure"], ""]
    parts += [f"## RTL of the current best version (round {ctx['best_round']})", ctx["files_text"], "",
              ctx.get("task", "Propose the next improvement toward the targets.")]
    return "\n".join(parts)


def explore_task(i: int, n: int, previous_ideas: list[str]) -> str:
    prev = "; ".join(previous_ideas) or "none yet"
    return (f"ARCHITECTURE EXPLORATION, candidate {i} of {n}: propose a substantially DIFFERENT architecture "
            f"for the same function (a different speed/area/power trade-off), not a small tweak. "
            f"Architectures already proposed (do NOT propose any of these again, even reworded): {prev}.")


TB_TIMING_RULES = """## Timing rules (follow exactly - they prevent race conditions between testbench and design)
- Generate the clock with: initial clk = 0; always #5 clk = ~clk;
- Change DUT inputs ONLY right after a FALLING edge: @(negedge clk); then assign the new input values.
- Model the reference model's STATE (for example a count) in an always @(posedge clk) block with NON-BLOCKING
  assignments (<=), so it samples exactly the same input values as the DUT at the same rising edge.
- Compute every EXPECTED OUTPUT that is a function of that state with a continuous assignment, for example:
      wire [7:0] exp_out = ref_count ^ (ref_count >> 1);
  Then an output that "shows the state" changes on the same clock edge as the state, as a registered output should.
  Do NOT write `ref_out <= f(ref_state);` inside the same always block: that uses the OLD state and silently adds
  one cycle of delay. Only outputs the specification defines as registers of the INPUTS are state themselves.
- Never assign to reference-model variables from the stimulus code (for example to jump to a corner case).
  Reach corner cases only by driving the DUT's inputs (and reset).
- Compare DUT outputs with the reference ONLY at the FALLING edge (after the rising edge has settled),
  and do the comparison BEFORE changing the inputs for the next cycle.
- Never assign inputs and compare outputs at the same instant as a rising edge."""


def spec_testbench_prompt(spec: str, top: str, interface: str | None, clock: str | None, reset: str | None,
                          previous_failure: str | None = None) -> str:
    retry = ""
    if previous_failure:
        retry = f"""
## A previous testbench was rejected
Several independent designs ALL failed against the previous testbench in the same way, so the testbench itself is
probably wrong. Most common causes: (1) the reference model adds a cycle of delay the specification does not ask
for (the designs' values then appear one cycle BEFORE the reference's), (2) an off-by-one-cycle race between the
reference model and the design, (3) the stimulus code changes reference-model variables directly. Its errors were:
{previous_failure.strip()[:2000]}
Write a NEW testbench that follows the timing rules below exactly.
"""
    iface = f"Use exactly this interface:\n```verilog\n{interface.strip()}\n```" if interface else \
        "Choose a clean port list and state it at the top of the testbench as a comment."
    return f"""Write a SELF-CHECKING Verilog testbench for the design described below. Do not write the design itself.

## Specification
{spec.strip()}

## Design under test
Module name: {top}
{iface}
Clock port: {clock or 'none'}; reset port: {reset or 'none'}

## Testbench requirements
- Module name: tb_{top}. Instantiate {top} as "dut".
- Generate the clock (period 10 time units) and apply reset first.
- Contain an independent behavioural REFERENCE MODEL of the specification (do not copy any RTL).
- Drive at least 1000 cycles of random inputs plus important corner cases.
- Compare every DUT output with the reference model every cycle; count errors; print the first 10 as lines starting with "ERROR".
- At the end print exactly "TEST PASSED" if there were no errors, otherwise "TEST FAILED", then call $finish.
- Must compile with Icarus Verilog (iverilog -g2012). No SystemVerilog classes, no assertions library.
- Model exactly what the specification says about timing. Do not add a clock cycle of delay that the
  specification does not ask for (a "registered output" changes on the SAME clock edge as the state it shows).

{TB_TIMING_RULES}
{retry}
Reply with only:
### FILE: tb_{top}.v
```verilog
...
```"""


def spec_rtl_prompt(spec: str, top: str, interface: str | None, tb_text: str | None) -> str:
    iface = f"Use exactly this interface:\n```verilog\n{interface.strip()}\n```" if interface else \
        "The testbench below defines the interface; match it exactly."
    tb = f"\n## The testbench your design must pass\n```verilog\n{tb_text.strip()}\n```\n" if tb_text else ""
    return f"""Write synthesizable RTL for the specification below. Aim first for a correct, clean design.

## Specification
{spec.strip()}

## Timing
Follow the specification's timing exactly. Do not add a clock cycle of delay it does not ask for: a "registered
output" changes on the SAME clock edge as the state it shows (compute it from the next state, not the old one).

## Top module
Name: {top}. File name: {top}.v
{iface}
{tb}
{REPLY_FORMAT}"""


def spec_fix_prompt(error_text: str, files: dict[str, str]) -> str:
    return f"""Your design failed its check:

{error_text.strip()[:3000]}

Here is the design that failed:
{files_text(files)}

Fix the design so it meets the specification and passes the testbench. Keep the same interface.
{REPLY_FORMAT}"""
