# PYNQ 下载路径与时钟风险核查

日期：2026-10-18。证据为板上 PYNQ 3.0.1 安装源码的只读文本采集 [loader_source01.json](loader_source01.json)，采集过程中没有导入 PYNQ 或访问设备。

## 已核实

- `Overlay.__init__(..., download=False)` 不调用 `Overlay.download()`；但会初始化设备/元数据对象，不能称整个调用绝对无副作用。
- `Overlay.download()` 先按 HWH 的 `clock_dict` 调用 `Clocks.set_pl_clk`，再调用父类下载。当前 HWH 的 DIV10 配合板上约 1.5 GHz IOPLL 曾产生约 150 MHz；不能因 HWH 标称 100 MHz 就继续测试。
- `Bitstream.download(self, parser)` 调用设备下载，不执行上述 Overlay 时钟设置循环。
- `EmbeddedDevice.download()` 会写本次 bitstream 的 `/lib/firmware/<stem>.bin` 临时加载文件、配置 FPGA manager、设置 AXI 端口宽度、调用 XRT，然后登记新的 bitfile/timestamp。这些是 Overlay 编程副作用，不是单纯环境检查。
- `Device.shutdown()` 对已登记的 shutdown manager 发起隔离请求；本次必须先检查登记内容和旧核 idle，不把它当可随意调用的恢复命令。
- 下层 XRT 调用存在，静态 Python 源码没有时钟 setter 不能证明所有驱动内部都不改时钟。因此即便使用父类下载路径，也必须在编程前后比较 PL0..3、5 个 PLL 与 ACPU 原始值，并核对真实 PL0 为目标频率；不满足则零核启动、保留阻塞，不自动调钟或重载旧核。

## PYNQ 两种时间戳的实际合同

补充只读证据 [cache_source01.json](cache_source01.json)：`EmbeddedDevice.gen_cache()` 在 FPGA manager 编程前生成持久化 global state 的时间戳；`Device.post_download()` 在成功后另生成当前进程的 bitstream/device 时间戳。因此这两个来源不要求相等。

下载进程分别核查新持久化路径/bit/HWH SHA/更新的持久化时间戳，以及新运行时路径、`PL.timestamp == overlay.timestamp` 且不同于旧运行时时间戳；不修改 global state。正式验收在真正的新解释器里运行，其首次 PL getter 回退到持久化状态，仍完整执行原 `reuse_overlay` 身份门，不放宽它。

新临时固件文件名还要同时满足不存在且不是符号链接，拒绝悬空链接导致的覆盖。使用独立新文件名，不清理旧固件文件。独立下载脚本及其单阶段启动器的 8 项离线测试已通过（`loader_runner_tests04.xml`）；首轮 fake 寄存器测试夹具失败保留在 `loader_tests01.xml`，它不代表硬件结果。

源码写入安全钩子曾阻止初稿：一处把兼容性 SHA1 比较当作安全算法，一处把函数名与拼接字符串误识别成 SQL；最终 loader 不再重复计算 SHA1，改为 pin 只读 inspection 的完整 global state SHA256，并继续核对 bit/HWH SHA256；正式 harness 仍保留既有 PYNQ SHA1 bookkeeping 兼容校验。启动器初稿也被命令注入钩子拦截，最终版明确使用参数列表、`shell=False` 和 SHA pin 的限定入口。没有经 shell 绕过钩子落盘或改安全配置。

## 采用边界

系统工件验收、旧核空闲、工件/加载器 SHA、管理员票据和所有静态安全门通过之后，才允许一次显式 `Bitstream.download(overlay, overlay.parser)`。不篡改 HWH、`clock_dict` 或 PYNQ 安装文件，不调用 `Overlay.download()`，不写 PLL/PL clock 寄存器，不在脚本里自动复位或回退。

本说明只确定拟用路径及其核查依据，不代表已经编程。实际编程及数值/性能结果以独立原始记录为准。
