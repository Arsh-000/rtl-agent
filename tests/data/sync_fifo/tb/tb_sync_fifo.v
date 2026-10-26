// Self-checking testbench for sync_fifo.
//
// A simple reference model (a queue held in an array) runs next to the
// design. Every cycle the testbench compares full, empty and dout against
// the model. It prints "TEST PASSED" or "TEST FAILED" at the end - that is
// how the agent knows whether a design version still works.
`timescale 1ns/1ps
module tb_sync_fifo;
    reg        clk = 1'b0;
    reg        rst_n = 1'b0;
    reg        wr_en = 1'b0, rd_en = 1'b0;
    reg  [7:0] din = 8'd0;
    wire [7:0] dout;
    wire       full, empty;

    sync_fifo dut (.clk(clk), .rst_n(rst_n), .wr_en(wr_en), .din(din),
                   .rd_en(rd_en), .dout(dout), .full(full), .empty(empty));

    always #5 clk = ~clk;

    // ---- reference model ----
    reg [7:0] model [0:15];
    integer head = 0, tail = 0, used = 0;
    integer errors = 0, cycle = 0, n_wr = 0, n_rd = 0;
    integer seed = 7;
    reg     m_wr, m_rd;

    task check;
        begin
            if (full !== (used == 16)) begin
                errors = errors + 1;
                if (errors < 10) $display("ERROR cycle %0d: full=%b, model used=%0d", cycle, full, used);
            end
            if (empty !== (used == 0)) begin
                errors = errors + 1;
                if (errors < 10) $display("ERROR cycle %0d: empty=%b, model used=%0d", cycle, empty, used);
            end
            if (used != 0 && dout !== model[head]) begin
                errors = errors + 1;
                if (errors < 10) $display("ERROR cycle %0d: dout=%h, expected %h", cycle, dout, model[head]);
            end
        end
    endtask

    initial begin
        repeat (3) @(negedge clk);
        rst_n = 1'b1;
        repeat (4000) begin
            @(negedge clk);
            cycle = cycle + 1;
            check;
            // Phases bias the traffic so the FIFO also fills up and drains.
            if (cycle % 600 < 200) begin          // mostly writes
                wr_en = ($random(seed) % 4) != 0;
                rd_en = ($random(seed) % 4) == 0;
            end else if (cycle % 600 < 400) begin // mostly reads
                wr_en = ($random(seed) % 4) == 0;
                rd_en = ($random(seed) % 4) != 0;
            end else begin                         // random
                wr_en = $random(seed);
                rd_en = $random(seed);
            end
            din = $random(seed);
            // Update the model with what the design should do at the next edge.
            m_wr = wr_en && (used < 16);
            m_rd = rd_en && (used > 0);
            @(posedge clk);
            #1;
            if (m_rd) begin head = (head + 1) % 16; used = used - 1; n_rd = n_rd + 1; end
            if (m_wr) begin model[tail] = din; tail = (tail + 1) % 16; used = used + 1; n_wr = n_wr + 1; end
        end
        $display("writes=%0d reads=%0d errors=%0d", n_wr, n_rd, errors);
        if (errors == 0) $display("TEST PASSED");
        else             $display("TEST FAILED");
        $finish;
    end
endmodule
