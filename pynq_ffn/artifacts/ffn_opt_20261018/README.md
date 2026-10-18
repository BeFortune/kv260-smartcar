# FFN 首轮优化：当前候选与验收入口

更新：2026-10-18。**用户已授权并完成 PL0 分频10→15，当前实读99.999MHz；100MHz下36项切点与117帧离线验收通过，但端到端加速目标失败（4线程混合P50 372.219ms，完整CPU图64.739ms）。** 开发主源为 `pynq_ffn/`；不改NUC/STM32、模型/预处理、相机顺序或控制语义，无车辆动作。

## 当前候选身份

- C++ SHA-256：`829c9ec63604e327ad4ea13855b405113ea74fea6a62721d512ded6341b0b9b6`。
- HPP SHA-256：`715f0ca11cc5e9920569654f7296c8443a0186f988557bd27ec1af3f476c7c35`。
- 架构：16 个输出通道分组并行、权重第二维 cyclic 16；每个输出内部仍按原索引顺序执行 FP32 累加。FC1/FC2 归约实际/目标 II=4/4，GELU 保持 erf 公式并单遍流水。没有量化、重新训练或更改 PS 模型边界。
- r03 与短路径 r03b 是同源：源码、生成 RTL 和 csynth XML 哈希相同；r03b 只为获得完整、未中断的 HLS 实现。

## 已验证

1. 本机 19 组合成和 173 组三摄回归：29,407,232 个真实层输出对旧串行主机核逐位相同，整数轮速无分歧。原报告在 `../ffn_opt_20261017/r03_bitwise_payload.json`；`host_validation/manifest.json` 又完整绑定了源快照、编译命令/退出码、新 DLL 哈希与成功回归报告，未覆盖旧文件。
2. HLS C simulation：完整 19 项 testbench 通过，含 659/660 token、权重重载、zero/bias 与非法计数。
3. RTL cosim：缩小到 12 事务、30 个有效 token，通过 Verilog 和 C 后检查；**不覆盖 659/660-token RTL**。仿真合计 1,279,573 cycles，约 95 分钟墙钟；均不是实板吞吐。
4. HLS C synthesis：估计 11,773,385 cycles/660-token 调用、100 MHz 目标约 117.7 ms；DSP 262、BRAM_18K 137、LUT 28,420、FF 33,593。
5. r03b HLS OOC 实现：WNS +2.609004 ns、WHS +0.032047 ns、未路由 nets=0。实现资源 LUT 23,065、FF 24,044、DSP 262、BRAM 66.5 tiles。**这是独立 HLS IP 实现，不是 PS+PL 系统实现。**
6. 同次 HLS IP：351,343 字节，SHA-256 `de24cf176e36e8ddf8aea03db02faa9b218133e799cd8ba0289c722a29c5e56b`，来源 `D:/f3/hls/build/ffn_accel_modern/ffn_accel.zip`。

完整 HLS 原始资料：`hls_evidence/`，193 个证据文件已逐个核对字节数与 SHA-256；压缩包 `hls_evidence.zip` SHA-256 `a9aba691b8c343cd4cb2af2f2b4725ca522038be2782e989e5fedf8128d910ef`。历史失败、r01/r02 候选保留，不改旧报告。

## 系统构建：已验证

- 工具机“4090内网”（192.168.110.248）全新 `D:/g3`，固定上述 IP SHA，未 fallback 到 retry04。BD、综合、实现、导出五阶段真实退出码均为 0。
- 系统实现：WNS **+2.433 ns**、WHS **+0.010 ns**；63,005/63,005 nets 完整布线、路由错误 0，DRC Error/Critical Warning 为 0；普通 Warning/Advisory 保留。实现约束为 **10 ns / 标称 100 MHz**。
- 整机资源：LUT 26,395、FF 29,753、DSP 262、RAMB36 65 块、RAMB18 3 块（66.5 个 RAMB36 等价块）、URAM 0。
- 同次 `.bit/.hwh/.xsa` 已校验：`system_build/load/ffn_overlay_wrapper.bit` SHA `c67dd63ccd579ab105840a30c4ca1a61715a26b7631cc323473328a821319eb1`；HWH SHA `f4040b704f14ab02ab25b5266226442541be1fc30cc6e786512d9a00ea675649`；XSA SHA `46dcbd22a08b3e4fdbc5e96ed506ff2fb42d14c64faa3e1dad1690a09e17327d`。XSA 内 bit 与 load bit 字节一致，HWH 来自同次顶层 `ffn_overlay.hwh`；ABI、两个 64 位 master 和 low/high HP0 映射均已核对。
- `system_scripts/` 为可复现构建驱动及 9 项离线测试；`system_build/records/` 及原始日志/报告共 86 份，逐文件哈希已验证。打包 `system_evidence.zip` SHA `5261e013253690d254775ae91fb47b4e80514ea9092266283a7b23f9cfc2aadf`。

