module sync_fifo (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       wr_en,
    input  wire [7:0] din,
    input  wire       rd_en,
    output wire [7:0] dout,
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

    // Look-ahead head register: compute NEXT cycle's head-of-queue value now, so the 16-to-1 read
    // multiplexer is no longer on the path to the dout port.
    // Next head = the slot the read pointer will point to, or din if that slot is being written now.
    wire [3:0] rd_ptr_next = rd_ptr + {3'd0, do_rd};
    wire [7:0] head_next   = (do_wr && wr_ptr == rd_ptr_next) ? din : mem[rd_ptr_next];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_ptr <= 4'd0;
            rd_ptr <= 4'd0;
            count  <= 5'd0;
            full   <= 1'b0;
            empty  <= 1'b1;
        end else begin
            if (do_wr) wr_ptr <= wr_ptr + 4'd1;
            if (do_rd) rd_ptr <= rd_ptr_next;
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
                default: ;
            endcase
        end
    end

    reg [7:0] head;
    always @(posedge clk)
        head <= head_next;
    // The original's reset is asynchronous: it moves the read pointer to slot 0 at once, so its
    // output shows mem[0] immediately. Copy that exactly while reset is active.
    assign dout = rst_n ? head : mem[0];
endmodule
