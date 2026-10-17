// From a real Groq run (round 0): 'gray' is a registered output.
module gray_counter (
    input  wire       clk,
    input  wire       rst,
    input  wire       en,
    input  wire       up,
    output reg  [7:0] gray
);
    reg [7:0] cnt;
    wire [7:0] next_cnt;
    assign next_cnt = (en) ? (up ? cnt + 8'd1 : cnt - 8'd1) : cnt;
    always @(posedge clk) begin
        if (rst) begin
            cnt  <= 8'd0;
            gray <= 8'd0;
        end else begin
            cnt  <= next_cnt;
            gray <= next_cnt ^ (next_cnt >> 1);
        end
    end
endmodule
