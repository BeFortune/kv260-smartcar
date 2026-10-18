# system_acceptance_review — ffn_opt_20261018

日期：2026-10-18  
范围：只读复核系统构建与导出证据；不连接远程、不编程器件、不重跑板测。  
根目录 `SB`：`C:\Users\BeFortuneEX\Source\Repos\FPGA\pynq_ffn\artifacts\ffn_opt_20261018\system_build`

## 结论

离线系统构建验收通过：五阶段退出码为 0，时序、布线、DRC、资源、XSA/HWH、源 IP 与补充 checkpoint 均一致。  
这不等于板上成功；主代理仍在运行。板测 run01 的结论见文末，本报告未复核该测试。

## 1. 阶段与日志

- `SB/records/{prepare,bd,synth,impl,export}.finished.json`：`exit_code` 均为 0，`input_sha256` 均为 `071a4baca488b6d3ca570f12fc1e00906bb5b1747970197d14c7e1bce09bac30`。
- 成功结尾：
  - `SB/records/prepare.console.log:1`：`Pinned r03 IP safely staged; no Vivado/board access in prepare.`
  - `SB/records/bd.console.log:82`：`FFN_BD_VALIDATED_AND_GENERATED`
  - `SB/records/synth.console.log:344`：`FFN_SYNTH_STATUS=synth_design Complete!`
  - `SB/records/impl.console.log:875`：`FFN_IMPLEMENTATION_STATUS=route_design Complete!`
  - `SB/records/export.console.log:1055` 写 bitstream 成功；`:1110` 创建 XSA；`:1113` `FFN_BITSTREAM_RUN_AND_XSA_COMPLETE`
- `SB/collection_manifest.json` 的 86 份文件逐份 SHA256/字节数均匹配。

## 2. 时序、布线与 DRC

- `SB/evidence/vivado/implemented_timing_summary.rpt:142`：WNS `2.433 ns`，WHS `0.010 ns`，TNS/THS `0.000`，失败端点 `0`；`:145` 所有用户约束满足；`:155` 时钟 `clk_pl_0` 周期 `10.000 ns`（标称 100 MHz）。
- `SB/evidence/vivado/implemented_route_status.rpt:8-10`：可布线 `63005`，已布通 `63005`，布线错误 `0`。
- `implemented`、`pre_bitgen`、`post_bitgen` 三份 gate 均为 `STATUS=PASS`，哈希同为 `40677d32cc8b8af667af051cd0159d5341a714c7addb8ac994d8ebe76c5d6de0`。
- `SB/evidence/vivado/implemented_drc.rpt:27`：`Checks found: 1401`；`:31-34` 4 条 Warning 共 1123 项（487+406+204+26）；`:35-36` 2 条 Advisory 共 278 项（214+64）。
- Error、Critical Warning 均为 0；普通 Warning/Advisory 保留。`pre_bitgen_drc.rpt`、`post_bitgen_drc.rpt` 同样为 1401 项且无 Error/Critical Warning。
- `SB/vivado03/ffn_overlay.runs/impl_1/runme.log:976` `Bitgen Completed Successfully.`；`:978` `0 Warnings, 0 Critical Warnings and 0 Errors`。

## 3. 实际资源（Routed）

来源：`SB/evidence/vivado/implemented_utilization.rpt:25`，顶层 `ffn_overlay_wrapper`。

| 资源 | 数值 | 单位/口径 |
|---|---:|---|
| Total LUTs | 26395 | LUT 计数；= Logic 24249 + LUTRAM 868 + SRL 1278，不可重复相加 |
| FFs | 29753 | 触发器计数 |
| RAMB36 | 65 | 36Kb block RAM 块 |
| RAMB18 | 3 | 18Kb block RAM 块；与 RAMB36 合计 66.5 个 RAMB36 等价块 |
| URAM | 0 | UltraRAM 块 |
| DSP Blocks | 262 | DSP48 块 |

器件：`xck26-sfvc784-2LV-c`，设计状态 `Routed`。

## 4. XSA / HWH

