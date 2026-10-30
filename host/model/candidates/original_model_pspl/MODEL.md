# 原模型布局优化候选模型包

这是 2026-10-29 的独立离线候选，不是当前 `conv_front_int8` 生产模型，也没有新 Overlay。

- 原模型 `golden.onnx` SHA-256：`ad58d1fc9168da94b9ce076ba8cc9754c371ead87974da8bc9cc601ad1650d5b`
- 候选完整图 `full.onnx` SHA-256：`b759e35903cabbfc3ef659e4baef439fea26f67200d374093a77ed5f9b5d56fa`
- `stage_0/1/2.onnx` 为三段 PS 图，`camera_0/1.onnx` 为两个大 camera FFN 接口，`weights.npz` 为对应权重。
- 原权重逐字节不变，只改可审计的布局/形状常量；旧 `ps/runtime.py` 不兼容此包。
- `manifest.json` 仅把原清单的本机绝对 `source` 路径改成仓库相对路径，其余文件与哈希未改。

173 组和 635 组最终软件回归均为四输出零差、整数零分歧；这只是 CPU 软件等价。新 HLS、真实 PL、资源时序和 15 Hz 目标仍阻塞，不能用于生产替换。
