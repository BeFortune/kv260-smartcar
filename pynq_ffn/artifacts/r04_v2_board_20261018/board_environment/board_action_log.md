# 实板操作日志（2026-10-18）

## 编程前

- 完整系统实现、bitgen 前后时序和接口门通过；原始系统交付 109 文件 SHA 重核对通过。
- 新 bit/HWH 同源核对，板侧 source-only 部署与静态门退出码 0。
- 当前 r03 身份、CTRL=4、真实 PL0=99.999 MHz 已只读核实；没有沿用旧任务写分频的授权，没有写任何时钟寄存器。
- 新核采用独立文件名 `r04_v2_20261018.bit/.hwh`，旧工件保留。显式使用官方父类 `Bitstream.download(overlay, overlay.parser)`，不调用会恢复 HWH 分频的 `Overlay.download()`。

## 一次实际下载与警告停点

`program01` 在 2026-10-18 10:54:58–10:55:07 UTC 执行，一次下载已经返回，但脚本捕获到以下唯一警告后按门禁退出码 1，零核启动：

```text
unclosed file <_io.BufferedWriter name='/usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq/pl_server/_current_metadata.pkl'>
```

原 `program01/report.json` 状态仍为“失败”，`hardware_programmed=true`、`kernel_starts=0`；不覆写、不重新下载。该警告对应已只读捕获的 PYNQ `gen_cache()` 中 `pickle.dump(parser, open(..., 'wb'))` 没有显式关闭文件的源码，不是计算超时。

## 新进程独立只读复核

`program_verified01` 使用真实的新解释器，先校验唯一警告、原编程记录 SHA、部署 SHA，再在构造 Overlay 前完整核对新 runtime/global state、bit/HWH/timestamp（原 harness 身份门未放宽）。退出码 0：

- 新 bit/HWH SHA 与系统确认单一致；没有第二次下载。
- CTRL=4、零 START/写入，实际 PL0=99.999 MHz。
- 四路 PL raw 全为 `0x01010f00`，五路 PLL 与 ACPU raw 和编程前完全一致；CPU=1333.32 MHz。
- 未执行回退；旧 r03 工件仍可用，但不把“可回退”写成“已回退”。

`launch_pins02.json` 只将后续计算的编程确认来源改为这个独立复核记录；不改原失败记录，不再运行编程阶段。

## 正式计算任务

已启动 Netcatty 长任务 `job_mvdppnm8_b05428bdc894`：三个顺序独立进程，各 117 帧、warmup3、4 线程；golden 保持 `spinning=None`，split-ORT/PS+PL 显式 `False`。PL 首先 1-token 真输入冒烟、再 36 层边界/轮换、最后正式帧；预期 517 次 START 写入。该任务开始记录不等于完成，最终以退出码、四输出/切点/整数门、原始计时和清理 idle 为准。

正式任务已完成，Netcatty 原始完成状态 `completed/exitCode=0`，实际 2026-10-18 10:58:32–10:59:52 UTC；三个模式和父进程均正常完成。新核 517 次 START，1-token 冒烟和 36 层通过，117 帧四输出/切点/整数轮速门通过。最终 `CTRL=4`、99.999 MHz、全部时钟 raw 不变，先 idle 再关闭后端并清理 ORT，无超时或隔离事件。run_total P50：golden 65.2109 ms、split-ORT 74.8666 ms、新 PS+PL 170.0301 ms，相对 CPU 的加速目标失败。

原始板测包 `board_evidence01.zip` 共 385 文件逐 SHA 核对，系统交付另有 109 文件重核对；最终状态见 `../final_status.json`、性能/误差独立复算见 `../board_analysis01.json`。本文件新增完成记录，不改写原编程警告或早期失败。最后已验证状态是测试结束时 r04 v2 留板 idle、未回退，不冒充此后另一次现场读回。

超时或在途状态不确定时的保活与不自动恢复规则始终保留。全程未接相机/串口/车辆，未刷写或改启动配置，未写 PL/PLL/CPU 时钟寄存器。

证据收尾后，本次由 AI 新开的 KV260 会话 `0d86f3d4-1637-47ed-b989-a84c1e1738aa` 已正常关闭，未中断在途任务、未执行设备复位或回退；原用户会话未关闭。板上结束状态仍以正式测试末尾记录为准。
