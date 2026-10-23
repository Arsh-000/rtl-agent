# rtl-agent

An AI agent that **writes, checks and optimizes RTL (Verilog)**. It works on
any synthesizable digital design: give it a plain-English specification, or
your existing RTL, plus your timing constraints (SDC).

The core idea in one sentence:

> **The LLM proposes. Real EDA tools measure. Plain code decides.**
> Every proposal must pass functional checks before it is even measured, the SDC
> can never be changed, and every round is saved so an engineer can see exactly
> what improved and why.

```
 spec (text) ──► LLM writes testbench + RTL ─┐
                                             ├─► round 0: inspect ─► check ─► synthesize ─► STA ─► baseline
 your RTL + SDC ─────────────────────────────┘                                              │
                                                                                            ▼
        ┌──────────── LLM reads measurements and proposes a change ◄──────────────── best so far
        ▼
   compile ─► same ports? ─► testbench / compare with original / bounded formal ─► Yosys ─► OpenSTA
        │ fail: rejected, reason fed back                                                    │
        └────────────────────────────────── keep if better, else reject ◄────────────────────┘
                                             stop when: targets met · no more progress ·
                                             stuck · budget used · LLM has no ideas
```

| Tool | Job |
|---|---|
| **Yosys** | reads the design, synthesizes it to standard cells, reports area |
| **OpenSTA** | timing against your SDC: setup/hold slack, critical paths, power |
| **Icarus Verilog** | compiles and simulates: testbenches and the equivalence check |
| **Any OpenAI-compatible LLM** | Groq (free tier), OpenAI, OpenRouter, a local Ollama, ... |

**Prefer a web page to the terminal?** Run `python3 -m rtl_agent web` (or `bash scripts/docker-web.sh`)
and open http://localhost:8501. See [The web interface](#the-web-interface).

New to the code? Read **[docs/GUIDE.md](docs/GUIDE.md)**. It explains every
file in plain language, in the order the agent uses them.

---

## Quick start

There are two ways to set it up. **Docker is the easiest**: you install only Docker, and every
tool comes inside a ready-made image.

### Option A: Docker (recommended)

```bash
# once: build the image with every tool inside (20-40 minutes)
cd rtl-agent
docker build -t rtl-agent .

# check, then run (the helper passes your API key in and writes results to the current folder)
scripts/docker-run.sh check-tools
scripts/docker-run.sh run examples/dot_product/config.yaml --scripted   # offline demo, no API key
export GROQ_API_KEY=gsk_...                                            # never put the key in a file
scripts/docker-run.sh run examples/dot_product/config.yaml              # the real thing

# or use it from the browser instead: then open http://localhost:8501
bash scripts/docker-web.sh
```

On Windows, install **Docker Desktop** (with "WSL 2 integration" turned on) and run these commands
in the WSL Ubuntu terminal. Without WSL, the same works from PowerShell with plain `docker` commands:

```powershell
cd rtl-agent
docker build -t rtl-agent .
$env:GROQ_API_KEY = "gsk_..."
docker run --rm -it -e GROQ_API_KEY -v "${PWD}:/work" -w /work rtl-agent python3 -m rtl_agent run examples/dot_product/config.yaml
```

 For **your own design**, go to its folder and call the helper from there:
`cd ~/my_designs/my_block && ~/rtl-agent/scripts/docker-run.sh run config.yaml`.
In GitHub Codespaces (or VS Code "Reopen in Container") the same image is used automatically
(`.devcontainer/`), so the commands work without `scripts/docker-run.sh`.

### Option B: install the tools yourself (Ubuntu / WSL2)

```bash
# 1. EDA tools (about 30 minutes; safe to run again - it skips what is already installed)
sudo bash scripts/install_eda_tools.sh
export PATH=/opt/eda/bin:$PATH

# 2. Python packages and the example cell library
pip install -r requirements.txt
bash scripts/get_library.sh            # -> libs/nangate45_typ.lib

# 3. Check everything is found
python3 -m rtl_agent check-tools

# 4a. Offline demo: no API key, replays hand-written proposals (NOT an LLM)
python3 -m rtl_agent run examples/dot_product/config.yaml --scripted

# 4b. The real thing with Groq's free tier
export GROQ_API_KEY=gsk_...              # never put the key in a file
python3 -m rtl_agent run examples/dot_product/config.yaml
```

Open `runs/<project>_<date>_<time>/report.html` to see the result.

