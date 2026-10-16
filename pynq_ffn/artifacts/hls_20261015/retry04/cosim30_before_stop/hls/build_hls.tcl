# Run with: vitis_hls -f /absolute/path/to/build_hls.tcl
# All generated project/export files stay beneath this script's directory.
if {![info exists ::env(FPGA_PART)] || [string trim $::env(FPGA_PART)] eq ""} {
    error "Set FPGA_PART to the verified full FPGA part name; no device is assumed."
}
set part [string trim $::env(FPGA_PART)]
set clock_ns 10
if {[info exists ::env(CLOCK_NS)]} {
    set clock_ns [string trim $::env(CLOCK_NS)]
}
if {![string is double -strict $clock_ns]} {
    error "CLOCK_NS must be a finite positive number in nanoseconds."
}
if {[catch {expr {$clock_ns > 0 && $clock_ns < Inf}} valid_clock] || !$valid_clock} {
    error "CLOCK_NS must be a finite positive number in nanoseconds."
}
set run_cosim 0
if {[info exists ::env(RUN_COSIM)]} {
    if {$::env(RUN_COSIM) ni {0 1}} {
        error "RUN_COSIM must be 0 or 1."
    }
    set run_cosim $::env(RUN_COSIM)
}

set source_dir [file dirname [file normalize [info script]]]
cd $source_dir
file mkdir [file join $source_dir build]
# Deliberately no -reset: do not silently discard a previous project.
open_project [file join $source_dir build ffn_accel_hls]
set_top ffn_accel
set cflags "-std=c++11 -DFFN_USE_HLS_MATH -ffp-contract=off"
add_files [file join $source_dir ffn_accel.hpp]
add_files [file join $source_dir ffn_accel.cpp] -cflags $cflags
add_files -tb [file join $source_dir testbench.cpp] -cflags $cflags
open_solution -flow_target vivado solution1
set_part $part
create_clock -period $clock_ns -name default
# Mandatory: AXI master addresses and pointer control registers are 64-bit.
# If a tool version rejects this option, fail rather than generate 32-bit IP.
config_interface -m_axi_addr64=true
csim_design
csynth_design
if {$run_cosim} {
    cosim_design -rtl verilog
}
export_design -format ip_catalog -rtl verilog
exit
