"""Spec mode: from a plain-English specification to a first working RTL.

    1. The LLM writes a self-checking TESTBENCH with a reference model.
    2. In a separate call, the LLM writes the RTL.
    3. The RTL is simulated against the testbench. If it fails, the errors are
       sent back and the LLM fixes the RTL (up to spec.rtl_attempts tries).
    4. If EVERY RTL attempt fails, the testbench itself becomes the suspect
       (a common LLM mistake is an off-by-one-cycle race in the testbench).
       A new testbench is requested, with the old errors as feedback, and the
       RTL versions we already have are re-checked against it first. This
       repeats up to spec.testbench_attempts testbenches.

The testbench and RTL come from separate calls so the RTL is not simply
written to match the testbench's assumptions. Still, both come from the same
spec and the same model: a human should REVIEW the generated testbench
(saved in <run>/spec/). The report says so.

Everything is saved under <run>/spec/ and the LLM conversations under
<run>/round_00/.
"""

from __future__ import annotations

from pathlib import Path

from . import prompts
from .adapters import DesignFiles
from .adapters.simulate import compile_check, run_testbench
from .guardrails import safe_filename
from .reply_parser import parse_reply


def _check_rtl(agent, files: dict, adir: Path, tb: dict) -> str | None:
    """Write one RTL version to adir and run the testbench on it. Returns None if it passes,
    otherwise the error text."""
    cfg = agent.cfg
    adir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in files.items():
        p = adir / safe_filename(name)
        p.write_text(text)
        paths.append(p)
    design = DesignFiles(paths, cfg.design.top, cfg.design.include_dirs, cfg.design.defines)
    comp = compile_check(design, adir / "work", agent.tools["iverilog"])
    if not comp.ok:
        return "The RTL does not compile:\n" + comp.detail
    sim = run_testbench(design, [Path(f) for f in tb["files"]], tb["top"], adir / "work",
                        agent.tools["iverilog"], agent.tools["vvp"], tb["pass_pattern"],
                        tb["fail_pattern"], tb["timeout_s"])
    return None if sim.passed else "The testbench reported failures:\n" + sim.detail


def _new_testbench(agent, spec_dir: Path, tb_no: int, previous_failure: str | None) -> tuple[dict, str]:
    from .controller import StopRun, log
    cfg, spec, top = agent.cfg, agent.cfg.spec, agent.cfg.design.top
    reply = agent.ask_llm(prompts.SPEC_SYSTEM, prompts.spec_testbench_prompt(
        spec.text, top, spec.interface, spec.clock_port, spec.reset_port, previous_failure),
        0, f"testbench_{tb_no}")
    prop = parse_reply(reply, [f"tb_{top}.v"])
    if not prop.files:
        raise StopRun(f"spec mode: the LLM's testbench reply was not usable ({prop.problem})")
    name, tb_text = next(iter(prop.files.items()))
    tb_dir = spec_dir / f"testbench_{tb_no}"
    tb_dir.mkdir(parents=True, exist_ok=True)
    tb_path = tb_dir / safe_filename(name)
    tb_path.write_text(tb_text)
    log(f"spec mode: generated testbench {tb_no} saved to {tb_path} - please review it")
    return ({"files": [str(tb_path)], "top": f"tb_{top}", "pass_pattern": "TEST PASSED",
             "fail_pattern": r"TEST FAILED|ERROR", "timeout_s": 120}, tb_text)


def build_from_spec(agent, user_testbench: dict | None):
    from .controller import StopRun, log
    cfg = agent.cfg
    spec, top = cfg.spec, cfg.design.top
    spec_dir = agent.store.dir / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "specification.txt").write_text(spec.text)

    # A testbench given by the user is trusted and never regenerated.
    tb_rounds = 1 if user_testbench else max(1, spec.testbench_attempts)
    candidates: list[dict] = []          # every RTL version written so far (re-checked on a new testbench)
    last_error = ""
    for tb_no in range(1, tb_rounds + 1):
        if user_testbench:
            tb, tb_info = user_testbench, None
            tb_text = "\n".join(Path(f).read_text() for f in user_testbench["files"])
        else:
            tb_info, tb_text = _new_testbench(agent, spec_dir, tb_no, last_error if tb_no > 1 else None)
            tb = tb_info

        # First re-check the RTL versions we already have against the new testbench (costs no tokens).
        for i, files in enumerate(candidates, 1):
            if _check_rtl(agent, files, spec_dir / f"tb{tb_no}_recheck_{i}", tb) is None:
                log(f"spec mode: an earlier RTL version passes testbench {tb_no}")
                agent.state["spec_attempts"] = len(candidates)
                return files, tb_info

        # Then ask for RTL, repairing it with the error messages.
        reply = agent.ask_llm(prompts.SPEC_SYSTEM, prompts.spec_rtl_prompt(spec.text, top, spec.interface, tb_text),
                              0, f"tb{tb_no}_rtl_1")
        files: dict = {}
        for attempt in range(1, spec.rtl_attempts + 1):
            prop = parse_reply(reply, [f"{top}.v"])
            files = prop.files
            if not files:
                error = f"Your reply was not usable: {prop.problem or 'no FILE sections'}."
            else:
                candidates.append(files)
                error = _check_rtl(agent, files, spec_dir / f"tb{tb_no}_attempt_{attempt}", tb)
                if error is None:
                    log(f"spec mode: RTL attempt {attempt} passes testbench {tb_no}")
                    agent.state["spec_attempts"] = len(candidates)
                    return files, tb_info
            log(f"spec mode: RTL attempt {attempt} failed against testbench {tb_no}")
            (spec_dir / f"tb{tb_no}_attempt_{attempt}_error.txt").write_text(error)
            last_error = error
            if attempt < spec.rtl_attempts:
                reply = agent.ask_llm(prompts.SPEC_SYSTEM, prompts.spec_fix_prompt(error, files or {}), 0,
                                      f"tb{tb_no}_rtl_{attempt + 1}")
        if tb_no < tb_rounds:
            log(f"spec mode: every RTL attempt failed - suspecting testbench {tb_no}, asking for a new one")

    hint = ("Provide your own testbench in the config ('testbench:') or review the generated ones"
            if not user_testbench else "Check your testbench and the specification")
    raise StopRun(f"spec mode: no RTL passed the testbench ({len(candidates)} RTL version(s), {tb_rounds} "
                  f"testbench(es)). {hint} in {spec_dir}. Last error:\n{last_error[:1500]}")
