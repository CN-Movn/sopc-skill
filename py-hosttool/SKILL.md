---
name: py-hosttool
description: "Build, refactor, review, test, and package stable PySide6 desktop host tools and control/debugging applications for FPGA, SoPC, embedded-board, and serial/protocol workflows. Use when Codex should create a Python host tool that follows the proven ArqMinSystem_v1.1 and MasterController_v1.4 design language: custom frameless Windows chrome with pin/minimize/maximize/close controls, instrument-dashboard or protocol-workbench layouts, reusable serial console, threaded pySerial I/O, protocol parsing, diagnostics, logs, automated workflows, pytest coverage, and PyInstaller delivery."
---

# Python 上位机开发 Skill

本 skill 用于指导 coding agent / Codex 创建、改造、审核和交付 PySide6 上位机。其设计基线来自：

- `ArqMinSystem_v1.1`：设备连接、配置控制、一键流程、节点诊断、性能监测、操作/诊断日志；
- `MasterController_v1.4`：协议帧生成与解析、通用串口工作台、子串口窗口、彩色日志、周期发送、Windows 单文件打包。

目标不是机械复制两个旧工程，而是提炼其稳定架构、视觉语言和可复用资产，使新上位机可以快速起步并保持一致体验。

## Core Workflow

1. **先确认事实**：协议、帧格式、串口参数、寄存器表、刷新周期、设备数量、目标平台和交付方式不明确时，不要自行补关键字段。
2. **选择信息架构**：
   - 设备诊断/控制型工具优先采用 `instrument-dashboard`；
   - 指令生成/解析/串口调试型工具优先采用 `protocol-workbench`；
   - 纯串口或多串口工具复用 `serial-console` 与子窗口模式。
3. **先搭稳定外壳**：复用无边框窗口、自定义标题栏、窗口置顶、原生拖动、双击最大化和 Windows 边缘缩放。
4. **分离业务层**：GUI、串口线程、协议编解码、请求客户端、服务/工作流、数据模型、诊断规则分别放置，不把所有逻辑堆进一个窗口类。
5. **复用通用资产**：优先从 `assets/template/` 起步；需要追溯成熟实现时再查看 `assets/reference_projects/`。
6. **替换业务协议**：可以复用通道、日志、布局和状态管理，不得机械沿用 ARQ/MCP/主控协议、寄存器或业务提示语。
7. **验证再交付**：至少完成静态编译、协议单测、窗口 offscreen smoke test、断连/重连与关闭路径检查；只有实际运行过的项目才可声称通过。
8. **最后打包**：使用受控的 PyInstaller spec；不要用无差别 `collect_all()`，不要把构建缓存或 EXE 放回 skill。

## Hard Rules

- 保持现有设计语言，除非用户明确要求更换风格。
- 标题栏按钮必须使用矢量绘制或稳定图标资源，不依赖系统字体中的“— □ ×”字形。
- 无边框窗口必须保留拖动、双击最大化、最大化/还原图标切换和 Windows 四边四角缩放。
- 切换 `WindowStaysOnTopHint` 后必须恢复可见性与窗口状态；多串口窗口需要统一置顶状态时，使用共享窗口组。
- GUI 线程不得直接执行阻塞串口读写、长轮询、sleep 或大批量协议处理。
- 串口对象只允许由串口工作线程持有；GUI 通过线程安全命令队列和 Qt signals 交互。
- 协议流必须处理分段、粘包、噪声前缀、CRC/checksum 错误和剩余缓存，不能假设一次 `read()` 等于一帧。
- 周期刷新、周期发送和自动流程必须有取消/停止路径；断连时清除 busy、timer、pending request 和差分基线。
- 计数器差分必须考虑回绕、硬件清零和操作后重新建基线，不能制造虚假吞吐尖峰。
- 写寄存器必须依据访问属性和写掩码建立白名单，RO/保留位/脉冲寄存器不能被通用写入口误写。
- 日志同时服务“现场操作”和“问题复盘”：保留时间戳、方向、原始数据、语义结果、错误原因与导出能力。
- 不把 `release/`、`build/`、`dist/`、`.pytest_cache/`、`__pycache__/`、EXE、日志和用户环境绝对路径打包进 skill。
- 不声称未实际执行的 GUI、串口、设备联调或 PyInstaller 打包已验证通过。
- 不因复用模板而保留旧项目名称、版本号、设备名、默认路径、协议字段或 ARQ 专有文本。

## Design Baseline

默认视觉基线：

- Windows 桌面工具、浅色工业仪表风格；
- `Microsoft YaHei UI` 9 pt，HEX/日志使用 `Consolas`；
- 外框 `#85898f`，画布 `#f4f6f8`，标题栏 `#f3f6f9`，卡片/GroupBox 白色；
- 主文本 `#202124`，说明文字 `#59636e`，强调蓝 `#0b57d0`；
- 成功 `#188038`，警告 `#e37400`，错误 `#d93025`，关闭悬停 `#e81123`；
- 标题栏高度 36 px，窗口按钮 46×35 px，内容边距约 10–12 px；
- 用 `QSplitter`、固定侧栏宽度、最小宽度和 stretch factor 控制布局，不用大量绝对坐标。

## Reference Routing

- 整体视觉、颜色、字体、尺寸和交互：`references/ui_design_language.md`。
- 自定义标题栏、置顶、拖动、最大化和边缘缩放：`references/window_chrome.md`。
- 两类主布局与子串口窗口：`references/layout_patterns.md`。
- 串口线程、端口刷新、ASCII/HEX、周期发送和彩色日志：`references/serial_logging.md`。
- GUI/协议/服务/工作流/模型的分层：`references/architecture_workflow.md`。
- 哪些资产可直接复用、哪些必须替换：`references/reuse_boundaries.md`。
- 测试、静态检查、PyInstaller 和交付边界：`references/verification_and_delivery.md`。
- 模板、示例、原始工程资产索引：`references/asset_catalog.md`。
- 开工前需要向用户确认的关键事实：`review_questions.md`。

## Asset Routing

- 新项目优先从 `assets/template/py_hosttool_template/` 复制。
- 使用 `scripts/bootstrap_project.py` 可生成重命名后的新工程骨架。
- `assets/reference_projects/` 是两个来源工程的清理版源码，只用于查阅、对照和定点复用。
- 任何从来源工程复制的业务模块，都必须先执行“名称、协议、路径、版本、默认参数”残留检查。
