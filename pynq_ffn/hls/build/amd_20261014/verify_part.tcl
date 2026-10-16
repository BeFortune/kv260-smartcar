puts "AMD_VERSION=[version -short]"
set kvboards [get_board_parts -quiet *kv260*]
puts "KV260_BOARD_PARTS=$kvboards"
foreach b $kvboards {
    puts "BOARD_PART=$b"
    report_property $b
}
set kparts [get_parts -quiet *k26*]
puts "K26_DEVICE_PARTS=$kparts"
foreach p $kparts {
    puts "DEVICE_PART=$p"
    report_property $p
}
exit
