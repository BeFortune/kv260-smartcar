# r04 v2 系统集成与 KV260 离线实板验收

日期：2026-10-18。**本轮已完成真实 KV260 上板验收，不是仅仿真：新 r04 v2 在 99.999 MHz 下数值通过、517 次 START 正常完成，但 PS+PL 推理 P50 为 170.03 ms，仍比完整 CPU 的 65.21 ms 慢 2.61 倍，加速目标失败。**

- [板测结论与慢因分解](board_findings.md)
- [独立复算分析](board_analysis01.json)
- [最终状态清单](final_status.json)：385 份板测证据、109 份系统交付文件重核对通过；真实计算与数值“已验证”，相对 CPU 的加速目标“失败”，分别记状态。
- [原始板测证据](collected_board01/)：385 文件逐 SHA 校验；原始 ZIP SHA `f0ef8ff44db105af1e0ad8085ab7c04d7184a1f10d79d51c9a928b71d63348e9`。
- [完整系统构建复核](system_build/system_acceptance_report.json)：WNS +1.747 ns、WHS +0.010 ns、97,862 nets 全路由、0 错误；同次 bit/HWH/XSA 已核对。

## 本次授权与目的

用户明确请求：“这是不是模拟运行，如果不能代表真实情况请你上板子验证一下”。因此，本次范围是把第二轮已冻结的 r04 v2 IP 集成为独立 PS+PL 系统 Overlay，并在 KV260 上使用既有模型/录像检验数值和真实性能。不能把先前仿真结果当板测，也不把 KV260 CPU 实测误写成仿真。

- 正式 `hls/ffn_accel.cpp/.hpp` 和原 r03 bit/hwh/xsa 保留；新构建、部署和证据使用独立目录，不覆盖原件。
- 新 IP SHA-256：`3e7eb615f7fc0ecf61ad5e56443eab6f4665e698045cef2565a4990e340e4185`。
- 候选 CPP SHA-256：`553b9c9b36fee41d791908f1fc3b8314ea4631db403743f5e80665d18fad1350`。
- 最终生产 Runtime SHA-256：`49ace8fbf61a29e2e54efc174b45477cd08fc5fe469d13547df17326b9745576`；切图路径显式 `spinning=False`，完整 CPU 参考保持原默认。
- 新系统必须通过全系统时序、路由、ABI/地址/同源工件检查，才允许下载和启动。
- 实测前、后记录真实 PL0、PLL、CPU 时钟；目标 100 MHz 不等于实际 100 MHz。当前任务不自动延续旧任务的分频寄存器写入许可。

## 不包含的操作

不写 QSPI/SD/启动文件、不刷写、不重启、不改内核/CMA/系统服务/许可证；不接相机、不打开串口、不发车辆指令，不重训、不改模型/预处理/相机顺序或输出语义。不会自动下载旧 Overlay 作为清理；旧工件可用不等于已执行回退。

如果核超时或访问状态不确定，停止新测试并保留可能仍被 AXI 访问的内存和存活进程；不能 free、重用、强杀、退出进程当取消，或自动复位/重新编程。恢复另行确认。

## 工作分工和目录

- `system_build/`：4090 内网（192.168.110.248/JW）新目录的 Vivado 系统构建、报告、bit/hwh/xsa 和哈希。
- `board_validation/`：仅用于本次的板测脚本、离线测试和安全门；不覆盖旧脚本。
- `board_environment/`：KV260 只读预检、加载前后状态和原始留证。

`amd-pynq` 客户端入口已加载，但其引用的 `skill/amd-pynq/SKILL.md` 仍不存在；本次依照工程规则及已有原始脚本，不声称技能正文可复现。

## 预检记录

已连接新的 KV260 会话，核实主机 `kria`、内核 `5.15.0-1027-xilinx-zynqmp`、用户 `ubuntu`，模型 bundle 和录像目录存在，可用内存约 3.28 GB、磁盘约 106 GB；未发现匹配本工程的运行中 FFN/ROS 进程。此时没有导入 PYNQ、访问寄存器或编程硬件。

首次 `sudo -n true` 退出码 1，关键错误为 `sudo: a password is required`。已提示用户在当前 KV260 终端本地执行 `sudo -v`，不向 AI 提供密码；不修改 sudoers、不读凭据历史。

## 验收结果与状态

三个独立进程、同模型/输入/FP32/4线程、各117帧warmup3：完整CPU（spinning=None）P50 65.21 ms，切图CPU（False）74.87 ms，新PS+PL（False）170.03 ms；四输出/整数速度门全部通过。单层最大误差1.04e-7，最终speed_raw最大0.00012207，整数零分歧。PS+PL正常结束先idle再释放buffer，没有超时或自动恢复。

一次编程后因PYNQ缓存文件唯一ResourceWarning按门记录失败、零START，原记录在`collected_board01/supplement/program01/`保留；随后全新进程独立只读核实新身份/99.999MHz/全部PL、PLL、CPU raw不变通过，未二次下载，正式计算才开始。新核现在留在板上idle，**未执行回退**；正式r03源码和旧工件保留。

**已验证：** 系统构建/同次工件、Overlay编程后独立核实、真实PL数值和117帧离线性能。**失败：** 相对CPU加速目标。**未执行：** 完整大token RTL、长时稳定性、实时三摄、串口/电机/实车、15Hz闭环和最终交付ZIP同步。没有刷写、重启、维护或写时钟。

预检与构建时的权限/环境错误详见`board_environment/attempts.json`和`board_environment/board_action_log.md`，不抹除失败，也不把早期未执行快照覆盖较新验收。
