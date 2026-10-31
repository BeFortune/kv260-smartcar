# 交付脚本

在仓库根目录运行：

```bash
python metrics/scripts/build_metrics.py
python metrics/scripts/build_delivery.py
python metrics/scripts/verify_delivery.py
```

- `build_metrics.py`：只从当前主方案证据重建 `metrics.csv`。
- `build_delivery.py`：从相邻开发主源 `pynq_ffn/` 和固定左原始 CSV 复制最新白名单，并刷新 `delivery_manifest.json`。独立 GitHub 检出没有这些源目录时，只运行校验，不执行复制。
- `verify_delivery.py`：核对当前目录、清单、工件/模型哈希、链接、语法、大小和敏感路径，不导入 PYNQ 或打开设备。

白名单中的交付副本会被重新复制覆盖；修改实现应先回开发主源修改并验证。新增文档后重新运行 `build_delivery.py` 刷新清单。
