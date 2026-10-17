// my_block: replace this file with your own design.
// (This placeholder adds two 8-bit numbers and registers the result.)
module my_block (
    input  wire       clk,
    input  wire [7:0] a,
    input  wire [7:0] b,
    output reg  [8:0] y
);
    always @(posedge clk) y <= a + b;
endmodule
