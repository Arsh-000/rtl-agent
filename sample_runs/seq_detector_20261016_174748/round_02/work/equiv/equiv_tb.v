`timescale 1ns/1ps
module rtl_agent_equiv_tb;
    integer seed = 1;
    reg clk = 1'b0;
    reg [0:0] rst = 0;
    reg [0:0] bit_in = 0;
    reg [0:0] bit_valid = 0;
    wire [0:0] gold_found, cand_found;
    seq_detector__golden gold (.clk(clk), .rst(rst), .bit_in(bit_in), .bit_valid(bit_valid), .found(gold_found));
    seq_detector cand (.clk(clk), .rst(rst), .bit_in(bit_in), .bit_valid(bit_valid), .found(cand_found));
    wire [0:0] gold_all = {gold_found};
    wire [0:0] cand_all = {cand_found};
    reg  [0:0] hist [0:2];       // golden outputs of the last 3 cycles
    integer mism [0:0];
    integer first_shown [0:0];
    reg  [0:0] seen0, seen1;      // which golden output bits were ever 0 / 1
    integer cyc, k, compared, last_reset, toggled;
    // Diagnosis only (never a pass): does the candidate produce the right outputs too EARLY?
    reg  [0:0] chist [0:2];   // candidate outputs of the last cycles
    integer early [0:2];
    // Diagnosis only: does the candidate produce the right outputs too LATE (more delay than allowed)?
    integer late [0:2];
    always #5 clk = ~clk;
    // output order in messages: found[1]
    initial begin
        for (k = 0; k <= 0; k = k + 1) begin mism[k] = 0; first_shown[k] = 0; end
        for (k = 0; k <= 2; k = k + 1) early[k] = 0;
        for (k = 0; k <= 2; k = k + 1) late[k] = 0;
        seen0 = 0; seen1 = 0; compared = 0; last_reset = 0;
        bit_in = {$random(seed)};
        bit_valid = {$random(seed)};
        rst = 1'b1;
        for (cyc = 0; cyc < 2000; cyc = cyc + 1) begin
            @(negedge clk);
            // 1) remember the golden outputs, then compare for every allowed latency
            for (k = 2; k > 0; k = k - 1) hist[k] = hist[k-1];
            hist[0] = gold_all;
            for (k = 2; k > 0; k = k - 1) chist[k] = chist[k-1];
            chist[0] = cand_all;
            // latency 0 is compared on every cycle, including right after a reset;
            // shifted latencies skip the few cycles around a reset (pipelines restart there)
            if (cyc > 4) begin
                compared = compared + 1;
                if (^gold_all !== 1'bx) begin seen0 = seen0 | ~gold_all; seen1 = seen1 | gold_all; end
                for (k = 0; k <= 0; k = k + 1) begin
                    if ((k == 0 || cyc > last_reset + k + 2) && (^hist[k] !== 1'bx) && (cand_all !== hist[k])) begin
                        mism[k] = mism[k] + 1;
                        if (first_shown[k] < 3) begin
                            first_shown[k] = first_shown[k] + 1;
                            $display("@@MISMATCH latency=%0d cycle=%0d expected=%h got=%h", k, cyc, hist[k], cand_all);
                        end
                    end
                end
                for (k = 1; k <= 2; k = k + 1)
                    if (cyc > last_reset + k + 2 && (^gold_all !== 1'bx) && (chist[k] !== gold_all))
                        early[k] = early[k] + 1;
                for (k = 1; k <= 2; k = k + 1)
                    if (cyc > last_reset + k + 2 && (^hist[k] !== 1'bx) && (cand_all !== hist[k]))
                        late[k] = late[k] + 1;
            end
            // 2) apply new random inputs (reset held at the start, then pulsed several times)
            bit_in = {$random(seed)};
            bit_valid = {$random(seed)};
            if (cyc == 3 || cyc == 301 || cyc == 661 || cyc == 1001 || cyc == 1241 || cyc == 1601 || cyc == 1861) begin rst = 1'b0; end
            if (cyc == 300 || cyc == 660 || cyc == 1000 || cyc == 1240 || cyc == 1600 || cyc == 1860) begin rst = 1'b1; last_reset = cyc; end
        end
        toggled = 0; for (k = 0; k < 1; k = k + 1) if (seen0[k] && seen1[k]) toggled = toggled + 1;
        $display("@@COVERAGE compared=%0d toggled=%0d of 1", compared, toggled);
        for (k = 0; k <= 0; k = k + 1) $display("@@RESULT latency=%0d mismatches=%0d", k, mism[k]);
        for (k = 1; k <= 2; k = k + 1) $display("@@EARLY cycles=%0d mismatches=%0d", k, early[k]);
        for (k = 1; k <= 2; k = k + 1) $display("@@LATE cycles=%0d mismatches=%0d", k, late[k]);
        $finish;
    end
endmodule
