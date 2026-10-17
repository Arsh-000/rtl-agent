### PLAN
The next-state logic decodes a 3-bit binary state, which needs comparators on every transition. One-hot encoding (one flip-flop per state) makes each next-state bit a small OR of AND terms, which is usually faster and can be simpler.
### CHANGE_NOTE
{"idea": "one-hot state encoding (5 flip-flops)", "expected": {"setup_slack": "improve", "area": "improve", "power": "same"}, "latency_change": 0}
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
