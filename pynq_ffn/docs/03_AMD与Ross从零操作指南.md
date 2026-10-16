# 从零使用 AMD Vivado、Vitis HLS、PYNQ 和 Ross

核实日期：2026-10-14。本文针对现有资料中的 **Kria KV260 / K26 / Zynq UltraScale+ MPSoC**，不是 PYNQ-Z2。请先确认实际板卡仍相同。下面是可执行的操作说明，不表示本轮已安装 AMD 软件、登录 AMD、修改板卡或完成综合。

## 0. 先建立正确概念

- **PS**：K26 上的 ARM Cortex-A53 等处理系统，跑 Linux、Python、ONNX Runtime、PYNQ 和应用。
- **PL**：K26 的 FPGA 可编程逻辑，运行综合后的 FFN 电路。
- **PYNQ**：Python 与 FPGA 交互的软件框架，不是一种新的 FPGA 芯片架构，也不等于把 Python 自动变成 PL。
- **Vitis HLS**：把受支持的 C/C++ 算法变成 RTL/IP，处理流水线、并行、存储、接口。
- **Vivado**：把 IP、PS、AXI、时钟、复位组成硬件系统，综合、布局布线、检查时序并生成 bitstream。
- **Ross**：AMD 官方 agent 辅助层，把你的 AI 客户端与工具、技能、官方知识库连接起来。它不是替代上述工具的新综合器，也不负责替你证明硬件正确。

全流程：

```text
固定 ONNX 与输入合同
  → CPU 整图基线
  → PS 分图 + 软件 FFN 对照
  → HLS C/C++ 参考核
  → C simulation / synthesis / C-RTL co-simulation
  → Vivado Block Design / implementation
  → bit + hwh
  → PYNQ 单层 FFN 验证
  → PS/PL 混合模型验证
  → 性能优化
  → 最后再做实时相机和安全车控
```

不要倒序：生成了 bitstream 不代表 FFN 数值正确；Python 能 import pynq 不代表 PL 已加载或有加速。

## 1. 本次实际交付入口

在 `pynq_ffn/` 内：

- `ps/`：PS 运行时、预处理、FFN 软件替身与 PYNQ 后端。
- `tools/prepare_model.py`：从审核过的模型生成分图 bundle。
- `tools/validate_split.py`：整图/分图数值对照。
- `tools/board_preflight.py`：只读环境报告。
- `hls/`：float32 FFN 参考核和构建入口。
- `notebooks/01_ps_to_pl.ipynb`：逐步演示；默认不加载 PL。
- `artifacts/`：本轮模型 bundle 和验证记录，不是已经生成的 bitstream。
- `docs/02_FFN接口与流水线设计.md`：寄存器、矩阵布局和验收分级。

第一次只执行 CPU 模式即可，不需要先装 PYNQ 到 Windows，也不需要 4090。GPU 可以继续用于训练，但它不会自动加快 Vivado 的全部任务。

## 2. 选择软件版本：不要盲目点“最新补丁”

### 2.1 2026-10-14 官方信息

| 项目 | 官方已核实的信息 |
|---|---|
| Vivado/Vitis 当前页面 | `2026.1 Latest`；同时提供 Update 1，即 `2026.1.1` |
| 基版发布日期 | 2026-06-23 |
| Update 1 日期 | 2026-09-10 |
| Ross 包 | 2026.9.1；下载文件更新于 2026-09-29，仓库 changelog 为 09-30 |
| Ross 公开测试基线 | Vivado/Vitis 2026.1 |
| 新许可 | BASIC / CORE / PRO / ENTERPRISE / GOLD |
| BASIC | 免费、需年度续期；设备表包含 Kria SOMs |
| 重要限制 | 下载页明确 BASIC 不支持 2026.1.1 这样的 minor/update release |

**推荐从 2026.1 基版评估这个新 FFN 工程**，因为它是 Ross 公开测试基线且可用 BASIC 覆盖 Kria 的声明；先完成精确 part 和功能许可核对。不是建议给现有板卡盲目升级系统、XRT 或固件。

