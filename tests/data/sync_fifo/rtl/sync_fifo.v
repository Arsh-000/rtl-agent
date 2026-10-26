// sync_fifo: 16-entry x 8-bit synchronous FIFO built from flip-flops.
//
// The storage is a register array (mem). Write and read pointers walk
// through it. An occupancy counter drives the registered full/empty flags.
// The output dout is read straight from the array through a 16-to-1
// multiplexer selected by the read pointer.
module sync_fifo (
    input  wire       clk,
    input  wire       rst_n,     // active-low asynchronous reset
    input  wire       wr_en,
    input  wire [7:0] din,
    input  wire       rd_en,
    output wire [7:0] dout,      // data at the head of the FIFO (valid when !empty)
    output reg        full,
    output reg        empty
);
    reg [7:0] mem [0:15];
    reg [3:0] wr_ptr, rd_ptr;
    reg [4:0] count;

    wire do_wr = wr_en && !full;
    wire do_rd = rd_en && !empty;

    always @(posedge clk) begin
        if (do_wr)
            mem[wr_ptr] <= din;
    end

    assign dout = mem[rd_ptr];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_ptr <= 4'd0;
            rd_ptr <= 4'd0;
            count  <= 5'd0;
            full   <= 1'b0;
            empty  <= 1'b1;
        end else begin
            if (do_wr) wr_ptr <= wr_ptr + 4'd1;
            if (do_rd) rd_ptr <= rd_ptr + 4'd1;
            case ({do_wr, do_rd})
                2'b10: begin
                    count <= count + 5'd1;
                    full  <= (count == 5'd15);
                    empty <= 1'b0;
                end
                2'b01: begin
                    count <= count - 5'd1;
                    full  <= 1'b0;
                    empty <= (count == 5'd1);
                end
                default: ;   // no change, or a write and a read in the same cycle
            endcase
        end
    end
endmodule
