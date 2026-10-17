"""Finding and running the external EDA programs.

Every adapter (synthesis, STA, simulation, ...) runs its program through
``run()`` below, so timeouts, logging and error handling work the same way
everywhere.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Extra folders searched for tools (besides PATH). The setup script installs
# the tools into /opt/eda/bin; set RTL_AGENT_TOOL_PATH to add your own folders.
_EXTRA_DIRS = [d for d in os.environ.get("RTL_AGENT_TOOL_PATH", "/opt/eda/bin").split(os.pathsep) if d]


@dataclass
class ToolResult:
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def output(self) -> str:
        return self.stdout + ("\n" + self.stderr if self.stderr else "")


class ToolMissing(Exception):
    pass


def find_tool(name: str) -> Optional[str]:
    """Return the full path of a program, or None if it is not installed."""
    if os.path.sep in name:                       # an explicit path was configured
        return name if os.access(name, os.X_OK) else None
    found = shutil.which(name)
    if found:
        return found
    for d in _EXTRA_DIRS:
        candidate = Path(d) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def require_tool(name: str) -> str:
    path = find_tool(name)
    if not path:
        raise ToolMissing(
            f"'{name}' was not found. Install it (see README, 'Installing the EDA tools') "
            f"or set its full path in the config under 'tools:'.")
    return path


def run(cmd: list[str], cwd: Path, timeout: float = 600, log_file: Optional[Path] = None,
        env: Optional[dict] = None) -> ToolResult:
    """Run a program, capture its output, and optionally save it to a log file."""
    start = time.time()
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                              timeout=timeout, env=full_env, errors="replace")
        result = ToolResult(cmd, proc.returncode, proc.stdout, proc.stderr, time.time() - start)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        result = ToolResult(cmd, -1, out, err, time.time() - start, timed_out=True)
    except FileNotFoundError as exc:
        result = ToolResult(cmd, -1, "", str(exc), time.time() - start)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(f"$ {' '.join(cmd)}\n# exit={result.returncode} "
                            f"timed_out={result.timed_out} seconds={result.seconds:.1f}\n\n"
                            f"{result.stdout}\n{result.stderr}")
    return result


def tool_versions(tools_cfg) -> dict[str, str]:
    """Version string of each configured tool (shown in the report)."""
    probes = {
        "yosys": [tools_cfg.yosys, "-V"],
        "sta": [tools_cfg.sta, "-version"],
        "iverilog": [tools_cfg.iverilog, "-V"],
    }
    versions = {}
    for name, cmd in probes.items():
        path = find_tool(cmd[0])
        if not path:
            versions[name] = "NOT FOUND"
            continue
        res = run([path] + cmd[1:], cwd=Path("."), timeout=30)
        first = (res.stdout or res.stderr).strip().splitlines()
        versions[name] = first[0] if first else "unknown"
    return versions
