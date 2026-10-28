# 接口定义

## 1. 图像与 PL 接口

输入为三幅 `uint8 BGR 640x480`，顺序固定 **左、前、右**。每幅图执行：

1. 裁剪 `[150:479, 0:640]`。
2. 双线性缩放到 `200x66`。
3. BGR 转 YUV，CHW 排列。
4. 查表完成与原浮点预处理等价的 INT8 量化；输入 zero-point 为 0。

PL 输入为连续 `int8[3][3][66][200]`（118,800 字节），输出为连续 `int8[3][64][9][25]`（43,200 字节）。PS 用 `(q + 18) * 0.0806707814335823` 的 float32 查表结果反量化，再交给 `s16_j1c2_cut2int8_ps.onnx`。

寄存器以 `host/app/ps/pl_infer.py` 和 `metrics/evidence/system_artifact_verification.json` 为准：

| 名称 | 偏移 | 用途 |
|---|---:|---|
| CTRL | `0x00` | bit0 START、bit1 DONE、bit2 IDLE；采用 ap_ctrl_hs |
| in_r 低/高 32 位 | `0x10` / `0x14` | 输入缓冲 64 位设备地址 |
| out_r 低/高 32 位 | `0x1c` / `0x20` | 输出缓冲 64 位设备地址 |

控制地址预期为 `0xA0000000`，IP 名为 `conv_front_int8_0`。缓冲写地址后先 `flush`，启动并等待 DONE，再 `invalidate`。超时不代表硬件停止访问内存，不能立即释放或复用缓冲。

## 2. 模型文件身份

运行前核对固定 SHA-256：

- bit：`8ab0f411cc3ddee74045a753b379d265a121871dc656a53ef25b7c2edfe8da24`
- HWH：`c2b5c8138087c8e398d509df40e1c854311ebcca249f7a0e232c107d8db6d067`
- PS ONNX：`61bcec8f01ffca61ecc611f12bdf61a177f8ee0e9dc0febd256e02b8132cecdb`
- params：`889703bc0ea80da1a1f49f647c11714b249ee1eaea3149894f35dbbf4eb86a3f`
- 整数参考：`02feef12ea147f5b772a726ebd3c90d5f1e66bd33ff9fcefa50c3618a040edb4`

禁止把早期 FFN 或其他候选的文件混入这一组。

## 3. KV260 -> STM32 串口帧

串口参数为 115200、8N1。每帧固定 21 字节：

```text
'%' '@' '1'
+ 左速度3位 + '1' + 左速度3位 + '1'
+ 右速度3位 + '1' + 右速度3位 + '1'
+ 0xFF 0xFF
```

当前自动路径只接受 0–999 的左右速度，浮点向零截断。负数、超范围和非有限值拒绝编码。停车帧为四个 `000` 且标志均为 `1`。这与旧 NUC 自动路径的发送语义一致；停车补帧是 KV260 侧新增安全行为，仍需与当前 STM32 固件和实车隔离验证。

无 ACK 时，日志只能证明 KV260 写串口成功，不能证明 STM32 已接收或电机已执行。

## 4. 部署配置文件

- 相机口位：`host/app/ps/camera_ports.json`
- 串口口位与接线：`host/app/ps/serial_port.json`
- 手柄配置：`host/app/ps/joystick.json`
- 运行主入口：`host/app/tools/car/car_main.py`

这些文件是当前接法的配置证据，不应在其他接线或车辆上未经核对直接复用。
