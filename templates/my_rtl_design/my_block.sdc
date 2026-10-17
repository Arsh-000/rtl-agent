# Replace with your own timing constraints.
create_clock -name clk -period 1.0 [get_ports clk]
set_input_delay  0.1 -clock clk [get_ports {a b}]
set_output_delay 0.1 -clock clk [all_outputs]
