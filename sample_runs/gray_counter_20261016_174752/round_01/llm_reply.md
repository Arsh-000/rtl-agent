### PLAN
Architecture candidate: store only the Gray value. Each cycle, convert Gray back to binary (a prefix XOR), add or subtract one, and convert to Gray again. This halves the number of flip-flops (8 instead of 16) at the cost of a longer logic path.
### CHANGE_NOTE
{"idea": "Gray-only storage: 8 flip-flops, convert to binary and back each cycle", "expected": {"setup_slack": "worsen", "area": "improve", "power": "improve"}, "latency_change": 0}
### FILE: gray_counter.v
```verilog
module gray_counter (
    input  wire       clk,
    input  wire       rst,
    input  wire       en,
    input  wire       up,
    output reg  [7:0] gray
);
    // Gray -> binary: b[i] = XOR of gray[7:i]
    wire [7:0] bin;
    assign bin[7] = gray[7];
    genvar i;
    generate
        for (i = 6; i >= 0; i = i - 1) begin : g2b
            assign bin[i] = bin[i+1] ^ gray[i];
        end
    endgenerate

    wire [7:0] next = up ? bin + 8'd1 : bin - 8'd1;

    always @(posedge clk) begin
        if (rst)
            gray <= 8'd0;
        else if (en)
            gray <= next ^ (next >> 1);
    end
endmodule
```
