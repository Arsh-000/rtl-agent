"""Command-line interface.

    python -m rtl_agent check-tools                  are Yosys, OpenSTA, Icarus installed?
    python -m rtl_agent analyze  CONFIG              measure the design once (no LLM)
    python -m rtl_agent run      CONFIG              the full agent loop
    python -m rtl_agent run      CONFIG --resume RUN_DIR   continue a paused run
    python -m rtl_agent run      CONFIG --scripted         offline test (hand-written responses, not an LLM)
    python -m rtl_agent report   CONFIG RUN_DIR      rebuild report.html from saved results
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load_config


def _check_tools(args) -> int:
    from .config import ToolsCfg
    from .tools import find_tool, tool_versions
    t = ToolsCfg()
    if args.config:
        t = load_config(args.config).tools
    ok = True
    versions = tool_versions(t)
    for name in ("yosys", "sta", "iverilog", "vvp"):
        path = find_tool(getattr(t, name))
        ok &= path is not None
        print(f"  {name:9s} {'OK  ' if path else 'MISSING'} {path or ''}  {versions.get(name, '')}")
    print("All tools found." if ok else "Some tools are missing - see README, 'Installing the EDA tools'.")
    return 0 if ok else 1


def _analyze(args) -> int:
    """Baseline only: understand and measure the design without any LLM."""
    cfg = load_config(args.config)
    if cfg.mode != "rtl":
        print("'analyze' needs an existing design (mode: rtl).", file=sys.stderr)
        return 1
    from .controller import Agent
    state = Agent(cfg, analyze_only=True).run()
    return 0 if state.get("best_round") is not None else 1


def _run(args) -> int:
    cfg = load_config(args.config)
    if args.max_rounds is not None:
        cfg.loop.max_rounds = args.max_rounds
    if args.scripted:
        # Offline test: replay hand-written responses instead of calling an LLM.
        if not cfg.llm.scripted_dir:
            print("--scripted needs llm.scripted_dir in the config", file=sys.stderr)
            return 1
        cfg.llm.provider = "scripted"
    from .controller import Agent
    state = Agent(cfg, resume_dir=Path(args.resume) if args.resume else None).run()
    return 0 if state.get("status") == "finished" else 2


def _report(args) -> int:
    cfg = load_config(args.config)
    from .history import RunStore
    from .report import write_reports
    store = RunStore(Path(args.run_dir))
    write_reports(store, store.load_state(), cfg)
    print(f"wrote {store.dir / 'report.html'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rtl_agent", description="LLM-driven RTL generation and optimization")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check-tools", help="check that the EDA tools are installed")
    p.add_argument("config", nargs="?")
    p.set_defaults(fn=_check_tools)
    p = sub.add_parser("analyze", help="measure a design once, no LLM")
    p.add_argument("config")
    p.set_defaults(fn=_analyze)
    p = sub.add_parser("run", help="run the agent")
    p.add_argument("config")
    p.add_argument("--resume", metavar="RUN_DIR", help="continue a paused run")
    p.add_argument("--max-rounds", type=int, help="override loop.max_rounds")
    p.add_argument("--scripted", action="store_true",
                   help="offline test: replay the hand-written responses in llm.scripted_dir (not an LLM)")
    p.set_defaults(fn=_run)
    p = sub.add_parser("report", help="rebuild the report of a run")
    p.add_argument("config")
    p.add_argument("run_dir")
    p.set_defaults(fn=_report)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1
