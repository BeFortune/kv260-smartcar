# Reproducible first-reference overlay; no board programming occurs here.
if {![info exists build_root]} { set build_root [file dirname [file normalize [info script]]] }
if {![info exists project_dir]} { set project_dir [file join $build_root vivado03] }
set evidence_dir [file join $build_root evidence vivado]
set ip_repo [file join $build_root ip_repo]
set board_repo D:/Learning/AMDDesignTools/2026.1/data/xhub/boards/XilinxBoardStore/boards/Xilinx
if {[file exists [file join $project_dir ffn_overlay.xpr]]} { error "Refusing to replace existing Vivado project" }
if {![file exists [file join $ip_repo component.xml]]} { error "Exported IP component.xml not staged" }
file mkdir $evidence_dir
set_param board.repoPaths [list $board_repo]
create_project ffn_overlay $project_dir -part xck26-sfvc784-2LV-c
set_property board_part xilinx.com:kv260_som:part0:2.0 [current_project]
set_property target_language Verilog [current_project]
set_property ip_repo_paths [list $ip_repo] [current_project]
update_ip_catalog
set ffn_defs [get_ipdefs -all *:ffn_accel:*]
if {[llength $ffn_defs] != 1} { error "Expected one exported ffn_accel IP definition, got: $ffn_defs" }
create_bd_design ffn_overlay
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.5 zynq_ultra_ps_e_0]
apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset "1"} $ps
set_property -dict [list \
    CONFIG.PSU__USE__M_AXI_GP0 {1} \
    CONFIG.PSU__USE__M_AXI_GP1 {0} \
    CONFIG.PSU__USE__M_AXI_GP2 {0} \
    CONFIG.PSU__USE__S_AXI_GP0 {0} \
    CONFIG.PSU__USE__S_AXI_GP1 {0} \
    CONFIG.PSU__USE__S_AXI_GP2 {1} \
    CONFIG.PSU__USE__S_AXI_GP3 {0} \
    CONFIG.PSU__USE__S_AXI_GP4 {0} \
    CONFIG.PSU__USE__S_AXI_GP5 {0} \
    CONFIG.PSU__USE__S_AXI_GP6 {0} \
    CONFIG.PSU__SAXIGP2__DATA_WIDTH {128} \
    CONFIG.PSU__USE_DIFF_RW_CLK_GP2 {0} \
    CONFIG.PSU__HIGH_ADDRESS__ENABLE {1} \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} \
    CONFIG.PSU__FPGA_PL1_ENABLE {0} \
    CONFIG.PSU__FPGA_PL2_ENABLE {0} \
    CONFIG.PSU__FPGA_PL3_ENABLE {0} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__SRCSEL {IOPLL} \
    CONFIG.PSU__USE__FABRIC__RST {1} \
    CONFIG.PSU__NUM_FABRIC_RESETS {1} \
    CONFIG.PSU__USE__IRQ0 {0} \
    CONFIG.PSU__USE__IRQ1 {0}] $ps
set ffn [create_bd_cell -type ip -vlnv [lindex $ffn_defs 0] ffn_accel_0]
set control_sc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 control_sc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1} CONFIG.NUM_CLKS {1} CONFIG.HAS_ARESETN {1}] $control_sc
set memory_sc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 memory_sc]
set_property -dict [list CONFIG.NUM_SI {2} CONFIG.NUM_MI {1} CONFIG.NUM_CLKS {1} CONFIG.HAS_ARESETN {1}] $memory_sc
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps8_0]
set_property CONFIG.C_AUX_RESET_HIGH {1} $rst
set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 const_one]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $one
set zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 const_zero]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $zero
connect_bd_intf_net [get_bd_intf_pins zynq_ultra_ps_e_0/M_AXI_HPM0_FPD] [get_bd_intf_pins control_sc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins control_sc/M00_AXI] [get_bd_intf_pins ffn_accel_0/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins ffn_accel_0/m_axi_gmem0] [get_bd_intf_pins memory_sc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ffn_accel_0/m_axi_gmem1] [get_bd_intf_pins memory_sc/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins memory_sc/M00_AXI] [get_bd_intf_pins zynq_ultra_ps_e_0/S_AXI_HP0_FPD]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] \
    [get_bd_pins zynq_ultra_ps_e_0/maxihpm0_fpd_aclk] \
    [get_bd_pins zynq_ultra_ps_e_0/saxihp0_fpd_aclk] \
    [get_bd_pins ffn_accel_0/ap_clk] \
    [get_bd_pins control_sc/aclk] [get_bd_pins memory_sc/aclk] \
    [get_bd_pins rst_ps8_0/slowest_sync_clk]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins rst_ps8_0/ext_reset_in]
