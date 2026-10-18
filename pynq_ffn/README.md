# PYNQ FFN 迁移包

**最新 2026-10-18 r04 v2 已上板（数值通过，加速仍失败）：** r04 v2 独立 PS+PL 系统五阶段 exit 0，WNS +1.747 ns、WHS +0.010 ns、97862 nets 全路由 0 error、1692 项 PS CONFIG 与 r03 无差；bit/HWH/XSA `8f438a…/7254ea…/895134…` 同源。KV260 实际下载一次（`Bitstream.download(Overlay(download=False), parser)`，不写 clock），独立只读 `program_verified01` 通过且无第二次下载；真实 PL0 **99.999 MHz**。1-token 冒烟 + 36 层 case + 117 帧三模式独立进程 exit 0，四输出最大 `speed_raw` 0.0001220703125、整数 0、累计切点 1.78814e-7，517 次 START 正常完成。4 线程 `run_total` P50：golden 65.21 / split-ORT 74.87 / split-PL **170.03 ms**，比 golden 慢 **2.607 倍**、比 split-ORT 慢 2.271 倍，**PL 加速目标失败**；`offline_e2e` P50 196.49 ms，持续带校验/NPZ/fsync 3.860 Hz 不是真实控制新鲜率。正式 `hls/ffn_accel.cpp/.hpp` 仍是 r03，新核当前在板上 idle，旧 bit 保留、未回退；完整 659/660-token RTL 未执行。详见 `docs/04_验证记录.md` 第 22 节与 `artifacts/r04_v2_board_20261018/`。

**2026-10-18 r03 板测：** r03 候选（16 个输出通道分组并行，FC1/FC2 实际 II=4，GELU II=1）已完成 HLS、PS+PL 系统实现与 KV260 板测。获用户单独授权后仅改 PL0 DIV10→15，板上实测从 149.9985 MHz 回到 **99.999 MHz**，复用原已加载 Overlay 重跑：36 层/117 帧数值与整数轮速全一致，**100 MHz 离线验收已验证**；但 split-PL 中位 **372.22 ms** 仍比完整 golden **64.74 ms** 慢约 **5.75 倍**，**PL 加速目标失败**，不能称达到 15 Hz。长时稳定、完整 659/660-token RTL、实时相机/串口/电机/实车仍未执行。2026-10-17 四路候选和 2026-10-15 retry04 均为历史。详见 `docs/04_验证记录.md` 第 20 节（第 19 节保留 150 MHz 首轮历史）与 `artifacts/ffn_opt_20261018/board_run02_100mhz/`。

**上一轮 2026-10-18 第二轮（CPU 诊断已实机验证；当时 PL 候选未上板）：** 生产 `ps/runtime.py` 增加可选 `spinning`，`None` 保持原默认、`False` 关闭 ORT 两类忙等；最终 SHA `49ace8…5576`。KV260 纯 CPU 117 帧复验 split-4 `False` 四输出零差，P50 79.63 ms，仍慢于 golden 65.30 ms；**不称 15 Hz 或 PL 收益**。独立控制实验确认多 session 线程池忙等是 split-ORT 主要可消除开销（P50 约 404.5/411.0→75.2/77.3 ms，线程数同为 31）。r04 独立候选在 `hls/candidates/interleaved_r04/v2`：主机/HLS csim/csynth/小范围 RTL/OOC 通过，csynth 静态估计 36.21 ms/660 token，OOC WNS +2.435662 ns、WHS +0.034 ns（外部 I/O delay 不完整，非全系统时序签核）；该条保留系统/板测前的历史状态，最新上板结果见本节顶部与第 22 节。详见 `docs/04_验证记录.md` 第 21 节。

面向现有资料中的 **KV260/K26**：原模型拆成五段 CPU ONNX 子图，四个 `128→256→128`、erf-GELU FFN 通过统一后端接口替换。Attention、LayerNorm、残差、预处理和输出头留在 PS。原工程保持不变。

2026-10-14 的离线回归仍有效：已用原载荷的 **173 组三摄数据**完成三种模式逐帧数值对照，另有 **48 项软件测试**在两套依赖环境通过。它们是 CPU/host 证据，不是板端性能；详情和硬件验收边界见验证记录。

