"""Jobs for the web interface: turn a form into a config, start the agent, read its progress.

Nothing here knows about HTTP. The web server (server.py) calls these functions, and the
tests call them directly. Each run is a "job": one folder under the jobs directory with

    job.json      who/what/when, the process id, the command
    config.yaml   the config built from the form (the same format a terminal user writes)
    inputs/       the files the user uploaded
    agent.log     everything the agent printed
    runs/<run>/   the agent's normal run folder (rounds, report.html, best/, run_state.json)

The agent itself runs as a separate process (python -m rtl_agent run config.yaml), exactly as
from the terminal, so the web layer cannot change how designs are checked or judged.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional

import yaml

from ..config import ConfigError, load_config

PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent      # the rtl-agent folder
EXAMPLES_DIR = PACKAGE_ROOT / "examples"
DEFAULT_LIBRARY = "nangate45_typ.lib"

_JOB_ID = re.compile(r"^[a-z0-9_]{1,80}$")
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,100}$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_MODULE = re.compile(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)", re.M)
_INSTANCE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_$]*)\s+(?:#\s*\(.*?\)\s*)?[A-Za-z_][A-Za-z0-9_$]*\s*\(", re.M | re.S)
MAX_FILE_BYTES = 2_000_000


class JobError(Exception):
    """A problem the user can fix (shown in the web page as a message)."""


# ---------------------------------------------------------------------------------- small helpers
def safe_name(name: str, extensions: tuple[str, ...]) -> str:
    """A plain file name: no folders, no '..', one of the allowed extensions."""
    name = (name or "").strip()
    name = name.replace("\\", "/").split("/")[-1]
    if not _NAME.match(name) or ".." in name or not name.lower().endswith(extensions):
        raise JobError(f"file name not allowed: {name!r} (use letters, digits, _ . - and one of {', '.join(extensions)})")
    return name


def _identifier(value: str, what: str) -> str:
    value = (value or "").strip()
    if not _IDENT.match(value):
        raise JobError(f"{what} must be a Verilog name (letters, digits, _), got {value!r}")
    return value


def _project_name(value: str, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]+", "_", (value or "").strip()).strip("_")
    return (value or fallback)[:40]


def _number(value, what: str, positive: bool = False) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise JobError(f"{what} must be a number, got {value!r}")
    if positive and v <= 0:
        raise JobError(f"{what} must be greater than 0")
    return v


def _write_files(folder: Path, files: list[dict], extensions: tuple[str, ...]) -> list[str]:
    names = []
    for f in files or []:
        name = safe_name(f.get("name", ""), extensions)
        text = f.get("text", "")
        if not isinstance(text, str):
            raise JobError(f"{name}: expected text content")
        if len(text.encode()) > MAX_FILE_BYTES:
            raise JobError(f"{name} is larger than {MAX_FILE_BYTES // 1_000_000} MB")
        if name in names:
            raise JobError(f"two files are called {name}")
        (folder / name).write_text(text)
        names.append(name)
    return names


# ---------------------------------------------------------------------------------- design helpers
def detect_modules(files: list[dict]) -> dict:
    """Module names found in the uploaded Verilog, and a guess of the top module
    (a module that no other module instantiates; the last such one if there are several)."""
    modules, instantiated = [], set()
    for f in files or []:
        text = re.sub(r"//.*?$|/\*.*?\*/", "", f.get("text", ""), flags=re.M | re.S)
        found = _MODULE.findall(text)
        modules += [m for m in found if m not in modules]
        for m in _INSTANCE.findall(text):
            if m not in ("module", "input", "output", "inout", "wire", "reg", "logic", "assign", "always",
                         "if", "else", "case", "for", "begin", "end", "initial", "function", "task"):
                instantiated.add(m)
    tops = [m for m in modules if m not in instantiated]
    return {"modules": modules, "top": (tops[-1] if tops else (modules[-1] if modules else None))}


def inspect_files(files: list[dict], top: str, yosys: str = "yosys") -> dict:
    """Read the design with Yosys (as the agent will) and return its ports, clocks and resets."""
    from ..adapters import DesignFiles
    from ..adapters.inspect import inspect_design
    from ..tools import find_tool
    top = _identifier(top, "top module")
    path = find_tool(yosys)
    if not path:
        raise JobError("Yosys is not installed, so the design cannot be read")
    with tempfile.TemporaryDirectory(prefix="rtl_agent_inspect_") as tmp:
        tmp = Path(tmp)
        (tmp / "rtl").mkdir()
        names = _write_files(tmp / "rtl", files, (".v", ".sv", ".vh", ".svh"))
        if not names:
            raise JobError("upload at least one Verilog file")
        info = inspect_design(DesignFiles([tmp / "rtl" / n for n in names if n.endswith((".v", ".sv"))], top),
                              tmp / "work", path)
    if not info.ok:
        raise JobError(f"Yosys could not read the design: {info.error.strip()[:1500]}")
    return {"top": info.top, "ports": [p.__dict__ for p in info.ports], "clocks": info.clocks,
            "resets": info.resets, "flop_bits": info.flop_bits, "memory_bits": info.memory_bits,
            "registered_outputs": info.registered_outputs}


def list_examples() -> list[dict]:
    out = []
    for cfg in sorted(EXAMPLES_DIR.glob("*/config.yaml")):
        text = cfg.read_text()
        data = yaml.safe_load(text) or {}
        comment = " ".join(l.lstrip("# ").strip() for l in text.splitlines()[:4] if l.startswith("#"))
        out.append({"name": cfg.parent.name, "project": data.get("project_name", cfg.parent.name),
                    "mode": data.get("mode", "rtl"), "about": comment,
                    "offline": (cfg.parent / "scripted").is_dir()})
    return out


def server_status() -> dict:
    from ..config import ToolsCfg
    from ..tools import find_tool, tool_versions
    t = ToolsCfg()
    versions = tool_versions(t)
    tools = {n: {"found": find_tool(getattr(t, n)) is not None, "version": versions.get(n, "")}
             for n in ("yosys", "sta", "iverilog", "vvp")}
    return {"tools": tools, "tools_ok": all(v["found"] for v in tools.values()),
            "server_has_key": bool(os.environ.get("GROQ_API_KEY")), "examples": list_examples()}


# ---------------------------------------------------------------------------------- config building
def _common_sections(form: dict) -> dict:
    t = form.get("targets") or {}
    cfg: dict = {"targets": {"min_setup_slack_ns": _number(t.get("min_setup_slack_ns"), "minimum setup slack") or 0.0}}
    area = _number(t.get("max_area_um2"), "maximum area", positive=True)
    power = _number(t.get("max_power_mw"), "maximum power", positive=True)
    if area is not None:
        cfg["targets"]["max_area_um2"] = area
    if power is not None:
        cfg["targets"]["max_power_mw"] = power
    prio = form.get("priority") or ["timing", "area", "power"]
    if sorted(prio) != ["area", "power", "timing"]:
        raise JobError("priority must list timing, area and power once each")
    cfg["priority"] = list(prio)
    lat = form.get("latency") or {}
    policy = lat.get("policy", "fixed")
    if policy not in ("fixed", "flexible"):
        raise JobError("latency policy must be 'fixed' or 'flexible'")
    cfg["latency"] = {"policy": policy}
    if policy == "flexible":
        cfg["latency"]["max_extra_cycles"] = int(_number(lat.get("max_extra_cycles"), "extra cycles") or 2)
    loop = form.get("loop") or {}
    rounds = int(_number(loop.get("max_rounds"), "maximum rounds", positive=True) or 10)
    cfg["loop"] = {"max_rounds": max(1, min(rounds, 50)), "patience": 3}
    llm = form.get("llm") or {}
    effort = llm.get("reasoning_effort", "medium")
    if effort not in ("low", "medium", "high"):
        raise JobError("reasoning effort must be low, medium or high")
    cfg["llm"] = {"model": (llm.get("model") or "openai/gpt-oss-120b").strip(),
                  "max_output_tokens": 5000, "extra_params": {"reasoning_effort": effort}}
    if llm.get("base_url"):
        cfg["llm"]["base_url"] = str(llm["base_url"]).strip()
    cfg["library"] = {"liberty": DEFAULT_LIBRARY}
    cfg["output_dir"] = "runs"
    return cfg


def _testbench_section(form: dict, inputs: Path) -> Optional[dict]:
    tb = form.get("testbench") or {}
    if not tb.get("files"):
        return None
    names = _write_files(inputs, tb["files"], (".v", ".sv", ".vh", ".svh"))
    return {"files": [f"inputs/{n}" for n in names], "top": _identifier(tb.get("top"), "testbench top module"),
            "pass_pattern": (tb.get("pass_pattern") or "TEST PASSED").strip()}


def build_rtl_config(form: dict, job_dir: Path) -> dict:
    inputs = job_dir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    rtl = [n for n in _write_files(inputs, form.get("files"), (".v", ".sv", ".vh", ".svh"))]
    rtl = [n for n in rtl if n.endswith((".v", ".sv"))]
    if not rtl:
        raise JobError("upload at least one Verilog file (.v or .sv)")
    top = _identifier(form.get("top"), "top module")
    cfg: dict = {"project_name": _project_name(form.get("project_name"), top), "mode": "rtl",
                 "design": {"top": top, "rtl_files": [f"inputs/{n}" for n in rtl]}}
    if (form.get("description") or "").strip():
        cfg["design"]["description"] = form["description"].strip()[:500]
    timing = form.get("timing") or {}
    if timing.get("sdc_text"):
        name = safe_name(timing.get("sdc_name") or f"{top}.sdc", (".sdc",))
        (inputs / name).write_text(timing["sdc_text"])
        cfg["sdc"] = f"inputs/{name}"
    else:
        cfg["clock_period_ns"] = _number(timing.get("clock_period_ns"), "clock period", positive=True)
        if cfg["clock_period_ns"] is None:
            raise JobError("give a clock period (ns) or upload an SDC file")
    tb = _testbench_section(form, inputs)
    if tb:
        cfg["testbench"] = tb
    cfg.update(_common_sections(form))
    return cfg


def build_spec_config(form: dict, job_dir: Path) -> dict:
    inputs = job_dir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    top = _identifier(form.get("top"), "module name")
    text = (form.get("spec") or "").strip()
    if len(text) < 20:
        raise JobError("describe the design in a few sentences (the spec is too short)")
    spec: dict = {"text": text}
    if (form.get("interface") or "").strip():
        spec["interface"] = form["interface"].strip()
    spec["clock_port"] = _identifier(form.get("clock_port") or "clk", "clock port")
    if (form.get("reset_port") or "").strip():
        spec["reset_port"] = _identifier(form["reset_port"], "reset port")
    period = _number(form.get("clock_period_ns"), "clock period", positive=True)
    if period is None:
        raise JobError("give the required clock period (ns)")
    cfg: dict = {"project_name": _project_name(form.get("project_name"), top), "mode": "spec",
                 "design": {"top": top, "description": (form.get("description") or "").strip()[:500] or None},
                 "spec": spec, "clock_period_ns": period,
                 "explore": {"candidates": int(_number(form.get("candidates"), "candidates") or 2)}}
    if cfg["design"]["description"] is None:
        del cfg["design"]["description"]
    tb = _testbench_section(form, inputs)
    if tb:
        cfg["testbench"] = tb
    cfg.update(_common_sections(form))
    return cfg


def _example_config(name: str, job_dir: Path) -> dict:
    src = EXAMPLES_DIR / name
    if not _NAME.match(name or "") or not (src / "config.yaml").exists():
        raise JobError(f"unknown example {name!r}")
    for item in src.iterdir():
        dst = job_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)
    cfg = yaml.safe_load((job_dir / "config.yaml").read_text())
    cfg["output_dir"] = "runs"
    cfg.setdefault("library", {})["liberty"] = DEFAULT_LIBRARY     # found through rtl-agent/libs
    return cfg


# ---------------------------------------------------------------------------------- jobs
class JobManager:
    def __init__(self, workdir: Path):
        self.root = Path(workdir).resolve() / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self._procs: dict[str, subprocess.Popen] = {}

    # ---------------------------------------------------------------- paths
    def job_dir(self, job_id: str) -> Path:
        if not _JOB_ID.match(job_id or ""):
            raise JobError("bad job id")
        d = self.root / job_id
        if not (d / "job.json").exists():
            raise JobError(f"no such run: {job_id}")
        return d

    def _meta(self, job_id: str) -> dict:
        return json.loads((self.job_dir(job_id) / "job.json").read_text())

    def _save_meta(self, job_dir: Path, meta: dict) -> None:
        (job_dir / "job.json").write_text(json.dumps(meta, indent=1))

    def run_dir(self, job_id: str) -> Optional[Path]:
        runs = sorted((self.job_dir(job_id) / "runs").glob("*/run_state.json"))
        return runs[-1].parent if runs else None

    # ---------------------------------------------------------------- create + start
    def create(self, form: dict, api_key: Optional[str] = None) -> str:
        kind = form.get("kind")
        action = form.get("action", "run")
        if kind not in ("rtl", "spec", "example"):
            raise JobError("choose what you are starting from: rtl, spec or example")
        if action not in ("run", "analyze"):
            raise JobError("action must be 'run' or 'analyze'")
        if action == "analyze" and kind == "spec":
            raise JobError("'measure only' needs an existing design; in spec mode the AI writes the design first")
        stamp = time.strftime("%Y%m%d_%H%M%S")
        job_id = f"{stamp}_{os.urandom(3).hex()}"
        job_dir = self.root / job_id
        job_dir.mkdir()
        try:
            if kind == "rtl":
                cfg = build_rtl_config(form, job_dir)
            elif kind == "spec":
                cfg = build_spec_config(form, job_dir)
            else:
                cfg = _example_config(form.get("example", ""), job_dir)
            (job_dir / "config.yaml").write_text(
                "# Built by the rtl-agent web interface. The same file works from the terminal:\n"
                "#   python -m rtl_agent run config.yaml\n" + yaml.safe_dump(cfg, sort_keys=False))
            try:
                load_config(job_dir / "config.yaml")          # the agent's own checks, before anything starts
            except ConfigError as exc:
                raise JobError(str(exc).replace(str(job_dir) + "/", ""))
            scripted = bool(kind == "example" and form.get("offline"))
            if action == "run" and not scripted and not (api_key or os.environ.get(_key_env(cfg))):
                raise JobError(f"an AI key is needed for a real run: paste it in the form, or start the server "
                               f"with {_key_env(cfg)} set. (Built-in examples can run offline without a key.)")
            meta = {"id": job_id, "name": cfg.get("project_name", job_id), "kind": kind, "action": action,
                    "scripted": scripted, "created": time.time(),
                    "description": (cfg.get("design") or {}).get("description", ""), "pid": None}
            self._save_meta(job_dir, meta)
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        self._start(job_id, api_key=api_key)
        return job_id

    def _start(self, job_id: str, api_key: Optional[str] = None, resume: bool = False) -> None:
        job_dir = self.job_dir(job_id)
        meta = self._meta(job_id)
        cmd = [sys.executable, "-m", "rtl_agent", meta["action"], "config.yaml"]
        if meta["action"] == "run":
            if meta.get("scripted"):
                cmd.append("--scripted")
            if resume:
                rd = self.run_dir(job_id)
                if rd is None:
                    raise JobError("nothing to resume yet")
                cmd += ["--resume", str(rd.relative_to(job_dir))]
        env = dict(os.environ, PYTHONUNBUFFERED="1",
                   PYTHONPATH=str(PACKAGE_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        if api_key:
            cfg = yaml.safe_load((job_dir / "config.yaml").read_text())
            env[_key_env(cfg)] = api_key                  # only in the process environment, never on disk
        log = open(job_dir / "agent.log", "ab")
        log.write(f"\n$ python {' '.join(cmd[1:])}\n".encode())
        log.flush()
        proc = subprocess.Popen(cmd, cwd=job_dir, env=env, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        log.close()
        self._procs[job_id] = proc
        meta.update(pid=proc.pid, started=time.time(), exit_code=None)
        self._save_meta(job_dir, meta)

    def resume(self, job_id: str, api_key: Optional[str] = None) -> None:
        if self.is_running(job_id):
            raise JobError("this run is still running")
        meta = self._meta(job_id)
        if meta["action"] != "run":
            raise JobError("only agent runs can be resumed")
        if not meta.get("scripted"):
            cfg = yaml.safe_load((self.job_dir(job_id) / "config.yaml").read_text())
            if not (api_key or os.environ.get(_key_env(cfg))):
                raise JobError("an AI key is needed to continue this run")
        self._start(job_id, api_key=api_key, resume=True)

    def stop(self, job_id: str) -> None:
        """Pause a running job: the agent saves its state and report, and can be resumed."""
        proc = self._procs.get(job_id)
        pid = proc.pid if proc else self._meta(job_id).get("pid")
        if not self.is_running(job_id) or not pid:
            raise JobError("this run is not running")
        try:
            os.killpg(pid, signal.SIGINT)
        except ProcessLookupError:
            pass

    # ---------------------------------------------------------------- status
    def is_running(self, job_id: str) -> bool:
        proc = self._procs.get(job_id)
        if proc is not None:
            if proc.poll() is None:
                return True
            meta = self._meta(job_id)
            if meta.get("exit_code") is None:
                meta["exit_code"] = proc.returncode
                self._save_meta(self.job_dir(job_id), meta)
            return False
        pid = self._meta(job_id).get("pid")           # started by an earlier server process
        if not pid:
            return False
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False
        try:                                          # a finished child that nobody waited for
            return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] != "Z"
        except OSError:
            return True

    def summary(self, job_id: str) -> dict:
        meta = self._meta(job_id)
        detail = self.detail(job_id, rounds=False, log=False)
        return {k: detail[k] for k in ("id", "name", "kind", "action", "scripted", "created", "status",
                                       "stop_reason", "baseline", "best", "best_round", "rounds_done")}

    def detail(self, job_id: str, rounds: bool = True, log: bool = True) -> dict:
        job_dir = self.job_dir(job_id)
        meta = self._meta(job_id)
        running = self.is_running(job_id)
        rd = self.run_dir(job_id)
        state = {}
        if rd is not None:
            try:
                state = json.loads((rd / "run_state.json").read_text())
            except (OSError, ValueError):
                state = {}
        recs = state.get("rounds", [])
        best_no = state.get("best_round")
        base = next((r for r in recs if r.get("number") == 0), None)
        best = next((r for r in recs if r.get("number") == best_no), None)
        if running:
            status = "running"
        elif state.get("status") in ("finished", "paused", "stopped"):
            status = state["status"]
        else:
            status = "failed"
        log_text = ""
        if log and (job_dir / "agent.log").exists():
            lines = (job_dir / "agent.log").read_text(errors="replace").splitlines()
            log_text = "\n".join(lines[-80:])
        stop_reason = state.get("stop_reason", "")
        if status == "failed" and not stop_reason:
            stop_reason = "the agent stopped with an error - see the log below"
        out = {"id": job_id, "name": meta.get("name"), "kind": meta.get("kind"), "action": meta.get("action"),
               "scripted": meta.get("scripted", False), "created": meta.get("created"),
               "description": meta.get("description", ""), "status": status, "stop_reason": stop_reason,
               "baseline": (base or {}).get("metrics"), "best": (best or {}).get("metrics"),
               "best_round": best_no, "rounds_done": len(recs),
               "targets": _targets(job_dir), "has_report": bool(rd and (rd / "report.html").exists()),
               "has_best": bool(rd and (rd / "best").is_dir()), "llm_provider": state.get("llm_provider", ""),
               "tokens": state.get("tokens", 0), "llm_calls": state.get("llm_calls", 0)}
        if rounds:
            out["rounds"] = [{"number": r.get("number"), "phase": r.get("phase"), "status": r.get("status"),
                              "reason": r.get("reason"), "idea": r.get("idea"), "metrics": r.get("metrics")}
                             for r in recs]
        if log:
            out["log"] = log_text
        return out

    def list(self) -> list[dict]:
        out = []
        for d in sorted(self.root.iterdir(), reverse=True):
            if (d / "job.json").exists():
                try:
                    out.append(self.summary(d.name))
                except Exception:          # a damaged job folder should not break the list
                    continue
        return out

    # ---------------------------------------------------------------- files
    def report_html(self, job_id: str) -> str:
        rd = self.run_dir(job_id)
        if rd is None or not (rd / "report.html").exists():
            raise JobError("no report yet")
        return (rd / "report.html").read_text(errors="replace")

    def zip_best(self, job_id: str) -> tuple[str, bytes]:
        rd = self.run_dir(job_id)
        if rd is None or not (rd / "best").is_dir():
            raise JobError("no best design yet")
        return f"{self._meta(job_id)['name']}_best.zip", _zip_folder(rd / "best", Path("best"))

    def zip_all(self, job_id: str) -> tuple[str, bytes]:
        job_dir = self.job_dir(job_id)
        return f"{self._meta(job_id)['name']}_{job_id}.zip", _zip_folder(job_dir, Path(job_id), skip=(".vvp",))


def _key_env(cfg: dict) -> str:
    return ((cfg.get("llm") or {}).get("api_key_env")) or "GROQ_API_KEY"


def _targets(job_dir: Path) -> dict:
    try:
        cfg = yaml.safe_load((job_dir / "config.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return cfg.get("targets") or {"min_setup_slack_ns": 0.0}


def _zip_folder(folder: Path, arc_root: Path, skip: tuple[str, ...] = ()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file() and not p.name.endswith(skip):
                z.write(p, str(arc_root / p.relative_to(folder)))
    return buf.getvalue()
