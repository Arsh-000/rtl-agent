"""How two design versions are compared, and when targets are met.

The user's 'priority' list decides the order, for example
[timing, area, power] means:

    1. less timing violation wins (once timing is met, all are equal here)
    2. then smaller area wins
    3. then lower power wins

Small differences (below min_improvement_pct, or 5 ps for timing) count
as a tie, so the agent does not chase noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

TIMING_TIE_NS = 0.005


@dataclass
class Metrics:
    setup_ws: Optional[float]
    setup_tns: Optional[float]
    hold_ws: Optional[float]
    area: Optional[float]
    power_mw: Optional[float]
    flop_bits: int = 0
    cell_count: Optional[int] = None
    latency: int = 0

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "Metrics":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


def timing_violation(m: Metrics, targets) -> float:
    """How far (ns) the design is from meeting its setup and hold targets. 0 = met."""
    v = 0.0
    if m.setup_ws is not None:
        v += max(0.0, targets.min_setup_slack_ns - m.setup_ws)
    if m.hold_ws is not None and targets.min_hold_slack_ns is not None:
        v += max(0.0, targets.min_hold_slack_ns - m.hold_ws)
    return v


def targets_status(m: Metrics, targets) -> dict[str, bool]:
    status = {"timing": timing_violation(m, targets) <= 1e-9}
    if targets.max_area_um2 is not None:
        status["area"] = m.area is not None and m.area <= targets.max_area_um2
    if targets.max_power_mw is not None:
        status["power"] = m.power_mw is not None and m.power_mw <= targets.max_power_mw
    return status


def all_targets_met(m: Metrics, targets) -> bool:
    return all(targets_status(m, targets).values())


def _key(m: Metrics, objective: str, targets) -> float:
    if objective == "timing":
        return timing_violation(m, targets)
    if objective == "area":
        return m.area if m.area is not None else float("inf")
    return m.power_mw if m.power_mw is not None else float("inf")


def compare(a: Metrics, b: Metrics, priority: list[str], targets, min_improvement_pct: float):
    """Return (+1 if a is better, -1 if b is better, 0 if tie), and the deciding objective."""
    for obj in priority:
        ka, kb = _key(a, obj, targets), _key(b, obj, targets)
        if obj == "timing":
            if abs(ka - kb) <= TIMING_TIE_NS:
                continue
        else:
            scale = max(abs(kb), 1e-12)
            if abs(ka - kb) / scale * 100.0 < min_improvement_pct:
                continue
        return (1 if ka < kb else -1), obj
    return 0, None


def pct_change(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old) * 100.0


def judge_claims(expected: dict, new: Metrics, parent: Metrics) -> dict:
    """Compare what the LLM said would happen with what the tools measured.

    expected looks like {"setup_slack": "improve", "area": "worsen", "power": "same"}.
    """
    def direction(delta: Optional[float], better_if_positive: bool, tol: float) -> Optional[str]:
        if delta is None:
            return None
        if abs(delta) <= tol:
            return "same"
        good = delta > 0 if better_if_positive else delta < 0
        return "improve" if good else "worsen"

    measured = {
        "setup_slack": direction(None if new.setup_ws is None or parent.setup_ws is None
                                 else new.setup_ws - parent.setup_ws, True, TIMING_TIE_NS),
        "area": direction(pct_change(new.area, parent.area), False, 0.5),
        "power": direction(pct_change(new.power_mw, parent.power_mw), False, 0.5),
    }
    out = {}
    for key, actual in measured.items():
        claim = str(expected.get(key, "")).lower().strip() or None
        if claim and claim not in ("improve", "worsen", "same"):
            claim = None
        out[key] = {"claimed": claim, "measured": actual,
                    "correct": None if (claim is None or actual is None) else claim == actual}
    return out
