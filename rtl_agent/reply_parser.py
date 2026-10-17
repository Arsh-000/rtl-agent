"""Turn the LLM's text reply into a structured proposal.

The prompts ask for this format:

    ### PLAN
    why the design is limited and what will change
    ### CHANGE_NOTE
    {"idea": "...", "expected": {"setup_slack": "improve", ...}, "latency_change": 0}
    ### FILE: name.v
    ```verilog
    ...whole file...
    ```

Free models do not always follow formats exactly, so the parser is
forgiving: a missing CHANGE_NOTE is allowed, and a single unlabeled code
block is accepted when only one file could be meant.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


@dataclass
class Proposal:
    plan: str = ""
    idea: str = ""
    expected: dict = field(default_factory=dict)
    latency_change: int = 0
    files: dict = field(default_factory=dict)       # file name -> full text
    no_more_ideas: bool = False
    problem: str = ""                                # why the reply could not be used


_FENCE = re.compile(r"```[a-zA-Z]*[ \t]*\n(.*?)```", re.S)


# A heading may be written "### PLAN", "**PLAN**" or "PLAN:" - free models vary.
_HEAD = r"^[ \t]*(?:#+[ \t]*|\*\*)?"


def _section(text: str, name: str) -> str:
    m = re.search(rf"{_HEAD}{name}\b[*: \t]*\n(.*?)(?={_HEAD}(?:PLAN|CHANGE_NOTE|FILE)\b|\Z)", text, re.S | re.M | re.I)
    return m.group(1).strip() if m else ""


def _first_json_object(text: str):
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def parse_reply(text: str, known_files: list[str]) -> Proposal:
    p = Proposal()
    stripped = text.strip()
    if stripped.startswith("NO_MORE_IDEAS"):
        p.no_more_ideas = True
        return p
    if not stripped:
        p.problem = ("the reply was empty (a reasoning model may have spent all its output tokens "
                     "thinking - consider raising llm.max_output_tokens)")
        return p
    # Some reasoning models wrap their thinking in <think> tags; drop it.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)

    p.plan = _section(text, "PLAN")[:2000]
    note = _first_json_object(_section(text, "CHANGE_NOTE")) or {}
    if isinstance(note, dict):
        p.idea = str(note.get("idea", ""))[:300]
        exp = note.get("expected", {})
        p.expected = exp if isinstance(exp, dict) else {}
        try:
            p.latency_change = int(note.get("latency_change", 0) or 0)
        except (TypeError, ValueError):
            p.latency_change = 0

    for m in re.finditer(rf"{_HEAD}FILE:[ \t]*`?([^\s`*]+)`?[*]*[ \t]*\n(.*?)(?={_HEAD}FILE:|\Z)", text, re.S | re.M):
        name, body = m.group(1), m.group(2)
        code = _FENCE.search(body)
        if code:
            p.files[name] = code.group(1).rstrip() + "\n"

    if not p.files:
        blocks = [b for b in _FENCE.findall(text) if "module" in b]
        if len(blocks) == 1 and len(known_files) == 1:
            p.files[known_files[0]] = blocks[0].rstrip() + "\n"
        elif blocks:
            p.problem = ("the reply contained Verilog but not in '### FILE: <name>' sections, "
                         "so it is unclear which file each block replaces")
        else:
            p.problem = "the reply contained no Verilog code"
    if not p.idea:
        p.idea = (p.plan.split(".")[0][:150] if p.plan else "unnamed change")
    return p
