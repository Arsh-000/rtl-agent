module gray_counter (
    input  wire       clk,
    input  wire       rst,
    input  wire       en,
    input  wire       up,
    output reg  [7:0] gray
);

    // internal binary counter
    reg [7:0] count;
    reg [7:0] gray_next;

    // binary counter update
    always @(posedge clk) begin
        if (rst) begin
            count <= 8'd0;
        end else if (en) begin
            if (up)
                count <= count + 8'd1;   // wrap automatically on overflow
            else
                count <= count - 8'd1;   // wrap automatically on underflow
        end
    end

    // Gray code conversion (combinational)
    always @* begin
        gray_next = count ^ (count >> 1);
    end

    // register Gray code output
    always @(posedge clk) begin
        if (rst)
            gray <= 8'd0;
        else
            gray <= gray_next;
    end

endmodule
