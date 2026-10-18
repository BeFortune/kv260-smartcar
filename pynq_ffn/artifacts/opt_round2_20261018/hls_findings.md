# r04 token 交错候选：本轮硬件实验结果

日期：2026-10-18。正式板上基线仍为r03，板上工件/时钟/计算核均未由本轮实验修改。

## 为何改这条路径

r03单token内部16路输出分组，但浮点累加链需要4拍再用同一结果，综合只配置4个乘法器和4个加法器；有效乘加吞吐约4MAC/clk。新候选把独立token的累加状态分开保存，让A等待时先处理B、C等，回到A时上次结果已出来。每个token仍按原i/j递增做FP32乘加，bias仍最后加，GELU仍是erf形式。

## v1：4个上下文，方向有效但时序失败

- 独立输入：`hls/candidates/interleaved_r04/`，CPP SHA `bb7d5cdce5597b4c09c9a9505953aa5cc171ffd71f66265eb78cd1f6c063a781`，HPP SHA `b958fb3cf1ee318ab4fc11295835022c4565df4fc4fd13fe4bc24a5250e58eec`。
- 完整本地主机测试27项，真实173帧分别对串行和r03基线逐位相同；HLS完整C仿真真实exit0。
- 4个独立上下文、16输出lane，真实RAW依赖distance4，step II=1；但综合关键反馈路径包括fadd、sum写回/读出、context选择再到fadd，约11.002ns，超过7.3ns调度预算（目标10ns、uncertainty2.7ns）。**关键模块Slack -3.70ns，不能采纳。**
- 3,386,100 cycles只是假定目标频率下的估计，负时序意味着不能把这速度当可用；不进行该版本RTL/板测。
- 原输入、报告和失败保留于`hls/source_snapshot_v1/`、`hls/remote_partial01/v1/`，未覆盖。

## v2：8个上下文、6拍加法，综合与小范围RTL已验证

- 本轮唯一窄修正：8个独立上下文，`ctx=step&7`、输入索引`step>>3`，真实RAW distance8；fadd请求fulldsp latency6，fmul请求maxdsp latency3；仍10ns，不降频、不量化、不用false dependence。
- 独立候选 `hls/candidates/interleaved_r04/v2/`；CPP SHA `553b9c9b36fee41d791908f1fc3b8314ea4631db403743f5e80665d18fad1350`，HPP SHA `fcdced1362b460a6cea73fc5869564e6797c041469a50b9c56deba1253cd7478`。
- 冻结ZIP SHA `fc40b31a3a73fa838ac6d9be338be5a51f4881313353d50e6806b90ebb8c0ab5`，新目录`D:/i8`做仿真/综合/RTL，`D:/io8`同源独立work做IP实现，不与RTL进程覆盖工具结果。
- 完整主机测试30项通过，含7/8/9、15/16/17、659/660、重复尾块、权重重载/复访、zero/bias、保护区和非法计数；double最大绝对误差2.22897e-6。
- 两套基线各19组合成、各173帧/692层切点逐位零差，整数轮速零差；另有最小buffer紧贴不可访问页验证，无越界读写。
- HLS C仿真完整30逻辑用例通过，实际exit0；C综合exit0，FC1/FC2实际step **II=1/1**，关键模块Slack **+1.55/+1.62ns**，GELU pass +0.09ns。
- 最大660-token调用静态估计 **3,620,660 cycles**，目标100MHz换算约36.21ms，r03 11,773,385cycles约117.73ms，周期比约3.25。这是核的静态周期对比，不是实板延迟，也不是整模型/整车加速。
- 估计资源（`csynth.rpt`文本表）：BRAM_18K142/288、DSP333/1248、FF64719/234240、LUT58866/117120，URAM0。同源`ffn_accel_csynth.xml`的AreaEstimates/Resources/LUT为58860，与文本表58866差6，原因未证实，不能称XML与文本表完全等同（DSP333/BRAM142/FF64719一致）；routed阶段LUT51146是另一口径，不受此差异影响。比r03占更多LUT/寄存器，布局布线必须实测。
- 生成RTL已独立数出 **16个fadd、16个fmul实例**（FC阶段共享，不含嵌套GELU算子），见`hls/remote_v2_operator_counts.json`。RTL封装名称7/4的延迟参数与BIND_OP请求6/3不是可直接混用的同一口径。
- 小范围RTL cosim已通过：**10事务、30有效token**，覆盖1/3/7/8/9、重载/复访、zero/bias和三个非法计数；Verilog Pass且C后检查通过，实际exit0、约2390秒墙钟。报告合计929,038仿真cycles、最长事务169,903cycles，不能当作吞吐。**未覆盖659/660-token RTL。**

## 下一层验收与停点

独立HLS IP实现已在`D:/io8`完成，实际exit 0（1488 s）：10 ns/100 MHz约束下WNS **+2.435662 ns**、WHS **+0.034 ns**、TNS/THS **0**、未路由nets **0**、routing errors 0，post-route CP **7.564 ns**。routed资源为LUT **51146**、FF **46657**、DSP **333**、BRAM 18K等效 **131**（= 64 RAMB36 + 3 RAMB18，即 **65.5个36K tiles**，不是131 tiles）、URAM 0。该实现仍有 **127个输入/280个输出端口缺外部I/O delay（TIMING-18 427条）**，所以只是OOC IP内部时序通过，**不是KV260全系统/接口时序签核**；它也不是PS+PL系统Overlay，更不是上板加速。正式r03源码和板上工件没有被此候选替换。

本轮没有新Vivado系统bit/hwh/XSA、没有下载新Overlay、没有启动新PL计算，不把关闭ORT空转的CPU收益与r04静态估计叠加成虚构端到端成绩。

原始证据：`hls/remote_final/`（191文件SHA校验通过，含v1/v2/v2_ooc）；`hls/remote_partial01/`（172文件SHA校验通过的实现前快照，保留v1失败）。最终IP zip为`hls/remote_final/v2_ooc/work/ffn_accel.zip`，85条目，SHA-256 `3e7eb615f7fc0ecf61ad5e56443eab6f4665e698045cef2565a4990e340e4185`；它是IP目录包，不是系统bit/Overlay。`hls/v2/remote_summary.json`已含最终OOC与独立验收摘要。主机详细记录在`hls/local_validation_summary.json`及`hls/v2/local_validation_summary.json`，原r03基线和失败尝试均保留。
