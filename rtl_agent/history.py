"""Run history: one folder per round, never overwritten.

    runs/<project>_<date>_<time>/
        config.yaml           copy of the config used
        run_state.json        everything needed to resume or rebuild the report
        round_00/             the baseline
            rtl/              exact Verilog of this round
            diff_vs_parent.patch
            metrics.json
            change_note.md    what the LLM changed, why, and what it expected
            prompt.md / llm_reply.md   the exact text sent to and received from the LLM
            work/             raw tool logs and reports
        round_01/ ...
        best/                 the best correct version so far
        report.html, report.md

This is also the agent's "run memory": the LLM is shown one-line
summaries of every round, so it does not repeat ideas that failed.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .guardrails import ensure_inside, safe_filename


@dataclass
class RoundRecord:
    number: int
    phase: str                         # baseline | spec | explore | improve
    parent: Optional[int]              # round this proposal was derived from
    status: str = "pending"            # baseline | kept | rejected | invalid | duplicate
    reason: str = ""                   # why it was kept / rejected
    idea: str = ""                     # one-line summary of the change
    plan: str = ""                     # the LLM's diagnosis
    expected: dict = field(default_factory=dict)    # the LLM's claims
    claims: dict = field(default_factory=dict)      # claims vs measurements
    metrics: Optional[dict] = None
    checks: dict = field(default_factory=dict)      # testbench / equivalence / formal results
    analysis: dict = field(default_factory=dict)    # critical paths, cell usage, STA warnings
    rtl_hash: str = ""
    files: list[str] = field(default_factory=list)
    tokens: int = 0
    seconds: float = 0.0

    def one_liner(self) -> str:
        m = self.metrics or {}
        parts = [f"Round {self.number} [{self.phase}] {self.idea or '(no description)'}"]
        if m:
            ws = m.get("setup_ws")
            parts.append(f"slack={ws:.3f}ns" if ws is not None else "slack=n/a")
            if m.get("area") is not None:
                parts.append(f"area={m['area']:.1f}")
            if m.get("power_mw") is not None:
                parts.append(f"power={m['power_mw']:.4f}mW")
        parts.append(f"-> {self.status.upper()}: {self.reason}")
        return " | ".join(parts)


def rtl_hash(files: dict[str, str]) -> str:
    """Fingerprint of a design version, ignoring whitespace-only differences."""
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode())
        h.update(" ".join(files[name].split()).encode())
    return h.hexdigest()[:16]


class RunStore:
    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.dir / "run_state.json"

    # ---- round folders -------------------------------------------------
    def round_dir(self, n: int) -> Path:
        return self.dir / f"round_{n:02d}"

    def write_rtl(self, n: int, files: dict[str, str]) -> list[Path]:
        rtl_dir = self.round_dir(n) / "rtl"
        rtl_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for name, text in files.items():
            p = ensure_inside(rtl_dir / safe_filename(name), self.dir)
            p.write_text(text if text.endswith("\n") else text + "\n")
            paths.append(p)
        return paths

    def read_rtl(self, n: int) -> dict[str, str]:
        rtl_dir = self.round_dir(n) / "rtl"
        return {p.name: p.read_text() for p in sorted(rtl_dir.glob("*")) if p.is_file()}

    def write_diff(self, n: int, parent_files: dict[str, str], new_files: dict[str, str],
                   parent_label: str) -> str:
        chunks = []
        for name in sorted(set(parent_files) | set(new_files)):
            a = parent_files.get(name, "").splitlines(keepends=True)
            b = new_files.get(name, "").splitlines(keepends=True)
            chunks += difflib.unified_diff(a, b, f"{parent_label}/{name}", f"round_{n:02d}/{name}")
        text = "".join(chunks)
        (self.round_dir(n) / "diff_vs_parent.patch").write_text(text or "(no changes)\n")
        return text

    def write_text(self, n: int, name: str, text: str) -> None:
        d = self.round_dir(n)
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text)

    def write_record(self, rec: RoundRecord) -> None:
        d = self.round_dir(rec.number)
        d.mkdir(parents=True, exist_ok=True)
        (d / "metrics.json").write_text(json.dumps(
            {"metrics": rec.metrics, "checks": rec.checks, "status": rec.status,
             "reason": rec.reason, "claims": rec.claims}, indent=2))
        note = [f"# Round {rec.number} ({rec.phase})", "",
                f"**Status:** {rec.status} - {rec.reason}", "",
                f"**Idea:** {rec.idea or '-'}", ""]
        if rec.plan:
            note += ["**LLM diagnosis / plan:**", "", rec.plan, ""]
        if rec.claims:
            note += ["| metric | LLM expected | measured | claim correct? |", "|---|---|---|---|"]
            for k, v in rec.claims.items():
                note.append(f"| {k} | {v['claimed'] or '-'} | {v['measured'] or '-'} | "
                            f"{'-' if v['correct'] is None else ('yes' if v['correct'] else 'NO')} |")
        (d / "change_note.md").write_text("\n".join(note) + "\n")

    def save_best(self, n: int) -> None:
        best = self.dir / "best"
        if best.exists():
            shutil.rmtree(best)
        shutil.copytree(self.round_dir(n) / "rtl", best)
        (best / "FROM_ROUND.txt").write_text(f"This is the RTL of round {n}.\n")

    # ---- run state (for resume and for the report) -----------------------
    def save_state(self, state: dict) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, default=str))
        tmp.replace(self.state_file)        # atomic: a crash never leaves half a file

    def load_state(self) -> dict:
        return json.loads(self.state_file.read_text())


def record_from_dict(d: dict) -> RoundRecord:
    return RoundRecord(**{k: d[k] for k in RoundRecord.__dataclass_fields__ if k in d})


def record_to_dict(r: RoundRecord) -> dict:
    return asdict(r)
