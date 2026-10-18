# 16 路并行仍慢：定量归因（只读分析，2026-10-18）

## 结论（正式 100MHz，run02）
1. 16 lanes 只是把单 token 的 16 个输出通道展开；HLS 实际只分配 **4 个 fmul + 4 个 fadd**，FC1/FC2 都是 **II=4**。由算子数和 II 推导的有效吞吐为 **4 MAC/clk**（不是直接计数实测），不是 16 MAC/clk。
2. 正式100MHz：camera 660 wait **118.9ms/层**（6 个用例 118.88-118.99），HLS 117.73ms（+1.0%）；两层 camera 含 prepare/readback 253.25ms；全 FFN prepare 16.798 + wait 244.292 + readback 6.186 = **267.28ms**。
3. 1 thread split-PL `run_total_ms` mean **411.834** / p50 **411.313**；PS 五 stage 140.650ms。4 thread p50 **372.219** / p95 419.742 / p99 442.426；offline p50 412.987ms，持续 **2.367Hz**。15Hz 未达标。
4. CPU：golden1 p50 189.438，splitort1 193.083；golden4 p50 64.739 / p95 72.180 / p99 139.079，offline 91.289ms / 9.937Hz；splitort4 p50 318.341。PL FFN 约 **5.0x** CPU 单线程，PL 端到端比 golden1 慢 **2.17x**；不能称加速。
5. 条件/正确性：DIV0 10→15，未下载 overlay，前后均 **99.999MHz**；36 layer case、117 帧回归通过，1464 次 start，exit0，结束 CTRL=0x4。
6. 15Hz=66.7ms。即使 16 MAC/clk@100MHz，MAC 54.4ms + host 23ms 已 77ms；再加 PS stage 不可能达标。FFN 与 PS 分段必须同时解决。

## 1. 关键数字
- MAC/frame **87,031,808** = 2×660×65,536 + 2×4×65,536。
- 单 camera 层 660 token：HLS **11,773,385 cycles**；100MHz 117.73ms；正式100MHz 板测 wait **118.9ms/层**（6 个用例 118.88-118.99ms）；150MHz 历史值 78.49ms/79.46ms。
- 推导有效吞吐 4 MAC/clk（4 个乘加单元、II=4）：87.03M/4 = 21.76M cycles；100MHz 217.6ms（单 camera 纯 MAC 下限 108.13ms），150MHz 145.1ms。板测只测调用时间，未直接计数 MAC/clk；4 MAC/clk 是由算子数与 II 推出的有效值，板测时间吻合只是支持。
- FC1 `LOOP_82`：trip 128、II 4、iteration latency 12、521 cycles/16 输出组；FC2 `LOOP_108`：trip 256、II 4、1033 cycles/8 组。每 token 17,730 cycles，其中 MAC 16,384（92.4%），GELU 约 346（2.0%）；每次调用权重装载 71,585 cycles（100MHz 0.72ms，约 0.6%）。
- 资源：4×fmul（3 DSP）=12，4×fadd（2 DSP）=8，共 **20 DSP**；`generic_erf` **242 DSP**；总 262。242/262=92% 的 DSP 是 erf，但 erf 只占约 2% 时间。MAC 不在 LUT/comb，而在 DSP（另有少量 LUT 做特殊值）；DSP 分布不能推吞吐，II 和算子实例数才是证据。
- 16 lanes + cyclic 16 只保证每周期能读 16 个权重，不保证有 16 个乘加器；HLS 用 4 个算子把 16 lane 操作排到 4 个周期。

## 2. 为什么调 lanes/II 没有用
- r01：4 lanes、II=1，FC1 132 cycles/组 × 64 组 = 8,448 cycles/token，与 r03 16 lanes II=4 的 8,336 cycles/token 基本相同，推导吞吐仍是 4 MAC/clk。
- r01 在 10ns 目标下 estimated **25.733ns**；r02（II=3）**13.301ns**；r03b（II=4）**7.300ns**，OOC WNS +2.609ns。推导的 4 MAC/clk 是 100MHz 时序闭合的结果，不是漏调 pragma；简单降 II 会重新失去时序。
- 唯一有希望的上限路径：增加独立 token 上下文，让同一加法器隔 4 拍再服务同一累加器，同时增加乘加器实例。例如 4 token 在飞 ×16 lanes、16 fmul+16 fadd，理论 16 MAC/clk；每个 token 仍按 i 递增顺序累加，数值语义不变。资源约 80 DSP + 242 erf = 322 DSP（26%），但 100MHz 时序/布线未验证，不能承诺。

