# Changelog

## 1.2.0 - 2026-08-07

- 清理 starter template 中来源项目名/版本号 provenance 注释，确保新工程只保留通用设计语义。
- `validate_skill.py` 增加 `MasterController_v1.4` / `ArqMinSystem_v1.1` 生成工程残留检查。
- 当 `PySide6`、`pyserial` 与 `pytest` 均可用时，validator 自动对 workbench/dashboard 生成工程执行 pytest；依赖缺失时明确打印 skipped。
- `skills-ref` 可用时记录通过状态，不可用时明确打印 skipped。

## 1.1.0 - 2026-08-07

- 忠实恢复 MasterController_v1.4 串口日志语义：灰色头部、RX 绿 `#237a32`、TX 蓝 `#1565c0`。
- 恢复依据 viewport 与 font metrics 的动态 HEX 行宽，并在 resize 后重排历史日志。
- 恢复“仅在用户位于底部时自动跟随”的滚动行为。
- 恢复周期发送“立即首发 → timer → 发送/停止发送按钮状态机”，串口异常会终止活动周期并恢复断开显示。
- GUI smoke test 改为测试公开 `MainWindow`，避免 dashboard 误测 WorkbenchWindow，并增加串口资产回归断言。
- `reference/` 统一为标准惯例 `references/`。
- Dashboard 最小宽度统一为 1460 px，与左右区域最小宽度约束一致。
- `validate_skill.py` 增加 Agent Skill name/description/父目录一致性、manifest、双布局 bootstrap 与静态编译检查。
- SKILL description 将中式 `upper-computer` 改为 `desktop host tools and control/debugging applications`。


## 1.0.0 - 2026-08-07

- 基于 ArqMinSystem_v1.1 与 MasterController_v1.4 创建首版。
- 提炼统一 PySide6 视觉语言、无边框窗口和两类布局。
- 提供可运行的新项目模板、串口工作台、仪表盘示例和创建脚本。
- 保留两个来源工程的清理版源码，排除 EXE、缓存与构建产物。
