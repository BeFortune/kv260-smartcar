set build_root [file dirname [file normalize [info script]]]
set evidence_dir [file join $build_root evidence vivado]
open_project [file join $build_root vivado03 ffn_overlay.xpr]
if {[get_property STATUS [get_runs synth_1]] ne "synth_design Complete!"} { error "Successful synthesis required" }
launch_runs impl_1 -to_step route_design -jobs 8
wait_on_run impl_1
set implementation_status [get_property STATUS [get_runs impl_1]]
puts "FFN_IMPLEMENTATION_STATUS=$implementation_status"
if {$implementation_status ne "route_design Complete!"} { error "Implementation failed or incomplete: $implementation_status" }
open_run impl_1
report_utilization -hierarchical -file [file join $evidence_dir implemented_utilization.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose -file [file join $evidence_dir implemented_timing_summary.rpt]
report_timing -max_paths 20 -sort_by group -file [file join $evidence_dir implemented_timing_paths.rpt]
report_route_status -file [file join $evidence_dir route_status.rpt]
report_drc -file [file join $evidence_dir implemented_drc.rpt]
report_clock_utilization -file [file join $evidence_dir clock_utilization.rpt]
set setup_paths [get_timing_paths -delay_type max -max_paths 1]
set hold_paths [get_timing_paths -delay_type min -max_paths 1]
if {[llength $setup_paths] != 1 || [llength $hold_paths] != 1} { error "No usable setup/hold timing paths" }
set wns [get_property SLACK $setup_paths]
set whs [get_property SLACK $hold_paths]
set result [open [file join $evidence_dir timing_gate.txt] w]
puts $result "WNS_NS=$wns"
puts $result "WHS_NS=$whs"
puts $result "IMPLEMENTATION_STATUS=$implementation_status"
close $result
puts "FFN_TIMING_GATE WNS_NS=$wns WHS_NS=$whs"
if {$wns < 0 || $whs < 0} { error "Hard timing gate failed; bitstream will NOT be generated" }
write_checkpoint [file join $evidence_dir ffn_overlay_wrapper_routed.dcp]
close_project
