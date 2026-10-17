// From a real Groq run (round 5): same values at every clock edge, but 'gray' is now
// computed by XOR gates AFTER the flip-flops - no longer a registered output.
module gray_counter (
    input  wire       clk,
    input  wire       rst,
    input  wire       en,
    input  wire       up,
    output wire [7:0] gray
);
    reg [7:0] bin;
    always @(posedge clk) begin
        if (rst) begin
            bin <= 8'd0;
        end else if (en) begin
            bin <= up ? bin + 8'd1 : bin - 8'd1;
        end else begin
            bin <= bin;
        end
    end
    assign gray = bin ^ (bin >> 1);
endmodule
