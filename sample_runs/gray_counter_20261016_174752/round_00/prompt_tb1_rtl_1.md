# SYSTEM

You are an expert digital design engineer. You write clear, correct, synthesizable Verilog and
careful self-checking testbenches. Your output is compiled and simulated automatically, so follow the
requested reply format exactly.

# USER

Write synthesizable RTL for the specification below. Aim first for a correct, clean design.

## Specification
An 8-bit up/down counter whose value is output in Gray code.
- On every rising clock edge where 'en' is 1, the internal binary count
  goes up by 1 if 'up' is 1, or down by 1 if 'up' is 0. It wraps around
  (255 + 1 = 0, 0 - 1 = 255).
- 'rst' is an active-high synchronous reset that sets the count to 0.
- 'gray' is the current count converted to Gray code: gray = count ^ (count >> 1).
  It must be a registered output (it changes only on clock edges), and it changes on the
  SAME clock edge as the count: after every edge, gray already shows the Gray code of the new
  count (no extra cycle of delay). After reset, gray is 0.

## Timing
Follow the specification's timing exactly. Do not add a clock cycle of delay it does not ask for: a "registered
output" changes on the SAME clock edge as the state it shows (compute it from the next state, not the old one).

## Top module
Name: gray_counter. File name: gray_counter.v
Use exactly this interface:
```verilog
module gray_counter (
    input  wire       clk,
    input  wire       rst,
    input  wire       en,
    input  wire       up,
    output reg  [7:0] gray
);
```

## The testbench your design must pass
```verilog
// Self-checking testbench for gray_counter (reference model + random and corner-case stimulus)
`timescale 1ns/1ps
module tb_gray_counter;
    reg clk = 1'b0, rst = 1'b1, en = 1'b0, up = 1'b1;
    wire [7:0] gray;

    gray_counter dut (.clk(clk), .rst(rst), .en(en), .up(up), .gray(gray));

    always #5 clk = ~clk;

    // reference model: binary count, Gray conversion
    reg  [7:0] ref_count;
    wire [7:0] ref_gray = ref_count ^ (ref_count >> 1);
    always @(posedge clk) begin
        if (rst)      ref_count <= 8'd0;
        else if (en)  ref_count <= up ? ref_count + 8'd1 : ref_count - 8'd1;
    end

    integer errors = 0, cycle = 0, seed = 11;
    initial begin
        @(negedge clk); @(negedge clk);
        rst = 1'b0;
        repeat (3000) begin
            @(negedge clk);
            cycle = cycle + 1;
            if (gray !== ref_gray) begin
                errors = errors + 1;
                if (errors <= 10) $display("ERROR cycle %0d: gray=%h expected %h", cycle, gray, ref_gray);
            end
            if (cycle % 1000 < 300) begin            // long counting-up run: wraps 255 -> 0
                en = 1'b1; up = 1'b1;
            end else if (cycle % 1000 < 600) begin   // long counting-down run: wraps 0 -> 255
                en = 1'b1; up = 1'b0;
            end else begin                           // random enable and direction
                en = $random(seed); up = $random(seed);
            end
            rst = (cycle == 2500);                   // one reset in the middle
        end
        $display("cycles=%0d errors=%0d", cycle, errors);
        if (errors == 0) $display("TEST PASSED");
        else             $display("TEST FAILED");
        $finish;
    end
endmodule
```

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
