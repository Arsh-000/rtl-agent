// seq_detector: finite state machine that raises 'found' for one cycle
// whenever the last four input bits were 1,0,1,1 (overlaps allowed).
//
// Written as a Moore machine with one state per "how much of the pattern
// has been seen", using 3-bit binary state codes. The output is registered.
module seq_detector (
    input  wire clk,
    input  wire rst,        // active-high synchronous reset
    input  wire bit_in,
    input  wire bit_valid,  // only advance when a new bit arrives
    output reg  found
);
    localparam S_IDLE = 3'd0,   // nothing matched
               S_1    = 3'd1,   // seen "1"
               S_10   = 3'd2,   // seen "10"
               S_101  = 3'd3,   // seen "101"
               S_1011 = 3'd4;   // seen "1011" -> pattern found

    reg [2:0] state, next_state;

    always @* begin
        next_state = state;
        if (bit_valid) begin
            case (state)
                S_IDLE: next_state = bit_in ? S_1    : S_IDLE;
                S_1:    next_state = bit_in ? S_1    : S_10;
                S_10:   next_state = bit_in ? S_101  : S_IDLE;
                S_101:  next_state = bit_in ? S_1011 : S_10;
                S_1011: next_state = bit_in ? S_1    : S_10;
                default: next_state = S_IDLE;
            endcase
        end
    end

    always @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;
            found <= 1'b0;
        end else begin
            state <= next_state;
            found <= (next_state == S_1011) && bit_valid;
        end
    end
endmodule
