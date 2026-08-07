# py-hosttool

面向 FPGA / SoPC / 嵌入式板卡的 PySide6 上位机开发 skill。

它从 `ArqMinSystem_v1.1` 与 `MasterController_v1.4` 中提炼了：

- 自定义无边框 Windows 标题栏；
- 置顶、最小化、最大化/还原、关闭与原生边缘缩放；
- 设备诊断仪表盘和协议串口工作台两类布局；
- 可独立复用的成熟串口资产：参数区、RX 绿/TX 蓝日志、动态 HEX 排版、滚动保持、发送区、周期发送状态机与子串口窗口；
- QThread + 命令队列的 pySerial 所有权模型；
- 协议、服务、工作流、诊断、性能模型和 GUI 分层；
- pytest、offscreen smoke test 与 PyInstaller 交付约束。

## 目录

- `SKILL.md`：coding agent 的入口规则。
- `references/`：详细设计规范与复用边界。
- `assets/template/`：可直接复制的新项目模板。
- `assets/reference_projects/`：两个来源项目的清理版源码。
- `scripts/bootstrap_project.py`：创建新工程。
- `scripts/validate_skill.py`：检查 skill 结构和禁入产物。

## 快速创建工程

```bash
python scripts/bootstrap_project.py D:/Workspace/Python/MyHostTool \
  --app-name MyHostTool \
  --version 0.1.0 \
  --layout workbench
```

布局可选：

- `workbench`：左侧业务/协议区，右侧成熟串口工作台；
- `dashboard`：左侧连接/配置/操作区，右侧诊断/性能区。

模板默认只提供通用外壳与串口能力，不携带 ARQ 或主控业务协议。
