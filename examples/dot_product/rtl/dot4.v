// dot4: dot product of two vectors of four signed 8-bit numbers.
//
//   y = a0*b0 + a1*b1 + a2*b2 + a3*b3
//
// Inputs are registered, the four multiplies and three adds happen in ONE
// clock cycle, and the result is registered. That long combinational path
// (register -> 4 multipliers -> adder chain -> register) is the critical
// path the agent has to deal with.
module dot4 (
    input  wire        clk,
    input  wire        rst_n,      // active-low asynchronous reset
    input  wire        in_valid,
    input  wire [31:0] a,          // {a3, a2, a1, a0}, each signed 8-bit
    input  wire [31:0] b,          // {b3, b2, b1, b0}, each signed 8-bit
    output reg  [17:0] y,          // signed result
    output reg         out_valid
);
    reg [31:0] a_r, b_r;
    reg        v_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            a_r <= 32'd0;
            b_r <= 32'd0;
            v_r <= 1'b0;
        end else begin
            a_r <= a;
            b_r <= b;
            v_r <= in_valid;
        end
    end

    // Written as a simple running sum: each product is added to the
    // previous total, which builds a chain of adders.
    reg signed [17:0] sum;
    integer i;
    always @* begin
        sum = 18'sd0;
        for (i = 0; i < 4; i = i + 1)
            sum = sum + $signed(a_r[i*8 +: 8]) * $signed(b_r[i*8 +: 8]);
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            y         <= 18'd0;
            out_valid <= 1'b0;
        end else begin
            y         <= sum;
            out_valid <= v_r;
        end
    end
endmodule
