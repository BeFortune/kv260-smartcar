# FFN HLS correctness/reference IP

**最新 2026-10-18 r04 v2 系统与上板（数值通过，加速失败）：** 本目录正式 `ffn_accel.cpp/.hpp` 仍是 r03，不替换。独立候选 `candidates/interleaved_r04/v2`（8 context、fadd6/fmul3、真实 RAW distance 8）已通过主机 30 项、两套 19 组合成/173 帧、HLS C 30 项、小范围 RTL 10 事务/30 token 和同源 OOC；r04 v2 独立 PS+PL 系统五阶段 exit 0（WNS **+1.747 ns**、WHS **+0.010 ns**、97862 nets 全路由 0 error），并在 KV260 实际下载一次（绕开 `Overlay.download()` 时钟 setter，未写 clock；独立只读 `program_verified01` 无第二次下载）。真实 PL0 **99.999 MHz**；1-token + 36 层 + 117 帧三模式 exit 0，四输出最大 `speed_raw` **0.0001220703125**、整数 0、累计切点 **1.78814e-7**，517 次 START 正常完成。但板上 4 线程 `run_total` P50 split-PL **170.03 ms**，比 golden 65.21 ms 慢 **2.607 倍**、比 split-ORT 74.87 ms 慢 2.271 倍，**PL 加速目标失败**。完整 659/660-token RTL 仍未执行。下方构建命令仍针对本目录 r03；候选用各自目录的 `make_config.py`/源码独立构建，别移动或覆盖正式文件。详见 `../docs/04_验证记录.md` 第 22 节。

**2026-10-18 r03 状态（中文）：** 当前源码为 r03：`ffn_accel.cpp` SHA-256 `829c9ec63604e327ad4ea13855b405113ea74fea6a62721d512ded6341b0b9b6`，`ffn_accel.hpp` `715f0ca11cc5e9920569654f7296c8443a0186f988557bd27ec1af3f476c7c35`；16 个输出通道分组并行，FC1/FC2 实际/目标 II=4/4，GELU 单遍 II=1，保留通道内 FP32 累加顺序与 exact-erf。工具机上已完成：完整 C 仿真 19 项 PASS；缩小 RTL cosim 12 事务/30 有效 token PASS（**未覆盖 659/660-token**）；r03b HLS OOC WNS +2.609004 ns、WHS +0.032047 ns。系统实现与板端数值见 `../docs/04_验证记录.md` 第 19 节；100 MHz 离线复测（PL0 DIV15）见第 20 节：117 帧数值通过，但 split-PL 中位 372.22 ms，仍比完整 golden 64.74 ms 慢约 5.75 倍，**加速目标失败**。**下方 2026-10-17 四路候选（II=1）以及“本机无工具/许可阻塞”段落均为历史记录，不再代表当前源码或当前构建状态。**

**历史（2026-10-17 四路候选，已被 2026-10-18 r03 取代）：** 当时的 `ffn_accel.cpp` 把 FC1/FC2 输出通道按 4 路分组，并对每个归约循环请求 `PIPELINE II=1`，同时保留各通道原递增索引的 FP32 累加顺序。主机回归通过；**当时主机没有 Vivado/Vitis/HLS，因此该候选未跑 HLS C 仿真、综合、RTL 协同仿真、打包、Vivado 实现或板测，实际 II、资源、时序与加速未知。** 可移植 staged 源/配置与哈希在 [`artifacts/ffn_opt_20261017/staged_hls/manifest.json`](../artifacts/ffn_opt_20261017/staged_hls/manifest.json)；该目录只是输入暂存，不是生成的 IP 或 overlay。下方 retry04 bit/hwh 对应更旧的串行核，不能用来声称该候选已构建或已验证。

## 2026-10-18 r04 token-interleaved candidate (independent, not main)

独立候选目录 `candidates/interleaved_r04/`，不改本目录正式 r03；两个版本各自保留 `make_config.py`、host/testbench 和 `build/`。

