# 最新接口定义

## PL 与 PS partB

PL 输入为连续 `int8[3][3][66][200]`（118,800 字节），输出为 `int8[3][64][9][25]`（43,200 字节）。相机顺序固定左、前、右。PS 使用固定量化参数反量化并运行 `s16_j1c2_cut2int8_ps.onnx`，得到左右 `speed_raw`。

当前基线 SHA-256：

- bit：`8ab0f411cc3ddee74045a753b379d265a121871dc656a53ef25b7c2edfe8da24`
- HWH：`c2b5c8138087c8e398d509df40e1c854311ebcca249f7a0e232c107d8db6d067`
- partB：`61bcec8f01ffca61ecc611f12bdf61a177f8ee0e9dc0febd256e02b8132cecdb`
- params：`889703bc0ea80da1a1f49f647c11714b249ee1eaea3149894f35dbbf4eb86a3f`
- 整数参考：`02feef12ea147f5b772a726ebd3c90d5f1e66bd33ff9fcefa50c3618a040edb4`

## YOLO

真实模型固定上游提交 `5f28cbabff90ee318ddd0877257516fa03e95adb`，SHA-256 `dff54ac921f3898fcf2f06f04662cc68b19e6034c2c9472b275e1ec01ddb22ee`。输入为前视原图，float32 `[1,3,640,640]`；输出为 `[1,25200,15]`，4 框值 + 1 目标分 + 10 类分。没有锥桶类别。获取方式见 `../host/model/roadsign_MODEL.md`。

## v2 快照与决策

- `DriverSnapshot`：运行代号、帧序号、三路最早抓帧时间、驾驶完成时间、健康状态和左右输出。
- `SignSnapshot`：运行代号、帧序号、前视抓帧/完成时间、健康状态和接受的检测列表。
- `Decision`：最终左右输出、stop、原因和锁存状态。
- 只有合法最终决定才进入 `encode_side_speed()`；停车帧为四个 `000`，标志均为 `1`。

报文固定 21 字节，串口物理发送不在本次验收内。编码成功不表示 STM32 已接收或电机已执行。

## 集成仿真入口

`host/app/tools/integrated_sim_replay.py` 显式接收输入合同、策略配置、ref/params/partB 和真实 YOLO 路径。输出目录必须不存在；逐样本写入 `predictions.jsonl` 和 `summary.json`，任何阶段失败都保留失败文件并返回非零。

`host/app/tools/field_replay.py` 与 `roadsign_replay.py` 在交付包中只是图像哈希和时间/统计辅助模块，不含旧驾驶或旧仲裁实现。
