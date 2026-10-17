"""Tool adapters: each one wraps one EDA program.

They all have the same shape: files go in, a small result object with
numbers comes out, and the raw tool output is saved to a log file so an
engineer can always check the original report.

    inspect.py   Yosys   - what is in the design (ports, clocks, flip-flops)
    simulate.py  Icarus  - does it compile, does the testbench pass
    equiv.py     Icarus + Yosys - does a new version behave like the original
    synth.py     Yosys   - gates and area
    sta.py       OpenSTA - timing slack, critical paths, power
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DesignFiles:
    """The RTL of one design version, plus how to read it."""
    files: list            # list[Path]
    top: str
    include_dirs: list = field(default_factory=list)
    defines: dict = field(default_factory=dict)

    def __post_init__(self):
        # Tools run inside their own work folders, so every path must be absolute.
        self.files = [Path(f).resolve() for f in self.files]
        self.include_dirs = [Path(d).resolve() for d in self.include_dirs]

    def yosys_read_cmds(self) -> list[str]:
        flags = " ".join([f"-I{d}" for d in self.include_dirs] +
                         [f"-D{k}={v}" if v != "" else f"-D{k}" for k, v in self.defines.items()])
        return [f"read_verilog -sv {flags} {f}".replace("  ", " ") for f in self.files]

    def iverilog_flags(self) -> list[str]:
        return [f"-I{d}" for d in self.include_dirs] + \
               [f"-D{k}={v}" if v != "" else f"-D{k}" for k, v in self.defines.items()]