- **v1**：4 个独立 context、16 输出 lane、真实 RAW 依赖 distance 4；主机两套基线各 19 组合成和 173 帧回归等价。HLS 完整 C 仿真 27 项通过，csynth 估计 3,386,100 cycles，但关键反馈路径 slack **-3.70 ns**（目标 10 ns、uncertainty 2.7 ns），**失败**，不进入 RTL/板测。证据 `../artifacts/opt_round2_20261018/hls/source_snapshot_v1/`、`remote_partial01/v1/`。
- **v2**：唯一窄修正为 8 个独立 context、`ctx=step&7`、输入索引 `step>>3`，真实 RAW distance 8；`BIND_OP` 请求 fadd latency 6、fmul latency 3，仍 10 ns，不降频、不量化、不用 false dependence。每 token 仍按原 i/j 顺序做 FP32 乘加，bias 最后加，GELU 仍 exact-erf。CPP SHA `553b9c9b36fee41d791908f1fc3b8314ea4631db403743f5e80665d18fad1350`，HPP `fcdced1362b460a6cea73fc5869564e6797c041469a50b9c56deba1253cd7478`，冻结 ZIP `fc40b31a3a73fa838ac6d9be338be5a51f4881313353d50e6806b90ebb8c0ab5`。主机 30 项通过；两套基线各 19 组合成/173 帧逐位零差、整数零差；保护区页尾读写检查通过。HLS C 30 项通过，FC1/FC2 step II=1/1，生成 16 fadd/16 fmul RTL 实例。
- **v2 数字口径必须分开**：csynth **估计** 3,620,660 cycles/660 token，按目标 100 MHz 约 36.2066 ms；r03 静态 11,773,385 cycles≈117.73385 ms；比值 3.2517 只是静态周期比。csynth 文本表估计资源 DSP 333、BRAM_18K 142、FF 64719、LUT 58866（同目录 `csynth.xml` 的 AreaEstimates/Resources/LUT 为 58860，差 6 原因未证实，不能称 XML 与文本表完全等同；DSP/BRAM/FF 一致）。小范围 RTL 10 事务/30 有效 token 通过，929,038 模拟 cycles、约 2390 s 墙钟，**不是吞吐，也未覆盖 659/660-token**。同源 OOC 实现（`D:/io8`）实际 exit 0、1488 s、WNS +2.435662 ns、WHS +0.034 ns、TNS/THS 0、未路由 0、post-route CP 7.564 ns；routed 资源 LUT 51146、FF 46657、DSP 333、URAM 0、BRAM 18K 等效 131（= 64 RAMB36 + 3 RAMB18 = 65.5 个 36K tiles，不是 131 tiles）。该 OOC 有 127 个输入/280 个输出端口缺外部 I/O delay 约束（TIMING-18 427），**不是全系统接口时序签核**。**这些都不是板上延迟/吞吐，也不是 PS+PL 系统验收。**
- **边界（候选层历史）**：在本节记录时，v2 还只有 OOC IP，没有系统 bit/hwh/XSA、没有下载 Overlay/写时钟/启动新核；完整 659/660-token RTL 未执行。后续系统集成与真实上板已完成，见顶部与 `../docs/04_验证记录.md` 第 22 节；正式源仍 r03，新核当前板上 idle，旧 bit 保留但未回退。

## 2026-10-15 retry04 build update (historical kernel)

The K26 license/device gate passed in the **historical retry04 build on 2026-10-15**. Full HLS C simulation and C synthesis, the emitted register ABI and both 64-bit AXI master address channels, HLS backend implementation/IP packaging, and the KV260 Vivado system implementation have been verified with real tool logs. A matching `ffn_overlay_wrapper.bit` / `.hwh` pair is available in [`artifacts/vivado_20261015/retry04/overlay/`](../artifacts/vivado_20261015/retry04/overlay/).
System routed WNS is **+2.338 ns**, TNS **0**, and WHS **+0.010 ns**.

**RTL co-simulation for the historical retry04 kernel is deliberately limited to one 1-token smoke transaction.** It completed RTL and C postchecking with exit 0; the preceding 30-token attempt was actively stopped with exit 1 and is not a pass. Full 4/659/660-token, weight reload and invalid-count RTL verification for retry04 remains unperformed. Full C simulation coverage does not replace those RTL checks. The board bring-up in `docs/04_验证记录.md` §17 later documents loading and testing that retry04 overlay; it does not validate the 2026-10-17 source candidate or r03. r03 board evidence is in §19 (150 MHz first round) and §20 (100 MHz official retest).

See the [delivery notes and five-step board checklist](../artifacts/vivado_20261015/retry04/overlay/README.md)
and [`docs/04_验证记录.md`](../docs/04_验证记录.md), sections 15–16, for exact reached
stages, exit codes, failures and scope. The build/acceptance history below records **retry04 from 2026-10-15 only**; it is neither a successful-build claim for the 2026-10-17 four-lane candidate nor for the current r03. See the Chinese status block above and `../docs/04_验证记录.md` §19–20 for r03.



