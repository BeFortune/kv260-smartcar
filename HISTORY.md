# KV260 方案时间线

> 2026-10-19 路线调整：独立 FFN 卸载不再是加速主线，当前分两路（CNN 新模型 / Transformer 硬件流水线）。详见 [STATUS.md](STATUS.md)。05/06 的 r04 证据在 `history/r04-20261018` 分支。

本仓库按方案出现的时间顺序归档。每个方案只记录当时的真实状态；后续方案不覆盖前序结论，历史失败与阻塞原样保留。

| 顺序 | 日期 | 方案 | 归档状态 | 位置 |
|---|---|---|---|---|
| 01 | 2026-10-14 | 离线 PS 迁移基线、FFN 接口与主机验证 | 已在仓库 | `pynq_ffn/`（commit `63605e8e`） |
| 02 | 2026-10-15 | retry04 串行核：HLS、Vivado 系统实现、KV260 板端 bring-up | 已在仓库 | `pynq_ffn/artifacts/{hls,vivado,board}_20261015/`（commit `63605e8e`） |
| 03 | 2026-10-17 | 首轮四路输出通道候选（r01 → r02 主机修正） | 已归档 | `pynq_ffn/artifacts/ffn_opt_20261017/`、`pynq_ffn/tools/validate_cpp_parallel.py` |
| 04 | 2026-10-18 | r03 16 通道候选：系统构建、100 MHz 板测、加速目标失败 | 本次归档 | `pynq_ffn/artifacts/ffn_opt_20261018/`、`pynq_ffn/hls/ffn_accel.cpp/.hpp` |
| 05 | 2026-10-18 | 第二轮：ORT 空转诊断 + r04 token 交错候选 | 尚未推送 | 本地 `pynq_ffn/docs/04_验证记录.md` 第 21 节 |

## 03（2026-10-17 四路输出候选）边界

- 改动：FC1/FC2 输出通道按 4 路分组，权重第二维 cyclic factor=4；通道内仍按原递增索引做 FP32 累加，bias 最后加，GELU 保持 exact-erf；请求 `PIPELINE II=1`，但**实际 II 未测**。
- 已验证（仅主机 Windows x64）：原版/候选各 19 项 testbench、2,035 个有效 token；19 组定向数据 262,656 个输出逐位相同；173 组三摄回归 29,407,232 个输出逐位相同，整数轮速零差；故障注入 8/8 通过；本机 pytest 48 项通过、0 skip。
- 未执行：HLS C 仿真、C 综合、实际 II/资源/时序、RTL 协同仿真、Vivado 实现、`.bit/.hwh`、板测。当时本机没有 AMD 工具，HLS 为阻塞；远程工具机未连通。
- 证据入口：`pynq_ffn/artifacts/ffn_opt_20261017/delivery_manifest.json`、`regression_summary.json`、`staged_hls/manifest.json`、`fault_injection_r02/results.json`。
- 归档说明：Windows 主机 `.exe/.dll` 与 `__pycache__` 未提交（可由源码重新编译）；其余原始日志、JSON 与故障注入夹具保留。
- 该方案的源码是 `staged_hls/`、`candidate/`、`candidate_r02/` 下的快照；后续 r03 已替换正式 `hls/ffn_accel.cpp`。不得把 `artifacts/vivado_20261015/retry04/overlay/` 的旧串行核 bit/hwh 当作该候选的构建结果。

## 04（2026-10-18 r03 16 通道候选）边界

- 源码：`pynq_ffn/hls/ffn_accel.cpp` SHA-256 `829c9ec63604e327ad4ea13855b405113ea74fea6a62721d512ded6341b0b9b6`，`ffn_accel.hpp` `715f0ca11cc5e9920569654f7296c8443a0186f988557bd27ec1af3f476c7c35`。16 个输出通道分组并行，FC1/FC2 实际/目标 II=4/4，GELU 单遍 II=1，保留通道内 FP32 累加顺序与 exact-erf。
- 已验证：主机 19 组合成 + 173 组三摄回归 29,407,232 个输出逐位相同、整数轮速零差；HLS C 仿真 19 项；RTL cosim 12 事务/30 有效 token（**未覆盖 659/660-token**）；r03b HLS OOC WNS +2.609004 ns、WHS +0.032047 ns；PS+PL 系统五阶段 exit 0、WNS +2.433 ns、WHS +0.010 ns、63,005 nets 全路由且 error 0；KV260 150 MHz 首轮与 100 MHz 复测均完成 36 层/117 帧数值回归，整数轮速全一致。
- 100 MHz 复测：仅授权写 PL0 divisor0 10→15，实测 `149.9985→99.999 MHz`，复用已加载 Overlay、0 次新下载；36 层最大误差 7.45058e-8，117 帧 `speed_raw` 最大 0.0001220703125，1464 次 kernel start 正常结束。
- **失败**：100 MHz 下 4 线程 split-PL 中位 372.2187 ms，仍比完整 golden 64.7386 ms 慢约 5.75 倍；1 线程 411.313 vs 189.438 ms。**PL 加速目标失败，未达到 15 Hz。**
- 未执行：完整 659/660-token RTL cosim、长时稳定性/功耗/温度、实时相机/串口/电机/实车。
- 归档说明：Windows 主机 `.exe/.dll` 与 `__pycache__` 未提交；原始日志、报告、ZIP、`.bit/.hwh/.xsa` 与模型/权重测试夹具保留，便于按 SHA 复核。证据入口 `pynq_ffn/artifacts/ffn_opt_20261018/README.md`。
