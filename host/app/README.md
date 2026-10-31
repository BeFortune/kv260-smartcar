# 最新车端与仿真源码

- `ps/pl_infer.py`：整数参考/物理 PL 可替换的驾驶链；本验收只用整数参考。
- `ps/roadsign.py`：真实 YOLO 身份、CPU 推理与解析。
- `ps/control_v2/`：快照、策略、worker、唯一 Sender 与固定左附录。
- `tools/integrated_sim_replay.py`：635 组全链入口。
- `tools/control_v2_replay.py`：保存结果或合成场景策略回放。
- `tools/fixed_left_replay.py`：固定左虚拟时序工具。
- `tools/field_replay.py`、`roadsign_replay.py`：仅图像哈希和时间/统计辅助，不含旧驾驶或旧仲裁。
- `tests/`：当前保留的 9 个测试文件，共 342 项。

完整运行方法见 `../../docs/reproduction.md`。本目录不包含旧 FFN 候选、旧路标仲裁或生产控车入口；源码存在不代表硬件/车辆已启用。
