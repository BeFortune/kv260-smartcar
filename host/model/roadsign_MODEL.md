# 路标 YOLO 模型获取与身份

本仓库不直接附带第三方 `roadsign_best.onnx`，避免在未确认再分发权时复制 28.6 MB 模型。离线复现时由用户显式下载并核验：

- 上游仓库：`https://github.com/zhuzixuan2007/ros_craic_car`
- 固定提交：`5f28cbabff90ee318ddd0877257516fa03e95adb`
- 文件：`src/road_sign_param_server/src/best.onnx`
- 固定地址：`https://raw.githubusercontent.com/zhuzixuan2007/ros_craic_car/5f28cbabff90ee318ddd0877257516fa03e95adb/src/road_sign_param_server/src/best.onnx`
- 大小：`28577915` 字节
- SHA-256：`dff54ac921f3898fcf2f06f04662cc68b19e6034c2c9472b275e1ec01ddb22ee`
- Git blob：`317ca44183ace5561ebfc8fd5c93c412d20a4888`，仅作内容地址核对，完整性使用 SHA-256。

下载后保存为本目录的 `roadsign_best.onnx`，再用 `sha256sum roadsign_best.onnx` 核对。哈希不一致就停止，不替换为 `best.onnx.new` 或其他模型。不要覆盖已有文件。

模型输入为 float32 `[1,3,640,640]`，输出为 `[1,25200,15]`，包含 4 个框值、1 个目标分和 10 个类别分。没有锥桶类别。默认不启用 YOLO；模型存在不等于检测准确率、实时性能或车辆安全已验证。
