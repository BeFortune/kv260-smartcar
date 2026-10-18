# Explicit batch exit contract: never turn a Tcl error into a successful process exit.
if {[llength $argv] != 1} { puts stderr "Expected one stage script"; exit 2 }
if {[catch {source [lindex $argv 0]} message options]} {
    puts stderr "FFN_STAGE_FAILED: $message"
    if {[dict exists $options -errorinfo]} { puts stderr [dict get $options -errorinfo] }
    exit 1
}
exit 0
