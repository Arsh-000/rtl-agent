"""Synthesis with Yosys: turn RTL into real library gates and measure area.

The Yosys script below is the whole synthesis flow:

    synth        generic logic optimization (RTL -> generic gates)
    dfflibmap    map flip-flops to the library's flip-flop cells
    abc -D <ps>  map logic to library gates, aiming for the clock period
    stat         count cells and add up their area from the .lib file
    write_verilog  the gate-level netlist that OpenSTA will time

The netlist keeps Yosys' "src" attributes, so a timing path can be traced
back to the RTL line it came from.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..tools import run
from . import DesignFiles
from .inspect import yosys_errors


@dataclass
class SynthResult:
    ok: bool
    area: Optional[float] = None
    cell_count: Optional[int] = None
    cell_types: dict = field(default_factory=dict)   # cell name -> count
    netlist: Optional[Path] = None
    error: str = ""


def _parse_stat(text: str):
    """Pull the total area, cell count and per-cell counts out of 'stat -liberty'.

    Yosys has printed this report in a few different layouts over the years,
    so we look for the key lines rather than a fixed table position.
    """
    area = None
    m = re.findall(r"Chip area for (?:top )?module\s+'?\\?[^':]*'?:\s*([0-9.eE+-]+)", text)
    if m:
        area = float(m[-1])
    cells = None
    m = re.findall(r"Number of cells:\s*([0-9]+)", text)
    if m:
        cells = int(m[-1])
    else:   # newer layout:  "   123  1.2E+03  cells"
        m = re.findall(r"^\s*([0-9]+)\s+[0-9.eE+-]+\s+cells\s*$", text, re.M)
        if m:
            cells = int(m[-1])
    types: dict[str, int] = {}
    # older layout:  "     NAND2_X1      12"   newer: "   12   10.6  NAND2_X1"
    for line in text.splitlines():
        mm = re.match(r"^\s+([A-Za-z_][A-Za-z0-9_]*)\s+([0-9]+)\s*$", line)
        if mm and not mm.group(1).startswith("Number"):
            types[mm.group(1)] = int(mm.group(2))
            continue
        mm = re.match(r"^\s+([0-9]+)\s+[0-9.eE+-]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", line)
        if mm and mm.group(2) not in ("cells", "wires", "ports"):
            types[mm.group(2)] = int(mm.group(1))
    return area, cells, types


def synthesize(design: DesignFiles, liberty: Path, workdir: Path, yosys: str,
               clock_period_ns: Optional[float] = None, tie_hi: Optional[str] = None,
               tie_lo: Optional[str] = None, custom_cmd: Optional[str] = None) -> SynthResult:
    workdir.mkdir(parents=True, exist_ok=True)
    liberty = Path(liberty).resolve()
    netlist = workdir / "netlist.v"
    if custom_cmd:
        return _custom_synth(custom_cmd, design, liberty, workdir, clock_period_ns)

    abc = f"abc -liberty {liberty}"
    if clock_period_ns:
        abc += f" -D {int(round(clock_period_ns * 1000))}"   # abc wants picoseconds
    lines = [f"read_liberty -lib {liberty}"] + design.yosys_read_cmds() + [   # -lib: know the cells' pins
        f"hierarchy -check -top {design.top}",
        f"synth -top {design.top} -flatten",
        f"dfflibmap -liberty {liberty}",
        abc,
        "opt_clean -purge",
    ]
    if tie_hi and tie_lo:
        hc, hp = tie_hi.split("/")
        lc, lp = tie_lo.split("/")
        lines += ["setundef -zero", f"hilomap -singleton -hicell {hc} {hp} -locell {lc} {lp}"]
    lines += [
        "autoname",                                   # readable instance names in timing reports
        f"tee -o area.rpt stat -liberty {liberty}",
        "check",
        f"write_verilog -noexpr -nohex -nodec {netlist.name}",
    ]
    (workdir / "synth.ys").write_text("\n".join(lines) + "\n")
    res = run([yosys, "-q", "-l", "synth.log", "-s", "synth.ys"], cwd=workdir, timeout=1800)
    log = (workdir / "synth.log").read_text(errors="replace") if (workdir / "synth.log").exists() else ""
    if not res.ok or not netlist.exists():
        return SynthResult(ok=False, error=yosys_errors(log + "\n" + res.output))
    # OpenSTA's netlist reader does not accept the 'signed' keyword; in a gate-level
    # netlist it carries no meaning, so remove it.
    text = netlist.read_text()
    netlist.write_text(re.sub(r"\b(wire|input|output|inout|reg)\s+signed\b", r"\1", text))
    area, cells, types = _parse_stat((workdir / "area.rpt").read_text(errors="replace"))
    if area is None:
        return SynthResult(ok=False, error="could not read the area from Yosys' stat report (area.rpt)")
    return SynthResult(ok=True, area=area, cell_count=cells, cell_types=types, netlist=netlist)


def _custom_synth(cmd: str, design: DesignFiles, liberty: Path, workdir: Path,
                  clock_period_ns: Optional[float]) -> SynthResult:
    """Run the user's own synthesis script instead of the built-in one.

    Contract: the script gets its inputs as environment variables and must
    write OUT_DIR/netlist.v and OUT_DIR/synth_metrics.json ({"area": number}).
    """
    env = {"RTL_FILES": " ".join(str(f) for f in design.files), "TOP": design.top,
           "LIBERTY": str(liberty), "OUT_DIR": str(workdir),
           "CLOCK_PERIOD_NS": str(clock_period_ns or "")}
    res = run(["bash", "-c", cmd], cwd=workdir, timeout=3600, log_file=workdir / "synth.log", env=env)
    metrics_file, netlist = workdir / "synth_metrics.json", workdir / "netlist.v"
    if not res.ok or not metrics_file.exists() or not netlist.exists():
        return SynthResult(ok=False, error="custom synthesis command failed or did not write "
                                           "netlist.v and synth_metrics.json:\n" + res.output[-1500:])
    data = json.loads(metrics_file.read_text())
    return SynthResult(ok=True, area=float(data["area"]), cell_count=data.get("cell_count"),
                       cell_types=data.get("cell_types", {}), netlist=netlist)