## 当前100MHz复测：离线数值与性能测量已验证，加速目标失败

- 用户本轮明确授权仅修正PL0 DIV0从10到15，不改PLL/其他时钟。复测程序在构造Overlay前核对运行时工件身份及文件哈希，使用 `Overlay(loaded_path, download=False)` 复用同一工件，没有重新编程PL。
- 唯一时钟字段写入：PL0原始值`0x01010a00`→`0x01010f00`；实际`149.9985`→`99.999 MHz`。5个PLL和另外3路PL时钟原始值完全不变，复测后只读CPU时钟控制也未变。开始/结束均通过100MHz±0.5%实际频率门槛。
- 36项真实切点、117/117帧四输出与整数轮速全通过；最大层误差`7.4505806e-8`、累计FFN`1.7881393e-7`、speed_raw误差`0.0001220703125`。1464次核启动，退出码0、正常close，结束CTRL=`0x4`，无超时/隔离。
- 原始证据 `board_run02_100mhz/validation_run02_100mhz/{report.json,events.jsonl}`，摘要 `board_run02_100mhz_summary.json`；压缩包SHA `7b820017172a015315de54a15a7bdc8e27c4d148c1b4f0a86355fc409408d7cb`。
- v3修正及验收程序 `board_scripts/pl0_retest_v3/`：67项离线测试通过，实际部署脚本SHA `c45449e6923ee827b9e6fe499028812ae855333dcd08623eb651e5d65357da8b`。旧v1/v2、run01和旧清单均未覆写。

同一KV260、模型与117帧，warmup3、每种模式单独创建/销毁ORT会话；表中run不含读盘/预处理，离线E2E包含它们，持续吞吐还含证据落盘。

| 模式/线程 | run P50/P95/P99（ms） | 离线E2E P50（ms） | 持续离线吞吐（组/s） |
|---|---:|---:|---:|
| golden / 4 | 64.739 / 72.180 / 139.079 | 91.289 | 9.937 |
| split-ort / 4 | 318.341 / 410.044 / 433.375 | 397.987 | 2.457 |
| split-PL / 4 | 372.219 / 419.742 / 442.426 | 412.987 | 2.367 |
| golden / 1 | 189.438 / 193.316 / 197.290 | 215.661 | 4.471 |
| split-ort / 1 | 193.083 / 197.439 / 203.213 | 219.467 | 4.421 |
| split-PL / 1 | 411.313 / 415.360 / 416.636 | 437.674 | 2.244 |

**正式100MHz结论：混合路径中位run比完整CPU图慢约5.75倍，并非端到端加速。** 仅FFN两层camera硬件等待约118.9ms/层，接近HLS117.7ms估计；当前16lane、II4和4个实际乘加单元的有效吞吐约4MAC/clk，计算能力尚不足以超过CPU矩阵实现。单线程分项均值：PL等待244.292ms、PS剩余140.650ms、准备16.798ms、读回6.186ms，另3.908ms调度/检查等；不同分位数不可机械相加。多ORT会话4线程开销显著，线程池竞争为待profiling验证的候选原因，不认定唯一根因。详见 `parallelism_bottleneck_analysis.md`。

## 历史首轮 KV260 板测：150MHz数值通过，非正式100MHz结果

