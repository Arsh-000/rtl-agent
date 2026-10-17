module dot4 (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        in_valid,
    input  wire [31:0] a,
    input  wire [31:0] b,
    output reg  [17:0] y,
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

    // unsigned products (cheaper multipliers)
    wire [15:0] p0 = a_r[7:0]   * b_r[7:0];
    wire [15:0] p1 = a_r[15:8]  * b_r[15:8];
    wire [15:0] p2 = a_r[23:16] * b_r[23:16];
    wire [15:0] p3 = a_r[31:24] * b_r[31:24];
    wire [17:0] sum = p0 + p1 + p2 + p3;

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
