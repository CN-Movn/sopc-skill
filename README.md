<div align="center">

# sopc-skill ⚙️

### 面向 SoPC / FPGA / 嵌入式工程的 AI Skill 集合

把真实工程中反复验证过的约束、模板、检查脚本和复用边界沉淀为 coding agent 可执行的能力。

<p>
  <img src="https://img.shields.io/badge/Domain-SoPC%20%2F%20FPGA-4c78a8?style=flat-square" alt="Domain">
  <img src="https://img.shields.io/badge/RTL-Vivado%20%2F%20Verilog-2f855a?style=flat-square" alt="RTL">
  <img src="https://img.shields.io/badge/HostTool-PySide6-7b61ff?style=flat-square" alt="Host Tool">
  <img src="https://img.shields.io/badge/Agent-Codex-a35ac7?style=flat-square" alt="Agent">
  <img src="https://img.shields.io/badge/Release-v0.1.5-555?style=flat-square" alt="Release">
</p>

<p>
  <a href="#-为什么有这个仓库">为什么有这个仓库</a> ·
  <a href="#-当前-skill">当前 Skill</a> ·
  <a href="#-rtl-style">rtl-style</a> ·
  <a href="#-py-hosttool">py-hosttool</a> ·
  <a href="#-使用方式">使用方式</a> ·
  <a href="#-验证与发布">验证与发布</a>
</p>

</div>

---

## ✨ 为什么有这个仓库

真实硬件工程的难点不只在生成代码，而在保持协议、时序、验证和交付边界的一致：

- RTL 要兼顾可综合语义、CDC、握手、时序结构、资源推断和可维护性；
- 上位机要把串口或字节通道、协议解析、GUI 生命周期、日志、记录与交付拆清楚；
- 没有实际运行过的仿真、综合、GUI、设备联调和打包，不能声称已经验证；
- 成熟经验应当转成模板、检查器和按需加载的 reference，避免每次从零重复。

这个仓库目前维护两个相互独立、可以单独安装的 Skill。

## 🚀 当前 Skill

本仓库当前统一发布版本为 **`0.1.5`**。

| Skill | 版本 | 适用任务 | 主要资产 |
| :--- | :---: | :--- | :--- |
| [`rtl-style`](./rtl-style) | **0.1.5** | 创建、修改、重构或评审 AMD/Xilinx Vivado Verilog RTL | 中文注释合同、Timing by Construction、AMD UG901/UG906/UG949 方法、RTL 模板、静态 checker |
| [`py-hosttool`](./py-hosttool) | **0.1.5** | 创建、改造、测试和打包 FPGA / SoPC / 嵌入式板卡 PySide6 上位机 | 无边框窗口、仪表盘/工作台布局、串口线程与日志、通用记录/回放、模板、参考工程、验证与打包规则 |

各 Skill 根目录的 `VERSION` 是当前版本的机器可读来源。根 README、Skill README 和 CHANGELOG 的一致性由仓库级脚本检查。

`deepseek-subagent` 已停止维护并从当前仓库移除；旧安装应直接卸载，不再依赖其本地 bridge、credential 或 handoff 状态。

---

## 🧠 rtl-style

[`rtl-style`](./rtl-style) 面向 Vivado / Verilog RTL 创建、修改和评审，重点让 AI 在编码前处理真正影响工程的约束。

- **注释硬合同**：模块头、FSM、握手、CDC、计数边界和 pipeline 必须说明设计意图。
- **Timing by Construction**：先识别深组合逻辑、高扇出、优先级链、RAM/DSP 和 backpressure 风险。
- **官方方法论落地**：把 AMD UG949、UG901、UG906 中可执行的规则映射成 reference 和 checker 提示。
- **模板与 preflight**：提供普通模块、AXI4-Stream registered stage 模板和 Python 静态检查器。
- **诚实验证**：静态检查、lint、仿真、综合、实现和上板结果分别报告。

