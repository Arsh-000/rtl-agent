### PLAN
Keep a binary count register and compute the next value with an adder/subtractor. The Gray output is registered from the next binary value so it changes only on clock edges and always matches the count.
### CHANGE_NOTE
{"idea": "binary counter plus registered Gray conversion of the next value", "expected": {"setup_slack": "same", "area": "same", "power": "same"}, "latency_change": 0}
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
