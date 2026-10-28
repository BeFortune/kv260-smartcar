#!/bin/bash
# 开机把 2.4G 接收器（XBOX 360 模式 413d:2104）交给 xpad，生成 /dev/input/jsN。
# xpad 内置列表没有 413d，所以要先加载模块再写 new_id。重复执行无害（已存在会报 File exists，忽略）。
# 接收器在 2106 模式（手柄超时关机时）不会被认领；手柄唤醒回到 2104 后 xpad 自动绑定。
set -u
modprobe xpad || { echo "xpad: modprobe failed"; exit 1; }
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ -w /sys/bus/usb/drivers/xpad/new_id ] && break
  sleep 0.5
done
if ! echo "413d 2104" > /sys/bus/usb/drivers/xpad/new_id 2>/tmp/xpad_new_id.err; then
  grep -q "File exists" /tmp/xpad_new_id.err || { echo "xpad: new_id failed: $(cat /tmp/xpad_new_id.err)"; exit 1; }
fi
rm -f /tmp/xpad_new_id.err
echo "xpad: 413d:2104 registered"
exit 0