### Commands

| Command | What it does |
|---|---|
| `python3 -m rtl_agent check-tools` | are Yosys, OpenSTA and Icarus installed? |
| `python3 -m rtl_agent analyze CONFIG` | measure a design once: ports, clocks, area, timing, power. No LLM. |
| `python3 -m rtl_agent run CONFIG` | the full agent loop |
| `python3 -m rtl_agent run CONFIG --resume runs/<run>` | continue a paused run (e.g. after the daily LLM limit) |
| `python3 -m rtl_agent run CONFIG --scripted` | offline test with hand-written responses |
| `python3 -m rtl_agent report CONFIG runs/<run>` | rebuild the report from saved results |
| `python3 -m rtl_agent web` | the web interface at http://localhost:8501 (`--port`, `--host`, `--workdir`) |

---

## The web interface

The same agent, from a browser page instead of the terminal. Nothing extra to install: it uses
only Python's standard library.

```bash
python3 -m rtl_agent web            # tools installed yourself (Option B)
bash scripts/docker-web.sh          # with Docker (Option A)
```

Then open **http://localhost:8501**. On the page you can:

* **start from your Verilog files**: drag them in, the page reads the design with Yosys,
  suggests the top module and shows its ports and clock; give a clock period or your own SDC;
* **start from an English description**: the AI writes a testbench and the design first;
* **try a built-in example**, offline without a key, or with the real AI;
* set the goals (slack, area, power), what matters most, and whether extra latency is allowed;
* **Measure only (no AI)**: just the starting numbers, in about a minute;
* watch the rounds arrive live, **Pause** and **Continue** a run, open the report, and download the
  best design or everything as a zip.

Good to know:

* The AI key is pasted in the page (or taken from `GROQ_API_KEY` when the server starts). It is
  passed only to the run's process and is **never written to disk**. Paste it again to continue a
  paused run after restarting the server.
* Each run is a separate folder in `webruns/jobs/<id>/` (inputs, `config.yaml`, `agent.log`, and
  the usual `runs/` folder), so a web run can also be continued from the terminal.
* By default the page is reachable from **this computer only**. It has no login, so do not
  expose it to a network (`--host 0.0.0.0`) unless you trust everyone on that network.

---

## Using it on your own design

Start from a template (each is a folder with a commented `config.yaml`):

| You have | Copy this folder | Run (from inside your folder) |
|---|---|---|
| your own Verilog (+ SDC) | `templates/my_rtl_design/` | `~/rtl-agent/scripts/docker-run.sh analyze config.yaml`, then `... run config.yaml` |
| only an English specification | `templates/my_spec_design/` | `~/rtl-agent/scripts/docker-run.sh run config.yaml` |

```bash
cp -r ~/rtl-agent/templates/my_rtl_design ~/my_designs/my_block     # your own folder, anywhere
cd ~/my_designs/my_block                                              # put your .v / .sdc files here
nano config.yaml                                                      # edit the lines marked <--
export GROQ_API_KEY=gsk_...
~/rtl-agent/scripts/docker-run.sh analyze config.yaml                 # measure once, no AI
~/rtl-agent/scripts/docker-run.sh run config.yaml                     # the agent
```

Results go to `runs/` inside your folder. With Docker, keep every file the config uses inside
that folder (it is the only folder the container sees). `liberty: nangate45_typ.lib` finds the
bundled example library; for your own library, put the `.lib` file in the folder and name it.
Without Docker, use `python3 -m rtl_agent ...` instead of the helper script.

The fields that matter (the templates and the examples have the same fields, with comments):

```yaml
project_name: my_block
mode: rtl                       # rtl = start from your RTL;  spec = start from a text spec
design:
  top: my_block                 # top module name
  description: "What the block does, in one or two sentences"   # shown at the top of the report
  rtl_files: [rtl/my_block.v, rtl/helper.v]
sdc: constraints/my_block.sdc   # your timing rules - READ-ONLY for the agent
# clock_period_ns: 2.0          # ...or no SDC: a basic one is generated (and flagged)
testbench:                      # optional, but recommended
  files: [tb/tb_my_block.v]
  top: tb_my_block
  pass_pattern: "TEST PASSED"   # what your testbench prints on success
library:
  liberty: ../../libs/nangate45_typ.lib   # any Liberty file
targets:
  min_setup_slack_ns: 0.0
  max_area_um2: 5000            # optional
  max_power_mw: 2.0             # optional
priority: [timing, area, power] # how two versions are compared
latency:
  policy: fixed                 # fixed: same outputs every cycle; flexible: pipelining allowed
  max_extra_cycles: 2
llm:
  base_url: https://api.groq.com/openai/v1
  model: openai/gpt-oss-120b
  api_key_env: GROQ_API_KEY
  max_output_tokens: 5000                  # hidden reasoning counts toward this
  extra_params: {reasoning_effort: medium} # "low" gave up too early in real runs
```

