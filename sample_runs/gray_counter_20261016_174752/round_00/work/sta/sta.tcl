
read_liberty {/home/claude/rtl-agent/libs/nangate45_typ.lib}
read_verilog {/home/claude/rtl-agent/runs/gray_counter_20261016_174752/round_00/work/synth/netlist.v}
link_design gray_counter
read_sdc {/home/claude/rtl-agent/runs/gray_counter_20261016_174752/generated.sdc}

puts "@@RTLAGENT CLOCKS_BEGIN"
foreach clk [all_clocks] { puts "@@RTLAGENT CLOCK [get_name $clk] [get_property $clk period]" }
puts "@@RTLAGENT CLOCKS_END"

puts "@@RTLAGENT CHECKS_BEGIN"
check_setup -verbose
puts "@@RTLAGENT CHECKS_END"

puts "@@RTLAGENT SETUP_PATHS_BEGIN"
report_checks -path_delay max -group_path_count 3 -endpoint_path_count 1 -sort_by_slack -fields {src_attr} -digits 4
puts "@@RTLAGENT SETUP_PATHS_END"
puts "@@RTLAGENT HOLD_PATHS_BEGIN"
report_checks -path_delay min -group_path_count 1 -digits 4
puts "@@RTLAGENT HOLD_PATHS_END"

puts "@@RTLAGENT SUMMARY_BEGIN"
report_worst_slack -max -digits 4
report_worst_slack -min -digits 4
report_tns -max -digits 4
report_tns -min -digits 4
puts "@@RTLAGENT SUMMARY_END"

puts "@@RTLAGENT POWER_BEGIN"
# Same switching assumption for every version: each net toggles in 10% of clock cycles.
# (Without this, how activity spreads through the netlist depends on the tool versions; with
# Yosys 0.52 netlists it did not spread at all and combinational power came out as 0.)
set_power_activity -global -activity 0.1
report_power -digits 6
puts "@@RTLAGENT POWER_END"

exit
