# KV260 FFN 第一版 PL 参考核：2026-10-15 retry04 交付

## 实际结果与验收边界

已完成 HLS C simulation、C synthesis、RTL ABI/64 位主地址核对、**缩小到 1-token 的 RTL cosim**、HLS backend 实现、KV260 PS/PL Block Design、Vivado synthesis/implementation、时序检查和同一次实现的 bit/HWH 导出。

**没有执行板卡加载、真实权重 PL 运算或整帧板上计时。** 这是一版已构建的正确性参考 overlay，不是已验证的实时加速方案。当前串行 FP32 核在 HLS 中估计单次最大 660-token 调用约 1.733 秒；不能将其称为实时或宣称优于 PS。

### 必须保留的 cosim 限制

- 完整 C simulation 覆盖原 testbench 的全部 2035 个有效 token、659/660 边界、四套独立权重、zero/bias-only 和非法计数，退出码 0。
- 原计划 30-token RTL cosim 实际运行约 49.6 分钟，完成首个事务后主动中止，**退出码 1，不通过**，日志保留。
- 后续独立 **1-token RTL 冒烟**完整完成 RTL 和 C postcheck，退出码 0；128 个输出、保护区和未激活输出检查通过，误差 7.43869e-7，实测 345767 cycles。
- **4/659/660-token、四组重载及非法计数 RTL 验证仍未完成。** 它们在 C simulation 中通过不能替代 RTL 验收；多事务 II 在本次 cosim 为 NA。

## 可复制到板端的配套文件

本目录中的以下两个文件必须保持同名、同目录，一起复制，不与其他构建混用：

- `ffn_overlay_wrapper.bit` — 7797910 bytes
- `ffn_overlay_wrapper.hwh` — 274964 bytes

另有同次导出的 `ffn_overlay_wrapper.xsa`，用于追溯。

| 文件 | SHA-256 |
|---|---|
| bit | `8e22a7ffbc308c7741c697cb06d6c3d9c6faa4d886d23cf236014e5c9cb47409` |
| hwh | `bc305bab1cf1201bd3182aab2872694fa3d31e7ba54334fbc8e5ed2507fdcf70` |
| xsa | `b34ca29ef719c185eaeaf0498fdc4a433854fd35d01bc68fc2a03f0dc3117be5` |

最终 bit 与完成 `impl_1/write_bitstream` 的 run 输出和 XSA 内嵌 bit 逐字节一致。HWH 取自同一 XSA 的 `ffn_overlay.hwh`，不是两个 SmartConnect 子级 HWH；提取后按顶层模块文件名重命名。

## 硬件合同与实现证据

- Part：`xck26-sfvc784-2LV-c`；board preset：`xilinx.com:kv260_som:part0:2.0`。
- 顶层：`ffn_overlay_wrapper`；PYNQ IP 名：`ffn_accel_0`；VLNV：`xilinx.com:hls:ffn_accel:1.0`。
- 控制：PS HPM0_FPD → SmartConnect → `s_axi_control`，基址 **0xA0000000**，范围 64 KiB。
- 数据：`m_axi_gmem0/gmem1` 经 2→1 SmartConnect 共享 PS **HP0_FPD**；HLS 两个主地址口仍为 **64 位**。PS 物理地址口不必也是 64 位，必须覆盖实际 DDR 解码地址。
- 两个主口各自完整映射 LOW `[0x0,0x7fffffff]`，HIGH `[0x800000000,0xfffffffff]`。HIGH 是 **32 GiB 解码窗口，不是物理 RAM 容量**，分配必须仍来自实际可用的板端 PYNQ/CMA DDR。
- 同一 PL 时钟域，目标 100 MHz，PS metadata 实际传播 **99.999001 MHz**；PS 复位经 Processor System Reset，核 `ap_rst_n` 由 `peripheral_aresetn` 驱动。
- Routed timing：**WNS +2.338 ns，TNS 0，WHS +0.010 ns，THS 0**；无未约束内部端点，路由错误 0。
- 系统资源：Total LUTs **23962**，FF **26895**，RAMB36 **58**，RAMB18 **4**，DSP **247**，URAM **0**。RAMB36/RAMB18 口径不与 HLS 的 BRAM_18K 表混用。
- Bitgen DRC **0 Errors**。常规 DRC 仍有 DSP 输入/输出流水建议类 Warning/Advisory，完整报告保留。未降低 DRC 严重性或绕过时序门禁。
- BD 保留 AXI USER 宽度提示与 HLS 2-bit LOCK→AXI4 1-bit LOCK 提示；已核对核的 LOCK 常量为 `2'b00`，被丢弃的高位恒为 0。没有把警告伪装成“零警告”。

### HWH 寄存器逐项核对

