# 从零复现说明

## 1. 只复核交付包

执行位置：仓库根目录，Windows Git Bash 或 Linux Bash。需要 Python 3 和 `host/deploy/requirements-test.txt` 中已有的依赖；本轮没有安装新依赖。

```bash
python metrics/scripts/verify_delivery.py
python -m pytest host/app/tests -q
```

预期：两条均退出 0；当前 9 个测试文件共 342 项通过。任一项失败就停止，不把历史通过记录当作本轮结果。

## 2. 准备完整 635 仿真输入

完整回放需要以下原始材料，仓库不直接附带录像或第三方 YOLO：

1. `metrics/evidence/integrated_sim/input_contract.json` 对应的 635 组原始图像及 metadata，文件大小和 SHA-256 必须与合同一致。
2. `host/model/ref_int8.py`、`params.json` 和 `s16_j1c2_cut2int8_ps.onnx`，仓库已附带并校验。
3. 真实 YOLO `roadsign_best.onnx`，按 `host/model/roadsign_MODEL.md` 的固定上游提交和 SHA-256 获取。
4. 已有 OpenCV、NumPy、ONNX Runtime 和 pytest 的 Python 环境。

合同中的项目根路径已脱敏为相对路径。运行时必须让原始图像位于这些相对路径可解析的位置；不得通过修改图像或哈希凑通过。输出目录必须尚不存在。

## 3. 运行完整全链

以下命令适用于包含 `pynq_ffn/` 原始数据和 `kv260-smartcar-submission/` 交付目录的工作区。在已配置依赖的 Bash 中，从该工作区执行：

```bash
DELIVERY=kv260-smartcar-submission
PY=python
"$PY" "$DELIVERY/host/app/tools/integrated_sim_replay.py" \
  --contract "$DELIVERY/metrics/evidence/integrated_sim/input_contract.json" \
  --policy-scope "$DELIVERY/metrics/evidence/integrated_sim/policy_scope.json" \
  --ref "$DELIVERY/host/model/ref_int8.py" \
  --params "$DELIVERY/host/model/params.json" \
  --partb "$DELIVERY/host/model/s16_j1c2_cut2int8_ps.onnx" \
  --roadsign-model pynq_ffn/artifacts/roadsign_real_validation_20261028/run01/model/roadsign_best.onnx \
  --roadsign-sha256 dff54ac921f3898fcf2f06f04662cc68b19e6034c2c9472b275e1ec01ddb22ee \
  --driver-threads 4 --roadsign-threads 1 \
  --out-dir "$DELIVERY/build_runs/integrated_new01"
```

当前已封存结果对应退出码 **1**：635 组全部执行，但 1 次 `driver_stale` 使验收状态为“失败”，随后锁存 42 tick。这是实际验收结果，不是模型加载或脚本错误。若出现 `failure.json`、样本缺漏、哈希错误或非零未处理数，应先排查运行环境，不能把它与本次超时混为一谈。

重跑必须换一个新输出目录。不得覆盖 `metrics/` 中的封存证据，也不得调宽 0.5 秒诊断窗口来制造通过。

## 4. PL 模块复核

`fpga/src/` 提供当前 C++/HPP、四组向量和 `build/hls.cfg`。已有 HLS C 124 组和 RTL 4 事务是独立模块证据，不是 YOLO+PL+PS 全链。HLS 工具流程见 `fpga/src/README.md`；本交付没有重新执行 AMD 工具。

## 5. 固定左附录

`host/app/tools/fixed_left_replay.py` 仅支持合成 fixture、虚拟时钟和 MemorySink。它不会运行驾驶模型或 YOLO，也没有相机/串口/PL 参数。固定左生产启用和真实出口验收不在本复现范围内。
