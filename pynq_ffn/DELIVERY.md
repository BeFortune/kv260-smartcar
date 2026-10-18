# 独立交付包说明

**2026-10-18 r03 板测：** r03 候选已完成 HLS、PS+PL 系统实现与 KV260 板测。获用户单独授权后仅改 PL0 DIV10→15，板上实测 `149.9985→99.999 MHz`，复用原已加载 Overlay 重跑：36 层/117 帧数值、整数轮速与首轮一致，**100 MHz 离线验收已验证**；但 split-PL 中位 372.22 ms 仍比完整 golden 64.74 ms 慢约 5.75 倍，**加速目标失败**。长时稳定、完整 RTL、实时链路未执行。r03 证据见 `docs/04_验证记录.md` 第 20 节与 `artifacts/ffn_opt_20261018/board_run02_100mhz/`；本 ZIP 是否包含这些新工件仍以 `DELIVERY_MANIFEST.json` 为准，不能据本节推断包内已有 bit/hwh。下方“不是已上板运行的 overlay”“未完成”等表述是 2026-10-14–17 的历史交付快照，不再代表当前硬件状态。

**最新 2026-10-18 第二轮（CPU 诊断 + PL 候选，未进正式交付）：** 生产 Runtime 新增可选 `spinning`（`None` 保持旧默认、`False` 关闭 ORT 两类忙等），最终 SHA `49ace8…5576`；KV260 纯 CPU 117 帧复验四输出零差，但 split-4 P50 79.63 ms 仍慢于 golden 65.30 ms，不称 15 Hz 或 PL 收益。r04 v2 独立候选通过主机、HLS csim/csynth、小范围 RTL 和同源 OOC 实现，静态估计 36.21 ms/660 token、OOC WNS +2.435662 ns，但**没有新 PS+PL 系统 bit/hwh、未上板、未替换 r03**。本 ZIP 是否包含这些新文件仍以 `DELIVERY_MANIFEST.json` 为准；OOC IP 不是 PYNQ overlay。详见 `docs/04_验证记录.md` 第 21 节。

本包最初（2026-10-14）是 `pynq_ffn` 的离线 PS 迁移、FFN 接口/HLS 参考实现和操作文档交付，**当时不是已上板运行的 FPGA overlay**。

## 内容

- [README](README.md)：从这里开始，包含不需要板卡的快速验证。
- [文件分析与迁移决策](docs/01_文件分析与迁移决策.md)。
- [FFN 接口与流水线设计](docs/02_FFN接口与流水线设计.md)。
- [AMD 与 Ross 从零操作指南](docs/03_AMD与Ross从零操作指南.md)。
- [验证记录](docs/04_验证记录.md)：优先引用其中注明的最终报告；旧报告为过程证据。
- `ps/`、`tools/`、`tests/`：PS 软件、验证器和测试。
- `hls/`：FP32 / erf-GELU 参考核、主机验证桥及 HLS 构建入口。
- `notebooks/01_ps_to_pl.ipynb`：默认只运行 CPU，不下载 PL。
- `artifacts/bundle_20261014/`：已准备好的整图、分图、真实权重及哈希清单。
- `artifacts/sample_recording_1779374497/`：一组三摄快速验证样本。
- `hls/build/amd_20261014/`：本机 AMD 工具命令、日志及许可阻塞证据。

ZIP 内额外提供 `DELIVERY_MANIFEST.json`，逐文件记录大小与 SHA-256；ZIP 外侧提供压缩包本身的 SHA-256。哈希用于检查复制完整性，不是数字签名或可信来源认证。

## 有意不包含

原始训练数据 ZIP、原 `kv260_v1/v1_payload.zip`、历史板卡操作日志/凭据、Python 虚拟环境、缓存、Windows 主机 `.exe/.dll`、AMD 软件以及未生成的 `.bit/.hwh`。

1. 在独立副本中，使用已附 bundle 与样本运行 `tools/validate_split.py`、`python -m ps.replay` 不需要原 payload。
2. 复现173组三摄全量回归，需要自己保留的原 `kv260_v1/v1_payload.zip`，通过 `tools/validate_payload.py --zip` 指定真实路径。
3. 原48项pytest无跳过的验收使用了相邻 `kv260_v1/v1_payload.zip` 及历史 `kv260_v1/artifacts/results/single_100/first_input.npy`。独立副本中缺少这些文件时，相关集成测试会明确skip；这种结果不能称为复现48项全覆盖。
4. 重跑C++主机验证，需要按 `hls/README.md` 在目标主机重新编译，不把Windows DLL当作ARM板端依赖。
5. AMD证据目录中的 `run01_hls_config.cfg` / `run02_hls_config.cfg` 只作归档，路径对应原临时运行目录。新的构建副本必须重新运行 `hls/make_config.py` 生成配置。

## 验收边界

### 历史（截至 2026-10-17）

已完成：173组三摄CPU等价回归、两套依赖环境各48项测试、主机C++参考核及真实权重对照、Notebook默认CPU分支执行。

当日未完成：板端PS实跑、成功的HLS C simulation/C synthesis、RTL cosim、Vivado实现、bit/hwh、PL数值与性能、实时相机/车辆控制。本机真实Vivado返回exit42，HLS csim/compile均返回exit1，阻塞于part/有效许可证初始化。软件结果不能代替硬件验收。

**历史 2026-10-17 source-candidate update:** 当时 `hls/ffn_accel.cpp` 是四路输出候选，host C++/真实模型测试通过，但当日没有新的 AMD HLS、RTL、Vivado、IP 或板测。归档的 retry04 bit/hwh 和板端结果只对应更旧串行核。相关证据在 `artifacts/ffn_opt_20261017/`，见 `docs/04_验证记录.md` 第 18 节。不得把 retry04 硬件工件当作该候选验证。

