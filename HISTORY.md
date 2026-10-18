# KV260 方案时间线

本仓库按方案出现的时间顺序归档。每个方案只记录当时的真实状态；后续方案不覆盖前序结论，历史失败与阻塞原样保留。

| 顺序 | 日期 | 方案 | 归档状态 | 位置 |
|---|---|---|---|---|
| 01 | 2026-10-14 | 离线 PS 迁移基线、FFN 接口与主机验证 | 已在仓库 | `pynq_ffn/`（commit `63605e8e`） |
| 02 | 2026-10-15 | retry04 串行核：HLS、Vivado 系统实现、KV260 板端 bring-up | 已在仓库 | `pynq_ffn/artifacts/{hls,vivado,board}_20261015/`（commit `63605e8e`） |
| 03 | 2026-10-17 | 首轮四路输出通道候选（r01 → r02 主机修正） | 已归档 | `pynq_ffn/artifacts/ffn_opt_20261017/`、`pynq_ffn/tools/validate_cpp_parallel.py` |
| 04 | 2026-10-18 | r03 16 通道候选：系统构建、100 MHz 板测、加速目标失败 | 已归档 | `pynq_ffn/artifacts/ffn_opt_20261018/`、`pynq_ffn/hls/ffn_accel.cpp/.hpp` |
| 05 | 2026-10-18 | 第二轮：ORT 空转诊断 + r04 token 交错候选 | 已归档 | `pynq_ffn/artifacts/opt_round2_20261018/`、`pynq_ffn/hls/candidates/interleaved_r04/` |
| 06 | 2026-10-18 | r04 v2 系统集成与 KV260 上板：数值通过、加速目标仍失败 | 本次归档 | `pynq_ffn/artifacts/r04_v2_board_20261018/` |

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

## 05（2026-10-18 第二轮：ORT 空转诊断 + r04 候选）边界

- ORT 控制实验（纯 CPU，117 帧、warmup 3、正反序）：split-4 允许空转 P50 `404.512/411.005 ms`，关闭空转 `75.181/77.320 ms`，线程数同为 31；golden 4 线程 `64.793/64.624 ms`。四输出过 `1e-3` 与整数门槛，但 split-1 与 golden-4 非逐位。
- 生产 Runtime 新增可选 `Runtime(..., spinning: bool | None = None)`：`None` 保持旧默认，`False` 关闭两类 `allow_spinning`。`Runtime.run`、模型/ABI/manifest/精度/预处理/调度未变；SHA `49ace8fbf61a29e2e54efc174b45477cd08fc5fe469d13547df17326b9745576`。KV260 复验 golden `None` vs split `False` 四输出 0 差、整数 0 分歧，P50 `65.2979/79.6322 ms`；仍慢于 golden。
- 测试：97 项 = 92 通过、5 显式 skip；原 48 项独立通过，新 policy 21 项通过。
- r04 v1（4 context/16 lane）主机 27 项、HLS C 27 项通过，但 csynth 关键模块 slack **-3.70 ns**，失败不做 RTL。r04 v2（8 context、fadd6/fmul3）主机 30 项、两套 173 帧逐位零差、HLS C 30 项、小范围 RTL 10 事务/30 token 通过，FC1/FC2 II=1/1；csynth 静态估计 3,620,660 cycles/660 token ≈ 36.2066 ms@100 MHz；OOC WNS **+2.435662 ns**、WHS +0.034 ns、未路由 0。
- **未执行**：r04 v2 系统 bit/hwh、Overlay 下载/上板、完整 659/660-token RTL、长时稳定性/功耗/温度、实时链路/实车。OOC 不是系统 Overlay，36.21 ms 不是板测成绩。
- 勘误：csynth XML 的 `AreaEstimates/AvailableResources` 是器件容量，不是核用量；v2 核用量以 csynth.rpt 为准（BRAM_18K 142、DSP 333、FF 64,719、LUT 58,866；XML Resources LUT 58,860，差 6 未解释）。OOC routed 资源 LUT 51,146、FF 46,657、DSP 333、BRAM 131 个 18K 等效块（65.5 个 36K tiles）；127 输入/280 输出缺外部 I/O delay 约束（TIMING-18 427 条），不是系统接口时序签核。详见 `pynq_ffn/artifacts/opt_round2_20261018/closure_notes.md`。
- 归档说明：Windows 主机 `.exe/.dll` 与 `__pycache__` 未提交；ORT 证据、HLS/RTL/OOC 报告与候选源码保留。证据入口 `pynq_ffn/artifacts/opt_round2_20261018/README.md`。

## 06（2026-10-18 r04 v2 系统集成与 KV260 上板）边界

- 系统集成：独立 PS+PL 五阶段 exit 0；WNS **+1.747 ns**、WHS **+0.010 ns**、TNS/THS 0；**97,862 nets 全路由、0 error**；同次 bit/HWH/XSA 已核对。
- 上板：实际 99.999 MHz；下载一次后独立只读复核无第二次下载；1-token 冒烟、36 层 case、117 帧三模式全部 exit 0；**517 次 START**，结束时 CTRL=4，无 timeout。
- 数值：单层最大误差 **1.04308128e-7**；四输出最大 `speed_raw` **0.0001220703125**，整数轮速 0 分歧。
- 性能（117 帧、warmup 3、4 线程）：golden **65.210875 ms**、split-ORT **74.866631 ms**、split-PL **170.030144 ms**；PL 比 golden 慢 **2.607 倍**、比 split-ORT 慢 **2.271 倍**。**加速目标失败，未达到 15 Hz。**
- 口径：跨轮不能做纯内核 A/B（r03 372.22 ms 与本轮 170.03 ms 之间还有 ORT/流程变化）；csynth 36.2 ms 不能当作板上 camera wait 40.93 ms 的解释；PL wait 含软件轮询边界。
- 未执行：完整 659/660-token RTL、长时稳定性/功耗/温度、实时三摄/串口/电机/实车。
- 归档说明：`board_evidence01.zip`（385 份证据 + manifest，SHA `f0ef8ff4…48e9`）因单文件超过 GitHub API 推送上限未提交；其 386 个成员已逐文件解包在 `collected_board01/`，可按 manifest 逐文件复核。Windows 主机 `.exe/.dll`、`.pyc`、`__pycache__` 未提交。