## 阅读顺序

1. [文件分析与迁移决策](docs/01_文件分析与迁移决策.md)：目录、模型、数据、历史板端状态、ROS风险与范围。
2. [FFN 接口与流水线设计](docs/02_FFN接口与流水线设计.md)：四层边界、AXI与寄存器、缓存/超时、流水线优化。
3. [AMD 与 Ross 从零操作指南](docs/03_AMD与Ross从零操作指南.md)：安装与许可、PS部署、HLS、Vivado、PYNQ、Ross。
4. [验证记录](docs/04_验证记录.md)：真实命令、测试、误差、未完成硬件验收。
5. [逐步 Notebook](notebooks/01_ps_to_pl.ipynb)：默认只跑 CPU，PL 下载需要显式启用。
6. [独立交付包说明](DELIVERY.md)：ZIP内容、哈希清单，以及缺少原载荷时的复现边界。

## 最快跑通（不用板卡）

在本目录打开 PowerShell，使用 Python 3.10～3.12。下面以 Windows Python 3.12 为例：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest tests -q

.\.venv\Scripts\python.exe tools\validate_split.py `
  --bundle artifacts\bundle_20261014 `
  --recording artifacts\sample_recording_1779374497 `
  --random-cases 3 `
  --output artifacts\my_validation.json

.\.venv\Scripts\python.exe -m ps.replay `
  --bundle artifacts\bundle_20261014 `
  --recording artifacts\sample_recording_1779374497 `
  --backend split-ort --threads 4 `
  --output-dir artifacts\my_replay
```

这些输出路径必须尚不存在；重跑换新名字。样本只有一组三摄，用于快速连通和等价检验，不是驾驶准确率测试集。

本轮48项测试是在相邻 `kv260_v1/v1_payload.zip` 和历史 `first_input.npy` 都存在时通过的，**没有skip**。如果只复制本目录或使用独立交付ZIP，依赖原载荷的集成测试会明确skip，不能称为复现48项全覆盖；已附bundle与样本仍可直接用于 `validate_split.py` 和 `ps.replay`。

本次已经生成 bundle，也可以从原 payload 重建：

```powershell
.\.venv\Scripts\python.exe tools\prepare_model.py `
  --payload ..\kv260_v1\v1_payload.zip `
  --output-dir artifacts\my_bundle
```

准备脚本只读取指定成员 `model/model_current.onnx`，核验固定 SHA-256：

```text
ad58d1fc9168da94b9ce076ba8cc9754c371ead87974da8bc9cc601ad1650d5b
```

新训练模型需要重新审查切图，不提供跳过此检查的开关。bundle 所有文件均有哈希清单；运行时拒绝被修改或混搭的成员。

## 三种 CPU 模式与 PL 注入

| 模式 | 实际执行 |
|---|---|
| `golden` | 原始完整模型，CPU ORT |
| `split-ort` | 五段 PS + 四段 FFN，全部 CPU ORT；用于检查切图 |
| `split-numpy` | 五段 PS 用 CPU ORT，FFN 用 exact-erf NumPy；用于检查后端合同 |

```python
from ps.runtime import Runtime

runner = Runtime("artifacts/bundle_20261014", mode="split-ort", threads=4)
outputs = runner.run(images)  # float32 [1,3,3,66,200]
print(outputs["speed_raw"])  # [1,2]，不要再乘300/500
print(runner.last_timings_ms)
```

四输出均保留：`speed_norm`、`speed_raw`、`region_logits`、`region_probs`。区域输出一致不等于该头具有经验证的分类准确率。需要关闭 ORT 空转时可传 `spinning=False`；省略或 `None` 与旧默认一致，`True` 显式开启。CPU-only 复验见 `docs/04_验证记录.md` 第 21 节。

PL 对接是 `Runtime(..., mode="split-numpy", backend=pynq_backend)`；模式名表示可替换路径，**只有显式注入 `PynqFFN` 才访问 PL**。该后端接收已经由调用者明确加载的 IP，不自行下载 Overlay。详细示例与板前提见教程/Notebook；请勿把演示路径或实例名当成已探测结果。

## 代码地图

```text
ps/
  partition.py       固定模型边界/拓扑验证、五段PS与四段FFN导出
  runtime.py         CPU整图/分图、live tensor调度、后端注入
  backends.py        NumPy exact-erf FFN
  preprocess.py      原NUC一致的三摄预处理和文件读取
  replay.py          离线录制推理，仅写结果文件
  pynq_backend.py    AXI-Lite、64位地址、缓存同步、超时隔离

