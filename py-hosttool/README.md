# py-hosttool

面向 FPGA / SoPC / 嵌入式板卡的 PySide6 上位机开发 skill。

Current release: v1.2.2

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
  --baudrate 921600 \
  --layout workbench
```

`--baudrate` 只设置串口下拉框的初始值，但必须显式提供并按目标设备文档确认；脚手架不会从来源工程猜测该参数。

布局可选：

- `workbench`：左侧业务/协议区，右侧成熟串口工作台；
- `dashboard`：左侧连接/配置/操作区，右侧诊断/性能区。

模板默认只提供可运行的通用 UI/窗口外壳与串口工作台能力，不携带 ARQ 或主控业务协议；dashboard 的连接、协议 client、寄存器模型、诊断采集和自动流程仍是项目必须实现的边界，不能把模板占位控件当成业务成品。

## 统一发布流程

开发阶段按以下顺序维护版本信息：

```text
开发阶段
   ↓
CHANGELOG 顶部维护 Unreleased
   ↓
功能/规则/测试完成
   ↓
判断 PATCH / MINOR / MAJOR
   ↓
更新 VERSION
   ↓
将 Unreleased 转为带日期的正式版本
   ↓
新建空 Unreleased 区域供下一轮开发使用
   ↓
README 当前版本同步
   ↓
manifest 同步（若维护文件清单）
   ↓
运行 validator / tests
   ↓
打包 / commit / tag
```

每次正式发布时，`VERSION`、CHANGELOG 正式标题、README 当前版本和 `manifest.txt` 必须同步；版本元数据检查、bootstrap/validator 与相关测试全部通过后再提交。推荐使用带 Skill 名称的 tag，例如 `py-hosttool-v1.2.2`，避免与同仓库的其他 Skill 混淆。

版本规则采用 Semantic Versioning：规则修正、checker 修复、测试增强、模板 bugfix 和兼容性工程增强使用 PATCH；新增向后兼容能力使用 MINOR；改变核心行为合同或兼容性时才使用 MAJOR。
