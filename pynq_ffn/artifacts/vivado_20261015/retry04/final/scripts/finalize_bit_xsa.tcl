set build_root [file dirname [file normalize [info script]]]
set output_dir [file join $build_root output]
if {![file exists [file join $build_root evidence vivado timing_gate.txt]]} { error "Successful timing gate required" }
open_project [file join $build_root vivado03 ffn_overlay.xpr]
if {[get_property STATUS [get_runs impl_1]] ne "route_design Complete!"} { error "Timing-passed routed run required" }
launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set bit_status [get_property STATUS [get_runs impl_1]]
puts "FFN_BITSTREAM_RUN_STATUS=$bit_status"
if {$bit_status ne "write_bitstream Complete!"} { error "Bitstream run did not complete: $bit_status" }
open_run impl_1
set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
if {$wns < 0 || $whs < 0} { error "Timing gate no longer passed" }
write_hw_platform -fixed -include_bit -file [file join $output_dir ffn_overlay_wrapper.xsa]
puts "FFN_BITSTREAM_RUN_AND_XSA_COMPLETE WNS_NS=$wns WHS_NS=$whs"
close_project
