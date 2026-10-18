set build_root [file dirname [file normalize [info script]]]
set evidence_dir [file join $build_root evidence vivado]
open_project [file join $build_root vivado03 ffn_overlay.xpr]
launch_runs synth_1 -jobs 8
wait_on_run synth_1
set synth_status [get_property STATUS [get_runs synth_1]]
puts "FFN_SYNTH_STATUS=$synth_status"
if {$synth_status ne "synth_design Complete!"} { error "Synthesis failed or did not finish: $synth_status" }
open_run synth_1
report_utilization -hierarchical -file [file join $evidence_dir post_synth_utilization.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $evidence_dir post_synth_timing.rpt]
close_project