## 3. 没有任务级并行
- ABI schedule 严格串行：stage_0→camera_0→stage_1→camera_1→stage_2→cross_0→stage_3→cross_1→stage_4。camera_1 依赖 camera_0，cross 依赖两个 camera，四层不能并行。
- camera 输入 [3,220,128] 只是把 3 帧拼成 660 token 的一次调用；kernel 的 t 循环逐 token 串行，FC1→GELU→FC2 也逐 token 串行。16 lanes 是单 token 内的输出通道分组，不是 16 个 token/任务并行。
- 每帧 4 次 kernel 调用，每次重载 w1+w2（71,585 cycles）。camera 调用只占 0.6%；4-token cross 计算只有 70,920 cycles，权重装载约一半。100MHz 下 cross stage 7.89ms，wait 约 3.4ms，纯计算约 1.4ms；150MHz 历史 wait 2.30ms、纯计算 0.95ms，固定开销明显。

## 4. PS 侧固定成本
- 以下分项来自 `report.json` 的 `ffn_*`/`ps_remaining_stage_ms` 均值（逐次汇总），不是 `run_total_ms`。正式100MHz split-PL1：prepare **16.798**、wait **244.292**、readback **6.185**、PS 五 stage **140.650ms**；对应 `run_total_ms` mean 411.834 / p50 411.313ms。
- 150MHz run01（历史）：prepare 16.8、wait 163.5、readback 6.3、PS 140.5ms，`run_total_ms` p50 330.95 / mean 331.54ms。
- `_prepare_weights` 每次调用都对 w1/b1/w2/b2（263,680 B）做 SHA-256 + isfinite + ascontiguousarray，即使已缓存；4.2ms/次里 hash 占多少没有分项计时，不能断言。
- `forward` 每次都 copy x（最多 337,920 B）+ x.sync_to_device + y.sync_to_device + 多次 MMIO 写 + 0.1ms 轮询；readback 约 2.9ms/660 token（约 115MB/s），含 sync_from_device、np.array 复制、isfinite。
- runtime 第 101-102 行每帧复制 last_ffn_inputs/outputs（camera 约 1.35MB），run_total 已包含；生产路径应去掉。
- 本轮前 100MHz 预测为 camera wait 约 118.7ms/层、FFN 约 266ms、thread1 总 405-406ms；实测分别 118.9ms、267.28ms、411.3-411.8ms（+1.4%），见 §8。prepare/readback 基本不随时钟变慢。

## 5. CPU 对比与 15Hz
- 以下 FFN 分项取 `stage_ms`（camera_0/camera_1/cross_0/cross_1），不是 `run_total_ms`。正式100MHz ORT 单线程 split-ort：camera FFN 24.945/25.179ms，cross 0.488/0.479ms，FFN 合计约 51.09ms。单 camera 43.25M MAC / 24.95ms = **1.73 GMAC/s**。
- PL 100MHz：43.25M / 118.9ms = **0.364 GMAC/s**，慢约 4.76x；PL FFN 267.28ms vs CPU FFN 约 53.21ms，慢约 5.0x。
- 16 MAC/clk@100MHz = 1.6 GMAC/s，只与 CPU 单线程大致持平，不是加速。
- golden4 `run_total_ms` p50 64.739ms（mean 67.542、p95 72.180、p99 139.079、max 148.983）是唯一接近 15Hz 的实测；offline p50 91.289ms / 9.937Hz，compute-only 14.81Hz，不能称稳定 15Hz。

## 6. ORT 多 session/threadpool：怀疑成立但未证明
- 以下统一取 `report.json` `phases.performance.conditions[*].run_total_ms`（正式100MHz）：golden4 p50 64.739、mean 67.542；split-ort4 p50 318.341、mean 317.238（约 4.92x）。thread1：golden p50 189.438，split-ort p50 193.083（+1.9%）。
- 以下分项来自 `stage_ms`（逐层），不是 `run_total_ms`。同一 split-ort，thread1→4：cross_0 0.474→11.284ms（24x），cross_1 0.467→11.779ms（25x），stage_2 14.022→30.743ms（2.2x），stage_3 13.739→36.386ms（2.6x）；camera FFN 24.90→36.29/36.04ms（1.45x）；stage_4 0.608→0.702ms（未放大）；非 FFN 合计 139.877→217.949ms。
- 4-token cross FFN 被放大 24-25x；这像每个 session 4 个 intra-op 线程的池创建/同步/spin 开销，而不是 graph fusion 损失（fusion 损失在 thread1 也应出现，但 thread1 只差 1.9%）。stage_4 本轮未放大，说明不是所有小 stage 都必然坏，仍需 profiler 定位。
- split-PL4 的 PS 五 stage 降到 98.491ms（FFN 不占 CPU 线程），进一步支持 CPU 线程竞争是主因。
- 但没做 ORT profiling、没测 allow_spinning/每 stage 线程数、没做 CPU affinity，不能认定唯一根因。