Independent float32 reference kernel for KV260/PYNQ migration, not a final
high-performance design. Only `GELU(X @ W1 + b1) @ W2 + b2` is in PL: LayerNorm,
residuals and Dropout stay in PS. `D=128`, `H=256`; C-contiguous row-major right
multiplication with W1 `[128,256]`, W2 `[256,128]`, b1 `[256]`, b2 `[128]`.
Two camera calls use 660 tokens each and two cross calls use 4 each, with **four
different weight sets**. One call handles one weight set and `1..660` tokens.
Counts 0 or >660 return before any buffer access; output remains unchanged.

## Math and storage

The erf-based GELU is `0.5*z*(1+erf(z/sqrt(2)))`, never tanh GELU. Kernel values,
weights, intermediates and reductions are float32. HLS uses `<hls_math.h>` /
`hls::erff`; a normal host build uses the float overload of `std::erf`. The Tcl
script sets `FFN_USE_HLS_MATH` for C simulation too: a missing vendor API must
fail C simulation, not silently substitute host math. The installed Vitis
2026.1 headers have now been inspected locally: `hls_math.h:31` opens namespace
`hls`, and line 887 declares `float erff(float)` (both `Vitis/include` and
`Vitis/common/technology/autopilot` copies). This confirms the API declaration,
**not compilation, linkage, numerical accuracy or successful synthesis**.
Real HLS compilation remains unverified; see the local tool evidence below.
"Exact" means the mathematical erf formulation, not bit-identical PyTorch,
BLAS, double arithmetic or perfectly rounded transcendental hardware.

Each valid call still loads all weights/biases once into fixed local storage
(263,680 bytes / 257.5 KiB); there is no per-token DDR weight reload. These arrays
are static to avoid a large host stack frame; the host entry is not thread-safe or
reentrant. The 2026-10-15 retry04 kernel used serial floating-point reductions.
The 2026-10-17 historical candidate computed four output channels per group
with cyclic-partitioned local weights and kept each channel's original ordered
float32 reduction. That candidate's actual II/area were unknown at the time.
The current 2026-10-18 r03 source instead groups 16 channels, reports actual
FC1/FC2 II=4 and GELU II=1 after synthesis, and has passed HLS C simulation,
small-scope RTL co-simulation and OOC implementation (see the Chinese status
block above). Host `std::erf` checks are still not HLS `hls::erff` validation.
Input/output and weight buffers must be valid, DMA-accessible and nonoverlapping.

## Interfaces and register ABI

Default **ap_ctrl_hs**, AXI4-Lite `control` (32-bit register words), two AXI4
masters: `x,y -> gmem0`; `w1,b1,w2,b2 -> gmem1`. Vivado must connect both masters
to PS-accessible memory; bundle separation does not imply separate DDR chips.
`config_interface -m_axi_addr64=true` explicitly requires 64-bit master address
ports. Do not remove it to work around a tool-version error.

| Argument | Low word | High word |
|---|---:|---:|
| x | 0x10 | 0x14 |
| y | 0x1c | 0x20 |
| w1 | 0x28 | 0x2c |
| b1 | 0x34 | 0x38 |
| w2 | 0x40 | 0x44 |
| b2 | 0x4c | 0x50 |
| num_tokens (uint32) | 0x58 | — |

Control is at **0x00** (`ap_start` bit 0, `ap_done` bit 1 clear-on-read,
`ap_idle` bit 2, `ap_ready` bit 3, auto-restart bit 7). Standard interrupt
registers occupy 0x04/0x08/0x0c. Reserved words after pointer pairs are not
addresses. Confirm all offsets and 64-bit widths against the **real exported**
register header/component metadata before integrating a Python driver.

Python must validate an integer `1 <= num_tokens <= 660` **before** programming
registers (avoid wrapping negative/large values to uint32). Allocate contiguous
float32 X/Y `[num_tokens,128]`, W1/W2 and biases with exactly the shapes above.
Program each physical address with `low = address & 0xffffffff` and
`high = (address >> 32) & 0xffffffff` into **both words**, even when high is zero;
never truncate to a single 32-bit word or pass a virtual NumPy address. Minimum
full-size X or Y is 337,920 bytes; each weight matrix is 131,072 bytes; biases
are 1,024 and 512 bytes. Use PYNQ DMA-accessible allocation, flush/sync X and
all weights/biases before start, invalidate/sync Y only after completion. Keep
buffers alive; do not modify/free them while busy. Serialize callers, disable
auto-restart, check idle, write arguments then start, and poll done with a
bounded timeout. An invalid kernel call still completes but sets no error
register and leaves potentially stale Y; Python validation is mandatory.

