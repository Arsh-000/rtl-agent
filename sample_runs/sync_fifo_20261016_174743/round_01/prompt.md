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
Top module: sync_fifo
Files: sync_fifo.v (56 lines)
Ports: clk(inp,1), rst_n(inp,1), wr_en(inp,1), din(inp,8), rd_en(inp,1), dout(out,8), full(out,1), empty(out,1)
Clocks (from the SDC): clk period 0.7 ns
Resets: rst_n (active-low, async)
Flip-flop bits: 15, memory bits: 128

## Targets: setup slack >= 0.0 ns
Priority when comparing versions: timing > area > power
Unmet targets right now: timing (setup slack -0.111 ns, hold slack 0.019 ns)

## Measurements
| version | setup WS (ns) | setup TNS (ns) | hold WS (ns) | area (um2) | power (mW) | flop bits | extra latency |
|---|---|---|---|---|---|---|---|
| original (round 0) | -0.111 | -0.660 | 0.019 | 1151.5 | 1.2125 | 15 | 0 |

## Worst setup paths of the current best version (round 0)
1. slack -0.111 ns, 6 gates: rd_ptr_DFFR_X1_Q_2 (rising edge-triggered flip-flop clocked by clk) -> dout[6] (output port clocked by clk)
   RTL lines on this path: sync_fifo.v:31
2. slack -0.096 ns, 6 gates: rd_ptr_DFFR_X1_Q_2 (rising edge-triggered flip-flop clocked by clk) -> dout[7] (output port clocked by clk)
   RTL lines on this path: sync_fifo.v:31
3. slack -0.082 ns, 6 gates: rd_ptr_DFFR_X1_Q_2 (rising edge-triggered flip-flop clocked by clk) -> dout[1] (output port clocked by clk)
   RTL lines on this path: sync_fifo.v:31

## Gates used by the current best version
MUX2_X1 x153, DFF_X1 x128, AOI221_X1 x22, NAND2_X1 x21, NOR2_X1 x21, INV_X1 x19, OR2_X1 x18, DFFR_X1 x14, AOI21_X1 x11, OAI211_X1 x11

## RTL of the current best version (round 0)
### FILE: sync_fifo.v
```verilog
// sync_fifo: 16-entry x 8-bit synchronous FIFO built from flip-flops.
//
// The storage is a register array (mem). Write and read pointers walk
// through it. An occupancy counter drives the registered full/empty flags.
// The output dout is read straight from the array through a 16-to-1
// multiplexer selected by the read pointer.
module sync_fifo (
    input  wire       clk,
    input  wire       rst_n,     // active-low asynchronous reset
    input  wire       wr_en,
    input  wire [7:0] din,
    input  wire       rd_en,
    output wire [7:0] dout,      // data at the head of the FIFO (valid when !empty)
    output reg        full,
    output reg        empty
);
    reg [7:0] mem [0:15];
    reg [3:0] wr_ptr, rd_ptr;
    reg [4:0] count;

    wire do_wr = wr_en && !full;
    wire do_rd = rd_en && !empty;

    always @(posedge clk) begin
        if (do_wr)
            mem[wr_ptr] <= din;
    end

    assign dout = mem[rd_ptr];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_ptr <= 4'd0;
            rd_ptr <= 4'd0;
            count  <= 5'd0;
            full   <= 1'b0;
            empty  <= 1'b1;
        end else begin
            if (do_wr) wr_ptr <= wr_ptr + 4'd1;
            if (do_rd) rd_ptr <= rd_ptr + 4'd1;
            case ({do_wr, do_rd})
                2'b10: begin
                    count <= count + 5'd1;
                    full  <= (count == 5'd15);
                    empty <= 1'b0;
                end
                2'b01: begin
                    count <= count - 5'd1;
                    full  <= 1'b0;
                    empty <= (count == 5'd1);
                end
                default: ;   // no change, or a write and a read in the same cycle
            endcase
        end
    end
endmodule
```

Propose the next improvement toward the targets.
