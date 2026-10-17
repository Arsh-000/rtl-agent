# Timing constraints for sync_fifo (read-only for the agent).
# 0.7 ns is deliberately tight: the original read path (pointer -> 16-to-1 multiplexer -> dout)
# does not fit, so the job is to fix timing without changing behaviour.
create_clock -name clk -period 0.7 [get_ports clk]
set_input_delay  0.2 -clock clk [get_ports {wr_en din rd_en rst_n}]
set_output_delay 0.2 -clock clk [all_outputs]
