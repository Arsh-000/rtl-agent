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
Files: seq_detector.v (36 lines)
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
| current best (round 2) | 0.283 | 0.000 | 0.074 | 39.1 | 0.0588 | 3 | 0 |

## Worst setup paths of the current best version (round 2)
1. slack 0.283 ns, 5 gates: state_DFF_X1_Q_1 (rising edge-triggered flip-flop clocked by clk) -> state_DFF_X1_Q_2 (rising edge-triggered flip-flop clocked by clk)
   RTL lines on this path: n/a
2. slack 0.290 ns, 5 gates: state_DFF_X1_Q_1 (rising edge-triggered flip-flop clocked by clk) -> state_DFF_X1_Q (rising edge-triggered flip-flop clocked by clk)
   RTL lines on this path: n/a
3. slack 0.304 ns, 4 gates: state_DFF_X1_Q_3 (rising edge-triggered flip-flop clocked by clk) -> state_DFF_X1_Q_3 (rising edge-triggered flip-flop clocked by clk)
   RTL lines on this path: n/a

## Gates used by the current best version
DFF_X1 x5, NAND2_X1 x4, INV_X1 x3, NOR2_X1 x3, OAI21_X1 x2, AND2_X1 x1, AND3_X1 x1, AOI211_X1 x1, NAND3_X1 x1, NAND4_X1 x1

## History of this run (do not repeat failed ideas)
- Round 1 [improve] one-hot state encoding (5 flip-flops) | slack=0.244ns | area=45.0 | power=0.0699mW | -> KEPT: better area than round 0
- Round 2 [improve] merge the equivalent states S_1011 and S_1: 4 states in 2 flip-flops | slack=0.283ns | area=39.1 | power=0.0588mW | -> KEPT: better area than round 1

## RTL of the current best version (round 2)
### FILE: seq_detector.v
```verilog
module seq_detector (
    input  wire clk,
    input  wire rst,
    input  wire bit_in,
    input  wire bit_valid,
    output reg  found
);
    localparam S_IDLE = 2'd0,   // nothing matched
               S_1    = 2'd1,   // seen "1" (also the state right after a full "1011")
               S_10   = 2'd2,   // seen "10"
               S_101  = 2'd3;   // seen "101"

    reg [1:0] state, next_state;

    always @* begin
        next_state = state;
        if (bit_valid) begin
            case (state)
                S_IDLE: next_state = bit_in ? S_1   : S_IDLE;
                S_1:    next_state = bit_in ? S_1   : S_10;
                S_10:   next_state = bit_in ? S_101 : S_IDLE;
                S_101:  next_state = bit_in ? S_1   : S_10;
            endcase
        end
    end

    always @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;
            found <= 1'b0;
        end else begin
            state <= next_state;
            found <= bit_valid && bit_in && (state == S_101);
        end
    end
endmodule
```

Propose the next improvement toward the targets.