Every option is documented in `rtl_agent/config.py`. A misspelled option is an
error, not silently ignored.

**Correctness checks used on every proposal:**

1. your testbench, if you give one;
2. automatic **co-simulation against the original design**: a testbench is
   generated from the port list, drives identical random inputs into the
   original and the new version, and compares every output on every cycle. With
   `latency.policy: flexible` it also accepts "same outputs, N cycles later";
3. a **bounded formal check** (Yosys SAT): no input sequence can make the outputs
   differ in the first N cycles after reset. Runs when latency is unchanged.

If none of these can run (for example a design with several clocks and no
testbench), proposals are rejected rather than trusted.

One structural rule is checked too: outputs that come straight from flip-flops in the original
must still do so (`correctness.keep_registered_outputs`, on by default). Moving logic after the
output registers can look "faster" while giving the same values at every clock edge, so the
behaviour checks above cannot see it.

**Plugging in your own flow.** Replace the built-in synthesis or STA with your
own script (commercial tools, OpenROAD, ...):

```yaml
custom_commands:
  sta: "my_sta_flow.sh"   # gets NETLIST, TOP, LIBERTY, SDC, OUT_DIR as environment variables;
                          # must write $OUT_DIR/sta_metrics.json: {"setup_ws":..,"setup_tns":..,"hold_ws":..,"power_mw":..}
  synth: "my_synth.sh"    # gets RTL_FILES, TOP, LIBERTY, CLOCK_PERIOD_NS, OUT_DIR;
                          # must write $OUT_DIR/netlist.v and $OUT_DIR/synth_metrics.json: {"area":..}
```

---

## The examples

Four very different designs, all run with **no code changes**: the evidence
that the tool is general. The numbers below are real Yosys/OpenSTA measurements
(Nangate45 typical) from the offline `--scripted` runs saved in
`sample_runs/`. The proposals in those runs are hand-written, not LLM output.

| Example | What it shows | Result of the offline run |
|---|---|---|
| `dot_product` (arithmetic, 4 multipliers + adders) | existing RTL + SDC, no testbench, **flexible latency** | Round 1 (unsigned multipliers) **rejected**: outputs differ from the original. Round 2 (pipeline + adder tree, +1 cycle) **kept**: setup slack −0.368 → +0.060 ns, area +6.4%, power +17% (more flip-flops). Target met. |
| `sync_fifo` (16×8 FIFO, holds memory) | user testbench + equivalence, **tight clock (0.7 ns)** | Round 1 **passes the user's testbench but is rejected** by the comparison with the original (it changes `dout` while the FIFO is empty, which the testbench never checks). Round 2 (look-ahead head register: next cycle's `dout` computed one cycle early, so the read multiplexer leaves the output path) **kept**: setup slack −0.111 → +0.002 ns, area +6%. Target met. |
| `seq_detector` (finite state machine) | **no SDC** (generated), area first | One-hot encoding kept (−4.9%), then merging two equivalent states kept: area 47.3 → 39.1 µm², 4 → 3 flip-flops. |
| `spec_gray_counter` | **spec mode**: text → testbench → RTL → explore → optimize | 2 architectures explored (one rejected: smaller but much slower), then a look-ahead register version meets the 0.45 ns clock: slack −0.106 → +0.035 ns, at twice the area. |

### With a real LLM

The same examples were also run with a real LLM: Groq's free `openai/gpt-oss-120b`, "medium"
reasoning. These ran on the development laptop before the Docker image existed (Yosys 0.52), so
the absolute numbers differ a little from the table above; what matters is the before/after.