connect_bd_net [get_bd_pins const_one/dout] [get_bd_pins rst_ps8_0/dcm_locked]
connect_bd_net [get_bd_pins const_zero/dout] [get_bd_pins rst_ps8_0/aux_reset_in] [get_bd_pins rst_ps8_0/mb_debug_sys_rst]
connect_bd_net [get_bd_pins rst_ps8_0/interconnect_aresetn] [get_bd_pins control_sc/aresetn] [get_bd_pins memory_sc/aresetn]
connect_bd_net [get_bd_pins rst_ps8_0/peripheral_aresetn] [get_bd_pins ffn_accel_0/ap_rst_n]
assign_bd_address -offset 0xA0000000 -range 0x10000 -target_address_space [get_bd_addr_spaces zynq_ultra_ps_e_0/Data] [get_bd_addr_segs ffn_accel_0/s_axi_control/Reg]
set low [get_bd_addr_segs zynq_ultra_ps_e_0/SAXIGP2/HP0_DDR_LOW]
set high [get_bd_addr_segs zynq_ultra_ps_e_0/SAXIGP2/HP0_DDR_HIGH]
if {[llength $low] != 1 || [llength $high] != 1} { error "Expected low and high HP0 DDR segments" }
foreach {segment offset expected_range} [list $low 0x0000000000 0x80000000 $high 0x0800000000 0x800000000] {
    set range [get_property RANGE $segment]
    if {[expr {$range != $expected_range}]} { error "Unexpected PS exported DDR range $segment=$range" }
    foreach master {m_axi_gmem0 m_axi_gmem1} {
        set space [get_bd_addr_spaces -of_objects [get_bd_intf_pins ffn_accel_0/$master]]
        if {[llength $space] != 1} { error "Missing HLS master address space $master" }
        assign_bd_address -offset $offset -range $range -target_address_space $space $segment
    }
}
validate_bd_design
if {[get_property CONFIG.C_EXT_RESET_HIGH $rst] != 0} { error "PS active-low reset polarity was not propagated to reset controller" }
if {![get_property CONFIG.PSU__HIGH_ADDRESS__ENABLE $ps]} { error "High DDR addressing disabled" }
if {![get_property CONFIG.PSU__DDRC__ENABLE $ps] || ![get_property CONFIG.PSU__DDR_HIGH_ADDRESS_GUI_ENABLE $ps]} { error "Board preset did not expose enabled low and high DDR" }
proc save_properties {object path} {
    set stream [open $path w]
    foreach property [lsort [list_property $object]] {
        puts $stream "$property\t[get_property $property $object]"
    }
    close $stream
}
save_properties $low [file join $evidence_dir low_ddr_target_properties.txt]
save_properties $high [file join $evidence_dir high_ddr_target_properties.txt]
save_properties [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [file join $evidence_dir pl_clock_properties.txt]
set address_report [open [file join $evidence_dir bd_address_map.txt] w]
foreach space [get_bd_addr_spaces] {
    puts $address_report "ADDRESS_SPACE $space"
    foreach segment [get_bd_addr_segs -of_objects $space] {
        puts $address_report "  $segment OFFSET=[get_property OFFSET $segment] RANGE=[get_property RANGE $segment]"
    }
}
close $address_report
save_properties $ps [file join $evidence_dir ps_properties.txt]
save_properties $rst [file join $evidence_dir reset_properties.txt]
foreach master {gmem0 gmem1} {
    save_properties [get_bd_intf_pins ffn_accel_0/m_axi_$master] [file join $evidence_dir hls_${master}_properties.txt]
}
save_bd_design
write_bd_tcl [file join $evidence_dir ffn_overlay_bd.tcl]
set bd_file [get_files */ffn_overlay.bd]
generate_target all $bd_file
set wrapper [make_wrapper -files $bd_file -top]
add_files -norecurse $wrapper
set_property top ffn_overlay_wrapper [current_fileset]
update_compile_order -fileset sources_1
puts "FFN_BD_VALIDATED_AND_GENERATED"
close_project
