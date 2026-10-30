# 证据索引

这些文件是与当前 `conv_front_int8` 基线相关的小型证据副本，不包含录像、训练缓存或大型证据压缩包。

- `system_artifact_verification.json`：bit/HWH/XSA 哈希和寄存器成员核对；XSA 本体未交付。
- `cpu_timing_run01.json`：KV260 纯 CPU 多条件计时，全部未导入 PYNQ。
- `ps_overhead_run01.json`：PS 预处理、查表和后半段开销。
- `car_loop_board_run01.json`：2026-10-22 录像输入的 15 Hz 推理/编码调度，不是相机串口闭环。
- `host_replay01.json`：x86 整数参考离线回放，不是板测。
- `field_turning_diagnosis.md`：2026-10-27 新模型现场转弯失败诊断和不可安全替换结论。
- `original_model/`：2026-10-29 原 NUC 模型布局优化、准备式后端和 128 通道主机核的离线证据；15 Hz 与真实 PL 阻塞。
- `roadsign/`：真实路标模型 635 样本离线回放、性能和板前门禁；不是 KV260 结果。
- `control_v2/`：2026-10-30 独立控制候选的新 59/旧 83 测试和保存结果回放；未接车。

原始 JSON 中的 `/home/ubuntu/...` 是历史板端运行路径，用于证据追溯，不是本机复现命令。新增离线证据中的 Windows 项目根路径已在交付副本中改成仓库相对路径；数值、结论和文件 SHA-256 未改。
