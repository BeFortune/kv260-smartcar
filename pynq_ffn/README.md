# PYNQ FFN 迁移包



面向现有资料中的 **KV260/K26**：原模型拆成五段 CPU ONNX 子图，四个 `128→256→128`、erf-GELU FFN 通过统一后端接口替换。Attention、LayerNorm、残差、预处理和输出头留在 PS。原工程保持不变。


## 阅读顺序

1. [文件分析与迁移决策](docs/01_文件分析与迁移决策.md)：目录、模型、数据、历史板端状态、ROS风险与范围。
2. [FFN 接口与流水线设计](docs/02_FFN接口与流水线设计.md)：四层边界、AXI与寄存器、缓存/超时、流水线优化。
3. [AMD 与 Ross 从零操作指南](docs/03_AMD与Ross从零操作指南.md)：安装与许可、PS部署、HLS、Vivado、PYNQ、Ross。
4. [验证记录](docs/04_验证记录.md)：真实命令、测试、误差、未完成硬件验收。
5. [逐步 Notebook](notebooks/01_ps_to_pl.ipynb)：默认只跑 CPU，PL 下载需要显式启用。
6. [独立交付包说明](DELIVERY.md)：ZIP内容、哈希清单，以及缺少原载荷时的复现边界。

## 最快跑通（不用板卡）

在本目录打开 PowerShell，使用 Python 3.10～3.12。下面以 Windows Python 3.12 为例：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest tests -q

.\.venv\Scripts\python.exe tools\validate_split.py `
  --bundle artifacts\bundle_20261014 `
  --recording artifacts\sample_recording_1779374497 `
  --random-cases 3 `
  --output artifacts\my_validation.json

.\.venv\Scripts\python.exe -m ps.replay `
  --bundle artifacts\bundle_20261014 `
  --recording artifacts\sample_recording_1779374497 `
  --backend split-ort --threads 4 `
  --output-dir artifacts\my_replay
```

这些输出路径必须尚不存在；重跑换新名字。样本只有一组三摄，用于快速连通和等价检验，不是驾驶准确率测试集。

本轮48项测试是在相邻 `kv260_v1/v1_payload.zip` 和历史 `first_input.npy` 都存在时通过的，**没有skip**。如果只复制本目录或使用独立交付ZIP，依赖原载荷的集成测试会明确skip，不能称为复现48项全覆盖；已附bundle与样本仍可直接用于 `validate_split.py` 和 `ps.replay`。

本次已经生成 bundle，也可以从原 payload 重建：

```powershell
.\.venv\Scripts\python.exe tools\prepare_model.py `
  --payload ..\kv260_v1\v1_payload.zip `
  --output-dir artifacts\my_bundle
```

准备脚本只读取指定成员 `model/model_current.onnx`，核验固定 SHA-256：

```text
ad58d1fc9168da94b9ce076ba8cc9754c371ead87974da8bc9cc601ad1650d5b
```

新训练模型需要重新审查切图，不提供跳过此检查的开关。bundle 所有文件均有哈希清单；运行时拒绝被修改或混搭的成员。

## 三种 CPU 模式与 PL 注入

| 模式 | 实际执行 |
|---|---|
| `golden` | 原始完整模型，CPU ORT |
| `split-ort` | 五段 PS + 四段 FFN，全部 CPU ORT；用于检查切图 |
| `split-numpy` | 五段 PS 用 CPU ORT，FFN 用 exact-erf NumPy；用于检查后端合同 |

```python
from ps.runtime import Runtime

