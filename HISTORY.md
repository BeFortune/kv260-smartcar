# KV260 方案时间线

本仓库按方案出现的时间顺序归档。每个方案只记录当时的真实状态；后续方案不覆盖前序结论，历史失败与阻塞原样保留。

| 顺序 | 日期 | 方案 | 归档状态 | 位置 |
|---|---|---|---|---|
| 01 | 2026-10-14 | 离线 PS 迁移基线、FFN 接口与主机验证 | 已在仓库 | `pynq_ffn/`（commit `63605e8e`） |
| 02 | 2026-10-15 | retry04 串行核：HLS、Vivado 系统实现、KV260 板端 bring-up | 已在仓库 | `pynq_ffn/artifacts/{hls,vivado,board}_20261015/`（commit `63605e8e`） |
| 03 | 2026-10-17 | 首轮四路输出通道候选（r01 → r02 主机修正） | 本次归档 | `pynq_ffn/artifacts/ffn_opt_20261017/`、`pynq_ffn/tools/validate_cpp_parallel.py` |
| 04 | 2026-10-18 | r03 16 通道候选：系统构建、100 MHz 板测、加速目标失败 | 尚未推送 | 本地 `pynq_ffn/docs/04_验证记录.md` 第 19–20 节 |
| 05 | 2026-10-18 | 第二轮：ORT 空转诊断 + r04 token 交错候选 | 尚未推送 | 本地 `pynq_ffn/docs/04_验证记录.md` 第 21 节 |

## 03（2026-10-17 四路输出候选）边界

- 改动：FC1/FC2 输出通道按 4 路分组，权重第二维 cyclic factor=4；通道内仍按原递增索引做 FP32 累加，bias 最后加，GELU 保持 exact-erf；请求 `PIPELINE II=1`，但**实际 II 未测**。
- 已验证（仅主机 Windows x64）：原版/候选各 19 项 testbench、2,035 个有效 token；19 组定向数据 262,656 个输出逐位相同；173 组三摄回归 29,407,232 个输出逐位相同，整数轮速零差；故障注入 8/8 通过；本机 pytest 48 项通过、0 skip。
- 未执行：HLS C 仿真、C 综合、实际 II/资源/时序、RTL 协同仿真、Vivado 实现、`.bit/.hwh`、板测。当时本机没有 AMD 工具，HLS 为阻塞；远程工具机未连通。
- 证据入口：`pynq_ffn/artifacts/ffn_opt_20261017/delivery_manifest.json`、`regression_summary.json`、`staged_hls/manifest.json`、`fault_injection_r02/results.json`。
- 归档说明：Windows 主机 `.exe/.dll` 与 `__pycache__` 未提交（可由源码重新编译）；其余原始日志、JSON 与故障注入夹具保留。
- 该方案的源码是 `staged_hls/`、`candidate/`、`candidate_r02/` 下的快照；后续 r03 已替换正式 `hls/ffn_accel.cpp`。不得把 `artifacts/vivado_20261015/retry04/overlay/` 的旧串行核 bit/hwh 当作该候选的构建结果。
