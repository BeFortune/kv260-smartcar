# FFN HLS correctness/reference IP

## 2026-10-15 retry04 build update

The K26 license/device gate now passes. Full HLS C simulation and C synthesis,
the emitted register ABI and both 64-bit AXI master address channels, HLS backend
implementation/IP packaging, and the KV260 Vivado system implementation have
been verified with real tool logs. A matching `ffn_overlay_wrapper.bit` / `.hwh`
pair is available in
[`artifacts/vivado_20261015/retry04/overlay/`](../artifacts/vivado_20261015/retry04/overlay/).
System routed WNS is **+2.338 ns**, TNS **0**, and WHS **+0.010 ns**.

**RTL co-simulation is deliberately limited to one 1-token smoke transaction.**
It completed RTL and C postchecking with exit 0; the preceding 30-token attempt
was actively stopped with exit 1 and is not a pass. Full 4/659/660-token, weight
reload and invalid-count RTL verification remains unperformed. Full C simulation
coverage does not replace those RTL checks. No overlay download or board test
has occurred, and the serial FP32 design has no demonstrated board speedup.

See the [delivery notes and five-step board checklist](../artifacts/vivado_20261015/retry04/overlay/README.md)
and [`docs/04_验证记录.md`](../docs/04_验证记录.md), sections 15–16, for exact reached
stages, exit codes, failures and scope. Statements describing unverified HLS or
blocked builds in the original text below refer to the 2026-10-14 attempt; they
remain historical evidence, not the current build status.


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

Each valid call reloads all weights/biases once into fixed local storage
(263,680 bytes / 257.5 KiB); no per-token DDR weight reload. These arrays are
static to avoid a large host stack frame; the host entry is not thread-safe or
reentrant. Scratch per token is 384 floats. This design intentionally uses
serial floating-point reductions. Copy-loop PIPELINE hints specify no II;
there is no guaranteed II=1, throughput, resource fit or Fmax. Real synthesis,
RTL co-simulation and Vivado implementation are needed to establish these.
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

### Modern Vitis Unified flow (2026.1 options locally checked; build blocked)

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

Thus **neither HLS C simulation nor synthesis passed**. Part support/license
must be resolved without assuming which causes apply beyond the explicit
missing-license error; `get_parts` is still unverified. RTL co-simulation,
IP export/package and implementation were not attempted. No measured resource,
latency, II, estimated clock or achieved Fmax is available. No GUI, license
changes, device installation, board connection or bitstream download occurred.
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
