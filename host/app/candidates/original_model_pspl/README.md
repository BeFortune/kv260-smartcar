# 原模型 PS+PL 离线候选源码

- `attention01/`：布局转换、独立 CPU 运行器、数据回放和验证器。
- `backend01/`：准备式后端与运行适配器；热路径减少重复权重哈希、分配和同步。
- 模型位于 `host/model/candidates/original_model_pspl/bundle01/`。
- 128 通道主机 C++ 核位于 `fpga/src/candidates/original_model_ffn128/`。

这些文件来自独立实验目录，未接入当前 `ps/runtime.py`、车端 CLI 或 Overlay。假 IP、假 DDR、g++ 和 CPU ONNX 结果不能写成真实 FPGA 计算。最终状态见 `metrics/evidence/original_model/final_status.json`。
