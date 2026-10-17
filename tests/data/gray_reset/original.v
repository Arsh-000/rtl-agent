module gray_counter (
    input  wire       clk,
    input  wire       rst,
    input  wire       en,
    input  wire       up,
    output reg  [7:0] gray
);

    // internal binary counter
    reg [7:0] count;

    // synchronous logic
    always @(posedge clk) begin
        if (rst) begin
            count <= 8'd0;
        end else if (en) begin
            if (up)
                count <= count + 8'd1;   // wrap automatically on overflow
            else
                count <= count - 8'd1;   // wrap automatically on underflow
        end
        // register Gray code output
        gray <= count ^ (count >> 1);
    end

endmodule