### 2026-10-18 r03 板测（最新板测）

- **已验证**：r03 源码身份（C++ `829c9e...b9b6`、HPP `715f...7c35`）；完整 HLS C 仿真 19 项；缩小 RTL cosim 12 事务/30 有效 token；r03b HLS OOC WNS +2.609004 ns/WHS +0.032047 ns；PS+PL 系统五阶段 exit 0、WNS +2.433 ns/WHS +0.010 ns、63005 nets 全路由且 error 0；KV260 上 36 层/117 帧数值通过、整数轮速全一致。
- **100 MHz 离线验收已验证**：授权后只写 PL0 divisor0=15（raw `0x1010a00→0x1010f00`），实测 99.999 MHz；5 个 PLL 与其余 3 路 PL 原始值不变，CPU 时钟结束只读仍相同。复用原已加载 Overlay（0 次新下载），构造前校验 state/timestamp/bit-hwh SHA。1464 次 kernel start，exit 0/normal close，无 timeout，CTRL=0x4。
- **失败/不足**：100 MHz 下 4 线程 split-PL 中位 372.2187 ms，仍比完整 golden 中位 64.7386 ms 慢约 5.75 倍；1 线程为 411.313 vs 189.438 ms。不能宣称 PL 加速或达到 15 Hz。
- **未执行**：659/660-token 完整 RTL cosim、长时稳定性/功耗/温度、实时相机/串口/电机/实车任务。
- **证据**：`artifacts/ffn_opt_20261018/board_run02_100mhz/`（`validation_run02_100mhz/report.json`、`events.jsonl`、`board_run02_100mhz_summary.json`）与 `docs/04_验证记录.md` 第 20 节；第 19 节保留 150 MHz 首轮历史。

### 第二轮（2026-10-18，CPU/候选，未上板）

- **已验证（CPU）**：`tools/ort_thread_study.py` 六条件正反序各 117 帧、warmup 3、独立进程；split-4 空转 ON P50 `404.512/411.005 ms`，OFF `75.181/77.320 ms`，线程数同为 31；mixed 4+1 ON `237.702/232.873 ms`，OFF `87.649/95.033 ms`（16 线程）；golden 4 `64.793/64.624 ms`。输出过 `1e-3`/整数门槛，但 split-1 与 golden-4 非逐位，不能写所有策略彼此逐位一致。
- **已验证（生产接口）**：`Runtime(..., *, spinning: bool | None = None)`；`None` 不写配置，`False` 写两项 `allow_spinning=0`，`True` 写 1；`Runtime.run`、模型/manifest/精度/预处理/调度未变。Runtime SHA `49ace8fbf61a29e2e54efc174b45477cd08fc5fe469d13547df17326b9745576`。KV260 纯 CPU 复验 golden `None` vs split `False` 四输出 0 差、整数 0 分歧，P50 `65.2979/79.6322 ms`；不称超过 golden。
- **已验证（测试）**：`final_software_tests.xml` 97 项 = 92 通过、5 跳过；5 项是当前树对旧冻结 adapter/CLI 的显式 skip，不是动态哈希自动跳过。旧冻结 ZIP 在旧源码根 27 项通过；原 48 项独立通过，新 Runtime policy 21 项通过。
- **已验证（r04 v2 候选，主机/HLS/OOC）**：独立路径 `hls/candidates/interleaved_r04/v2/`；v1 4 context/slack -3.70 ns 失败；v2 8 context/fadd6/fmul3/真实 RAW distance 8，主机 30 项、两套 19 组合成/173 帧逐位零差、HLS C 30 项、小范围 RTL 10 事务/30 token 通过；csynth 静态估计 36.2066 ms/660 token，OOC WNS +2.435662 ns、post-route CP 7.564 ns。**OOC 不是系统 bit/hwh，未上板，完整 659/660-token RTL 未执行。**
- **未进本 ZIP**：正式 `hls/ffn_accel.cpp/.hpp` 仍是 r03；新候选与 OOC 工件在 `hls/candidates/` 和 `artifacts/opt_round2_20261018/`，是否打包以 manifest 为准。继续上板需单独授权。

本 ZIP 描述的"未完成"清单已于当日之后大幅推进，请以 `docs/04_验证记录.md` 第 15–17 节与 `artifacts/` 新增目录为准：

1. HLS/Vivado 阻塞解除（retry04）：C simulation/C synthesis/1-token RTL cosim 冒烟/`--impl` 全部通过；KV260 系统级综合/实现/时序通过，产出真实 `artifacts/vivado_20261015/retry04/overlay/ffn_overlay_wrapper.bit/.hwh/.xsa`。
2. 板端 bring-up 完成：真实 KV260 上 overlay 加载、真权重四层 FFN PL vs ORT（≤2.68e-7）、`Database` 真值录像 117 帧混合推理（整数轮速与 CPU 全一致）、三模式计时；证据在 `artifacts/board_20261015/`。
3. 计时诚实结论：串行参考核 PL 路径整帧中位 ~2497 ms，慢于纯 CPU（~411 ms）与 golden（~71 ms），无加速收益。
4. 仍未完成：完整 659/660-token RTL cosim、实时相机/实车控制、多录像全量板上回归、功耗/温度/长时稳定性。

所有默认运行入口只处理文件；原固件/CMA/boot 均未被修改，车辆动作从未发生。PYNQ 超时不等于硬件停止，未确认 AXI master 停止前不得释放缓冲区或退出其持有进程。本轮板上加载通过 notebook 显式开关之外的测试脚本完成，仍遵守同一安全合同。
