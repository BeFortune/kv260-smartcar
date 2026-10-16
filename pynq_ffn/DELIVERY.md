# 独立交付包说明

本包是 `pynq_ffn` 的离线 PS 迁移、FFN 接口/HLS 参考实现和操作文档交付，**不是已上板运行的 FPGA overlay**。

## 内容

- [README](README.md)：从这里开始，包含不需要板卡的快速验证。
- [文件分析与迁移决策](docs/01_文件分析与迁移决策.md)。
- [FFN 接口与流水线设计](docs/02_FFN接口与流水线设计.md)。
- [AMD 与 Ross 从零操作指南](docs/03_AMD与Ross从零操作指南.md)。
- [验证记录](docs/04_验证记录.md)：优先引用其中注明的最终报告；旧报告为过程证据。
- `ps/`、`tools/`、`tests/`：PS 软件、验证器和测试。
- `hls/`：FP32 / erf-GELU 参考核、主机验证桥及 HLS 构建入口。
- `notebooks/01_ps_to_pl.ipynb`：默认只运行 CPU，不下载 PL。
- `artifacts/bundle_20261014/`：已准备好的整图、分图、真实权重及哈希清单。
- `artifacts/sample_recording_1779374497/`：一组三摄快速验证样本。
- `hls/build/amd_20261014/`：本机 AMD 工具命令、日志及许可阻塞证据。

ZIP 内额外提供 `DELIVERY_MANIFEST.json`，逐文件记录大小与 SHA-256；ZIP 外侧提供压缩包本身的 SHA-256。哈希用于检查复制完整性，不是数字签名或可信来源认证。

## 有意不包含

原始训练数据 ZIP、原 `kv260_v1/v1_payload.zip`、历史板卡操作日志/凭据、Python 虚拟环境、缓存、Windows 主机 `.exe/.dll`、AMD 软件以及未生成的 `.bit/.hwh`。

1. 在独立副本中，使用已附 bundle 与样本运行 `tools/validate_split.py`、`python -m ps.replay` 不需要原 payload。
2. 复现173组三摄全量回归，需要自己保留的原 `kv260_v1/v1_payload.zip`，通过 `tools/validate_payload.py --zip` 指定真实路径。
3. 原48项pytest无跳过的验收使用了相邻 `kv260_v1/v1_payload.zip` 及历史 `kv260_v1/artifacts/results/single_100/first_input.npy`。独立副本中缺少这些文件时，相关集成测试会明确skip；这种结果不能称为复现48项全覆盖。
4. 重跑C++主机验证，需要按 `hls/README.md` 在目标主机重新编译，不把Windows DLL当作ARM板端依赖。
5. AMD证据目录中的 `run01_hls_config.cfg` / `run02_hls_config.cfg` 只作归档，路径对应原临时运行目录。新的构建副本必须重新运行 `hls/make_config.py` 生成配置。

## 验收边界

已完成：173组三摄CPU等价回归、两套依赖环境各48项测试、主机C++参考核及真实权重对照、Notebook默认CPU分支执行。

未完成：板端PS实跑、成功的HLS C simulation/C synthesis、RTL cosim、Vivado实现、bit/hwh、PL数值与性能、实时相机/车辆控制。本机真实Vivado返回exit42，HLS csim/compile均返回exit1，阻塞于part/有效许可证初始化。软件结果不能代替上述硬件验收。

## 2026-10-15 更新（数字说明在此包之上）

本 ZIP 描述的"未完成"清单已于当日之后大幅推进，请以 `docs/04_验证记录.md` 第 15–17 节与 `artifacts/` 新增目录为准：

1. HLS/Vivado 阻塞解除（retry04）：C simulation/C synthesis/1-token RTL cosim 冒烟/`--impl` 全部通过；KV260 系统级综合/实现/时序通过，产出真实 `artifacts/vivado_20261015/retry04/overlay/ffn_overlay_wrapper.bit/.hwh/.xsa`。
2. 板端 bring-up 完成：真实 KV260 上 overlay 加载、真权重四层 FFN PL vs ORT（≤2.68e-7）、`Database` 真值录像 117 帧混合推理（整数轮速与 CPU 全一致）、三模式计时；证据在 `artifacts/board_20261015/`。
3. 计时诚实结论：串行参考核 PL 路径整帧中位 ~2497 ms，慢于纯 CPU（~411 ms）与 golden（~71 ms），无加速收益。
4. 仍未完成：完整 659/660-token RTL cosim、实时相机/实车控制、多录像全量板上回归、功耗/温度/长时稳定性。

所有默认运行入口只处理文件；原固件/CMA/boot 均未被修改，车辆动作从未发生。PYNQ 超时不等于硬件停止，未确认 AXI master 停止前不得释放缓冲区或退出其持有进程。本轮板上加载通过 notebook 显式开关之外的测试脚本完成，仍遵守同一安全合同。
