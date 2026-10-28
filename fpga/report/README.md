# FPGA 报告索引

- `hls_fixed_acceptance01.json`：2026-10-20 的 HLS C、综合、4 事务 RTL、单 IP OOC 资源/时序汇总。
- `ooc_timing_routed01.rpt`：单 IP 布线后时序原始文本。
- `pl_board_20261021.md`：2026-10-21 KV260 Overlay、真实 PL 数值与性能说明。

边界：OOC 只证明单 IP 在指定约束下的内部实现结果，缺外部 I/O delay，不是整系统时序签核。120 帧全量 RTL、post-route 功能仿真和长时稳定未执行。更完整的小型板测 JSON 在 `../../metrics/`，本目录不复制大型证据 ZIP、DCP、录像或训练数据。
