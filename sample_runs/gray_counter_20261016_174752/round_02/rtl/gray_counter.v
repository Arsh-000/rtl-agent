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
