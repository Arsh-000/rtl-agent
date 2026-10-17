"""Reads the project's YAML config file and checks it for mistakes.

Every setting the agent uses comes from here. Nothing about a specific
design is hard-coded anywhere else in the tool - that is what makes it
general-purpose.

Pydantic does the checking: if a field is missing, has the wrong type, or
is misspelled, loading fails with a clear message instead of the agent
crashing later in the middle of a run.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class _Section(BaseModel):
    # extra="forbid" turns a typo such as "max_aera_um2" into an error.
    model_config = ConfigDict(extra="forbid")


class DesignCfg(_Section):
    top: str                                  # name of the top-level module
    description: Optional[str] = None         # plain-English "what is this design", shown in the report
                                              # (if empty: the spec text, or the comment above the module)
    rtl_files: list[Path] = Field(default_factory=list)
    include_dirs: list[Path] = Field(default_factory=list)
    defines: dict[str, str] = Field(default_factory=dict)


class SpecCfg(_Section):
    text: str                                 # plain-English specification
    interface: Optional[str] = None           # optional: exact Verilog port list to use
    clock_port: Optional[str] = "clk"         # used to generate the SDC
    reset_port: Optional[str] = None
    rtl_attempts: int = 3                     # tries to get RTL that passes the testbench
    testbench_attempts: int = 2               # if every RTL attempt fails, the testbench itself is
                                              # suspected and regenerated (up to this many testbenches)


class TestbenchCfg(_Section):
    files: list[Path]
    top: str
    pass_pattern: str = "TEST PASSED"         # text the testbench prints on success
    fail_pattern: str = r"TEST FAILED|ERROR|FATAL"
    timeout_s: int = 120


class LibraryCfg(_Section):
    liberty: Path                             # standard-cell library (.lib)
    tie_hi: Optional[str] = None              # e.g. "LOGIC1_X1/Z" (cell/pin for constant 1)
    tie_lo: Optional[str] = None              # e.g. "LOGIC0_X1/Z"


class TargetsCfg(_Section):
    min_setup_slack_ns: float = 0.0
    # Hold violations are normally fixed during physical design (buffer insertion),
    # not in RTL, so by default hold slack is reported but is not a target.
    min_hold_slack_ns: Optional[float] = None
    max_area_um2: Optional[float] = None
    max_power_mw: Optional[float] = None


class LatencyCfg(_Section):
    # fixed:    outputs must match the original on every cycle.
    # flexible: outputs may arrive up to max_extra_cycles later (pipelining allowed).
    policy: Literal["fixed", "flexible"] = "fixed"
    max_extra_cycles: int = 2


class CorrectnessCfg(_Section):
    # Random co-simulation against the original design ("golden reference").
    equivalence: Literal["auto", "on", "off"] = "auto"
    cycles: int = 2000
    seed: int = 1
    # Bounded formal check with Yosys' SAT solver (extra evidence, not required).
    formal: Literal["auto", "off"] = "auto"
    formal_depth: int = 10
    formal_timeout_s: int = 60
    # Outputs driven directly by flip-flops in the original must stay that way (glitch-free, full cycle
    # for the next block). Behaviour checks cannot see this, so it is checked on the structure.
    keep_registered_outputs: bool = True


class ExploreCfg(_Section):
    # How many different architectures to try before the improvement loop.
    # None means: 3 in spec mode, 0 in rtl mode.
    candidates: Optional[int] = None


class LoopCfg(_Section):
    max_rounds: int = 20                      # safety cap, not the normal way to stop
    patience: int = 3                         # stop after this many CORRECT proposals in a row that were not better
    max_consecutive_failures: int = 4         # stop if this many proposals in a row are broken
    min_improvement_pct: float = 1.0          # smaller gains count as "no real improvement"
    optimize_beyond_targets: bool = False     # keep going after all targets are met?
    max_minutes: float = 120


class LLMCfg(_Section):
    # openai_compatible works with Groq, OpenAI, OpenRouter, a local Ollama, ...
    # scripted replays hand-written responses from a folder (offline testing only).
    provider: Literal["openai_compatible", "scripted"] = "openai_compatible"
    base_url: str = "https://api.groq.com/openai/v1"
    model: str = "openai/gpt-oss-120b"
    api_key_env: str = "GROQ_API_KEY"         # name of the environment variable, never the key
    temperature: float = 0.2
    max_output_tokens: int = 5000            # room for hidden reasoning + the reply (cut-offs seen at 3000-4500)
    tokens_per_minute: Optional[int] = 8000   # your plan's limits; None = no limit
    tokens_per_day: Optional[int] = 200000
    request_timeout_s: int = 180
    max_retries: int = 5
    extra_params: dict = Field(default_factory=dict)   # e.g. {"reasoning_effort": "low"}
    scripted_dir: Optional[Path] = None


class ToolsCfg(_Section):
    yosys: str = "yosys"
    sta: str = "sta"
    iverilog: str = "iverilog"
    vvp: str = "vvp"


class CustomCommandsCfg(_Section):
    # Replace a built-in step with your own script. See docs/GUIDE.md ("Plugging in your own flow").
    synth: Optional[str] = None
    sta: Optional[str] = None


class Config(_Section):
    project_name: str
    mode: Literal["rtl", "spec"] = "rtl"
    design: DesignCfg
    spec: Optional[SpecCfg] = None
    sdc: Optional[Path] = None
    clock_period_ns: Optional[float] = None   # used only when no SDC is given
    testbench: Optional[TestbenchCfg] = None
    library: LibraryCfg
    targets: TargetsCfg = Field(default_factory=TargetsCfg)
    priority: list[Literal["timing", "area", "power"]] = Field(
        default_factory=lambda: ["timing", "area", "power"])
    latency: LatencyCfg = Field(default_factory=LatencyCfg)
    correctness: CorrectnessCfg = Field(default_factory=CorrectnessCfg)
    explore: ExploreCfg = Field(default_factory=ExploreCfg)
    loop: LoopCfg = Field(default_factory=LoopCfg)
    llm: LLMCfg = Field(default_factory=LLMCfg)
    tools: ToolsCfg = Field(default_factory=ToolsCfg)
    custom_commands: CustomCommandsCfg = Field(default_factory=CustomCommandsCfg)
    output_dir: Path = Path("runs")

    # Folder the config file lives in. Relative paths are resolved against it.
    base_dir: Path = Path(".")

    @model_validator(mode="after")
    def _check_combinations(self) -> "Config":
        if self.mode == "rtl" and not self.design.rtl_files:
            raise ValueError("mode 'rtl' needs design.rtl_files")
        if self.mode == "spec" and self.spec is None:
            raise ValueError("mode 'spec' needs a 'spec' section")
        if self.sdc is None and self.clock_period_ns is None:
            raise ValueError("give either 'sdc' (a constraints file) or 'clock_period_ns'")
        if len(set(self.priority)) != len(self.priority):
            raise ValueError("'priority' must not repeat an entry")
        return self

    @property
    def explore_candidates(self) -> int:
        if self.explore.candidates is not None:
            return self.explore.candidates
        return 3 if self.mode == "spec" else 0


class ConfigError(Exception):
    pass


def _resolve(base: Path, p: Optional[Path]) -> Optional[Path]:
    if p is None:
        return None
    p = Path(p).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def _find_library(p: Path) -> Path:
    """If the Liberty file is not where the config says, look for a file with the same name in
    $RTL_AGENT_LIB_DIR, then in rtl-agent's own libs/ folder. The Docker image sets the variable to
    its bundled libs folder, so example configs, the templates and configs written for another
    machine still find the library, with or without Docker."""
    if p.exists():
        return p
    folders = [d for d in os.environ.get("RTL_AGENT_LIB_DIR", "").split(os.pathsep) if d]
    folders.append(str(Path(__file__).resolve().parent.parent / "libs"))
    for d in folders:
        if (Path(d) / p.name).exists():
            return Path(d) / p.name
    return p


def load_config(path: str | Path) -> Config:
    """Load and validate a config file. Relative paths become absolute."""
    path = Path(path).resolve()
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    data["base_dir"] = str(path.parent)
    try:
        cfg = Config(**data)
    except ValidationError as exc:
        raise ConfigError(f"problem in {path}:\n{exc}") from exc

    base = cfg.base_dir
    cfg.design.rtl_files = [_resolve(base, p) for p in cfg.design.rtl_files]
    cfg.design.include_dirs = [_resolve(base, p) for p in cfg.design.include_dirs]
    cfg.sdc = _resolve(base, cfg.sdc)
    cfg.library.liberty = _find_library(_resolve(base, cfg.library.liberty))
    cfg.output_dir = _resolve(base, cfg.output_dir)
    if cfg.testbench:
        cfg.testbench.files = [_resolve(base, p) for p in cfg.testbench.files]
    if cfg.llm.scripted_dir:
        cfg.llm.scripted_dir = _resolve(base, cfg.llm.scripted_dir)

    # Fail early on missing input files.
    must_exist = list(cfg.design.rtl_files) + [cfg.library.liberty]
    if cfg.sdc:
        must_exist.append(cfg.sdc)
    if cfg.testbench:
        must_exist += cfg.testbench.files
    missing = [str(p) for p in must_exist if not p.exists()]
    if missing:
        raise ConfigError("these files from the config do not exist:\n  " + "\n  ".join(missing))
    return cfg
