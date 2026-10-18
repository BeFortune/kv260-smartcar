# Minimal continuation of the reviewed retry04 implementation flow; no resets/deletes.
set build_root [file dirname [file normalize [info script]]]
set evidence_dir [file join $build_root evidence vivado]
source [file join $build_root gate_overlay.tcl]
open_project [file join $build_root vivado03 ffn_overlay.xpr]
if {[get_property STATUS [get_runs synth_1]] ne "synth_design Complete!"} { error "Successful synthesis required" }
launch_runs impl_1 -to_step route_design -jobs 8
wait_on_run impl_1
set implementation_status [get_property STATUS [get_runs impl_1]]
puts "FFN_IMPLEMENTATION_STATUS=$implementation_status"
if {$implementation_status ne "route_design Complete!"} { error "Implementation failed or incomplete: $implementation_status" }
open_run impl_1
report_utilization -hierarchical -file [file join $evidence_dir implemented_utilization.rpt]
report_timing -max_paths 20 -sort_by group -file [file join $evidence_dir implemented_timing_paths.rpt]
report_clock_utilization -file [file join $evidence_dir clock_utilization.rpt]
ffn_gate implemented
write_checkpoint [file join $evidence_dir ffn_overlay_wrapper_routed.dcp]
close_project
