# Common hard gate, evaluated on the OPEN implemented design before bitgen and export.
proc ffn_gate {prefix} {
    global evidence_dir
    set route [report_route_status -return_string]
    set f [open [file join $evidence_dir ${prefix}_route_status.rpt] {WRONLY CREAT EXCL}]
    puts $f $route
    close $f
    foreach {label variable} {"routable nets" routable "fully routed nets" routed "nets with routing errors" errors} {
        set pattern [format {# of %s[. ]*:[ ]*([0-9]+)} $label]
        if {![regexp -nocase $pattern $route -> count]} { error "Cannot parse route status: $label" }
        set $variable $count
    }
    if {$routable <= 0 || $routable != $routed || $errors != 0} { error "Route gate failed: routable=$routable routed=$routed errors=$errors" }
    set timing [report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose -return_string]
    set f [open [file join $evidence_dir ${prefix}_timing_summary.rpt] {WRONLY CREAT EXCL}]
    puts $f $timing
    close $f
    if {![string match {*All user specified timing constraints are met.*} $timing]} { error "Timing summary did not explicitly meet all constraints" }
    set setup [get_timing_paths -delay_type max -max_paths 1]
    set hold [get_timing_paths -delay_type min -max_paths 1]
    if {[llength $setup] != 1 || [llength $hold] != 1} { error "Missing usable setup/hold paths" }
    set wns [get_property SLACK $setup]
    set whs [get_property SLACK $hold]
    if {![string is double -strict $wns] || ![string is double -strict $whs] || $wns < 0 || $whs < 0} { error "Setup/hold hard gate failed: $wns / $whs" }
    report_drc -file [file join $evidence_dir ${prefix}_drc.rpt]
    set fatal [get_drc_violations -filter {SEVERITY == Error || SEVERITY == "Critical Warning"}]
    if {[llength $fatal] != 0} { error "DRC gate failed: $fatal" }
    set f [open [file join $evidence_dir ${prefix}_gate.txt] {WRONLY CREAT EXCL}]
    puts $f "STATUS=PASS\nWNS_NS=$wns\nWHS_NS=$whs\nROUTABLE=$routable\nROUTED=$routed\nROUTE_ERRORS=$errors"
    close $f
}
