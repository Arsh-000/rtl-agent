module seq_detector (
    input  wire clk,
    input  wire rst,
    input  wire bit_in,
    input  wire bit_valid,
    output reg  found
);
    localparam S_IDLE = 2'd0,   // nothing matched
               S_1    = 2'd1,   // seen "1" (also the state right after a full "1011")
               S_10   = 2'd2,   // seen "10"
               S_101  = 2'd3;   // seen "101"

    reg [1:0] state, next_state;

    always @* begin
        next_state = state;
        if (bit_valid) begin
            case (state)
                S_IDLE: next_state = bit_in ? S_1   : S_IDLE;
                S_1:    next_state = bit_in ? S_1   : S_10;
                S_10:   next_state = bit_in ? S_101 : S_IDLE;
                S_101:  next_state = bit_in ? S_1   : S_10;
            endcase
        end
    end

    always @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;
            found <= 1'b0;
        end else begin
            state <= next_state;
            found <= bit_valid && bit_in && (state == S_101);
        end
    end
endmodule
