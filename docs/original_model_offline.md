# 原 NUC 模型优化：2026-10-29 离线候选

## 结论

保留原权重、预处理、左/前/右顺序和四输出的布局优化、准备式后端及 128 通道主机核已经完成离线联检；**原模型 PS+PL 持续 15 Hz 仍阻塞，候选不可部署。**

## 已验证

- 最终运行器对 173 组固定迁移数据和 635 组现场数据分别回归：完整候选及 CPU 分图相对原模型四输出最大差均为 0，速度整数 0 分歧，跳过 0。
- 本机 CPU 完整推理均值 6.0258→5.6416 ms，减少约 6.38%；但最大值 7.4283→9.7014 ms，关闭 ORT 图优化时候选也略慢，不能外推 KV260。
- 准备式后端把 660-token 热调用权重哈希处理 263,680→0 字节，flush 请求 675,840→337,920 字节；这是假缓冲计数，不是硬件带宽测量。
- 128 通道核与旧核主机比较 700,672 个 float32 位差 0；没有 HLS/RTL/实现证据。
- 12 帧跨组件主机联检 `speed_raw` 最大差 0.0000610，整数 0 分歧；不是 635 帧真实 PL。

## 交付位置与边界

- 候选运行源码：`../host/app/candidates/original_model_pspl/`
- 候选模型：`../host/model/candidates/original_model_pspl/bundle01/`
- 主机 FFN 核：`../fpga/src/candidates/original_model_ffn128/`
- 证据：`../metrics/evidence/original_model/`

核心板不可用和本机无 AMD 工具使新 HLS、Overlay、真实 PL 与 15 Hz 验收阻塞。没有将这些文件接入当前生产路径，也没有改变当前 conv_front_int8 基线。
