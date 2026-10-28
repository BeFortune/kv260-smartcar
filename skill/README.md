# Skill 交付状态

## 当前结论

**完整通用 `amd-pynq` 技能包尚未交付。** 本地只找到 `.agents/skills/amd-pynq/SKILL.md` 客户端发现入口，它指向当时不存在的根目录 `skill/amd-pynq/SKILL.md`。只复制该入口会产生失效链接，因此本包不把它冒充为完整技能正文。

## 已可复用的工程材料

- HLS 配置与四组验证向量：`../fpga/src/`
- bit/HWH/模型哈希门禁：`../docs/interface.md`
- 分层验证报告：`../fpga/report/` 与 `../metrics/`
- 交付白名单和哈希脚本：`../metrics/scripts/build_delivery.py`
- 离线校验脚本：`../metrics/scripts/verify_delivery.py`
- 车端可复现打包器：`../host/deploy/make_bundle.py`
- 失败诊断示例：`../metrics/evidence/field_turning_diagnosis.md`

这些材料可作为后续技能提炼的输入，但“脚本存在”不等于已经在第二块板卡或干净环境验证通用性。

## 后续完整技能必须补齐

每项写清适用场景、输入/前提、使用方法、输出、失效条件、已验证效果和来源失败/观察；区分通用 PYNQ 步骤与 KV260、当前模型、车控协议的专用案例。未完成前，本目录只作为诚实的缺项占位和索引。