| Design | Result with the free LLM |
|---|---|
| `dot_product` | **target met**: setup slack −0.500 → +0.123 ns (pipelining, +1 cycle of latency, allowed by the config) |
| `sync_fifo` (0.7 ns clock) | **target met**: −0.118 → +0.070 ns (one-hot read pointer) |
| `seq_detector` | area −2.8%; 3 wrong designs proposed by the LLM were caught and rejected |
| `spec_gray_counter` | improved but **not met**: best correct design −0.061 → −0.034 ns |
| a new design (`moving_avg`, written by a "new user") | improved but **not met**: −0.227 → −0.005 ns (a running-sum design; a known better design reaches +0.035) |

In every run, wrong proposals were rejected by the checks before they could be kept. When a target
was missed, it was because the free model ran out of good ideas (repeating itself, or giving up),
not because a wrong design got through. A stronger model is a one-line change in the config
(`llm.model`, `llm.base_url`).

---

## What a run produces

```
runs/dot4_20261009_115527/
  report.html          comparison of all rounds: table, charts, side-by-side RTL diffs, claims vs measurements
  report.md            short text version
  best/                RTL of the best correct version
  round_00/            baseline        (one folder per round, nothing is ever overwritten)
    rtl/               exact Verilog of this round
    diff_vs_parent.patch
    metrics.json       slack, area, power, flip-flops, latency, check results
    change_note.md     what the LLM changed, why, what it expected, whether that came true
    prompt.md          exactly what the LLM was sent
    llm_reply.md       exactly what it answered
    work/              raw Yosys / OpenSTA / Icarus logs and reports
  round_01/ ...
  golden/              the original design, used as the reference
  run_state.json       everything needed to resume or rebuild the report
```

---

## Installing the EDA tools

* **Docker (easiest, any OS):** `docker build -t rtl-agent .` builds an Ubuntu 24.04 image with
  Yosys 0.69, OpenSTA 3.1.0, Icarus Verilog, the Python packages and the example library, using the
  same install script as below. `scripts/docker-run.sh` runs it on the current folder, with your
  own user id (so you can edit the results) and your API key passed in. Tested on Windows 11 +
  WSL2 with Docker Desktop 4.95: the image builds in about 30 minutes, and the offline dot_product run
  inside it gives exactly the same numbers as the development machine (-0.368 -> +0.060 ns,
  area 2686.3 -> 2859.0 um2), where the same laptop's own older Yosys 0.52 had given -0.500 ns.
* **Ubuntu 24.04 / 22.04 or WSL2:** `sudo bash scripts/install_eda_tools.sh` builds Yosys (fixed
  version, `YOSYS_TAG`) and OpenSTA (fixed commit) from source into `/opt/eda` and installs Icarus
  Verilog. It checks before each step: tools already installed are kept, an **older Yosys** is
  detected and a correct one built next to it, and a too-old cmake (Ubuntu 22.04) is replaced
  automatically. `DRY_RUN=1` shows what it would do without doing it.
* **macOS:** Yosys and Icarus are available with Homebrew (`brew install yosys icarus-verilog`);
  OpenSTA must be built from source (see the OpenSTA section of the script).

Different tool versions give somewhat different absolute numbers (for example Yosys 0.52 vs 0.69
gave -0.227 vs -0.121 ns on the same design). Versions are compared on one machine, and every report
lists the tool versions it used.

Tools are found on `PATH` or in `/opt/eda/bin`. Set other locations in the config under `tools:`.
If a config's Liberty file is not found, a file with the same name is looked for in the folder named
by `RTL_AGENT_LIB_DIR` (the Docker image sets it), then in rtl-agent's own `libs/` folder. So
`liberty: nangate45_typ.lib` finds the example library with or without Docker.

## Using Groq's free tier

Free tiers have tight limits (on 9 Oct 2026 Groq listed 8,000 tokens/minute and
200,000 tokens/day for its main open models; check your console). The agent is
built for this:

* **one LLM call per round**: the tools are run by the agent's own code, not by
  the LLM asking for them one by one;
* compact prompts: measured numbers and the worst three paths, not raw reports;
* it waits instead of exceeding tokens/minute, and on a 429 "rate limited"
  answer it waits and retries;
* when the daily limit is reached, the run is **saved and paused**; continue it
  with `--resume`;
* if a design is too big for your limits, it says so before sending anything.

A round of the dot-product example uses about 1,500 prompt tokens plus the reply.
Expect roughly 5–15 rounds per day on the free tier for designs of a few hundred lines.

## Testing

```bash
python3 -m unittest discover -s tests -v        # or: pytest tests
```

