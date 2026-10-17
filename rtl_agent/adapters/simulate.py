"""Simulation with Icarus Verilog: does the design compile, does it work?

Two jobs:
  compile_check  - does the RTL even compile? (fast, catches syntax errors)
  run_testbench  - run a self-checking testbench and look for its
                   PASS / FAIL message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..tools import run
from . import DesignFiles


@dataclass
class SimResult:
    ok: bool              # the simulation ran
    passed: bool = False  # ...and the testbench reported success
    detail: str = ""      # error messages / tail of the output
    seconds: float = 0.0


def _errors(text: str, limit: int = 20) -> str:
    lines = [l for l in text.splitlines() if l.strip()]
    keep = [l for l in lines if re.search(r"error|syntax|unknown|not declared|undefined", l, re.I)]
    return "\n".join((keep or lines)[:limit])


def compile_check(design: DesignFiles, workdir: Path, iverilog: str) -> SimResult:
    workdir.mkdir(parents=True, exist_ok=True)
    cmd = [iverilog, "-g2012", "-Wall", "-o", "compile_check.vvp", "-s", design.top] + \
        design.iverilog_flags() + [str(f) for f in design.files]
    res = run(cmd, cwd=workdir, timeout=300, log_file=workdir / "compile.log")
    if not res.ok:
        return SimResult(ok=False, detail=_errors(res.output), seconds=res.seconds)
    return SimResult(ok=True, passed=True, seconds=res.seconds)


def run_testbench(design: DesignFiles, tb_files: list[Path], tb_top: str, workdir: Path,
                  iverilog: str, vvp: str, pass_pattern: str, fail_pattern: str,
                  timeout_s: int = 120, log_name: str = "testbench") -> SimResult:
    workdir.mkdir(parents=True, exist_ok=True)
    exe = f"{log_name}.vvp"
    cmd = [iverilog, "-g2012", "-o", exe, "-s", tb_top] + design.iverilog_flags() + \
        [str(f) for f in design.files] + [str(f) for f in tb_files]
    res = run(cmd, cwd=workdir, timeout=300, log_file=workdir / f"{log_name}_compile.log")
    if not res.ok:
        return SimResult(ok=False, detail="testbench did not compile:\n" + _errors(res.output),
                         seconds=res.seconds)
    sim = run([vvp, "-n", exe], cwd=workdir, timeout=timeout_s, log_file=workdir / f"{log_name}.log")
    out = sim.output
    tail = "\n".join(out.strip().splitlines()[-25:])
    if sim.timed_out:
        return SimResult(ok=False, detail=f"simulation timed out after {timeout_s}s\n{tail}",
                         seconds=sim.seconds)
    failed = re.search(fail_pattern, out) is not None
    passed = re.search(pass_pattern, out) is not None and not failed and sim.returncode == 0
    return SimResult(ok=True, passed=passed, detail=tail, seconds=sim.seconds)
