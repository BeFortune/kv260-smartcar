# 模型与量化参数

本目录属于 `conv_front_int8_20261021` 基线：

| 文件 | SHA-256 | 用途 |
|---|---|---|
| `s16_j1c2_cut2int8_ps.onnx` | `61bcec8f01ffca61ecc611f12bdf61a177f8ee0e9dc0febd256e02b8132cecdb` | PS 后半段 INT8 输入模型 |
| `params.json` | `889703bc0ea80da1a1f49f647c11714b249ee1eaea3149894f35dbbf4eb86a3f` | PL 量化参数与检查依据 |
| `ref_int8.py` | `02feef12ea147f5b772a726ebd3c90d5f1e66bd33ff9fcefa50c3618a040edb4` | 主机整数参考，不是 PL 实测 |

对应 bit/HWH 在 `../../fpga/build/`，哈希见 [../../docs/interface.md](../../docs/interface.md)。这些文件必须作为一组使用。

## 未包含

- 原始训练集、现场录像、预测 NPZ 和训练缓存。
- 早期 FFN 或其他候选模型。
- 完整 CPU FP32/INT8 对照模型；相关对照的哈希和指标保留在证据 JSON 中。
- 第三方 Python/ONNX Runtime 二进制。

原 NUC 模型、派生 ONNX、训练数据和 AMD 生成内容的许可证不自动等同于本仓库 MIT。当前仓库按用户决定保持私有；公开前仍须确认这些材料的再分发权。

没有完整黄金模型时，只能复现本包明确提供的整数参考和车端 PS+PL 路径，不能声称可从零复现全部 CPU 对照实验。

## 独立候选与可选路标模型

- 原 NUC 模型布局优化包在 `candidates/original_model_pspl/`，与上述生产模型无关；仅完成 CPU 软件等价，未晋升部署。
- 路标 YOLO 不直接附模型，固定上游提交、地址和 SHA-256 见 [roadsign_MODEL.md](roadsign_MODEL.md)。默认不启用 YOLO，模型获取不等于准确率或实车安全通过。
