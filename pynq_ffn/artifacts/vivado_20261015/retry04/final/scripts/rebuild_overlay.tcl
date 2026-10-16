# Recreate the validated BD and regenerate its targets in an explicitly NEW directory.
# Invoke: vivado -mode batch -source rebuild_overlay.tcl -tclargs D:/short_new_build D:/verified/ip_repo
if {[llength $argv] != 2} { error "Expected NEW_BUILD_ROOT and VERIFIED_EXTRACTED_IP_REPO" }
set new_root [file normalize [lindex $argv 0]]
set verified_ip [file normalize [lindex $argv 1]]
if {[file exists $new_root]} { error "Build root already exists; no project is deleted or overwritten" }
if {![file isfile [file join $verified_ip component.xml]]} { error "Verified extracted IP repository is required" }
set scripts [file dirname [file normalize [info script]]]
file mkdir $new_root
file copy $verified_ip [file join $new_root ip_repo]
foreach name {create_overlay_bd.tcl run_overlay_synth.tcl run_overlay_impl.tcl finalize_bit_xsa.tcl} {
    file copy [file join $scripts $name] [file join $new_root $name]
}
set build_root $new_root
set project_dir [file join $new_root vivado03]
source [file join $new_root create_overlay_bd.tcl]
open_project [file join $new_root vivado03 ffn_overlay.xpr]
set bd_file [get_files */ffn_overlay.bd]
reset_target all $bd_file
generate_target all $bd_file
close_project
puts "FFN_REPRODUCIBLE_BD_TARGETS_REBUILT; use the copied synthesis/implementation/export stage scripts next"
