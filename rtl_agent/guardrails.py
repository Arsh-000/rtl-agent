"""Guardrails: rules enforced by code, not by asking the LLM nicely.

1. The SDC is read-only        -> its fingerprint is checked every round.
2. Writes stay in the run folder -> file names from the LLM are sanitised.
3. The interface is frozen     -> same port names, directions and widths.
4. Only working designs count  -> enforced in evaluate.py (tests must pass).
5. The best version is kept    -> enforced in controller.py.
"""

from __future__ import annotations

import re
from pathlib import Path

from .adapters.inspect import DesignInfo
from .sdc import fingerprint


class GuardrailViolation(Exception):
    pass


def check_sdc_unchanged(sdc: Path, expected_hash: str) -> None:
    if fingerprint(sdc) != expected_hash:
        raise GuardrailViolation(
            f"the SDC file {sdc} changed during the run. The SDC is read-only; "
            "the run is stopped so no result is measured against different rules.")


_SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\.(v|sv|vh|svh)$")


def safe_filename(name: str) -> str:
    """Accept only a plain Verilog file name - no folders, no '..'."""
    name = name.strip().strip("`'\"")
    if "/" in name or "\\" in name or ".." in name or not _SAFE_NAME.match(name):
        raise GuardrailViolation(f"rejected file name from the LLM: {name!r} "
                                 "(only plain .v/.sv names inside the run folder are allowed)")
    return name


def ensure_inside(path: Path, root: Path) -> Path:
    path, root = path.resolve(), root.resolve()
    if root != path and root not in path.parents:
        raise GuardrailViolation(f"refused to write outside the run folder: {path}")
    return path


def interface_differences(reference: DesignInfo, candidate: DesignInfo) -> list[str]:
    """The top-level ports must stay exactly the same so the design still fits its surroundings."""
    ref = {p.name: p for p in reference.ports}
    cand = {p.name: p for p in candidate.ports}
    diffs = []
    for name in sorted(set(ref) - set(cand)):
        diffs.append(f"port '{name}' was removed")
    for name in sorted(set(cand) - set(ref)):
        diffs.append(f"port '{name}' was added")
    for name in sorted(set(ref) & set(cand)):
        a, b = ref[name], cand[name]
        if a.direction != b.direction:
            diffs.append(f"port '{name}' changed direction {a.direction} -> {b.direction}")
        if a.width != b.width:
            diffs.append(f"port '{name}' changed width {a.width} -> {b.width}")
    return diffs


def registered_output_differences(reference: DesignInfo, candidate: DesignInfo) -> list[str]:
    """Outputs that came straight from flip-flops in the original must still do so.

    Moving logic AFTER the output registers can make the design look faster (the logic leaves the
    register-to-register path), but the output is then no longer glitch-free and the next block loses
    part of its clock cycle. Behaviour checks cannot see this: the values at each clock edge are the same.
    """
    return [f"output '{name}' was registered (driven directly by flip-flops) in the original, "
            f"but now has logic between the flip-flops and the port"
            for name in reference.registered_outputs if name not in candidate.registered_outputs]