tools/
  prepare_model.py   生成新bundle
  validate_split.py  最终四输出、整数轮速、四FFN切点严格对照
  validate_cpp.py    主机C++参考核与真实权重对照，不是PL
  validate_payload.py 直接读原ZIP完成173组三摄全量等价验证
  board_preflight.py 只读本机环境，不导入PYNQ/不装载硬件
  ort_thread_study.py 六条件 ORT 线程/空转控制实验；不直接改生产 Runtime
  runtime_spin_validate.py 生产 Runtime 可选 spinning 的纯 CPU 复验

hls/
  ffn_accel.cpp/.hpp r03：16通道分组并行、FC1/FC2 II=4、GELU II=1；不是实时闭环
  testbench.cpp     边界/尾token/权重重载/独立double参考
  build_hls.tcl     传统HLS入口，须给真实FPGA_PART
  make_config.py    现代2026.1 cfg生成，已核实64位地址键；脚本/配置已验，许可阻塞为历史，当前构建见验证记录第19–21节
  host_bridge.cpp  主机验证专用；不要加入HLS综合
  candidates/interleaved_r04/ 独立 r04 token 交错候选；v1 时序失败，v2 已通过主机/HLS/OOC、系统集成与 KV260 上板数值（见第 22 节），但正式源仍是本目录 r03，完整 659/660-token RTL 未执行
