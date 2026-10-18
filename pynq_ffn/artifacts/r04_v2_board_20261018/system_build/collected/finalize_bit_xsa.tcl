# Never trust a sentinel alone: re-run the live design hard gate BEFORE bitgen.
set build_root [file dirname [file normalize [info script]]]
set evidence_dir [file join $build_root evidence vivado]
source [file join $build_root gate_overlay.tcl]
open_project [file join $build_root vivado03 ffn_overlay.xpr]
if {[get_property STATUS [get_runs impl_1]] ne "route_design Complete!"} { error "Completed routed run required" }
open_run impl_1
ffn_gate pre_bitgen
close_design
launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set bit_status [get_property STATUS [get_runs impl_1]]
if {$bit_status ne "write_bitstream Complete!"} { error "Bitstream failed: $bit_status" }
open_run impl_1
ffn_gate post_bitgen
file mkdir [file join $build_root traceability]
write_hw_platform -fixed -include_bit -file [file join $build_root traceability ffn_overlay_wrapper.xsa]
close_project
puts "FFN_BITSTREAM_RUN_AND_XSA_COMPLETE"
