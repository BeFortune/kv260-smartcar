set build_root [file dirname [file normalize [info script]]]
set evidence_dir [file join $build_root evidence vivado]
set output_dir [file join $build_root output]
if {![file exists [file join $evidence_dir timing_gate.txt]]} { error "Successful timing gate evidence required" }
file mkdir $output_dir
open_project [file join $build_root vivado03 ffn_overlay.xpr]
if {[get_property STATUS [get_runs impl_1]] ne "route_design Complete!"} { error "Routed implementation required" }
open_run impl_1
set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
if {$wns < 0 || $whs < 0} { error "Hard timing gate failed before export" }
write_bitstream [file join $output_dir ffn_overlay_wrapper.bit]
write_hw_platform -fixed -include_bit -file [file join $output_dir ffn_overlay_wrapper.xsa]
puts "FFN_MATCHED_BIT_XSA_WRITTEN WNS_NS=$wns WHS_NS=$whs"
close_project
