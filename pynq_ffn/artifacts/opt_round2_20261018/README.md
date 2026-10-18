# 第二轮优化：ORT 线程诊断与独立 token 交错候选

日期：2026-10-18。用户明确要求直接开始两项：查明 ORT 多会话四线程为何更慢；在不改模型的前提下，尝试多个独立 token 交错流水提高 PL 实际吞吐。**本轮两条线已完成：关闭空转的生产Runtime可选项已在KV260完成117帧零误差复验；r04 v2交错候选通过完整C仿真、小范围RTL及独立IP布局布线，但未系统集成/上板。** 用户要求中间通报必要信息，用大白话说明做什么、为什么和新旧路径；真正需要用户取舍的事项再问。

## 基线和边界

- `ps/runtime.py` 仅新增显式可选 `spinning: bool | None = None`；`None`默认完全不变，现有 `hls/ffn_accel.cpp/.hpp` 仍为r03，既有板上工件保持不替换。实验归档继续绑定其旧Runtime哈希，不用新版本冒充旧基准。
- 已验证 r03：16 lane、归约 II=4，100 MHz 单 camera 硬等约118.9 ms；117帧四线程完整图 CPU P50约64.74 ms，split-ORT约318.34 ms，PS+PL约372.22 ms。
- 新任务只运行纯 CPU 录像实验、主机 C++/数值回归及 AMD 工具机仿真/综合；**不加载新 Overlay、不写时钟、不启动 PL 核或设备 DMA，不接相机/串口/电机，不更改系统服务、启动参数、CMA/固件或工具许可**。
- 不重训、不量化、不近似 GELU，不改变相机顺序、原预处理、四输出或数值门槛。
- 禁止将当前任务自动执行解释为无人值守或设备维护/车辆总授权；不 commit/push。

## ORT 路线（CPU侧）

原路径：五个 PS ONNX 会话和四个 FFN ONNX 会话，逐段串行执行；每会话可能各有线程池。4个CPU核上多个会话的后台线程即使没在算，也可能空转争用资源。

实验路径：同样的模型/分图/输入顺序，只控制会话线程数和 `session.intra_op.allow_spinning`/`session.inter_op.allow_spinning`、小子图线程数。每个条件独立进程、固定预加载输入、warmup与样本数；正式性能和 profiling 分开，保存CPU time、线程数、上下文切换、分段耗时和四输出/整数速度等价。

代码入口：`tools/ort_thread_study.py`（旧Runtime冻结实验适配器）；新生产接口为 `Runtime(..., spinning=False)`，直接复验脚本 `tools/runtime_spin_validate.py`。证据在本目录 `ort/`。纯CPU输入复用只读 `/home/ubuntu/ffn_migrate/bundle` 与 `data/1778852165`，新部署 `/home/ubuntu/opt_round2_20261018/ort`。

**实测结论：** 每条件117帧、warmup3、正反序各一遍。切图4线程spin ON P50=404.512/411.005ms，OFF=75.181/77.320ms，同为31线程，数值/整数门槛均通过；CPU占用与非自愿切换显著下降，空转是主要可消除开销。直接生产Runtime的117帧复验四输出全部零误差，split-off P50=79.632ms，golden默认None P50=65.298ms。默认未改，也未测PS+PL组合收益或达到15Hz。详见 [ORT实验解释](ort_findings.md)、`ort/comparison_report.json`。

## PL 路线（候选硬件，不替换当前已验证核）

原路径：单token的16个输出一起计算，同一累加链的下一次加法需要等待4拍，实际算子被共享，吞吐约4 MAC/clk。

实验路径：v1先采用4个独立token轮流计算；因关键反馈路径时序失败，v2唯一窄修正为8个独立token、加法请求6拍，每个token自己的输入索引依然按原顺序增加，不把一个输出拆成乱序部分和。输入/权重存储分区配合真实读口；吞吐依据真实RTL算子数、II、循环周期及实现时序核验，不只看UNROLL数字。

候选：`hls/candidates/interleaved_r04/v2/`（正式核r03不动）；证据本目录 `hls/`。工具机使用新的 `D:/i8` 做仿真/综合/RTL、`D:/io8` 同源独立实现，旧 `D:/i4` 保留v1失败现场；`D:/f3`、`D:/g3`不动。

**结果：** v2主机逐位与173帧回归通过，HLS完整C仿真30项、小RTL10事务/30有效token PASS（**不覆盖完整659/660 RTL**）。生成RTL有16个fadd+16个fmul，计算step实际II=1，660-token静态估计3,620,660 cycles（目标100MHz约36.21ms；对r03的3.25倍只是静态周期比）。独立HLS IP布局布线通过：WNS+2.435662ns、WHS+0.034ns、未布线网络0。布线后LUT51146、FF46657、DSP333、BRAM131个18K等效块（64RAMB36+3RAMB18）。OOC外部延迟未覆盖系统接口，不能代替PS+PL系统时序。

新IP包867640字节，SHA-256 `3e7eb615f7fc0ecf61ad5e56443eab6f4665e698045cef2565a4990e340e4185`，原始证据在 `hls/remote_final/v2_ooc/work/ffn_accel.zip`。**没有生成新系统Overlay或替换板上r03，没有新核板测成绩。** 详见 [HLS候选解释](hls_findings.md)、`hls/v2/remote_summary.json`。

## 连接、工具与留证

本轮优先使用已挂载的 Netcatty 原生工具，先 `get_environment` 核对名称/地址：工具机 **4090内网=192.168.110.248/JW**（不是zt/ts），板卡KV260=192.168.110.200/ubuntu。远程2026.1工具用完整路径 `D:/Learning/AMDDesignTools/2026.1/`。短命令terminal_execute，构建terminal_start后按offset轮询；不再用无支持的waitMs、截断客户端输出或反复检查已完成阶段。全输出写原始文件，前一阶段失败则相关后续停止。

权限如实际要求审批，遵循先通知用户再发起的约定；当前Netcatty无审批设置由用户控制，AI不更改安全配置。凭据只用已有凭据服务/环境变量，不写可用密码、私钥或token到源码、示例、测试或报告。