* `tests/test_units.py`: parsers, scoring, guardrails, SDC helpers, config checking (no tools needed)
* `tests/test_llm_http.py`: the LLM client against a fake local server: success, 429 + retry, daily limit, token pacing
* `tests/test_web.py`: the web interface: form checks, then the real server with real tools:
  measure-only run, offline run with pause and continue, downloads, error messages
* `tests/test_integration.py`: real tools: full offline runs, a full run through the HTTP client,
  "SDC changed mid-run stops the run", pause on daily limit + resume, custom STA command

All 64 tests pass in the development environment (about five minutes).

## Honest limits

* Timing and area come from synthesis without placement and wiring: good for
  comparing versions, not sign-off numbers. Power assumes every signal toggles in 10% of clock
  cycles (rough: useful for comparing versions, not as an absolute number).
* Random co-simulation only tests what it happens to hit (the report shows how many output
  bits toggled). The formal check is bounded (first N cycles after reset).
* Automatic equivalence supports one clock. Multi-clock designs need a testbench.
* Hold violations are reported, not optimized (they are fixed in physical design).
  Set `targets.min_hold_slack_ns` to make hold a target anyway.
* Open models write wrong Verilog more often than the strongest models, so expect more
  rejected rounds. They cost tokens but never corrupt the result.
* The free model often stops improving before the target (see "With a real LLM" above). The
  agent detects repeats, cut-off replies and "no more ideas" and stops cleanly, but it cannot make
  a weak model smarter.
* In spec mode the LLM writes the testbench too. The agent checks it against its own reference
  model, but an engineer should still read it: a testbench can only check what the spec says.
* The formal check can end "inconclusive" on wide arithmetic (multipliers); the random
  co-simulation still runs, and the report says which checks ran.
* The Docker image takes about 30 minutes to build the first time (Yosys is compiled from source).

## Troubleshooting

| You see | Do this |
|---|---|
| `permission denied while trying to connect to the Docker daemon` | `sudo usermod -aG docker $USER`, then open a **new** terminal |
| `Docker image 'rtl-agent' not found` | build it first: `docker build -t rtl-agent .` (in the rtl-agent folder) |
| `Permission denied` when running `scripts/docker-run.sh` | run it with bash: `bash scripts/docker-run.sh ...` |
| `$'\r': command not found` | the files got Windows line endings: `sed -i 's/\r$//' scripts/*.sh` |
| `GROQ_API_KEY is not set` | `export GROQ_API_KEY=gsk_...` in the same terminal (create a key at console.groq.com) |
| `Address already in use` when starting the web page | another program uses port 8501: `python3 -m rtl_agent web --port 8600` (Docker: `PORT=8600 bash scripts/docker-web.sh`) |
| the web page does not open from Windows | use http://localhost:8501 (not 127.0.0.1 inside WSL), and keep the terminal that runs the server open |
| `429` / "daily limit" | the free tier's limit: the run pauses; continue later with `--resume runs/<run>` |

## Project layout

```
rtl_agent/
  controller.py     the agent loop and stop rules      <- start reading here
  evaluate.py       the "judge": checks + measurements for one design version
  config.py         reads and validates the YAML config
  prompts.py        every text the LLM sees
  reply_parser.py   turns the LLM's reply into files + claims
  spec_mode.py      spec -> testbench -> RTL
  scoring.py        comparing versions, targets, claim checking
  guardrails.py     SDC read-only, safe file names, frozen interface
  history.py        round folders, diffs, run state
  sdc.py            light SDC helpers (fingerprint, clocks, generated SDC)
  report.py         report.html / report.md
  tools.py          finding and running external programs
  cli.py            command line
  web/              the web interface: server.py (web server), jobs.py (runs as background
                    processes), static/index.html (the page)
  adapters/         inspect.py  simulate.py  equiv.py  synth.py  sta.py
  llm/              base.py  openai_compat.py  scripted.py
examples/           four example designs with configs
tests/              unit, fake-LLM and integration tests
scripts/            tool installer, library download
templates/          starting folders for your own RTL or your own spec
docs/               GUIDE.md (walkthrough); RTL_Agent_Design.pdf is the original design document
                    (written before Docker and the later checks; GUIDE.md and this README are current)
sample_runs/        the offline runs shown above, with their reports
Dockerfile          the image with every tool; scripts/docker-run.sh runs it
.devcontainer/      the same image for GitHub Codespaces / VS Code
```
