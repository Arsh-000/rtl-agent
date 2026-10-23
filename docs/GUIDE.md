# rtl-agent: a guided walkthrough

This guide explains how the tool works, file by file, in plain language. Read it
with the code open. Each section names the file and function to look at.

---

## 1. The big picture

An "agent" here is **an LLM inside a loop that our code controls**. The LLM never
runs anything itself. Each round:

1. our code **measures** the current best design (real tools),
2. our code **sends** the LLM a compact summary: numbers, worst timing paths, the RTL, history,
3. the LLM **replies** with a new version of the RTL plus a note on what it expects,
4. our code **checks** the new version (does it compile? same ports? same behaviour?),
5. our code **measures** it (Yosys area, OpenSTA timing and power),
6. our code **decides**: keep it if it is better, reject it otherwise, and saves everything,
7. our code checks the **stop rules**: targets met? no progress? stuck? budget used?

The LLM only does step 3. Everything else is ordinary, deterministic Python.

---

## 2. One run, step by step

Command: `python3 -m rtl_agent run examples/dot_product/config.yaml`

| Step | What happens | Code | What appears on disk |
|---|---|---|---|
| A. Setup | config is read and validated; tools are located | `config.load_config`, `controller.Agent._setup` | `runs/dot4_<time>/config.yaml` |
| B. Start design | rtl mode: your files are copied. spec mode: the LLM writes a testbench, then RTL (`spec_mode.py`) | `Agent._baseline` | `round_00/rtl/` |
| C. Understand | Yosys reads the design: ports, clocks, resets, flip-flops, memory bits. The SDC is fingerprinted and checked against the ports | `adapters/inspect.py`, `sdc.py` | `round_00/work/inspect/` |
| D. Baseline | the original is synthesized and timed. A flattened copy becomes the **golden reference** | `evaluate.Evaluator.evaluate`, `adapters/equiv.make_golden` | `round_00/metrics.json`, `golden/golden.v`, `best/` |
| E. Explore | (optional) the LLM proposes *different architectures*; each is judged like any proposal | `Agent.run` → `_try_proposal("explore")` | `round_01/`... |
| F. Improve | the loop of section 1, until a stop rule fires | `Agent._try_proposal`, `Agent._stop_reason` | one `round_NN/` per proposal |
| G. Finish | the report is written; `best/` holds the best correct RTL | `report.write_reports` | `report.html`, `report.md` |

---

## 3. The files, one by one

### `controller.py`: the loop (start here)
`Agent.run()` is the whole agent in about 50 lines: baseline, then exploration,
then `while True: check stop rules → _try_proposal()`. `_try_proposal()` is one
round: build the prompt (`_context`), ask the LLM (`ask_llm`), parse the reply,
write the files, reject duplicates, evaluate, compare with the best, keep or
reject. If a rejected version measures exactly the same as an earlier round (same
timing, area, power, flip-flops), the LLM is told it rebuilt the same circuit with different syntax. Likewise, a
broken version that fails in exactly the same way as an earlier one is reported as "the same mistake as
round N", so the LLM stops rewording an idea that does not work. `_stop_reason()` holds the stop rules:

* **success**: all targets met (unless `optimize_beyond_targets`)
* **plateau**: `patience` rounds in a row without a meaningful improvement
* **stuck**: `max_consecutive_failures` broken or duplicate proposals in a row
* **budget**: `max_rounds` or `max_minutes`
* and the LLM itself may answer `NO_MORE_IDEAS`.

There is **no fixed number of rounds**. `max_rounds` is only a safety net.

### `evaluate.py`: the judge
`Evaluator.evaluate()` runs the same six steps for every version (baseline,
candidates, improvements), so all are measured the same way:
SDC unchanged → compiles → same ports → correct (co-simulation against the original,
bounded formal, then the testbench) → synthesis → STA. A version that fails steps 1–4 is
never measured. The comparison with the original runs before the testbench because it can
explain failures precisely, e.g. "right outputs, but 1 clock cycle EARLIER" (or LATER, when a change
adds a delay the latency policy does not allow).

### `adapters/`: one file per tool
Same shape everywhere: files in, a small result object out, raw logs saved.

* `inspect.py` runs Yosys `proc; flatten; opt_dff`, writes JSON, and looks at the
  flip-flop cells: whatever drives their clock pin is a clock, whatever drives their
  reset pin is a reset (with polarity). This is how the tool learns *any* design.
* `simulate.py` compiles with Icarus (`compile_check`) and runs a self-checking
  testbench, looking for its PASS/FAIL text (`run_testbench`).
