# 交付脚本

在仓库根目录运行：

```bash
python metrics/scripts/build_metrics.py
python metrics/scripts/build_delivery.py
python metrics/scripts/verify_delivery.py
```

- `build_metrics.py`：读取随附证据，重建 `metrics.csv`。
- `build_delivery.py`：从相邻开发主源 `pynq_ffn/` 重新复制审查过的白名单，并刷新 `delivery_manifest.json`。没有该主源的独立 GitHub 检出不应执行复制步骤，只需运行校验。
- `verify_delivery.py`：只读取交付目录，不依赖开发主源，不导入车端运行时或 PYNQ。

`build_delivery.py` 会覆盖白名单中的交付副本，手工修改这些副本前应先回开发主源修改并重新验证。新增生成文档后需重新运行它刷新清单。
