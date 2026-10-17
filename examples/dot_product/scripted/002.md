### PLAN
The previous attempt was rejected because unsigned products change the result for negative numbers, so the signed arithmetic must stay. The real problem is that four multipliers and three adders sit between two registers. Latency may grow by up to 2 cycles, so register the four products (one extra pipeline stage) and add them as a balanced tree in the next cycle. All outputs, including out_valid, are delayed by exactly one cycle.
### CHANGE_NOTE
{"idea": "pipeline: register the four signed products, then add them as a balanced tree (+1 cycle latency)", "expected": {"setup_slack": "improve", "area": "worsen", "power": "worsen"}, "latency_change": 1}
### FILE: dot4.v
```verilog
module dot4 (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        in_valid,
    input  wire [31:0] a,
    input  wire [31:0] b,
    output reg  [17:0] y,
    output reg         out_valid
);
    // stage 1: input registers (unchanged)
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

    // stage 2: the four signed products, registered
    reg signed [15:0] p0, p1, p2, p3;
    reg               v_p;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            p0 <= 16'sd0; p1 <= 16'sd0; p2 <= 16'sd0; p3 <= 16'sd0;
            v_p <= 1'b0;
        end else begin
            p0 <= $signed(a_r[7:0])   * $signed(b_r[7:0]);
            p1 <= $signed(a_r[15:8])  * $signed(b_r[15:8]);
            p2 <= $signed(a_r[23:16]) * $signed(b_r[23:16]);
            p3 <= $signed(a_r[31:24]) * $signed(b_r[31:24]);
            v_p <= v_r;
        end
    end

    // stage 3: balanced adder tree (18-bit signed context sign-extends the products)
    wire signed [17:0] s01 = p0 + p1;
    wire signed [17:0] s23 = p2 + p3;
    wire signed [17:0] sum = s01 + s23;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            y         <= 18'd0;
            out_valid <= 1'b0;
        end else begin
            y         <= sum;
            out_valid <= v_p;
        end
    end
endmodule
```
