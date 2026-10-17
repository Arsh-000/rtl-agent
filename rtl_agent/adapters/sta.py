"""Static timing analysis with OpenSTA: slack, critical paths and power.

OpenSTA reads three things: the cell library (gate delays), the gate-level
netlist from synthesis, and YOUR SDC file (the timing rules). It then checks
every path between flip-flops against the clock.

  setup slack  > 0  : signals arrive before the next clock edge (good)
  setup slack  < 0  : the design is too slow for this clock
  hold slack   < 0  : a signal changes too soon after the clock edge

The SDC is passed to OpenSTA untouched - we never write our own SDC parser
and the agent is never allowed to change it.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from ..tools import run

_MARK = "@@RTLAGENT"


@dataclass
class TimingPath:
    startpoint: str
    endpoint: str
    slack: Optional[float]
    path_group: str = ""
    cells: int = 0                                   # gates on the path (logic depth)
    rtl_lines: list[str] = field(default_factory=list)   # "file.v:12" lines the path goes through


@dataclass
class StaResult:
    ok: bool
    setup_ws: Optional[float] = None      # worst setup slack (ns); None = no constrained paths
    setup_tns: Optional[float] = None     # total negative setup slack (ns)
    hold_ws: Optional[float] = None
    hold_tns: Optional[float] = None
    power_mw: Optional[float] = None
    clocks: list[dict] = field(default_factory=list)   # {"name", "period"}
    paths: list[TimingPath] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # from check_setup: unconstrained endpoints etc.
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _tcl_script(liberty: Path, netlist: Path, top: str, sdc: Path, n_paths: int) -> str:
    return f"""
read_liberty {{{liberty}}}
read_verilog {{{netlist}}}
link_design {top}
read_sdc {{{sdc}}}

puts "{_MARK} CLOCKS_BEGIN"
foreach clk [all_clocks] {{ puts "{_MARK} CLOCK [get_name $clk] [get_property $clk period]" }}
puts "{_MARK} CLOCKS_END"

puts "{_MARK} CHECKS_BEGIN"
check_setup -verbose
puts "{_MARK} CHECKS_END"

puts "{_MARK} SETUP_PATHS_BEGIN"
report_checks -path_delay max -group_path_count {n_paths} -endpoint_path_count 1 -sort_by_slack -fields {{src_attr}} -digits 4
puts "{_MARK} SETUP_PATHS_END"
puts "{_MARK} HOLD_PATHS_BEGIN"
report_checks -path_delay min -group_path_count 1 -digits 4
puts "{_MARK} HOLD_PATHS_END"

puts "{_MARK} SUMMARY_BEGIN"
report_worst_slack -max -digits 4
report_worst_slack -min -digits 4
report_tns -max -digits 4
report_tns -min -digits 4
puts "{_MARK} SUMMARY_END"

