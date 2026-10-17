### PLAN
Architecture candidate: compute the incremented and decremented values (and their Gray codes) in parallel, then pick with 'up' at the very end. The direction input then only drives a final multiplexer instead of steering the adder, which should shorten the path from 'up' and from the count register.
### CHANGE_NOTE
{"idea": "parallel increment and decrement with late select on 'up'", "expected": {"setup_slack": "improve", "area": "worsen", "power": "worsen"}, "latency_change": 0}
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
    wire [7:0] inc  = count + 8'd1;
    wire [7:0] dec  = count - 8'd1;
    wire [7:0] ginc = inc ^ (inc >> 1);
    wire [7:0] gdec = dec ^ (dec >> 1);

    always @(posedge clk) begin
        if (rst) begin
            count <= 8'd0;
            gray  <= 8'd0;
        end else if (en) begin
            count <= up ? inc  : dec;
            gray  <= up ? ginc : gdec;
        end
    end
endmodule
```
