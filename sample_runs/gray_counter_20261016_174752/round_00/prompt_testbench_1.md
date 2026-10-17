# SYSTEM

You are an expert digital design engineer. You write clear, correct, synthesizable Verilog and
careful self-checking testbenches. Your output is compiled and simulated automatically, so follow the
requested reply format exactly.

# USER

Write a SELF-CHECKING Verilog testbench for the design described below. Do not write the design itself.

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

## Design under test
Module name: gray_counter
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
Clock port: clk; reset port: rst

## Testbench requirements
- Module name: tb_gray_counter. Instantiate gray_counter as "dut".
- Generate the clock (period 10 time units) and apply reset first.
- Contain an independent behavioural REFERENCE MODEL of the specification (do not copy any RTL).
- Drive at least 1000 cycles of random inputs plus important corner cases.
- Compare every DUT output with the reference model every cycle; count errors; print the first 10 as lines starting with "ERROR".
- At the end print exactly "TEST PASSED" if there were no errors, otherwise "TEST FAILED", then call $finish.
- Must compile with Icarus Verilog (iverilog -g2012). No SystemVerilog classes, no assertions library.
- Model exactly what the specification says about timing. Do not add a clock cycle of delay that the
  specification does not ask for (a "registered output" changes on the SAME clock edge as the state it shows).

## Timing rules (follow exactly - they prevent race conditions between testbench and design)
- Generate the clock with: initial clk = 0; always #5 clk = ~clk;
- Change DUT inputs ONLY right after a FALLING edge: @(negedge clk); then assign the new input values.
- Model the reference model's STATE (for example a count) in an always @(posedge clk) block with NON-BLOCKING
  assignments (<=), so it samples exactly the same input values as the DUT at the same rising edge.
- Compute every EXPECTED OUTPUT that is a function of that state with a continuous assignment, for example:
      wire [7:0] exp_out = ref_count ^ (ref_count >> 1);
  Then an output that "shows the state" changes on the same clock edge as the state, as a registered output should.
  Do NOT write `ref_out <= f(ref_state);` inside the same always block: that uses the OLD state and silently adds
  one cycle of delay. Only outputs the specification defines as registers of the INPUTS are state themselves.
- Never assign to reference-model variables from the stimulus code (for example to jump to a corner case).
  Reach corner cases only by driving the DUT's inputs (and reset).
- Compare DUT outputs with the reference ONLY at the FALLING edge (after the rising edge has settled),
  and do the comparison BEFORE changing the inputs for the next cycle.
- Never assign inputs and compare outputs at the same instant as a rising edge.

Reply with only:
### FILE: tb_gray_counter.v
```verilog
...
```