puts "{_MARK} POWER_BEGIN"
# Same switching assumption for every version: each net toggles in 10% of clock cycles.
# (Without this, how activity spreads through the netlist depends on the tool versions; with
# Yosys 0.52 netlists it did not spread at all and combinational power came out as 0.)
set_power_activity -global -activity 0.1
report_power -digits 6
puts "{_MARK} POWER_END"
"""


def _section(text: str, name: str) -> str:
    m = re.search(rf"{_MARK} {name}_BEGIN\n(.*?){_MARK} {name}_END", text, re.S)
    return m.group(1) if m else ""


def _num(summary: str, key: str) -> Optional[float]:
    """Read lines like 'worst slack max -0.1234' or 'tns min 0.0000'."""
    patterns = {"SETUP_WS": r"worst slack max\s+(\S+)", "HOLD_WS": r"worst slack min\s+(\S+)",
                "SETUP_TNS": r"tns max\s+(\S+)", "HOLD_TNS": r"tns min\s+(\S+)"}
    m = re.search(patterns[key], summary)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:            # "INF" when there are no constrained paths
        return None
    return None if abs(v) > 1e20 else v


def parse_paths(report: str) -> list[TimingPath]:
    """Split a report_checks output into individual paths."""
    paths = []
    for block in re.split(r"\n(?=Startpoint:)", report):
        if "Startpoint:" not in block:
            continue
        sp = re.search(r"Startpoint:\s*(.+)", block)
        ep = re.search(r"Endpoint:\s*(.+)", block)
        grp = re.search(r"Path Group:\s*(\S+)", block)
        sl = re.search(r"^\s*(-?[0-9.]+)\s+slack", block, re.M)
        srcs = []
        for s in re.findall(r"([\w./-]+\.s?v):(\d+)(?:\.\d+-\d+\.\d+)?", block):
            line = f"{Path(s[0]).name}:{s[1]}"
            if line not in srcs:
                srcs.append(line)
        # count gate output pins in the data-arrival part of the path
        arrival = block.split("data arrival time")[0]
        cells = len(re.findall(r"^\s*[0-9.]+\s+[0-9.]+\s+[\^v]\s+\S+/\S+\s+\(\w+\)", arrival, re.M))
        paths.append(TimingPath(
            startpoint=sp.group(1).strip() if sp else "?",
            endpoint=ep.group(1).strip() if ep else "?",
            slack=float(sl.group(1)) if sl else None,
            path_group=grp.group(1) if grp else "",
            cells=max(0, cells - 1) if cells else 0,
            rtl_lines=srcs[:12]))
    return paths


def _parse_power_mw(report: str) -> Optional[float]:
    # The "Total" row: Internal  Switching  Leakage  Total  (Watts)
    m = re.search(r"^Total\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)", report, re.M)
    return float(m.group(4)) * 1000.0 if m else None


def run_sta(netlist: Path, top: str, liberty: Path, sdc: Path, workdir: Path, sta: str,
            n_paths: int = 3, custom_cmd: Optional[str] = None) -> StaResult:
    workdir.mkdir(parents=True, exist_ok=True)
    netlist, liberty, sdc = Path(netlist).resolve(), Path(liberty).resolve(), Path(sdc).resolve()
    if custom_cmd:
        return _custom_sta(custom_cmd, netlist, top, liberty, sdc, workdir)
    tcl = workdir / "sta.tcl"
    tcl.write_text(_tcl_script(liberty, netlist, top, sdc, n_paths) + "\nexit\n")
    res = run([sta, "-no_init", "-no_splash", "-exit", tcl.name], cwd=workdir, timeout=1800,
              log_file=workdir / "sta.log")
    out = res.output
    if not res.ok or f"{_MARK} SUMMARY_END" not in out:
        errs = [l for l in out.splitlines() if re.search(r"error|Error", l)]
        return StaResult(ok=False, error="\n".join(errs[:15]) or out[-1500:])

    result = StaResult(ok=True)
    for name, period in re.findall(rf"{_MARK} CLOCK (\S+) (\S+)", out):
        try:
            result.clocks.append({"name": name, "period": float(period)})
        except ValueError:
            pass
    summary = _section(out, "SUMMARY")
    result.setup_ws = _num(summary, "SETUP_WS")
    result.hold_ws = _num(summary, "HOLD_WS")
    result.setup_tns = _num(summary, "SETUP_TNS")
    result.hold_tns = _num(summary, "HOLD_TNS")
    result.power_mw = _parse_power_mw(_section(out, "POWER"))
    setup_rpt = _section(out, "SETUP_PATHS")
    result.paths = parse_paths(setup_rpt)
    (workdir / "timing_setup.rpt").write_text(setup_rpt)
    (workdir / "timing_hold.rpt").write_text(_section(out, "HOLD_PATHS"))
    (workdir / "power.rpt").write_text(_section(out, "POWER"))
    checks = _section(out, "CHECKS")
    (workdir / "checks.rpt").write_text(checks)
    result.warnings = [l.strip() for l in checks.splitlines() if l.strip()
                       and not l.startswith(" ")][:20]
    return result


def _custom_sta(cmd: str, netlist: Path, top: str, liberty: Path, sdc: Path, workdir: Path) -> StaResult:
    """User-supplied STA. Must write OUT_DIR/sta_metrics.json with
    setup_ws, setup_tns, hold_ws (ns) and optionally power_mw."""
    env = {"NETLIST": str(netlist), "TOP": top, "LIBERTY": str(liberty), "SDC": str(sdc),
           "OUT_DIR": str(workdir)}
    res = run(["bash", "-c", cmd], cwd=workdir, timeout=3600, log_file=workdir / "sta.log", env=env)
    f = workdir / "sta_metrics.json"
    if not res.ok or not f.exists():
        return StaResult(ok=False, error="custom STA command failed or did not write sta_metrics.json:\n"
                                         + res.output[-1500:])
    d = json.loads(f.read_text())
    return StaResult(ok=True, setup_ws=d.get("setup_ws"), setup_tns=d.get("setup_tns"),
                     hold_ws=d.get("hold_ws"), hold_tns=d.get("hold_tns"), power_mw=d.get("power_mw"))
