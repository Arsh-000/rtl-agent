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
Top module: gray_counter
Files: gray_counter.v (20 lines)
Ports: clk(inp,1), rst(inp,1), en(inp,1), up(inp,1), gray(out,8)
Clocks (from the SDC): clk period 0.45 ns
Resets: rst (active-high, sync)
Flip-flop bits: 16, memory bits: 0

## Targets: setup slack >= 0.0 ns
Priority when comparing versions: timing > area > power
Unmet targets right now: timing (setup slack -0.106 ns, hold slack 0.048 ns)

## Measurements
| version | setup WS (ns) | setup TNS (ns) | hold WS (ns) | area (um2) | power (mW) | flop bits | extra latency |
|---|---|---|---|---|---|---|---|
| original (round 0) | -0.106 | -0.345 | 0.048 | 150.6 | 0.2292 | 16 | 0 |

## Worst setup paths of the current best version (round 0)
1. slack -0.106 ns, 11 gates: count_DFF_X1_Q (rising edge-triggered flip-flop clocked by clk) -> gray_DFF_X1_Q_2 (rising edge-triggered flip-flop clocked by clk)
   RTL lines on this path: gray_counter.v:11
2. slack -0.081 ns, 11 gates: count_DFF_X1_Q (rising edge-triggered flip-flop clocked by clk) -> gray_DFF_X1_Q_3 (rising edge-triggered flip-flop clocked by clk)
   RTL lines on this path: gray_counter.v:11
3. slack -0.052 ns, 11 gates: count_DFF_X1_Q (rising edge-triggered flip-flop clocked by clk) -> gray_DFF_X1_Q_4 (rising edge-triggered flip-flop clocked by clk)
   RTL lines on this path: gray_counter.v:11

## Gates used by the current best version
AOI21_X1 x18, DFF_X1 x15, NOR2_X1 x13, NAND2_X1 x9, AOI211_X1 x8, XNOR2_X1 x7, INV_X1 x6, AND2_X1 x4, AOI221_X1 x3, OAI21_X1 x3

## RTL of the current best version (round 0)
### FILE: gray_counter.v
```verilog
module gray_counter (
    input  wire       clk,
    input  wire       rst,
    input  wire       en,
    input  wire       up,
    output reg  [7:0] gray
);
    reg  [7:0] count;
    wire [7:0] next = up ? count + 8'd1 : count - 8'd1;

    always @(posedge clk) begin
        if (rst) begin
            count <= 8'd0;
            gray  <= 8'd0;
        end else if (en) begin
            count <= next;
            gray  <= next ^ (next >> 1);
        end
    end
endmodule
```

ARCHITECTURE EXPLORATION, candidate 1 of 2: propose a substantially DIFFERENT architecture for the same function (a different speed/area/power trade-off), not a small tweak. Architectures already proposed (do NOT propose any of these again, even reworded): none yet.
