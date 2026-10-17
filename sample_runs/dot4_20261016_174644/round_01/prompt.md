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
4. Latency is FLEXIBLE: you may add up to 2 clock cycle(s) of latency (for example pipeline registers). If you do, delay ALL outputs (including valid/flag outputs) by the same number of cycles, and keep reset behaviour the same.
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
Top module: dot4
Files: dot4.v (52 lines)
Ports: clk(inp,1), rst_n(inp,1), in_valid(inp,1), a(inp,32), b(inp,32), y(out,18), out_valid(out,1)
Clocks (from the SDC): clk period 1.2 ns
Resets: rst_n (active-low, async)
Flip-flop bits: 84, memory bits: 0

## Targets: setup slack >= 0.0 ns
Priority when comparing versions: timing > area > power
Unmet targets right now: timing (setup slack -0.368 ns, hold slack 0.019 ns)

## Measurements
| version | setup WS (ns) | setup TNS (ns) | hold WS (ns) | area (um2) | power (mW) | flop bits | extra latency |
|---|---|---|---|---|---|---|---|
| original (round 0) | -0.368 | -2.590 | 0.019 | 2686.3 | 1.1299 | 84 | 0 |

## Worst setup paths of the current best version (round 0)
1. slack -0.368 ns, 30 gates: b_r_DFFR_X1_Q_17 (rising edge-triggered flip-flop clocked by clk) -> y_DFFR_X1_Q_8 (rising edge-triggered flip-flop clocked by clk)
   RTL lines on this path: dot4.v:21, dot4.v:43
2. slack -0.342 ns, 30 gates: b_r_DFFR_X1_Q_17 (rising edge-triggered flip-flop clocked by clk) -> y_DFFR_X1_Q (rising edge-triggered flip-flop clocked by clk)
   RTL lines on this path: dot4.v:21, dot4.v:43
3. slack -0.336 ns, 29 gates: b_r_DFFR_X1_Q_17 (rising edge-triggered flip-flop clocked by clk) -> y_DFFR_X1_Q_7 (rising edge-triggered flip-flop clocked by clk)
   RTL lines on this path: dot4.v:21, dot4.v:43

## Gates used by the current best version
XNOR2_X1 x327, NAND2_X1 x325, NOR2_X1 x235, XOR2_X1 x230, AOI21_X1 x172, OAI21_X1 x143, INV_X1 x100, DFFR_X1 x84, OR2_X1 x73, AND2_X1 x72

## RTL of the current best version (round 0)
### FILE: dot4.v
```verilog
// dot4: dot product of two vectors of four signed 8-bit numbers.
//
//   y = a0*b0 + a1*b1 + a2*b2 + a3*b3
//
// Inputs are registered, the four multiplies and three adds happen in ONE
// clock cycle, and the result is registered. That long combinational path
// (register -> 4 multipliers -> adder chain -> register) is the critical
// path the agent has to deal with.
module dot4 (
    input  wire        clk,
    input  wire        rst_n,      // active-low asynchronous reset
    input  wire        in_valid,
    input  wire [31:0] a,          // {a3, a2, a1, a0}, each signed 8-bit
    input  wire [31:0] b,          // {b3, b2, b1, b0}, each signed 8-bit
    output reg  [17:0] y,          // signed result
    output reg         out_valid
);
    reg [31:0] a_r, b_r;
    reg        v_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            a_r <= 32'd0;
            b_r <= 32'd0;
            v_r <= 1'b0;
        end else begin
            a_r <= a;
            b_r <= b;
            v_r <= in_valid;
        end
    end

    // Written as a simple running sum: each product is added to the
    // previous total, which builds a chain of adders.
    reg signed [17:0] sum;
    integer i;
    always @* begin
        sum = 18'sd0;
        for (i = 0; i < 4; i = i + 1)
            sum = sum + $signed(a_r[i*8 +: 8]) * $signed(b_r[i*8 +: 8]);
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            y         <= 18'd0;
            out_valid <= 1'b0;
        end else begin
            y         <= sum;
            out_valid <= v_r;
        end
    end
endmodule
```

Propose the next improvement toward the targets.