## 7. 可验证推荐次序（本轮只完成复测；以下 2-5 未执行，需另授权）
1. 100MHz 复测已完成（exit0）：camera wait 118.9ms、FFN 267.28ms、thread1 总 411.3-411.8ms，均在预测 +1.5% 内；预测 vs 实测见 §8。
2. PS 低成本测量：在 `_prepare_weights`/copy/sync/MMIO 加分项计时；把权重校验移到初始化（每层一次），去掉每帧 instrumentation copy。上界是 prepare 16.8ms + readback 6.3ms，能省多少需实测，不预设。
3. ORT 线程实验（离线、同 bundle、不改模型）：threads 1/2/4；小 stage intra=1；allow_spinning=0；开 profiling。证实或排除 threadpool 假说。
4. 若仍要 PL 加速：先做 HLS 设计实验（不改正式源码）——4 token 在飞 ×16 lanes、16 fmul+16 fadd、II=4、每累加器 4 拍复用；目标 16 MAC/clk，数值顺序不变。硬门槛：10ns 时序闭合、C 仿真、缩小 RTL cosim；再谈板测。
5. 战略取舍交用户/主代理：当前证据不支持“PL FFN 加速”。若 15Hz 是硬目标，优先解决非 FFN 五 stage 的 ORT 线程/分段问题，或评估 CPU golden4 的稳定性。

## 8. 预测 vs 实测（正式 100MHz，run02）
统一取 `validation_run02_100mhz/report.json` 的 `phases.performance.conditions[*]` 与 `phases.layers.rows`。

| 项目 | 本轮前预测 | 正式100MHz实测 | 偏差 |
|---|---|---|---|
| camera 660 wait | 118.7ms/层 | 118.9ms/层（6 用例 118.88-118.99） | +0.2ms / +0.2% |
| 两层 camera wait | 237.4ms | 237.8ms（2×118.9） | +0.4ms / +0.2% |
| FFN（prepare+wait+readback） | ~266ms | 267.28ms（16.798+244.292+6.186） | +1.3ms / +0.5% |
| split-PL1 `run_total` | 405-406ms | mean 411.834 / p50 411.313ms | +1.4% |
| split-PL4 `run_total` | 未给单点预测 | p50 372.219 / p95 419.742 / p99 442.426ms | — |
| camera wait vs HLS | — | 118.9 / 117.73 = +1.0% | — |
| 150→100MHz wait 比 | 1.5（时钟比） | 118.9 / 79.46 = 1.496 | -0.3% |

- 纯 MAC 下限（4 MAC/clk@100MHz）为 108.13ms/camera、217.58ms/整帧；HLS 117.73ms 含 FC1/FC2 尾声、GELU、token load、权重装载；实测 118.9ms 再加 AXI/轮询。三者同量级，没有出现额外大头。
- 4 units + II=4 的 4 MAC/clk 模型与实测时间吻合；但这是端到端时间吻合，不是直接 cycle 计数。
- CPU 对照（`run_total_ms`）：golden1 189.438、splitort1 193.083；golden4 p50 64.739 / p95 72.180 / p99 139.079，offline 91.289ms / 9.937Hz；splitort4 p50 318.341。CPU 与上轮 150MHz 基本一致（±2%）；PL 端到端慢 golden1 2.17x，PL FFN 慢 CPU 单线程约 5.0x。
- 4 thread：PS 140.650→98.491ms，FFN wait 244.292→247.615ms（PL 时钟不变，基本持平），p50 372.219ms 仍远超 66.7ms；split-ort4 的 PS 反而 217.949ms，呼应 §6 的线程池怀疑。
- 条件/正确性：DIV0 10→15、`overlay_loads=0`、前后 99.999MHz、36 layer case（最大 7.45058e-08）、117 帧回归通过、1464 starts、exit0、结束 CTRL=0x4。

## 证据路径
- `pynq_ffn/hls/ffn_accel.cpp:47-48,78-97,104-119`
- `artifacts/ffn_opt_20261018/hls_evidence/r03b/build/ffn_accel_modern/hls/syn/report/ffn_accel_csynth.rpt`（11,773,385 cycles；262 DSP）
- 同目录 `ffn_accel_Pipeline_VITIS_LOOP_82_9_csynth.rpt`（II 4、128、521）、`..._LOOP_108_14_csynth.rpt`（II 4、256、1033）、`..._LOOP_100_12_csynth.rpt`（346 cycles）
- `artifacts/ffn_opt_20261018/hls_evidence/r01/.../ffn_accel_Pipeline_VITIS_LOOP_82_9_csynth.rpt`（II 1、132×64）
- `artifacts/ffn_opt_20261018/board_run02_100mhz/validation_run02_100mhz/report.json`（正式100MHz conditions/layers；本文件数字以此为准）
- `artifacts/ffn_opt_20261018/board_run02_100mhz_summary.json`（时钟/start/汇总）
- `artifacts/ffn_opt_20261018/board_run01_summary.json`、`.../board_run01/validation_run01/events.jsonl`（150MHz run01，历史对照）
- `pynq_ffn/ps/runtime.py:100-102`；`pynq_ffn/ps/pynq_backend.py:121-145,152-204`
- `pynq_ffn/ps/partition.py:30`（[3,220,128]/[1,4,128]）、`:146-173`（串行 schedule）
