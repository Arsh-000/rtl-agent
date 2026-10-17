### FILE: tb_gray_counter.v
```verilog
// Self-checking testbench for gray_counter (reference model + random and corner-case stimulus)
`timescale 1ns/1ps
module tb_gray_counter;
    reg clk = 1'b0, rst = 1'b1, en = 1'b0, up = 1'b1;
    wire [7:0] gray;

    gray_counter dut (.clk(clk), .rst(rst), .en(en), .up(up), .gray(gray));

    always #5 clk = ~clk;

    // reference model: binary count, Gray conversion
    reg  [7:0] ref_count;
    wire [7:0] ref_gray = ref_count ^ (ref_count >> 1);
    always @(posedge clk) begin
        if (rst)      ref_count <= 8'd0;
        else if (en)  ref_count <= up ? ref_count + 8'd1 : ref_count - 8'd1;
    end

    integer errors = 0, cycle = 0, seed = 11;
    initial begin
        @(negedge clk); @(negedge clk);
        rst = 1'b0;
        repeat (3000) begin
            @(negedge clk);
            cycle = cycle + 1;
            if (gray !== ref_gray) begin
                errors = errors + 1;
                if (errors <= 10) $display("ERROR cycle %0d: gray=%h expected %h", cycle, gray, ref_gray);
            end
            if (cycle % 1000 < 300) begin            // long counting-up run: wraps 255 -> 0
                en = 1'b1; up = 1'b1;
            end else if (cycle % 1000 < 600) begin   // long counting-down run: wraps 0 -> 255
                en = 1'b1; up = 1'b0;
            end else begin                           // random enable and direction
                en = $random(seed); up = $random(seed);
            end
            rst = (cycle == 2500);                   // one reset in the middle
        end
        $display("cycles=%0d errors=%0d", cycle, errors);
        if (errors == 0) $display("TEST PASSED");
        else             $display("TEST FAILED");
        $finish;
    end
endmodule
```
