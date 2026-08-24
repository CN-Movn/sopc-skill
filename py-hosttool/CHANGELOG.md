# Changelog

## Unreleased


## [1.2.2] - 2026-08-24

### Added

- 增加 staging/生成标记保护的 bootstrap 路径与纯 Python 回归测试。
- 为模板补充 `--smoke-test` 入口，并把 frozen smoke 纳入交付验证路径。

### Changed

- 收紧 bootstrap 的 `--force` 安全边界，安全注入应用名、版本和显式确认的初始波特率，生成后执行无字节码 AST 语法检查。
- 串口模板增加有界发送队列、控制命令优先级、写超时、部分写检查、异常关闭和可验证 shutdown。
- 默认构建先验证 onedir；同时明确模板、来源工程、transport/parser/client 的复用边界，并补充严格验证要求。

### Fixed

- ASCII/HEX 日志始终保留原始 bytes，并将通用串口计数明确为 TX 操作次数。

## 1.2.1 - 2026-08-07

- 将当前 `py-hosttool` 发布版本更新为 `v1.2.1`；其余功能内容保持不变。

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