```

`hls/build/*.exe/*.dll` 是 Windows 主机测试产物，不是 FPGA bitstream，也不能复制到 ARM 当板端程序执行。普通运行时不加载这些文件。

## 当前关键限制（按日期；旧条目标为历史）

- **2026-10-18 r04 v2 已上板（最新；数值通过 / 加速失败）**：独立 PS+PL 系统五阶段 exit 0，WNS +1.747 ns、WHS +0.010 ns、97862 nets 全路由 0 error、1692 项 PS CONFIG 与 r03 无差；bit/HWH/XSA `8f438a…/7254ea…/895134…` 同源。KV260 一次实际下载（绕开 `Overlay.download()` 时钟 setter，未写 clock），独立只读 `program_verified01` 通过且无第二次下载；真实 PL0 99.999 MHz，5 PLL/四路 PL/ACPU raw 不变。1-token + 36 层 + 117 帧三模式 exit 0，`speed_raw` 最大 0.0001220703125、整数 0、累计切点 1.78814e-7，517 次 START 正常完成。4 线程 `run_total` P50 为 golden 65.21 / split-ORT 74.87 / split-PL **170.03 ms**，PS+PL 慢 golden 2.607 倍，**加速目标失败**；持续 3.860 Hz 含校验/NPZ/fsync，不是真实控制新鲜率。正式源仍 r03，新核当前板上 idle，旧 bit 保留、未回退；完整 659/660-token RTL 未执行。
- **2026-10-18 第二轮（历史；当时 CPU 已实机验证，PL 候选未上板）**：生产 Runtime 新增 `spinning` 可选参数；`None` 不写配置、保持旧默认，`False` 关闭两类 ORT 空转，`True` 显式开启，只接受 `None`/布尔。KV260 纯 CPU 117 帧复验 golden `None` vs split `False` 四输出零差、整数零分歧，P50 65.2979/79.6322 ms；未超过 golden，不称 15 Hz 或 PL 收益。r04 v2 候选主机/HLS/csynth/小范围 RTL/OOC 通过，静态估计 36.21 ms/660 token、OOC WNS +2.435662 ns/WHS +0.034 ns（routed 资源 LUT 51146/FF 46657/DSP 333/BRAM 18K等效131=65.5个36K tiles；外部 I/O delay 不完整，非系统时序签核）。该条保留系统/板测前状态；最新结果见上一条与第 22 节。
- **2026-10-18 100 MHz 离线验收（r03 板测，已验证数值 / 加速失败）**：仅在授权下写 PL0 divisor0=15，raw `0x1010a00→0x1010f00`，`149.9985→99.999 MHz`；5 个 PLL 与其余 3 路 PL 原始值不变，CPU 时钟结束只读仍相同。复用原已加载 Overlay（0 次新下载），构造前校验 state/timestamp/bit-hwh SHA。36 层最大误差 7.45058e-8；117 帧四输出最大 `speed_raw` 0.0001220703125、累积层最大 1.78814e-7、整数轮速全一致；1464 次 kernel start，exit 0/normal close，无 timeout。100 MHz 正式计时 n=117、warmup 3：4 线程 golden 中位 64.7386 ms、split-ort 318.3409 ms、split-PL 372.2187 ms，后者慢约 5.75 倍；1 线程分别为 189.438/193.083/411.313 ms。这是 117 帧离线录像测量，不是长稳、实时或完整 RTL；不能称加速或 15 Hz。
- **2026-10-18 时钟修正前的 150 MHz 首轮（历史）**：当时板上实读 `Clocks.fclk0_mhz=149.9985`，与 10 ns/100 MHz 签核不一致；该轮 split-PL 中位 301.1576 ms、慢约 4.59 倍，只作历史，不用于正式验收。详见 `docs/04_验证记录.md` 第 19 节。
- **历史 2026-10-15 旧核板端 bring-up 已完成**——retry04 overlay 在真实 KV260（PYNQ 3.0.1）加载成功，四层真权重数值及 117 帧录像回归通过。**仅证明当时 retry04 串行核**，不包含 2026-10-17 四路源码候选或 2026-10-18 r03；其 bit/hwh 与当前候选不匹配，不可用于候选验收。历史数据和边界见 `docs/04_验证记录.md` 第 17 节及 `artifacts/board_20261015/`。
- **历史 2026-10-15 旧 retry04 性能基线**：板测整帧 split-PL 中位约 2497 ms；split-ort 中位约 411 ms。计时脚本中 PL/hybrid 使用 1 线程、split-ort 使用 4 线程，不能作为同条件加速比。golden CPU 约 71.19 ms 是唯一一次调用（脚本将其作为 warmup），没有 warm 后的独立测量。旧核 HLS 静态最大延迟估计为 173,252,285 cycles，即目标 100 MHz 下约 1.733 s；板上 660-token 单层墙钟约 1156 ms。**旧板运行时频率未独立核实**（只有设计/HWH 标称 100 MHz），所以不能写“约 115.6M cycles”当实测周期，也不能把 1156 ms 乘 100 MHz 反推真实频率；静态 cycle 与墙钟不能强换算。以上计时都对应 retry04 旧串行核。
- **历史 2026-10-17 首轮四路候选（已被 r03 取代）**：FC1/FC2 各 4 个输出通道并行，保留通道内 FP32 累加顺序与 exact-erf GELU。主机 testbench、双核逐位对比及 173 组三摄回归通过；当日 HLS/RTL/Vivado/板测因主机缺 AMD 工具未执行。该状态不代表 r03；候选源码与旧 retry04 bit/hwh 不匹配，禁止拿旧 overlay 冒充候选验收。
- **安全与系统边界**：没有实时 USB 三摄同步、串口控制或实车测试；STM32 源码有已记录安全问题，不能原样直接启动。未修改固件/CMA/boot。2026-10-18 仅在 KV260 复用已加载候选 Overlay 做离线 PL 数值/性能验收；未接相机/串口/电机，未做车控。长时稳定与实时链路仍未执行。

项目资料中的实际目标仍需确认：最终允许误差/帧率门槛、是否继续保持 KV260 镜像、后续是否将参考核替换为优化核。基础软件可以在这些确认之前离线运行；硬件验收不能省略它们。

## 许可证

本项目原创代码采用 [MIT License](LICENSE)，版权署名为 `2026 BeFortune`。第三方依赖、模型、数据及 AMD/Xilinx 生成内容的授权仍以各自来源的许可为准；本许可证不替代第三方授权。
