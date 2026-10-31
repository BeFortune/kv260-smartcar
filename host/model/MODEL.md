# 当前模型与获取边界

## 主方案模型

- `s16_j1c2_cut2int8_ps.onnx`：PS 后半段，SHA-256 `61bcec8f01ffca61ecc611f12bdf61a177f8ee0e9dc0febd256e02b8132cecdb`。
- `params.json`：PL 量化参数，SHA-256 `889703bc0ea80da1a1f49f647c11714b249ee1eaea3149894f35dbbf4eb86a3f`。
- `ref_int8.py`：整数参考，SHA-256 `02feef12ea147f5b772a726ebd3c90d5f1e66bd33ff9fcefa50c3618a040edb4`。

对应 bit/HWH 位于 `../../fpga/build/`。这些文件必须作为同一基线使用，不能与旧候选混用。

## 真实 YOLO

第三方 YOLO 不直接附带。固定上游提交、下载地址、大小和 SHA-256 见 [roadsign_MODEL.md](roadsign_MODEL.md)。模型获取不等于检测准确率、实时性能或车辆安全通过。

原始训练集、现场录像、旧 FFN 和旧候选模型均未纳入当前快照。模型和 AMD 生成内容的许可证不自动等同于本仓库 MIT，公开前仍须核查再分发权。