* `equiv.py` holds the correctness checks against the original:
  * `make_golden`: the original, flattened and renamed `<top>__golden`;
  * `generate_cosim_tb`: **writes a testbench automatically** from the port list. It
    instantiates golden and candidate side by side, holds reset, then drives the same
    random inputs into both and compares all outputs every cycle. Reset is pulsed several
    times during the run, so the cycles right after a reset are compared in different states. For flexible latency
    it keeps a short history of the golden outputs and compares the candidate against
    "the golden output L cycles ago" for every allowed L, all in one simulation;
  * `formal_check`: Yosys `miter` + `sat`: "is there any input sequence of up to N
    cycles after reset where the outputs differ?" No → bounded proof. Yes → the exact input
    sequence is turned into a short trace ("cycle 1: rst=1 ... at cycle 8 the outputs differ")
    and shown to the LLM, so it can see why its change was rejected.
* `synth.py`: Yosys `synth` → `dfflibmap` (flip-flops to library cells) →
  `abc -D <clock period>` (logic to library gates, aiming for the clock) →
  `stat -liberty` (area). The netlist keeps `src` attributes, so a timing path
  can be traced back to RTL line numbers.
* `sta.py`: an OpenSTA Tcl script: read library, netlist, **your SDC**; `check_setup`;
  `report_checks` (worst paths, sorted by slack); `report_worst_slack`/`report_tns`;
  `report_power`. The output is cut into sections and parsed.