适合 AXI4-Stream、FSM、scheduler、RAM/FIFO、DSP、CDC、reset、packet/video datapath 等 RTL 工作。

## 🖥️ py-hosttool

[`py-hosttool`](./py-hosttool) 面向 FPGA / SoPC / 嵌入式板卡的 PySide6 上位机。设计输入来自多套实际工具，包括串口协议工作台、设备诊断仪表盘和带离线记录/回放机制的 CAN 上位机。

- **成熟外壳**：无边框 Windows 标题栏、置顶、最大化/还原、拖动和边缘缩放。
- **两类布局**：设备诊断仪表盘与协议/串口工作台。
- **线程所有权的串口层**：GUI 不直接阻塞读写，串口句柄由 worker 持有，控制命令优先，队列有界。
- **协议与恢复边界**：分段/粘包、CRC 参数、generation、超时、迟到响应和不可幂等命令不自动重发。
- **通用记录/回放资产**：后台有界写入 JSONL + CSV，按方向回放，便于无设备复现问题。
- **验证到交付**：协议 golden vectors、offscreen GUI smoke、断连/取消路径、onedir/frozen EXE 分层验收。

CAN 上位机只作为经过过滤的设计来源。仓库不包含其业务协议、CAN ID、遥测字段、厂家 SDK、设备序列号、现场日志、EXE 或构建产物。

---

## 🧩 两个 Skill 如何组合

```text
rtl-style
  └─ 约束 RTL 的接口、注释、CDC 与时序结构
       ↓
py-hosttool
  └─ 构建配套的串口/字节通道调试与诊断工具
```

RTL 开发可以先使用 `rtl-style` 做设计门禁和 code review，再使用 `py-hosttool` 构建对应的板级控制、遥测或协议调试工具。两者也可以独立使用。

## 📁 仓库结构

```text
sopc-skill/
├── README.md
├── scripts/
│   └── validate_release_versions.py
├── tests/
│   └── test_release_versions.py
├── rtl-style/
│   ├── VERSION
│   ├── SKILL.md
│   ├── scripts/
│   ├── references/
│   └── tests/
└── py-hosttool/
    ├── VERSION
    ├── SKILL.md
    ├── scripts/
    ├── references/
    └── assets/
        ├── template/
        └── reference_projects/
```

## 🚀 使用方式

将需要的 Skill 目录作为一个完整目录引入 ChatGPT / Codex。包含 `scripts/`、`references/` 或 `assets/` 的 Skill 不应只复制 `SKILL.md`。

```text
Use $rtl-style to review this AXI4-Stream scheduler and identify timing-by-construction risks before I run Vivado.
```

```text
Use $py-hosttool to build a PySide6 diagnostic application with the reusable serial worker, bounded capture recording and offscreen GUI smoke tests.
```

## ✅ 验证与发布

在仓库根目录检查两个 Skill 的版本元数据：

```powershell
python scripts/validate_release_versions.py
python -B -m unittest discover -s tests -v
```

分别验证两个 Skill：

```powershell
python -B -m unittest discover -s rtl-style/tests -v
python py-hosttool/scripts/validate_skill.py
```

`py-hosttool` 的严格发布检查会要求 PySide6、pyserial、pytest 和可选外部 validator 均已安装：

```powershell
python py-hosttool/scripts/validate_skill.py --strict
```

验证结果必须区分静态检查、单元测试、offscreen GUI、真实 Windows UI、真实设备、frozen EXE 和目标机运行。未执行的项目明确标为未验证。

## 🔐 安全与边界

- `assets/reference_projects/` 用于追溯代码形状，不提供新项目协议和寄存器事实。
- 业务协议、CRC 变体、字节序、寄存器权限和自动重试策略必须来自用户提供的 source of truth。
- 设备日志、串口抓包、用户设置、凭据、构建目录、缓存和 EXE 不进入 Skill 仓库。
- 静态 checker、模拟 transport 和 offscreen smoke 不能替代真实设备联调或真实 Windows 交付验证。
