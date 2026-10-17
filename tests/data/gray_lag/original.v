// gray_counter.v
// 8‑bit up/down counter with registered Gray‑code output.
// Synchronous active‑high reset, enable, and direction control.

module gray_counter (
    input  wire       clk,
    input  wire       rst,
    input  wire       en,
    input  wire       up,
    output reg  [7:0] gray
);

    // internal binary count
    reg [7:0] cnt;

    // synchronous logic
    always @(posedge clk) begin
        if (rst) begin
            cnt <= 8'd0;
        end else if (en) begin
            if (up)
                cnt <= cnt + 8'd1;   // wrap automatically on overflow
            else
                cnt <= cnt - 8'd1;   // wrap automatically on underflow
        end
        // else hold current count
    end

    // Gray code conversion, registered on same clock edge
    always @(posedge clk) begin
        gray <= cnt ^ (cnt >> 1);
    end

endmodule