- 用户授权新 Overlay 离线 PL 验收，并在 KV260 终端本地 `sudo -v` 授权；密码未进入代码/对话/日志。没有维护启动配置、CMA、服务、风扇或固件，没有使用相机、串口或电机。
- 全新 `/home/ubuntu/ffn_opt_20261018/deploy`、匹配 bit/hwh 和外部清单：23 个部署成员逐项校验；静态验证不导入 PYNQ、不编程硬件。
- 随后真实编程一次 Overlay，并核对 idle/ABI、真实模型切点。36 项测试（camera 1/4/659/660，cross 1/4，权重轮换/复访）最大绝对误差 `7.4505806e-8`。117/117 帧四输出与 CPU 比较通过，整数轮速全部一致；累计 FFN 最大误差 `1.7881393e-7`，`speed_raw` 最大误差 `0.0001220703125`。
- 全过程 1,464 次核启动；程序正常退出、正常释放缓冲区，无超时或隔离。结束后只读 CTRL=`0x4`，核 idle。
- **关键异常：实际 PL0 时钟 149.9985 MHz，而本次硬件按 100 MHz 实现验收。** HWH 设置 DIVISOR0=10、DIVISOR1=1，但板上 IOPLL 实际约 1.5 GHz，因此为 150 MHz；Linux clock cache 显示约100 MHz，不能推翻当前寄存器计算。不能将这轮记为100 MHz性能或稳定性验收。
- 发现异常后曾尝试经独立会话仅发 SIGINT；原会话被长任务占用，新会话 sudo 未授权，因此没有发送信号。随后原测试自行正常完成。未调用强制停止、未杀进程或复位，也未写任何时钟寄存器。
- 首轮结束时尚未取得修正授权，故当时未修正分频/重跑；该历史阻塞已由上文用户授权的100MHz复测解除。run01仍保留异常频率标记，不改成正式100MHz结果。
- 原始报告保留为 `board_run01/validation_run01/report.json`（测试脚本的原始“已验证”不包含时钟门槛）；验收汇总 `board_run01_summary.json` 明确正式条件为“阻塞”。`board_evidence_run01.zip` SHA `262beda3edd6b0854fe9ae1df6edc186d46d235b6f0bab4f12bbae6cb87d6d39`。

### 首轮异常频率下的性能观察（不可作为100 MHz验收）

117帧、warmup=3，各模式逐一创建/释放ORT会话，同线程比较，PYNQ实时计算时钟约150MHz。`run_total_ms` 不含读盘/预处理；offline E2E 包含它们；持续吞吐还包含证据落盘。

| 4线程模式 | run P50/P95/P99（ms） | offline E2E P50（ms） | 持续离线吞吐（组/s） |
|---|---:|---:|---:|
| golden 整图 CPU | 65.568 / 83.752 / 147.521 | 92.291 | 9.610 |
| split-ort 全 CPU 切图 | 325.540 / 425.560 / 452.407 | 399.041 | 2.442 |
| split-PL | 301.158 / 346.089 / 369.501 | 339.147 | 2.880 |

**结论：即使约150 MHz，split-PL 也仍比整图 CPU 慢，不能称为端到端加速。** 单线程 golden P50=189.735 ms、PL P50=330.949 ms，也没有收益。这不等于100 MHz性能结果；未满足15 Hz新鲜帧闭环目标。

## 性能口径

- 旧 173,252,285 / 新 11,773,385 ≈ 14.7 仅是两版 HLS 静态估算周期比，不是板上加速。
- 旧 retry04 8 帧 PL/CPU 比较使用不同线程，golden 71.19 ms 也只是单次 warmup 本身，不是新核或公平稳态基准。
- 新核性能必须在同一 KV260、同模型、同输入、同线程、同 warmup/样本数下比较完整 CPU 图、切图 CPU、PS+PL，并分别报告 PS 计算、准备/搬运、硬件等待、读回及端到端耗时。
- 录像吞吐不代表实时三摄的新鲜帧推理率；未实测前不宣称 15 Hz、避障或赛道成功。

## 历史快照和缺口

`../ffn_opt_20261017/delivery_manifest.json`、`staged_hls/manifest.json` 代表早期四路候选，不能当当前 r03 清单。旧 r02 host DLL 曾被同路径覆盖；原结果保留，但不冒称二进制可复现。对账见 `evidence_reconciliation.md`。

本轮系统工件和100MHz条件下的真实PL数值及117帧性能测量已验证；加速目标“失败”。长期稳定性、实时相机、串口/电机、实车任务和完整659/660-token RTL cosim仍“未执行”。当前核idle、实际PL0=99.999MHz，未改上游PLL/系统配置或实施新的优化。
