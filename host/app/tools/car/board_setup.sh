#!/bin/bash
# 板上一条命令完成：核对包 -> 安装 -> 关键文件备份 -> 手动起一次服务验证 -> 停服务。
# 用法（ubuntu 用户，在包所在目录）：bash board_setup.sh kv260car_<时间>.tar.gz
# 不做：enable 开机自启、重启、打开串口发任何字节。没插相机/串口/手柄时主程序只会停在“等设备”。
# 每一步结果都写到 setup_<时间>.log，最后打印总结。哪一步失败就停在那一步。
set -u
PKG=${1:?usage: bash board_setup.sh kv260car_<time>.tar.gz}
[[ "$PKG" =~ ^kv260car_[0-9]{8}_[0-9]{6}\.tar\.gz$ ]] || { echo "unexpected package name: $PKG"; exit 1; }
VERSION=${PKG%.tar.gz}
LOG=$PWD/setup_$(date -u +%Y%m%d_%H%M%S).log
step() { echo; echo "=== $*" | tee -a "$LOG"; }
fail() { echo "FAILED: $*" | tee -a "$LOG"; echo "log: $LOG"; exit 1; }

step "0 sudo"
sudo -v || fail "sudo"

step "1 sha256"
sha256sum -c "$PKG.sha256" 2>&1 | tee -a "$LOG"
[ "${PIPESTATUS[0]}" = 0 ] || fail "package sha256"

step "2 unpack"
rm -rf "$VERSION"
tar xzf "$PKG" || fail "tar"
ls "$VERSION/tools/car" | tee -a "$LOG"

step "3 install (files + units only, not enabled)"
sudo bash "$VERSION/tools/car/install.sh" 2>&1 | tee -a "$LOG"
[ "${PIPESTATUS[0]}" = 0 ] || fail "install"

step "4 backup (read-only copy)"
sudo bash /opt/kv260car/current/tools/car/backup_board.sh 2>&1 | tee -a "$LOG"
[ "${PIPESTATUS[0]}" = 0 ] || fail "backup"

step "5 xpad bind"
sudo systemctl start car-xpad-bind 2>&1 | tee -a "$LOG"
systemctl --no-pager status car-xpad-bind 2>&1 | grep -E "Active:|xpad:" | tee -a "$LOG"
ls -l /dev/input/by-id 2>&1 | grep -i joystick | tee -a "$LOG"

step "6 pl load (downloads the pinned bit once; same method as 2026-10-21)"
sudo systemctl start car-pl-load 2>&1 | tee -a "$LOG"
journalctl --no-pager -u car-pl-load -b -n 30 2>&1 | tee -a "$LOG"
[ "$(systemctl is-active car-pl-load)" = active ] || fail "car-pl-load (see journal above)"
cat /run/kv260car/pl_loaded.json | tee -a "$LOG"

step "7 car-main for 25 s, then stop"
sudo systemctl start car-main 2>&1 | tee -a "$LOG"
sleep 25
systemctl --no-pager status car-main 2>&1 | grep -E "Active:|Main PID" | tee -a "$LOG"
RUN=$(ls -1dt /home/ubuntu/car_runs/run_* 2>/dev/null | head -n 1)
echo "run dir: $RUN" | tee -a "$LOG"
[ -n "$RUN" ] && cat "$RUN/events.jsonl" | tee -a "$LOG"
sudo systemctl stop car-main 2>&1 | tee -a "$LOG"
sleep 2
[ -n "$RUN" ] && tail -n 3 "$RUN/events.jsonl" | tee -a "$LOG"

step "8 summary"
{
  echo "version: $(readlink -f /opt/kv260car/current)"
  for u in car-xpad-bind car-pl-load car-main; do
    printf '%s enabled=%s active=%s\n' "$u" "$(systemctl is-enabled $u 2>&1)" "$(systemctl is-active $u 2>&1)"
  done
  grep -h '"kind": "pl_self_test"' "$RUN/events.jsonl" 2>/dev/null
  grep -h '"kind": "devices_not_ready"\|"kind": "state"' "$RUN/events.jsonl" 2>/dev/null | tail -n 3
  ls -1t /home/ubuntu/kv260_backup_*.tar.gz 2>/dev/null | head -n 1
} | tee -a "$LOG"
echo "log: $LOG"
echo "NOT enabled yet. To start on boot: sudo carctl enable"
