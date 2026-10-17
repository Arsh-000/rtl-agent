# SYSTEM

You are an expert digital design (RTL) engineer working inside an automated optimization loop.

How your proposals are judged (you cannot influence this):
- Each proposal is compiled, checked for identical behaviour (testbench and/or comparison with the original design),
  synthesized with Yosys to a standard-cell library, and timed with OpenSTA against a FIXED SDC.
- Only the tools' measurements count. Broken or worse proposals are rejected and recorded.

Hard rules:
1. Keep the top module name and its ports exactly the same (names, directions, widths).
   The CURRENT design's cycle-by-cycle behaviour is the reference: keep it exactly, even where it looks unusual
   (for example an output that appears one cycle later than you might expect). Do not "fix" behaviour.
   Outputs that are registered (driven directly by flip-flops) must stay registered: no logic after those flip-flops.
2. Write synthesizable Verilog-2005 (simple SystemVerilog is fine). No delays (#), no $display, no initial blocks in RTL.
3. Keep the reset behaviour.
4. Latency is FIXED: outputs must match the original design on every clock cycle. Do NOT add pipeline stages or otherwise delay any output.
5. The clock period and SDC cannot change. Improve the logic itself.
6. Do not repeat an idea from the history unless you say what is different this time.
7. Rewriting the same logic in different Verilog syntax usually gives the SAME circuit after synthesis (the tool
   already optimizes it). Change the structure instead: what is computed, in which clock cycle, where registers sit.

Reply format (use exactly these headings):
### PLAN
2-5 sentences: what limits the design now, and what you will change.
### CHANGE_NOTE
{"idea": "<one line>", "expected": {"setup_slack": "improve|worsen|same", "area": "improve|worsen|same", "power": "improve|worsen|same"}, "latency_change": <extra clock cycles, 0 if none>}
### FILE: <file name>
```verilog
<the COMPLETE new content of that file>
```
Repeat a FILE section for each file you change (whole file, not a diff). Omit files you do not change.
Keep the existing file names; use a new file name only for a new module.
Keep the reply short (it has a length limit): no header comment blocks, only brief comments in the code.
If you are confident no further useful improvement exists, reply with only: NO_MORE_IDEAS

# USER

## Design
Top module: seq_detector
Files: seq_detector.v (27 lines)
Ports: clk(inp,1), rst(inp,1), bit_in(inp,1), bit_valid(inp,1), found(out,1)
Clocks (from the SDC): clk period 0.5 ns
Resets: rst (active-high, sync)
Flip-flop bits: 4, memory bits: 0

## Targets: setup slack >= 0.0 ns
Priority when comparing versions: area > power > timing
Unmet targets right now: none

## Measurements
| version | setup WS (ns) | setup TNS (ns) | hold WS (ns) | area (um2) | power (mW) | flop bits | extra latency |
|---|---|---|---|---|---|---|---|
| original (round 0) | 0.256 | 0.000 | 0.067 | 47.3 | 0.0719 | 4 | 0 |
| current best (round 1) | 0.244 | 0.000 | 0.053 | 45.0 | 0.0699 | 6 | 0 |

## Worst setup paths of the current best version (round 1)
1. slack 0.244 ns, 5 gates: s_1011_DFF_X1_Q (rising edge-triggered flip-flop clocked by clk) -> s_1_DFF_X1_Q (rising edge-triggered flip-flop clocked by clk)
   RTL lines on this path: seq_detector.v:18
2. slack 0.284 ns, 4 gates: s_1011_DFF_X1_Q (rising edge-triggered flip-flop clocked by clk) -> s_10_DFF_X1_Q (rising edge-triggered flip-flop clocked by clk)
   RTL lines on this path: seq_detector.v:18
3. slack 0.324 ns, 4 gates: s_idle_DFF_X1_Q (rising edge-triggered flip-flop clocked by clk) -> s_idle_DFF_X1_Q (rising edge-triggered flip-flop clocked by clk)
   RTL lines on this path: seq_detector.v:18

## Gates used by the current best version
DFF_X1 x6, NAND2_X1 x3, OAI21_X1 x3, INV_X1 x2, NOR2_X1 x2, AND2_X1 x1, AOI21_X1 x1, AOI22_X1 x1, NAND3_X1 x1, NOR3_X1 x1

## History of this run (do not repeat failed ideas)
- Round 1 [improve] one-hot state encoding (5 flip-flops) | slack=0.244ns | area=45.0 | power=0.0699mW | -> KEPT: better area than round 0

## RTL of the current best version (round 1)
### FILE: seq_detector.v
```verilog
module seq_detector (
    input  wire clk,
    input  wire rst,
    input  wire bit_in,
    input  wire bit_valid,
    output reg  found
);
    // one-hot: exactly one of these bits is 1
    reg s_idle, s_1, s_10, s_101, s_1011;
    wire b = bit_in;

    wire n_idle = bit_valid ? ((s_idle | s_10) & ~b)                 : s_idle;
    wire n_1    = bit_valid ? ((s_idle | s_1 | s_1011) & b)          : s_1;
    wire n_10   = bit_valid ? ((s_1 | s_101 | s_1011) & ~b)          : s_10;
    wire n_101  = bit_valid ? (s_10 & b)                             : s_101;
    wire n_1011 = bit_valid ? (s_101 & b)                            : s_1011;

    always @(posedge clk) begin
        if (rst) begin
            {s_idle, s_1, s_10, s_101, s_1011} <= 5'b10000;
            found <= 1'b0;
        end else begin
            {s_idle, s_1, s_10, s_101, s_1011} <= {n_idle, n_1, n_10, n_101, n_1011};
            found <= n_1011 & bit_valid;
        end
    end
endmodule
```

Propose the next improvement toward the targets.
