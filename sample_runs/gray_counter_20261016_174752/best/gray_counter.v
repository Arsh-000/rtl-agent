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