若原硬件/镜像工程已有冻结版本，优先保留原版本做已知 overlay 的复现，再独立评估新版。PYNQ 3.0.1 与 2026.1 导出的 HWH/bit 的整条组合 **本轮没有实测**，不能因 Ross 支持2026.1就声称旧 PYNQ 已认证兼容。

### 2.2 Ross 文档版本措辞并不完全一致

- AMD 产品 FAQ：Vivado “All versions”，HLS 2025.2+。
- 仓库 FAQ：Vivado 2020.2+。
- HLS quickstart：写过 2025.1+，各示例另有最低版本。
- getting-started：明确测试版本 2026.1。

因此本文以 **2026.1 作为资料共同测试基线**，不把“架构兼容”说成“每个旧版本都测过”。

### 2.3 下载入口

- [AMD 2026.1 工具下载与 Unified Installer](https://www.amd.com/en/support/downloads/adaptive-socs-and-fpgas/development-tools/2026-1.html)
- [Vivado 当前许可与器件范围](https://www.amd.com/en/products/software/adaptive-socs-and-fpgas/vivado/vivado-licensing-options.html)
- [Ross 官方下载](https://www.amd.com/zh-cn/support/downloads/ross-agentic-ai.html)

AMD 工具/Ross 下载可能要求账户、许可或出口合规确认。由你使用自己的账户完成，不把密码写入工程或对话。本轮没有代你接受条款或创建账户。

## 3. 安装开发主机软件

### 3.1 工作目录

建议把用于 **构建** 的工程副本放在本地短路径，例如：

```text
D:\FPGA-Work\pynq_ffn
D:\FPGA-Work\vivado_ffn
D:\Tools\AMD-Ross
```

这些只是建议目录，不是声称已存在。当前原工程位于 Windows UNC 网络共享；某些编译器/AMD 工具不接受 UNC、空格或过长路径。本次 MinGW 主机测试就遇到了 UNC 链接输出限制，因此不要把网络路径问题误诊成 HLS 算法错误。保留原始目录不变，用可追踪副本构建，并记录源码哈希。

### 3.2 Unified Installer

1. 安装器选择所需 Vivado 和 Vitis/HLS 组件；不同版本界面用语会变化，以组件说明为准。
2. 选择 **Zynq UltraScale+ MPSoC / Kria** 器件支持，不是因为名字里有 PYNQ 就选择 Zynq-7000。
3. 若要走 JTAG，另装对应 cable drivers；本方案 PYNQ 加载不以 JTAG 为前提。
4. 按所选版安装 BASIC 或其他有效许可；用 License Manager 核验。
5. 核对该版安装指南支持的 Windows/Linux 版本、磁盘和内存要求；本机 Windows 版本兼容性本轮未认证。

从该版本的 **Vivado command prompt / Vitis 工具终端** 运行：

```text
vivado -version
v++ --version
vitis-run --version
```

若某命令不在 PATH，先从实际安装入口启动对应工具终端，不要反复全局乱改 PATH。Linux 可 source 实际安装目录中的 `settings64.sh`；不要把 Linux `source` 复制到 PowerShell。

保存以上版本输出、安装组件和许可状态。**这台机器实际已有安装，不应重复安装：**

```text
D:\Learning\AMDDesignTools\2026.1\Vivado\bin\vivado.bat
D:\Learning\AMDDesignTools\2026.1\Vitis\bin\vitis-run.bat
D:\Learning\AMDDesignTools\2026.1\Vitis\bin\v++.bat
```

本轮最初只查PATH未命中，后来按历史记录找到以上文件并现场确认。Vivado批处理真实启动返回exit42（未找到有效许可证），未执行其后的Tcl器件查询；这是实际阻塞，不应通过重装整个软件或重启服务来猜测解决。请在2026.1 License Manager核验有效许可；BASIC免费不等于无需激活/年度续期。

已安装boardstore的 `kv260_som/2.0/board.xml` 与 `k26c/2.0/board.xml` 给出的参考part为 **`xck26-sfvc784-2LV-c`**。这确认了本机KV260/K26C preset的器件选择，但实物若不是K26C仍需重新确认；board文件存在也不等于对应器件支持组件和许可已经可用。

## 4. 先在主机跑通 PS 软件

以下命令均在 **pynq_ffn 目录** 下执行。PowerShell 示例：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-prepare.txt
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest tests -q
```

准备模型，输出必须是新目录：

```powershell
.\.venv\Scripts\python.exe tools\prepare_model.py `
  --payload ..\kv260_v1\v1_payload.zip `
  --output-dir artifacts\my_bundle
```

若只复制了 `pynq_ffn` 到新目录，应把 `--payload` 改为原包的实际路径。准备脚本只读取指定模型成员，核对固定 SHA，不会整包解压数据和 wheels。

验证：

```powershell
.\.venv\Scripts\python.exe tools\validate_split.py `
  --bundle artifacts\my_bundle `
  --output artifacts\my_validation.json
```

有历史输入张量时增加 `--first-input` 指向真实 `.npy`。具体测试覆盖/误差见报告与脚本 `--help`。

回放单个录制片段：

```powershell
.\.venv\Scripts\python.exe -m ps.replay `
  --recording "<含auto_data.csv的片段目录>" `
  --bundle artifacts\my_bundle `
  --backend split-ort `
  --output-dir artifacts\my_replay `
  --threads 4 --limit 10
```

`golden` 为整图 CPU；`split-ort` 是分图 CPU；`split-numpy` 是软件 FFN 替身。**没有一种模式名代表已经调用 PL。** 输出是文件，不会打开摄像头或串口。

## 5. 在 KV260 的 PS 部署

### 5.1 先复用已有系统，不重新刷卡

现存报告已经记录 Ubuntu 22.04.4 / Python3.10 / PYNQ3.0.1。先在板上运行：

```bash
python3 tools/board_preflight.py --output board_preflight_new.json
```

输出记录板型、Python、包位置、CMA 和工具路径，不导入 PYNQ或装载 PL。它不是修复器。`CmaTotal=0` 时停在 CPU 软件阶段；不能靠多 pip install 解决启动内存问题。

若是全新板，官方 [Kria-PYNQ](https://github.com/Xilinx/Kria-PYNQ) 的 main 安装脚本当前只接受 Ubuntu **22.04**，约束 PYNQ **3.0.1**；不是任意新版 Ubuntu 都支持。应按照官方镜像、boot firmware 配对要求准备 SD 卡。烧卡、QSPI、boot 参数改动和重启都属于需单独确认的维护操作；本轮没有执行。

全新、已确认可重配板卡上的官方安装形式是：

```bash
git clone https://github.com/Xilinx/Kria-PYNQ.git
cd Kria-PYNQ
sudo bash install.sh -b KV260
```

**不要在现有能用的 PYNQ 环境上无理由重跑安装器。** 本地使用板卡安装脚本前还要核对当前仓库 revision，记录 commit。官方默认 Jupyter 登录凭据应及时更改，服务只放可信网络。

### 5.2 应用环境与 PYNQ 环境分开考虑

先为纯 CPU PS 应用建独立 venv，不更新系统 Python 或官方 PYNQ venv：

```bash
cd /home/ubuntu/pynq_ffn
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-runtime.txt
.venv/bin/python -m ps.replay --help
```

准备好的 bundle 可从开发主机复制，不需要板上安装 PyTorch 或重新导出模型。复制后 runtime 会核对文件 hash。应用源码、bundle、必要录制片段一起传递；**不复制 x86 wheels、build、devel**。

CPU 模式只需 NumPy、OpenCV、ORT。PL 混合模式必须在**同一个 Python 进程**里同时可用 PYNQ 与 ORT；不能在一个 venv 运行 ORT、另一个 venv 运行 PYNQ，然后以为普通 NumPy 指针可直接共享。

建议在**新的集成环境**中评估官方 PYNQ 环境的可见性，例如用已核验的 PYNQ Python 建带 system-site-packages 的应用 venv，再检查 `sys.executable`、`pynq/numpy/onnxruntime` 的真实位置和兼容性。不能保证所有 venv 套嵌方式都对该镜像有效，必须实测；也不能直接把最新 NumPy 装入官方 PYNQ venv覆盖它。历史原应用环境是隔离的，这并不自动意味着能 import PYNQ。

启动脚本/`/etc/profile.d/pynq_venv.sh` 可能带设备树操作；读取说明并明确授权后再 source，不拿它做只读检查。历史 `xclbinutil` 有路径问题，须确认选中的是经过验证的 PYNQ 工具，不执行盲目系统升级。

## 6. 使用 Vitis HLS 生成 FFN IP

### 6.1 建组件

在 Vitis Unified IDE 的 HLS component 创建流程中：

1. 创建独立 component，顶层函数 `ffn_accel`。
2. 加 design source `hls/ffn_accel.cpp` 和头文件；加 testbench `hls/testbench.cpp`。
3. 选择 **Vivado IP flow**，不是无缘由选择 XRT/Vitis kernel flow。
4. 选择板上实际 FPGA part；从 KV260 官方 board preset/器件属性查证完整型号和速度等级，不复制其他板示例。
5. 时钟目标先用 10 ns（100 MHz）作为保守设计目标，不把目标当 achieved frequency。
6. 指定 64 位 m_axi address；核验所有 pointer 64 位。设置 `FFN_USE_HLS_MATH` 使 C simulation 走 HLS 数学库，避免主机 `std::erf` 分支掩盖 HLS API 兼容问题。

本机2026.1的 `Vitis/include/hls_math.h` 已核实声明 `hls::erff(float)`；配置schema也已核实 `syn.interface.m_axi_addr64`。这解决了前期资料不足，但头文件里存在声明不等于整个核已经完成HLS编译/综合。真实csim/csynth结果和阻塞见 `04_验证记录.md`；不能因编译问题偷偷换成tanh GELU。

### 6.2 四道验证

1. **C Simulation**：先验证数值、输入边界和 testbench。
2. **C Synthesis**：生成 RTL，读取 latency、II、DSP/LUT/FF/BRAM、时钟估计和 warning。
3. **C/RTL Co-simulation**：验证 RTL 与 C 模型，含 4/660 tokens、多次调用及不同权重；不能只测全零。
4. **Export IP / Package IP**：得到 Vivado IP repository 包；目录/IP标识以实际工具输出为准。

`hls/build_hls.tcl` 是传统 Vitis HLS 脚本入口，需要该版提供 `vitis_hls`：

```powershell
$env:FPGA_PART = "<已核实的完整part>"
$env:CLOCK_NS = "10"
$env:RUN_COSIM = "1"
vitis_hls -f hls/build_hls.tcl
```

`<...>` 不是可照抄的器件值。没有设 FPGA_PART 时脚本会拒绝生成硬件，避免选错器件。

新版官方 HLS skills 使用 `v++`/`vitis-run`，无需 MCP：

```text
vitis-run --mode hls --config "<真实HLS配置.cfg>" --work_dir "<独立输出目录>" --csim
v++ --compile --mode hls --config "<真实HLS配置.cfg>" --work_dir "<独立输出目录>"
vitis-run --mode hls --config "<真实HLS配置.cfg>" --work_dir "<独立输出目录>" --cosim
vitis-run --mode hls --config "<真实HLS配置.cfg>" --work_dir "<独立输出目录>" --impl
```

cfg 必须由该版 IDE 或核实过的配置入口产生；**不能把 `.tcl` 当 `.cfg` 传给这些命令**。work_dir 不要设为项目根。第四条 implementation 也不能代替整个 PS/AXI Block Design 的最终 Vivado implementation。

本包 `hls/make_config.py` 按环境变量 FPGA_PART/CLOCK_NS 生成现代cfg，默认输出为 `hls/build/hls_config.cfg`；本机2026.1 schema已确认64位选项为 `[hls]` 下的 `syn.interface.m_axi_addr64=true`，不再需要猜键。源码及per-file flags按cfg所在目录生成相对路径，因此移动cfg时不能脱离对应源码布局。依然须检查最终RTL地址宽度和寄存器表；配置键正确不是HLS编译、许可、器件支持或时序通过的替代。真实csim与compile均退出码1，在part/许可初始化阶段失败；未运行testbench、未生成RTL。命令、日志及本地证据见 `hls/README.md` 与 `hls/build/amd_20261014/`。

对已确认使用本机KV260/K26C preset的构建副本，可用：

```powershell
$env:FPGA_PART = "xck26-sfvc784-2LV-c"
$env:CLOCK_NS = "10"
python hls/make_config.py
```

命令行使用上面已确认的 `vitis-run.bat`/`v++.bat` 完整路径，工作目录放本地短路径，勿直接在UNC网络共享构建。

### 6.3 首版综合报告看什么

- 顶层是否仍为 `ffn_accel`，控制接口是否 `ap_ctrl_hs`。
- pointer 寄存器实际偏移/位宽与 `docs/02` 是否一致。
- `m_axi_gmem0/gmem1` 是否能访问所需 DDR 地址空间。
- 循环实际 II，而不是源码里写过 PIPELINE 就说成功。
- exact erf 与 FP32 乘加的资源/延迟。
- 权重是否真的映射到片上存储；数组分区是否造成寄存器爆炸。
- C/RTL 是否因 stream/循环界限挂起。

首版参考核的主要计算仍是串行归约，预计不是最优性能。先正确，再按 `02_FFN接口与流水线设计.md` 做分块、并行MAC和DATAFLOW。

## 7. 在 Vivado 组成 PYNQ overlay

### 7.1 创建工程

1. Create Project → RTL Project。
2. 选择 KV260 对应官方 board preset；若未安装匹配 board files，先从官方来源配置。不能随便选一个相近芯片代替。
3. 在 Project Settings 的 IP Repository 设置中加入 HLS 导出的 IP repository。
4. Create Block Design。
5. 添加 **Zynq UltraScale+ MPSoC**，应用与目标板/镜像匹配的 PS preset/automation。
6. 添加导出的 `ffn_accel`、SmartConnect、Processor System Reset。

不要把 Zynq-7000 教程中的 `processing_system7` 原封不动复制到 KV260。也不要覆盖已有 base overlay 的相机/显示配置来做首次 FFN验收；先用最小独立overlay。

### 7.2 连 AXI、时钟、复位

按 `docs/02` 连接：

- PS master → AXI-Lite control。
- FFN 两个 m_axi → PS HP DDR slave 端口。
- 所有关联 AXI 时钟和IP时钟接受同域时钟；跨时钟时明确加入相应转换器。
- Processor System Reset 正确连接极性、同步时钟、互连复位和外设复位。
- 首版不开未配置的中断线路，使用轮询。

在 **Address Editor**：

1. 为 control 分配一个不冲突地址段。
2. 检查 FFN master 的 DDR 可访问映射，尤其是高地址。
3. 记录地址宽度和范围。PS MMIO 基址由 HWH/PYNQ 发现，Python不写死。

不用加 AXI DMA，因为该核已经是 memory-mapped AXI master；DMA属于另一种stream接口方案。

### 7.3 生成 bitstream

1. Validate Design，解决所有关键 warning。
2. 生成 Block Design 输出产物、创建 HDL wrapper。
3. Run Synthesis。
4. Run Implementation。
5. 检查 timing summary（WNS/TNS）、utilization、未约束路径、clock/reset 和 CDC。
6. 只有满足约束且无关键 DRC 后 Generate Bitstream。

失去时序裕量时先修设计/频率，不把“bit文件存在”当验收。

### 7.4 交付 PYNQ 文件

收集**同一次构建**的 `.bit` 和 `.hwh`，重命名同一 stem：

```text
ffn.bit
ffn.hwh
```

HWH 常在：

```text
<project>.gen/sources_1/bd/<bd_name>/hw_handoff/<bd_name>.hwh
```

实际位置随版本/工程而变，按生成目录核对。记录 bit/hwh SHA-256、source commit/hash、工具版本、part、时钟、IP版本、寄存器头文件及综合/实现报告。不能拿旧 HWH 配新 bit。

只做简单 memory-mapped IP 通常由 PYNQ 用户态访问；不要无依据自动加设备树覆盖。若实际镜像/平台另需 DTBO 或 xclbin 元数据，按该版本官方流程核验，不捏造文件。

## 8. 板上加载与单层验收

**前提：** 已确认正确板型、CMA/工具路径、匹配bit/hwh、车辆电机断开或具备独立急停、维护操作已获授权。

`Overlay()` 会真实修改 PL/时钟等状态，不是只读检查。加载一次后复用，不要每帧下载。

```python
from pynq import Overlay
from ps.pynq_backend import PynqFFN
from ps.runtime import Runtime

overlay = Overlay("/absolute/verified/path/ffn.bit")
print(overlay.ip_dict.keys())
# 用实际Block Design导出的实例名，不能猜。
ip_name = "ffn_accel_0"
ip = getattr(overlay, ip_name)
print(ip.register_map)

backend = PynqFFN(ip, overlay.ip_dict[ip_name]["registers"], timeout_s=5.0)
hybrid = Runtime("artifacts/bundle_20261014", mode="split-numpy", threads=4, backend=backend)
```

这里 `split-numpy` 是可注入 backend 的运行路径，注入 `PynqFFN` 后四个 FFN 才调用 PL；非FFN仍在CPU。示例 bundle 路径相对当前 `pynq_ffn`，按实际路径调整。

首先做单层小输入与 ORT FFN golden 对比，再用真实 660 token，再整个模型；具体演示在 notebook。记录误差和各阶段耗时。不能只看输出非零就说正确。

正常完成、确认无在途硬件访问时 `backend.close()`。**若超时或启动状态不明确，不 free、不重复start、不退出进程，先按已核验流程停止/复位AXI master。** 当前软件会隔离缓冲区但不能替你复位硬件。

## 9. Ross：建议先用 VSIX，再考虑直接接现有客户端

### 9.1 官方归属和支持边界

- [Ross 产品页](https://www.amd.com/en/products/software/ross-agentic-ai.html)
- [AMD 官网链接的官方 GitHub 仓库](https://github.com/Xilinx/ross-ai-assistant)

Ross 包含工具 MCP、文档知识库、技能、设计示例。Vivado控制需要MCP；HLS技能走CLI，不需要MCP。Ross不另收许可，但AMD工具许可及第三方客户端/模型费用仍适用。

它不是只支持 Versal：官方矩阵乘示例用 Zynq UltraScale+ ZU9EG。但未发现专门的 PYNQ板级认证矩阵，也没有“自动把这份FFN变成PYNQ overlay”的保证。`vai-ffn-quantization` 是 Vitis AI 6.3+、NPU/相关平台的量化路线，不等于本工程的自定义HLS核。

Ross Windows/Linux下载程序是 **amd64开发主机程序**，不是安装在KV260 ARM上运行的工具。

### 9.2 VS Code/Cursor 安装（较容易上手）

**这台机器并非完全未装Ross：** 已现场确认以下扩展MCP程序存在：

```text
C:\Users\JW\.Xilinx\Vitis\2026.1\.vitis\deployedPlugins\vivado-ai-extension-2026.9.1\extension\bin\vivado-mcp-server-windows-amd64-2026.9.1.exe
```

先检查现有Vitis扩展及客户端连接，不必再次下载同版本。文件存在不等于服务连接成功；本轮没有可用vivado MCP工具，未确认历史18090监听仍有效，也没有停止/另起服务或修改ZCode配置。下列步骤适用于确实尚未安装的客户端。

1. 从 Ross 官方下载页登录并下载 `vivado-ai-extension-2026.9.1.vsix`。
2. VS Code/Cursor：**Extensions → `...` → Install from VSIX...**。
3. 在 Settings 搜索 **Vivado Path**，填写真实 Vivado 启动程序完整路径；PATH正确可自动发现。
4. 进入支持工具调用的 Agent chat，询问：

```text
List the available MCP tools.
```

5. 先只检查环境，再询问：

```text
Start a Vivado session in my dedicated FFN build directory.
Only inspect tool version, project part and available reports initially.
Do not program hardware or change system/boot settings.
```

官方亦提供：

```text
code --install-extension vivado-ai-extension-2026.9.1.vsix
```

官方排障文档要求 VS Code≥1.114 或最新 Cursor；仍以当前扩展要求为准。VSIX已带技能/知识库配置，不要再多种方式重复装同一套。

### 9.3 独立 MCP（供其他支持MCP的客户端）

下载页给出：

```text
vivado-mcp-server-windows-amd64-2026.9.1.exe
vivado-mcp-server-linux-amd64-2026.9.1
```

标准方式由客户端通过 stdio 启动：

```json
{
  "mcpServers": {
    "vivado-mcp": {
      "command": "C:\\Tools\\AMD-Ross\\vivado-mcp-server-windows-amd64-2026.9.1.exe",
      "args": ["--stdio-bridge"],
      "type": "stdio"
    }
  }
}
```

路径只是示例；若Vivado不在PATH，按官方格式加 `env.VIVADO_PATH` 指向**真实启动程序**。JSON里的反斜杠要转义。

**这是官方通用/部分客户端配置示意，不是已核实的 ZCode 配置文件格式。** 不要直接粘贴进未知配置文件。本轮没有更改ZCode/MCP配置，也没有安装Ross。若在ZCode接入，应按其当前扩展配置机制添加该stdio命令；不能为了接入去关闭权限保护。

### 9.4 手动连接已有 Vivado（可选，不是必须）

官方文档给出的操作：

```text
vivado -mode tcl
```

在 Vivado Tcl console：

```tcl
webserver -start -port 8088 -key none
```

然后在客户端中要求：

```text
Connect to my Vivado session at http://localhost:8088
```

对应工具为 `vivado_connect`；自动启动是 `vivado_start`，执行Tcl为 `vivado_execute`。

**这套无key示例只用于本机可信连接。** 不开放公网端口、不随意开external、不因为远程Mac使用就做公网转发。MCP能改工程、IP/约束、启动长构建，因此先明确允许的目录与动作。

### 9.5 安装 HLS skills

官方方法之一：

```text
git clone https://github.com/Xilinx/ross-ai-assistant <本地真实目录>
npx skills add <本地真实目录> --list
npx skills add <本地真实目录> --skill hls-optimize
```

也可 `--all`，但首次按需装更清楚；需要Node.js。VSIX/plugin/npx/手工安装选一种，避免重复触发。克隆完整仓库，保留技能引用的scripts/resources。

**注意：** 官方 `hls-optimize` 可能修改代码并为尝试创建Git提交，不是只读问答。当前原工作区不是Git仓库，先在授权的工程副本建立可回退版本管理，再明确是否允许提交。不能让智能体擅自把原数据/权重/凭据推到远程。

官方矩阵乘入门的正确目录：

[examples/hls-intro-matmul](https://github.com/Xilinx/ross-ai-assistant/tree/main/examples/hls-intro-matmul)

它的示例器件ZU9EG和频率/性能目标只适用于示例，**不能原样套给K26**。仓库部分旧README链接指向不存在的路径，应以实际目录为准。

## 10. 给 Ross 的四段具体任务

每段验收后再进行下一段，不一次要求“全部自动优化并上板”。实际调用哪些skill以客户端已安装清单为准。

### 任务一：只读审查

```text
请先只读检查这个FFN HLS工程。目标板为已确认的KV260/K26，
完整part从我的项目属性读取，不要猜。
数学合同是FP32: (0.5*U*(1+erf(U/sqrt(2)))) @ W2 + b2，
U=X@W1+b1，D=128,H=256,T=1..660。
LayerNorm、残差、attention全部留PS。
报告接口、矩阵布局、潜在综合不支持点和可验证的性能瓶颈。
不要改文件，不要启动硬件，不要更改工具版本/系统设置。
```

### 任务二：跑HLS验证

```text
仅在指定构建副本中运行C simulation、C synthesis、C/RTL co-simulation。
保留FP32和erf，不使用tanh近似/量化，不改AXI ABI。
返回每条真实命令、退出状态、报告路径、实际II/latency/资源/时钟估计。
遇到失败解释原因并停止相关步骤，不把普通g++测试当HLS验证。
允许修复源码的范围请先列出；不创建Git提交、不推送、不下载bit到板卡。
```

### 任务三：一次一种优化

```text
在已通过数值验收的版本上，只尝试输出通道分块和受控并行MAC。
先给出资源/带宽/累加依赖分析，再采用小并行度。
保留660尾块和1/4/659/660边界测试。
每个候选都输出同一输入的误差、综合报告和对原版的差异。
不得仅凭PIPELINE pragma宣称II=1；无实现报告不得声称达到Fmax。
```

### 任务四：Vivado集成与报告

```text
在独立Vivado工程中把ffn_accel接ZynqMP PS。
AXI-Lite用于控制，两个m_axi访问DDR，64位地址端到端核对；
使用正确PS preset、时钟复位和地址映射，不额外加入不需要的DMA。
先Validate Design，再综合/实现，输出WNS/TNS/资源/关键警告。
生成同一构建的bit/hwh和SHA清单，但不要program hardware、
不要连接车辆串口、不要改CMA/boot/固件。
```

### 隐私与数据

模型、设计文件、报告可能经你选的LLM客户端发送到外部服务；先检查服务数据政策和项目授权。不要上传整包数据/权重/历史SSH日志作为“上下文”。本地RAG配云模型仍不是全离线；真正离线需模型与知识库都本地化。官方独立文档搜索服务配置本次未完整核实，故不编造 endpoint/鉴权参数。

## 11. 常见问题速查

| 现象 | 先检查 |
|---|---|
| `import pynq` 成功但overlay失败 | CMA、FPGA Manager、工具路径、bit/hwh配对，而不是先改模型 |
| `Cannot allocate memory` | 启动CMA是否建立；总空闲RAM不能代替连续DMA内存 |
| xclbin临时文件不存在 | 实际执行的xclbinutil、退出码/产物；别只相信包装脚本exit0 |
| 结果错位/数值很大 | 相机顺序、W转置、b广播、YUV/插值、重复轮速缩放 |
| 4 token正确、660失败 | buffer长度、AXI地址宽度、末token/尾块、缓存与时序 |
| FFN更快但整帧变慢 | PS子图碎片化、调用/搬运/flush/invalidate、小cross开销 |
| Vivado打不开网络目录 | 复制到本地短路径；不是改源码逻辑 |
| Ross找不到Vivado | 实际启动程序、PATH、stdio配置、许可；先版本检查 |
| HLS skill不显示 | 按客户端实际技能安装方式检查，避免多套重复加载 |
| 2026.1.1许可报错 | BASIC不支持该minor版本；不是Ross自身收费问题 |

## 12. 官方来源（查证截至2026-10-14）

1. [Ross 产品与FAQ](https://www.amd.com/en/products/software/ross-agentic-ai.html)
2. [Ross 下载](https://www.amd.com/zh-cn/support/downloads/ross-agentic-ai.html)
3. [Ross 官方仓库](https://github.com/Xilinx/ross-ai-assistant)
4. [Vivado MCP 安装说明](https://github.com/Xilinx/ross-ai-assistant/blob/main/docs/getting-started/vivado-mcp.md)
5. [Vivado MCP 工具参考](https://github.com/Xilinx/ross-ai-assistant/blob/main/docs/reference/vivado-mcp-tools.md)
6. [Ross FAQ](https://github.com/Xilinx/ross-ai-assistant/blob/main/docs/faq.md)
7. [HLS run-flow 技能与真实命令](https://github.com/Xilinx/ross-ai-assistant/blob/main/skills/hls-run-flow/SKILL.md)
8. [HLS矩阵乘示例](https://github.com/Xilinx/ross-ai-assistant/blob/main/examples/hls-intro-matmul/README.md)
9. [AMD 2026.1下载](https://www.amd.com/en/support/downloads/adaptive-socs-and-fpgas/development-tools/2026-1.html)
10. [Vivado许可选项](https://www.amd.com/en/products/software/adaptive-socs-and-fpgas/vivado/vivado-licensing-options.html)
11. [Kria-PYNQ官方项目](https://github.com/Xilinx/Kria-PYNQ)
12. [Kria-PYNQ实际安装脚本](https://raw.githubusercontent.com/Xilinx/Kria-PYNQ/main/install.sh)
13. [PYNQ Overlay设计](https://pynq.readthedocs.io/en/latest/overlay_design_methodology/overlay_design.html)
14. [PYNQ allocate与cache sync](https://pynq.readthedocs.io/en/latest/pynq_libraries/allocate.html)
15. [PYNQ 3.0.1 Buffer生命周期实现](https://github.com/Xilinx/PYNQ/blob/v3.0.1/pynq/buffer.py)
