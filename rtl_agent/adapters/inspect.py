"""Understand a design with Yosys: ports, clocks, resets, flip-flops.

This is the "discover the design" step that makes the tool general: nothing
about the design is assumed, everything is read from the RTL.

How it works: Yosys elaborates the design (``proc`` turns always-blocks into
flip-flop and logic cells), flattens it into one module, and writes it out as
JSON. We then look at the flip-flop cells: whatever drives their clock pin is
a clock, whatever drives their reset pin is a reset.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..tools import run
from . import DesignFiles


@dataclass
class Port:
    name: str
    direction: str      # "input" | "output" | "inout"
    width: int


@dataclass
class DesignInfo:
    ok: bool
    top: str = ""
    ports: list[Port] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    clocks: list[str] = field(default_factory=list)          # top-level input ports used as clocks
    internal_clocks: int = 0                                  # flip-flops clocked by internal signals
    resets: list[dict] = field(default_factory=list)          # {"port", "active_high", "kind"}
    flop_bits: int = 0
    memory_bits: int = 0
    registered_outputs: list[str] = field(default_factory=list)   # outputs driven directly by flip-flops
    error: str = ""

    @property
    def is_sequential(self) -> bool:
        return self.flop_bits > 0 or self.memory_bits > 0

    def port(self, name: str):
        return next((p for p in self.ports if p.name == name), None)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["is_sequential"] = self.is_sequential
        return d


_FF_TYPES = re.compile(r"^\$(_?)(a?l?dff|sdff|dffe|adffe|sdffe|sdffce|dffsr|dffsre|aldffe|dlatch|adlatch|dlatchsr)")


def _param_int(value) -> int:
    """Yosys JSON stores parameters as binary strings (or ints)."""
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if s and set(s) <= {"0", "1", "x", "z"}:
        return int(s.replace("x", "0").replace("z", "0"), 2)
    try:
        return int(s)
    except ValueError:
        return 0


def yosys_errors(text: str) -> str:
    lines = [l for l in text.splitlines() if "ERROR" in l or "error" in l.lower() and "warning" not in l.lower()]
    return "\n".join(lines[:15]) or text[-1500:]


def inspect_design(design: DesignFiles, workdir: Path, yosys: str) -> DesignInfo:
    workdir.mkdir(parents=True, exist_ok=True)
    json_path = workdir / "design.json"
    script = "\n".join(design.yosys_read_cmds() + [
        f"hierarchy -check -top {design.top}",
        "tee -q -o modules.txt ls",
        "proc",
        "flatten",
        "opt_clean",
        "opt_dff -nodffe",       # turns synchronous resets into $sdff cells we can recognise
        "opt_clean",
        "memory -nomap",
        f"write_json {json_path.name}",
    ])
    (workdir / "inspect.ys").write_text(script + "\n")
    res = run([yosys, "-q", "-l", "inspect.log", "-s", "inspect.ys"], cwd=workdir, timeout=300)
    if not res.ok or not json_path.exists():
        log = (workdir / "inspect.log").read_text(errors="replace") if (workdir / "inspect.log").exists() else res.output
        return DesignInfo(ok=False, top=design.top, error=yosys_errors(log + "\n" + res.output))

    data = json.loads(json_path.read_text())
    mod = data["modules"].get(design.top)
    if mod is None:
        return DesignInfo(ok=False, top=design.top, error=f"top module '{design.top}' not found")

    info = DesignInfo(ok=True, top=design.top)
    bit_to_port: dict[int, str] = {}
    for name, p in mod["ports"].items():
        info.ports.append(Port(name, p["direction"], len(p["bits"])))
        if p["direction"] == "input" and len(p["bits"]) == 1:
            bit_to_port[p["bits"][0]] = name

    modules_txt = workdir / "modules.txt"
    if modules_txt.exists():
        info.modules = [l.strip() for l in modules_txt.read_text().splitlines()
                        if l.strip() and not l.strip().endswith(":") and " " not in l.strip()]

    clocks, resets = set(), {}
    reg_bits: set = set()            # nets driven directly by a flip-flop (or a clocked memory read port)
    for cell in mod["cells"].values():
        ctype, conn, params = cell["type"], cell["connections"], cell.get("parameters", {})
        if _FF_TYPES.match(ctype):
            info.flop_bits += _param_int(params.get("WIDTH", 1)) if not ctype.startswith("$_") else 1
            reg_bits.update(b for b in conn.get("Q", []) if isinstance(b, int))
            for pin in ("CLK", "C"):
                if pin in conn and conn[pin]:
                    bit = conn[pin][0]
                    if bit in bit_to_port:
                        clocks.add(bit_to_port[bit])
                    else:
                        info.internal_clocks += 1
            for pin, pol_param, kind in (("ARST", "ARST_POLARITY", "async"), ("SRST", "SRST_POLARITY", "sync")):
                if pin in conn and conn[pin] and conn[pin][0] in bit_to_port:
                    port = bit_to_port[conn[pin][0]]
                    resets[port] = {"port": port, "active_high": bool(_param_int(params.get(pol_param, 1))),
                                    "kind": kind}
        elif ctype.startswith("$mem"):
            info.memory_bits += _param_int(params.get("WIDTH", 0)) * _param_int(params.get("SIZE", 0))
            if _param_int(params.get("RD_CLK_ENABLE", 0)):
                reg_bits.update(b for b in conn.get("RD_DATA", []) if isinstance(b, int))
            for key, bits in conn.items():
                if key in ("RD_CLK", "WR_CLK"):
                    for b in bits:
                        if b in bit_to_port:
                            clocks.add(bit_to_port[b])
    info.clocks = sorted(clocks)
    info.resets = list(resets.values())
    # An output is "registered" when every bit comes straight from a flip-flop (constant bits are fine).
    # Registered outputs are glitch-free and give the next block a full clock cycle: part of the contract.
    for name, p in mod["ports"].items():
        bits = [b for b in p["bits"] if isinstance(b, int)]
        if p["direction"] == "output" and bits and all(b in reg_bits for b in bits):
            info.registered_outputs.append(name)
    return info