| 内容 | 低/高偏移 | HWH 实际形式 | 结果 |
|---|---|---|---|
| CTRL | 0x00 | 32-bit CTRL | 符合 ap_ctrl_hs |
| x | 0x10 / 0x14 | x_1 / x_2，各32位 | 通过 |
| y | 0x1c / 0x20 | y_1 / y_2，各32位 | 通过 |
| w1 | 0x28 / 0x2c | w1_1 / w1_2，各32位 | 通过 |
| b1 | 0x34 / 0x38 | b1_1 / b1_2，各32位 | 通过 |
| w2 | 0x40 / 0x44 | w2_1 / w2_2，各32位 | 通过 |
| b2 | 0x4c / 0x50 | b2_1 / b2_2，各32位 | 通过 |
| num_tokens | 0x58 | 32-bit num_tokens | 通过 |

CTRL 字段 START/DONE/IDLE/READY/AUTO_RESTART 为 bit 0/1/2/3/7，各 1 位，未出现 AP_CONTINUE。真实 HWH 元数据经 `ps.pynq_backend.validate_registers` 验证通过，不需修改或关闭驱动校验。Notebook 已指向本目录真实 pair，`IP_NAME='ffn_accel_0'` 不变，**`ALLOW_PL_DOWNLOAD=False` 仍为默认值**。

## 供人执行的上板第一步到第五步

这些是清单，**本轮未执行**。不运行原 ROS 车控、不连接电机/串口，不修改 firmware/CMA/设备树。

1. **核对运行镜像兼容性后加载 overlay。** 在 KV260 上核对 PYNQ 版本、当前 PS DDR/PL 时钟与复位设置、分配区与上述 DDR 映射兼容，无其他任务控制 PL。将配套 bit/hwh 与 manifest 一起复制并核对 SHA-256。只有这些前提由人确认后才启用 notebook 的下载开关；`Overlay(...)` 是实际硬件配置，不是只读检查。加载后检查 `overlay.ip_dict['ffn_accel_0']`、`register_map`、时钟与 idle 状态；驱动应接受真实寄存器表。不得因基址看起来相同而跳过检查。
2. **单层真实权重小输入对比。** 使用同一已校验模型 bundle，从 `split-ort` 取得真实 `cross_0` FFN 输入，先 1 token，再 4 token；逐元素对比 ORT，建议沿用 `atol=rtol=1e-4`，记录最大误差、有限值、buffer 地址和分项时间。使用 `pynq.allocate`、64 位物理地址和显式 sync。超时不重试、不 free、不退出进程，先由人按核验流程停止/复位 AXI master。
3. **边界和重载扩展。** 分别运行 1/4/659/660 token、末 token 非零、两种 camera 权重交替/重访；检查完整输出和无跨层污染。非法计数优先用 Python 门禁拒绝，不跳过驱动直接试危险 MMIO。此步特别补足本次未完成的长事务 RTL 覆盖；实际硬件通过后仍不能改写历史 cosim 覆盖。
4. **四层逐层及混合模型对比。** 按 cross_0、cross_1、camera_0、camera_1 用四套真实权重对比，复用同一核；四处均通过后跑 `hybrid`，核对同帧四输出及整数轮速差异。不要只验证最后输出而忽略中间 FFN 误差。
5. **整帧计时与收益判断。** 同一模型、输入、线程与 warmup 比较 golden CPU、split-ort、split-PL；记录 PS 子图、准备/flush、硬件等待、invalidate/readback 和整帧 P50/P95/P99，不把 HLS latency 或 host mock 时间当板上 FPS。四层正确不代表该串行参考核有加速收益；保留实际结果，不做实车验收声明。

## 证据位置与复现

- 完整验证记录：`docs/04_验证记录.md` 第15节及之后。
- HLS 完整快照：`artifacts/hls_20261015/retry04/hls_complete/`。
- Vivado 最终快照：`artifacts/vivado_20261015/retry04/final/`（含全部本次命令、日志、BD Tcl、资源/时序/DRC、HWH核对和软件回归）。
- 本机短路径工程：`D:/ffn_build/retry04/vivado03/ffn_overlay.xpr`；原始 HLS work_dir 在同 retry04 的 `hls/build/ffn_accel_modern/`。
- `ffn_overlay_bd.tcl` 为工具导出的 BD 脚本；`rebuild_overlay.tcl` 用**新的**本地短路径与已校验提取 IP 建立工程，明确调用 `reset_target all` / `generate_target all`。重建脚本本身随交付提供，**未另做一次完整独立重建验证**。各 run 必须检查退出码和 `wait_on_run`，布局布线时序失败就停，不生成“合格”bit。

本次不改核数学、原 testbench、addr64 要求或驱动安全合同；不改 PATH、不安装/升级全局工具，不动 `kv260_v1/`，无 Git 提交，无板卡物理动作。
