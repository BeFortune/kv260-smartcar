#!/bin/bash
# 现场常用操作。用法：carctl status | rate | log | events | frames | start | stop | restart | enable | disable | pl
set -u
RUNS=/home/ubuntu/car_runs
# 板子没有实时时钟，重启后时间会倒退，按修改时间排会挑到上一次开机的目录。
# 先找这次开机（boot_id 前 8 位）的运行目录，找不到再退回按时间挑。
latest() {
  local b
  b=$(cut -c1-8 /proc/sys/kernel/random/boot_id 2>/dev/null)
  ls -1dt "$RUNS"/run_*_"$b"* 2>/dev/null | head -n 1 | grep . || ls -1dt "$RUNS"/run_* 2>/dev/null | head -n 1
}
case "${1:-status}" in
  status)
    systemctl --no-pager status car-xpad-bind car-pl-load car-main | grep -E "●|Active:|Main PID|code="
    echo "--- marker"; cat /run/kv260car/pl_loaded.json 2>/dev/null || echo "(no marker this boot)"
    echo "--- latest run: $(latest)"
    [ -n "$(latest)" ] && tail -n 5 "$(latest)/events.jsonl"
    ;;
  log)     journalctl --no-pager -u car-pl-load -u car-xpad-bind -u car-main -b -n "${2:-80}" ;;
  events)  r=$(latest); [ -n "$r" ] && tail -n "${2:-30}" "$r/events.jsonl" ;;
  frames)  r=$(latest); [ -n "$r" ] && tail -n "${2:-5}" "$r/frames.csv" ;;
  rate)    shift; exec /usr/local/share/pynq-venv/bin/python3 \
             /opt/kv260car/current/tools/car/carwatch.py "$@" ;;
  start|stop|restart) sudo systemctl "$1" car-main ;;
  enable)  sudo systemctl enable car-xpad-bind car-pl-load car-main ;;
  disable) sudo systemctl disable car-xpad-bind car-pl-load car-main ;;
  pl)      sudo systemctl restart car-pl-load && journalctl --no-pager -u car-pl-load -b -n 20 ;;
  *) echo "usage: carctl status|rate [--once]|log [n]|events [n]|frames [n]|start|stop|restart|enable|disable|pl"; exit 2 ;;
esac