runner = Runtime("artifacts/bundle_20261014", mode="split-ort", threads=4)
outputs = runner.run(images)  # float32 [1,3,3,66,200]
print(outputs["speed_raw"])  # [1,2]，不要再乘300/500
print(runner.last_timings_ms)
```

四输出均保留：`speed_norm`、`speed_raw`、`region_logits`、`region_probs`。区域输出一致不等于该头具有经验证的分类准确率。

PL 对接是 `Runtime(..., mode="split-numpy", backend=pynq_backend)`；模式名表示可替换路径，**只有显式注入 `PynqFFN` 才访问 PL**。该后端接收已经由调用者明确加载的 IP，不自行下载 Overlay。详细示例与板前提见教程/Notebook；请勿把演示路径或实例名当成已探测结果。

## 代码地图

```text
ps/
  partition.py       固定模型边界/拓扑验证、五段PS与四段FFN导出
  runtime.py         CPU整图/分图、live tensor调度、后端注入
  backends.py        NumPy exact-erf FFN
  preprocess.py      原NUC一致的三摄预处理和文件读取
  replay.py          离线录制推理，仅写结果文件
  pynq_backend.py    AXI-Lite、64位地址、缓存同步、超时隔离

tools/
  prepare_model.py   生成新bundle
  validate_split.py  最终四输出、整数轮速、四FFN切点严格对照
  validate_cpp.py    主机C++参考核与真实权重对照，不是PL
  validate_payload.py 直接读原ZIP完成173组三摄全量等价验证
  board_preflight.py 只读本机环境，不导入PYNQ/不装载硬件

hls/
  ffn_accel.cpp/.hpp 浮点FFN正确性参考核，不是已优化的实时流水线
  testbench.cpp     边界/尾token/权重重载/独立double参考
  build_hls.tcl     传统HLS入口，须给真实FPGA_PART
  make_config.py    现代2026.1 cfg生成，已核实64位地址键；构建受许可阻塞
  host_bridge.cpp  主机验证专用；不要加入HLS综合
```

`hls/build/*.exe/*.dll` 是 Windows 主机测试产物，不是 FPGA bitstream，也不能复制到 ARM 当板端程序执行。普通运行时不加载这些文件。

## 当前关键限制

- **2026-10-15 更新：板端 bring-up 已完成第一轮**——overlay 在真实 KV260（PYNQ 3.0.1）加载成功，四层真权重 PL 对比（误差 ≤2.68e-7）与 `Database` 真值录像 117 帧混合推理（整数轮速与 CPU 全一致）通过；细节与证据见 `docs/04_验证记录.md` 第 17 节与 `artifacts/board_20261015/`。板端 CMA 健康（`cma=256M` 已生效，CmaFree >249 MB），历史 CMA 预留失败问题在该板当前状态不存在。
- **性能是诚实的短板**：整帧 split-PL 中位 ~2497 ms，慢于纯 CPU 切图（~343–448 ms）约 6 倍，更远慢于 golden 整图 CPU（~71 ms）；两个 660-token camera 层各实测 ~1156 ms。当前参考核对串行浮点、II=4，不能承诺实时或加速，加速属于后续优化工作。
- HLS 阻塞已解除：2026-10-15 retry04 使用指定路径 AMD 2026.1 工具与 Enterprise 许可完成了 C simulation、C synthesis、1-token RTL cosim 冒烟与 `--impl`；KV260 系统级 Vivado 综合/实现/时序（WNS +2.338 ns）通过，同次导出 `ffn_overlay_wrapper.bit/.hwh/.xsa`（`artifacts/vivado_20261015/retry04/overlay/`）。完整 659/660-token RTL cosim 仍未完成，板上数值通过不替代 RTL 覆盖。
- NumPy FFN 与 PS 分图有额外开销；功能正确不代表加速。
- 不包含实时相机同步、ROS ARM重建或底盘串口控制；原车控有已记录安全问题，不能原样直接启动。
- 原固件/CMA/boot 未被修改；overlay 加载与 PL 编程以 `XILINX_XRT=/usr`、venv `xclbinutil` 前置和 sudo 运行（详见 `artifacts/board_20261015/README.md`）。

项目资料中的实际目标仍需确认：最终允许误差/帧率门槛、是否继续保持 KV260 镜像、后续是否将参考核替换为优化核。基础软件可以在这些确认之前离线运行；硬件验收不能省略它们。

## 许可证

本项目原创代码采用 [MIT License](LICENSE)，版权署名为 `2026 BeFortune`。第三方依赖、模型、数据及 AMD/Xilinx 生成内容的授权仍以各自来源的许可为准；本许可证不替代第三方授权。