## Build and tests

From Windows **CMD**, use a temporary mapped working directory for this UNC
share (direct MinGW linking to UNC output failed with `Invalid argument`):

```bat
pushd \\ENERTCOUNT-EX\Source\Repos\FPGA\pynq_ffn\hls
if not exist build mkdir build
C:\Strawberry\c\bin\g++.exe -std=c++11 -O2 -Wall -Wextra -Wpedantic -Wno-unknown-pragmas -ffp-contract=off ffn_accel.cpp testbench.cpp -o build/testbench.exe
if errorlevel 1 exit /b 1
build\testbench.exe
popd
```

Host g++ 13.2.0: **all tests passed**, including random independent double
reference checks at 1,2,3,4,5,7,8,9,659,660 tokens, all output elements including
the distinct final token, four different weight sets and repeated reload,
zero and bias-only exact-erf cases, output guards, and invalid counts
0/661/UINT_MAX with untouched output and null-input early return. Tolerance is
`abs_error <= 2e-5 + 2e-5*abs(reference)`; observed maximum absolute error was
`2.22897e-6`. Deterministic synthetic tests are not trained-model validation.

## HLS tools: two distinct entry routes

### Modern Vitis Unified flow (2026-10-14 local host attempt: build blocked; later completed on the tool machine — see top status)

`make_config.py` reads required `FPGA_PART` and optional `CLOCK_NS` (10 ns target
by default) and emits `build/hls_config.cfg`, not a DRAFT. It refuses overwrites
and output outside this directory. Real 2026.1 output established that Vitis
changes cwd to the cfg directory: source entries and per-file flags are now
relative to that directory (`../ffn_accel.cpp` and `../testbench.cpp` for the
default output), not to the invoking shell's working directory.

The installed Windows Vitis config editor's
`ide/electron-app/win64/resources/app/node_modules/@rigel/config-editor/src/browser/hls-form/hls_options.json:755-758`
declares boolean `hls.syn.interface.m_axi_addr64`, described as "Enable 64-bit
addressing for all m_axi interfaces". The cfg's `[hls]` section explicitly sets
**`syn.interface.m_axi_addr64=true`**. This replaces the missing draft key, not
the requirement for successful compilation and hardware verification.

**KV260 part evidence:** under
`D:/Learning/AMDDesignTools/2026.1/data/xhub/boards/XilinxBoardStore/boards/Xilinx/`,
`kv260_som/2.0/board.xml:290` and `k26c/2.0/board.xml:536` both specify
**`xck26-sfvc784-2LV-c`**; `part0_pins.xml:14` agrees. This is the commercial
KV260/K26C part, not a ZU9EG example. Board metadata does not prove usable
synthesis device support: the Vivado `get_parts` query could not execute due
to the missing-license startup gate.

**Hardware safety gate:** successful synthesis must still establish all six
pointer register pairs and both RTL master address widths before connecting
this IP to PYNQ. A configured 64-bit key, a default or two pointer words alone
is not proof of the emitted master bus width. No RTL was generated in the
blocked local attempt.

After licensing/device support is resolved, use a local short-path copy of
these sources (not a Windows UNC cwd), set `FPGA_PART=xck26-sfvc784-2LV-c`,
generate the cfg, and run with full installed tool paths on this Windows host

```sh
python make_config.py
CONFIG="$PWD/build/hls_config.cfg"
WORK_DIR="$PWD/build/ffn_accel_modern"
vitis-run --mode hls --config "$CONFIG" --work_dir "$WORK_DIR" --csim
v++ --compile --mode hls --config "$CONFIG" --work_dir "$WORK_DIR"
vitis-run --mode hls --config "$CONFIG" --work_dir "$WORK_DIR" --cosim
vitis-run --mode hls --config "$CONFIG" --work_dir "$WORK_DIR" --impl
```

These command forms were checked with installed tool help. The actual csim
and compile attempts both stopped during part/platform initialization, before
C compilation or synthesis; see logs below. Config source paths resolve from
the cfg directory. C synthesis must finish before cosim; full serial 660-token
RTL co-simulation may be slow. The 10 ns clock is a target, not an estimate or
achieved Fmax. An HLS IP-catalog package is not a PYNQ overlay.

