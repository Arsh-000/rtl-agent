# Timing constraints for dot4 (read-only for the agent).
# 1.2 ns clock (~833 MHz) on Nangate45 typical: the one-cycle multiply-add does not fit.
create_clock -name clk -period 1.2 [get_ports clk]
set_input_delay  0.2 -clock clk [get_ports {a b in_valid rst_n}]
set_output_delay 0.1 -clock clk [all_outputs]