### `llm/`: the LLM, behind one small interface
Every provider has `complete(messages) -> LLMReply`.
* `openai_compat.py` talks to any OpenAI-compatible API (Groq, OpenAI, Ollama...).
  It keeps a 60-second window of tokens used and **waits** rather than exceed
  tokens/minute; retries on 429; raises `DailyLimitReached` (the run pauses and
  can be resumed) or `RequestTooLarge` (clear message, nothing sent).
  If a reply is cut off at the output limit (the model's hidden reasoning used it up), the controller
  asks the same question once more with `reasoning_effort: low` (`Agent.ask_llm`).
* `scripted.py` is **not an LLM**: it replays hand-written answers, for offline
  tests and demos. Reports made with it say so in a banner.

### `prompts.py` and `reply_parser.py`: talking to the LLM
All text the LLM sees is built in `prompts.py`. Open any `round_NN/prompt.md`
to see a real one. The reply format is fixed (`### PLAN`, `### CHANGE_NOTE` with
a small JSON of expected effects, `### FILE: name.v` with the whole file).
`reply_parser.py` is forgiving: bold headings, a single unlabeled code block and
`<think>` sections are handled; an empty reply is a failed round, not "no ideas".

### `scoring.py`: what "better" means
`compare(a, b, priority, ...)` walks the user's priority list, e.g.
`[timing, area, power]`: less timing violation wins; if equal (e.g. both meet
timing), smaller area wins; then lower power. Differences under
`min_improvement_pct` (or 5 ps for timing) are ties, so noise is not "progress".
`judge_claims()` compares what the LLM said would happen with what was measured.

### `guardrails.py`: rules enforced by code
* SDC fingerprint (SHA-256) checked before every measurement. If the file
  changes during a run, the run stops.
* File names from the LLM must be plain `.v/.sv` names, written only inside the run folder.
* The top-level ports must stay identical (names, directions, widths).
* Registered outputs must stay registered (`registered_output_differences`). `inspect.py` marks an
  output as registered when every bit comes straight from a flip-flop. Moving the logic after the
  flip-flops gives the same values at each clock edge (so co-simulation passes) but the output is no
  longer glitch-free; a real Groq run did exactly this to "fix" timing. Can be turned off per design
  (`correctness.keep_registered_outputs: false`) when an output does not need to be registered.

### `history.py`: run memory
One folder per round, never overwritten; `run_state.json` (written atomically)
holds every round's record, so a run can be resumed and the report rebuilt.
`RoundRecord.one_liner()` is what the LLM sees as history ("Round 2 [improve]
... -> REJECTED: ..."), which stops it repeating failed ideas.

### `sdc.py`, `spec_mode.py`, `report.py`, `cli.py`, `tools.py`
* `sdc.py`: fingerprint, read `create_clock` lines, check SDC ports exist,
  generate a basic SDC when only a clock period is given (flagged in the report).
* `spec_mode.py`: LLM writes the testbench (with a reference model), then, in a
  separate call, the RTL; failures are fed back for up to `rtl_attempts` tries.
  If every RTL attempt fails, the *testbench* becomes the suspect (a common LLM mistake
  is an off-by-one-cycle race): a new one is requested with the errors as feedback, and
  the RTL already written is re-checked against it first (up to `testbench_attempts`).
  The testbench prompt also tells the LLM how to write its reference model so it cannot add a hidden
  one-cycle delay (expected outputs are computed from the model's state with `assign`, not as a second register).
* `report.py`: builds `report.html` from `run_state.json`. The summary text is
  generated by code from measurements, never by the LLM.
* `cli.py`: the commands. `tools.py`: finds programs, runs them with timeouts and logs.

### `web/`: the browser page (`python3 -m rtl_agent web`)
The web interface adds **no new agent logic**. It is a thin layer that fills in the same
`config.yaml` a terminal user would write, then runs the same command.
* `static/index.html`: the page (HTML, CSS and JavaScript in one file). It sends the form
  as JSON and asks for progress every 2 seconds while a run is going.
* `server.py`: a small web server from Python's standard library. Each address
  (`/api/runs`, `/api/inspect`, ...) calls one function in `jobs.py`. Mistakes in the form come
  back as plain messages (status 400); a crash never shows a stack trace to the page.
* `jobs.py`: one folder per run (`webruns/jobs/<id>/`). It checks the form, saves the uploaded
  files, writes `config.yaml` and checks it with the agent's own `load_config`, then starts
  `python -m rtl_agent run config.yaml` as a **separate background process**, so the page
  stays responsive and a crash in a run cannot take the server down. Progress is read from
  the run's `run_state.json` and `agent.log`.
  **Pause** sends Ctrl+C (SIGINT) to that process: the agent's normal `KeyboardInterrupt` handler
  saves everything and marks the run "paused". **Continue** starts it again with `--resume`.
  The AI key is put only in that process's environment, never in a file.

---

## 4. Agent memory: what the LLM knows

The LLM has **no memory** of its own. Every call starts blank. What it "knows" is
what `Agent._context()` puts in the prompt:

| Memory layer | Where it lives | Sent to the LLM? |
|---|---|---|
| Short-term: current best RTL, its metrics and worst paths, the last failure reason | built fresh each round | yes |
| Run memory: every round's RTL, diff, metrics, reports | `round_NN/` folders, `run_state.json` | one line per round |
| Project memory: config, targets, SDC, testbench | config files | targets and rules only |
| Long-term (across runs) | not implemented: a deliberate "future work" item | no |

If the prompt would be too big for the tokens-per-minute limit, `_context()`
shrinks it in a fixed order (older history, gate list, warnings, extra paths)
before giving up with a clear message.

---

## 5. Reading the report

* **The design**: what the design is (`design.description` in the config, else the spec text, else the
  comment above the module), its ports with their roles, clock, size, and the original RTL.
* **Summary**: generated from measurements: start, best, what each kept change did, why it stopped.
* **Targets**: original vs best for each target (green met, red not met).
* **Charts**: slack, area, power per round (green kept, orange measured but not better).
* **All rounds**: one row per round, including rejected ones and why.
* **Round details**: the LLM's plan; *LLM expected vs tools measured*; check results;
  worst paths; a side-by-side RTL diff.

---

## 6. Adding your own design: checklist

1. RTL files and the top module name.
2. An SDC (or `clock_period_ns` for a generated one; review it).
3. A Liberty `.lib` for your library (Nangate45 is the example one).
4. Optional but recommended: a self-checking testbench that prints a pass message.
5. Targets and priority; latency policy (`fixed` unless pipelining is acceptable).
6. `python3 -m rtl_agent analyze config.yaml` first: check the report's ports,
   clocks, resets and "Constraint checks" look right.
7. Then `run`.

Common problems: *"the starting design does not pass its own checks"* (the
original fails its testbench or does not compile with Icarus);
*"no correctness check possible"* (more than one clock and no testbench: add one);
*"request too large"* (design too big for your LLM's per-minute limit).

---

## 7. Explaining it in an interview

**One sentence:** "It's an LLM in a controlled loop: the LLM proposes RTL changes,
but every decision comes from real synthesis and timing tools, every change must
pass functional checks first, the SDC can't be touched, and every round is saved
so engineers can see what improved and why."

| Likely question | Short answer | Where in the code |
|---|---|---|
| How do you know the new RTL still works? | testbench + co-simulation against the original every round, plus a bounded formal proof when latency is unchanged | `evaluate.py`, `adapters/equiv.py` |
| What if the LLM says "faster" but it isn't? | its claim is recorded and compared with the measurement; only measurements decide | `scoring.judge_claims`, `compare` |
| How does it know when to stop? | targets met, plateau, stuck, budget, or the LLM has no ideas | `controller._stop_reason` |
| Why not just loosen the clock? | SDC is read-only; fingerprint checked every round | `guardrails.check_sdc_unchanged` |
| Pipelining changes latency: is it still "correct"? | the config's latency policy decides; co-simulation finds the latency that matches | `generate_cosim_tb` |
| How is it general? | nothing is design-specific: ports, clocks and resets are discovered by Yosys; flows are pluggable | `adapters/inspect.py`, `custom_commands` |
| Why no LangChain? | the loop is about 50 lines and fully visible; the decisions are custom code anyway | `controller.run` |
| Free LLM token limits? | one call per round, compact prompts, pacing, pause and resume | `llm/openai_compat.py` |
| Weaknesses? | pre-layout numbers, rough power, bounded formal, single-clock auto-equivalence, free-model reliability | README "Honest limits" |
