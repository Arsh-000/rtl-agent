### PLAN
The best version (round 2) still misses timing by a small margin. Its critical path is count register -> 8-bit increment/decrement carry chain -> Gray XOR -> select on 'up' -> gray register. Keep count+1 and count-1 ready in look-ahead registers (inc_r, dec_r). Then the gray register only needs one XOR and a 2:1 select, and the carry chains move to the inc_r/dec_r registers, which have a whole cycle with no XOR after them. Outputs are unchanged cycle for cycle; it costs 16 extra flip-flops.
### CHANGE_NOTE
{"idea": "look-ahead registers for count+1 and count-1 so the Gray output needs only XOR + select", "expected": {"setup_slack": "improve", "area": "worsen", "power": "worsen"}, "latency_change": 0}
### FILE: gray_counter.v
```verilog
module gray_counter (
    input  wire       clk,
    input  wire       rst,
    input  wire       en,
    input  wire       up,
    output reg  [7:0] gray
);
    // Look-ahead registers: inc_r is always count+1 and dec_r is always count-1,
    // so the next value is already waiting in a register when 'en' arrives.
    reg  [7:0] count, inc_r, dec_r;
    wire [7:0] ginc = inc_r ^ (inc_r >> 1);
    wire [7:0] gdec = dec_r ^ (dec_r >> 1);

    always @(posedge clk) begin
        if (rst) begin
            count <= 8'd0;
            inc_r <= 8'd1;
            dec_r <= 8'd255;
            gray  <= 8'd0;
        end else if (en) begin
            if (up) begin
                count <= inc_r;
                inc_r <= inc_r + 8'd1;
                dec_r <= count;
                gray  <= ginc;
            end else begin
                count <= dec_r;
                dec_r <= dec_r - 8'd1;
                inc_r <= count;
                gray  <= gdec;
            end
        end
    end
endmodule
```
