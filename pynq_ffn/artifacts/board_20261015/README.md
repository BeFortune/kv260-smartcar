# 板端 bring-up 证据（2026-10-15）

本目录是 **pynq_ffn 首次真实上板 bring-up** 的证据归档：KV260 板卡（192.168.110.200，ubuntu/PYNQ 3.0.1 镜像）上加载 retry04 overlay，并用真实权重完成四层 FFN 数值对比、真值数据集混合推理与三模式计时。这些结果补充 `docs/04_验证记录.md` 第 17 节。

## 结果一句话

- overlay 加载成功、寄存器表经 `validate_registers` 通过、CTRL=0x00000004（IDLE）。
- 四层 FFN（camera_0/camera_1 各 660 token、cross_0/cross_1 各 4 token）真实权重 PL vs CPU ORT：**最大绝对误差 7.45e-8**（门槛 1e-4）。
- `Database/record_data_5.16.zip` 真值录像 `1778852165`（117 帧）整帧 split-PL 混合推理：**全部帧 PL 与 CPU ORT 整数轮速一致**，逐帧逐层 1e-3 门禁通过。
- 计时（8 帧、warmup 后）：split-PL ~2497 ms/帧、split-ort ~343–448 ms/帧、golden CPU ~71 ms/帧。**PL 参考核比 CPU 慢**（两个 660-token 层各实测 ~1156 ms 硬件等待），符合文档"参考核无加速承诺"的预期。

## 文件

| 文件 | 内容 |
|---|---|
| `pl_contract_20261015.json` | 加载、IP 表、phys_addr=0xA0000000、CTRL、时钟、CMA |
| `pl_layers_20261015.json` | 四层 1/4/660-token 对比、重访、权重篡改拒绝、0/661/float64 门禁、缓冲区地址、正常 close |
| `pl_hybrid_20261015.json` | 8 帧混合推理首个证据（最终目录版另存为 `pl_hybrid_117f_20261015.json`） |
| `pl_timing_20261015.json` | 8 帧 split-PL vs split-ort 分段计时 + golden warm |
| `board_ffn_test.py` | 板端测试脚本（本轮实际执行版本，含 117 帧命名修正） |
| `push_files.py` / `sshboard.py` | 传输与 SSH 通道脚本（paramiko；板卡密码不入库，使用前 `export FFN_BOARD_PASSWORD=...`）|

## 板端环境（实测）

- Ubuntu 22.04.4 aarch64，内核 `5.15.0-1027-xilinx-zynqmp`，设备树 model "ZynqMP KV260 revB"。
- PYNQ 3.0.1 venv `/usr/local/share/pynq-venv`（root 所有）：numpy 1.26.4、opencv 4.11、pynq 3.0.1。本轮经 `sudo pip install 'onnxruntime==1.20.1'` 补装（安装后 numpy/pynq 版本未变）。
- XRT 2.13.479（系统 `/usr`）；`/usr/bin/xclbinutil` 为坏壳脚本（执行即段错误），**必须 PATH 前置 `/usr/local/share/pynq-venv/bin/xclbinutil`**（上次部署留下的健康副本）。
- 启动参数含 `cma=256M`，`CmaFree` 全程 >249 MB。

## 复现要点（板端执行）

```bash
sudo env XILINX_XRT=/usr PATH=/usr/local/share/pynq-venv/bin:$PATH \
  PYTHONUNBUFFERED=1 /usr/local/share/pynq-venv/bin/python3 /home/ubuntu/ffn_migrate/board_ffn_test.py --stage contract
# 同 --stage layers / hybrid --frames N / timing --frames N
```

三条本机经验（换镜像/重装时都可能遇到）：

1. **加载目录只能放 bit+hwh 成对文件**。同目录有同名 `.xsa` 时，PYNQ 3.0.1 的 `is_xsa()` 会把 `.bit` 当成 XSA 解析，报 `UnknownInputFileExtension`。XSA 已移至 `xsa_traceability/`。
2. **XILINX_XRT=/usr 必须显式导出**（SSH 非登录 shell 不运行 `/etc/profile.d/pynq_venv.sh`），否则 "No Devices Found"。
3. **时钟/PL 编程需要 sudo**；xclbinutil 用 venv 副本。

## 边界与不做声明

- 板上无摄像头/底盘参与；数据全部来自预先传输的 `D:\Learning\FPGA\Database` 录像（`joystick.csv` 按 `auto_data.csv` 五列格式使用）。
- `recorded_*` 是操纵杆指令值，不是独立真值；`pred_vs_recorded_*` 只是模型预测偏差记录，与 PL/CPU 等价性无关（等价性由逐帧 PL vs CPU ORT 断言保证）。
- 板上 opencv 为 4.11（主机基线 4.10）：等价性判断以逐帧 PL vs ORT 及输出门禁为准；这不构成跨实现准确率证明。
- 未验证：完整 659/660-token RTL cosim（板上数值通过不替代 RTL 覆盖）、相机实时链路、车辆控制、长期稳定性/功耗/温度。
- 全部结果为一次性运行证据；重跑必须使用新的结果文件名（脚本强制要求，不得覆盖历史）。