Configuration/command evidence (public AMD/Xilinx sources):

- [Ross run-flow: modern commands](https://github.com/Xilinx/ross-ai-assistant/blob/main/skills/hls-run-flow/SKILL.md).
- [Official IP-catalog cfg](https://github.com/Xilinx/Vitis-HLS-Introductory-Examples/blob/aa5c160faf5d5ebf58674df8f0591f9984ebae0f/Array/array_partition_complete/hls_config.cfg):
  `part`, `[hls] clock`, `flow_target`, `syn.top`, `syn.file`, `tb.file`,
  `package.output.format`, `package.output.syn`.
- [Official 2026.1 cfg with per-file flags](https://github.com/Xilinx/Vitis-Tutorials/blob/08ae81dcfdd48d7611c6550d19a5d82e2d0a23de/AI_Engine_Development/AIE/Design_Tutorials/21-Back-Projection-SAR/hls/image_buffer_1engine/hls.cfg):
  `syn.file_cflags`, `syn.file_csimflags`, `tb.file_cflags`.

### Legacy Vitis HLS Tcl flow (tool-version compatibility unverified)

On a machine providing `vitis_hls`, set `FPGA_PART` to the **verified full
device part name**, optionally `CLOCK_NS` (default 10 ns is a target only), and
`RUN_COSIM=1` for RTL co-simulation. Run
`vitis_hls -f /absolute/path/to/build_hls.tcl`. The script explicitly sets
`config_interface -m_axi_addr64=true`, runs C simulation, synthesis, optional
RTL co-simulation and IP-catalog export. All outputs go under this directory's
`build/ffn_accel_hls/`; no automatic project reset. A tool rejecting the 64-bit
option must fail rather than proceed with 32-bit addressing. Legacy Tcl is
**not verified with Vitis 2026.1** and newer installations may not provide
`vitis_hls`; do not mistake its absence for a complete Unified tools check.

The earlier PATH-only check missed installed tools. On 2026-10-14, full paths
under `D:/Learning/AMDDesignTools/2026.1/` successfully returned Vitis/v++ help
and versions: SW build **6511674**, HLS build **6493734**, version **2026.1**.
Real local-cwd commands and exit codes are archived in `build/amd_20261014/`

- `vivado.bat -mode batch -source verify_part.tcl -nolog -nojournal`: **42**,
  startup error "a valid license was not found"; Tcl query never ran.
- `vitis-run.bat --mode hls --config ... --work_dir ... --csim`: **1**, same
  missing-license error followed by HLS 200-1023/200-1912 during part setup;
  testbench was not compiled or executed. The initial relative-source warnings
  led to the generator path fix above.
- `v++.bat --compile --mode hls --config ... --work_dir ...`: **1**, corrected
  source paths and flags were accepted, then identical part/license failure;
  no synthesis scheduling, RTL or csynth report was produced.

Thus **on that 2026-10-14 local host, neither HLS C simulation nor synthesis
passed**. Part support/license had to be resolved without assuming which cause
applied beyond the explicit missing-license error. This historical local block
does not describe the later 2026-10-17/18 tool-machine runs, where C simulation,
synthesis, small-scope RTL co-simulation, OOC implementation and system build
completed. No GUI, license changes, device installation, board connection or
bitstream download occurred on that local host.
Tcl's older tests used stubs only. No `.bit` or `.hwh` is supplied. A real Vivado
block design, address assignment and implemented matching bitstream/HWH remain
required elsewhere.

## Optional host DLL for real-weight checks

`host_bridge.cpp` only exports the C ABI `ffn_accel_host` with the same seven
arguments; it must not be added to HLS synthesis. The kernel math is unchanged.
A Windows x64 `build/ffn_host.dll` was built and loaded successfully using:

```bat
C:\Strawberry\c\bin\g++.exe -std=c++11 -O2 -Wno-unknown-pragmas -ffp-contract=off -shared -static-libgcc -static-libstdc++ ffn_accel.cpp host_bridge.cpp -o build/ffn_host.dll
```

In Python use `ctypes.CDLL`, `argtypes=[ctypes.POINTER(ctypes.c_float)]*6 +
[ctypes.c_uint]`, `restype=None`; supply contiguous float32 buffers in the order
X, Y, W1, b1, W2, b2, count. Serialize calls because weights use static host
storage. This is host validation only; loading the DLL does not validate HLS,
AXI or the FPGA. Actual trained weights are not bundled in this directory.