- XSA：`SB/traceability/ffn_overlay_wrapper.xsa`，SHA256 `46dcbd22a08b3e4fdbc5e96ed506ff2fb42d14c64faa3e1dad1690a09e17327d`。
- XSA 内含一个 `.bit`：`ffn_overlay_wrapper.bit`，SHA256 `c67dd63ccd579ab105840a30c4ca1a61715a26b7631cc323473328a821319eb1`。
- XSA 内嵌顶层 HWH：`ffn_overlay.hwh`，SHA256 `f4040b704f14ab02ab25b5266226442541be1fc30cc6e786512d9a00ea675649`，与 `SB/load/ffn_overlay_wrapper.hwh` 字节级一致。
- 顶层 HWH 唯一主 IP：`ffn_accel_0`，VLNV `xilinx.com:hls:ffn_accel:1.0`。
- 两个独立 master：`m_axi_gmem0`、`m_axi_gmem1`，`ADDR_WIDTH` 均为 64。
  - gmem0 为 READ_WRITE，承载 x 读、y 写。
  - gmem1 为 READ_ONLY，承载 w1/b1/w2/b2 只读权重与偏置。
- 控制基址：`0xA0000000`，范围 `0x10000`，高址 `0xA000FFFF`。
- HP0 DDR 映射：低段 `0x00000000-0x7FFFFFFF`，高段 `0x800000000-0xFFFFFFFFF`；两个 master 均映射到 `S_AXI_HP0_FPD`。
- 六指针：x/y/w1/b1/w2/b2 分别在 `0x10/0x1C/0x28/0x34/0x40/0x4C`，各为低/高 32 位寄存器对；与 `hls_evidence/r03b/ffn_accel.cpp:30-35` 一致。
- 时钟：时序约束为 100.000 MHz；HWH/BD 的 `FREQ_HZ=99999001`（约 100 MHz，PS `pl_clk0` 实际值），不是精确 100000000。

## 5. 源 IP

- `SB/run_system.py:28` 固定源 IP SHA256 为 `de24cf176e36e8ddf8aea03db02faa9b218133e799cd8ba0289c722a29c5e56b`；`:237` 同时校验 SHA 与 `351343` 字节。
- 归档 `hls_evidence/r03b/ffn_accel.zip` 独立复算：SHA256 `de24cf17...e56b`，大小 `351343` 字节。
- 该 ZIP 的 81 个成员与 `SB/manifest.json` 中 `ip_repo/*` 哈希逐一匹配。
- retry04 的 `ffn_accel.cpp` 为 `9587ed0b...`，r03b 为 `829c9ec6...`，源不同；未回退 retry04。

## 6. 补充 checkpoint 复核（消除前一版缺口）

归档：`system_checkpoints.zip`，SHA256 `ddb925019f016d473e2ecd16ce2ac997301f19bb8b0c42757c77c0504db43d11`，与给定值一致。  
ZIP 内 `manifest.json` 三成员逐文件流式 SHA256 复核全部通过：

| ZIP 内文件 | 字节 | SHA256 | 对照 |
|---|---:|---|---|
| `vivado03/ffn_overlay.runs/impl_1/ffn_overlay_wrapper.bit` | 7797910 | `c67dd63c...19eb1` | XSA 内 bit、load bit 完全一致 |
| `vivado03/ffn_overlay.runs/synth_1/ffn_overlay_wrapper.dcp` | 112884 | `cfea2a34...cda4` | 与 `synth.finished.json` 一致 |
| `evidence/vivado/ffn_overlay_wrapper_routed.dcp` | 36818823 | `39706516...dd97` | 与 `impl.finished.json` 一致 |

前一版指出的 `run_bit`、synth DCP、routed DCP 缺失缺口已消除。按授权只做 hash，不展开/打开 DCP 内容。

## 7. 剩余口径与限制

1. HWH 的 `FREQ_HZ` 为 `99999001`，时序约束为 100.000 MHz；对外应写“标称 100 MHz”，不要写精确 100000000 Hz。
2. 本报告只证明构建与产物一致；未做板上编程、真实 PL 数值、连续性能测试。板测 run01 不在本次复核范围。
3. DCP 仅做完整性 hash，未做 checkpoint 打开后的逻辑/布局等价复核。

## 8. 板测 run01（主代理提供，本报告未复核）

据主代理：run01 已真实执行，117 个数值全部一致；但运行频率 150 MHz 不是本次 build 的 100 MHz。  
本次 WNS 2.433 ns 是 10.000 ns 周期下的结果，不能外推到 150 MHz，因此正式性能结论阻塞，交由主代理处理。  
本报告不称板上成功，也不把 117 数值一致扩展为性能或 150 MHz 时序通过。
