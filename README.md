# sopc-skill ⚙️

> 面向 SoPC / FPGA / 嵌入式板卡与 Codex 多 Agent 工作流的自定义 AI Skill 集合

<div align="center">

### 让 AI 更懂 FPGA 工程，也更懂工具开发与 Agent 协作边界

面向真实工程场景沉淀的可复用 Skill：覆盖 **Vivado RTL 开发与评审、PySide6 上位机开发、Codex DeepSeek 子 Agent 集成**。

</div>

---

## 📖 项目简介

`sopc-skill` 是一个面向 **FPGA / SoPC / 嵌入式开发与 AI coding agent 协作** 的自定义 Skill 仓库。

仓库关注的不是让 AI “多写一点代码”，而是把真实工程中反复验证过的约束、方法论、模板、检查脚本和协作边界固化下来，让 Codex / ChatGPT 等 coding agent 在参与工程开发时更加稳定、可维护、可复核。

核心原则包括：

- 不虚构协议、寄存器、时钟、复位、接口和性能事实；
- 不随意破坏已经验证的接口与工程边界；
- 没有实际运行仿真、综合、实现、GUI、串口或打包时，不声称已经验证通过；
- 对 AXI4-Stream、CDC、pipeline、RAM/DSP inference、timing closure 等 FPGA 风险保持敏感；
- 对 GUI、串口线程、协议层、日志、工作流和打包边界进行工程化分层；
- 对 Codex 父/子 Agent、Provider、外部服务和凭据边界进行明确约束；
- 优先把高频、确定性的检查固化为脚本，把详细知识按需放入 references，减少上下文浪费。

---

## ✨ 当前 Skill

| Skill | 方向 | 说明 |
| :--- | :--- | :--- |
| [`rtl-style`](./rtl-style) | Verilog RTL | AMD/Xilinx Vivado RTL 编码、评审、中文注释与 Timing by Construction |
| [`py-hosttool`](./py-hosttool) | Python / PySide6 | FPGA / SoPC / 嵌入式板卡桌面上位机、串口/协议工具与交付规范 |
| [`deepseek-subagent`](./deepseek-subagent) | Codex 子 Agent | 通过本地兼容桥为 Codex 提供 DeepSeek 子 Agent，并约束持久化生命周期 |

> 原 `matlab-toolkit` 已移除。上位机方向现由更完整的 `py-hosttool` 承担。

---

## 🧠 rtl-style

`rtl-style` 面向 AMD/Xilinx Vivado FPGA 工程中的可综合 Verilog RTL。

它不仅定义代码格式，更强调 **Timing by Construction**：在编码阶段主动识别深组合锥、大 fan-in、高 fanout、priority chain、AXIS ready 长链、RAM/DSP 边界以及 pipeline 对齐风险，而不是等实现后再把时序问题当成纯约束问题处理。

### 主要能力

- 默认 Verilog-2001，避免无意引入 SystemVerilog；
- 强制中文模块头和关键设计意图注释；
- AXI4-Stream ready/valid/backpressure 规则；
- CDC、reset、FSM、counter、FIFO/RAM wrapper 审查；
- Timing Design Gate：编码前分析流水级、组合深度、fan-in/fanout 和关键边界；
- 基于 AMD UG949 / UG901 / UG906 的工程化参考规则；
- `scripts/check_rtl_style.py` 静态预检；
- 普通模块和 AXIS registered stage 模板；
- 明确区分静态风险提示与真实 Vivado timing signoff。

### 使用示例

```text
Use $rtl-style to review this AXI4-Stream scheduler and suggest timing-friendly RTL changes.
```

---

## 🖥️ py-hosttool

`py-hosttool` 面向 FPGA / SoPC / 嵌入式板卡的 **PySide6 Windows 桌面上位机**开发。

它从成熟工程中提炼通用设计语言和可复用资产，但明确要求替换具体业务协议、寄存器、设备名和路径，避免把旧项目逻辑机械复制到新工具。

### 主要能力

- 自定义无边框 Windows 标题栏、置顶、最小化、最大化/还原、关闭与边缘缩放；
- `instrument-dashboard` 与 `protocol-workbench` 两类主布局；
- 通用串口工作台、ASCII/HEX、彩色 RX/TX 日志、周期发送和子串口窗口；
- QThread + 命令队列的 pySerial 所有权模型，避免 GUI 线程阻塞；
- 分段/粘包/噪声/CRC 等协议流处理；
- GUI、协议、服务、工作流、模型、诊断、性能统计分层；
- 计数器回绕/清零/基线重建、寄存器访问属性与写掩码等板卡调试边界；
- `assets/template/` 新工程模板；
- `assets/reference_projects/` 成熟工程清理版源码，用于定点参考而非直接复制业务；
- `scripts/bootstrap_project.py` 新工程生成；
- `scripts/validate_skill.py` Skill / 模板静态验证；
- pytest、offscreen smoke test 与受控 PyInstaller 交付规范。

### 使用示例

```text
Use $py-hosttool to build a PySide6 serial debug tool for this FPGA register protocol.
```

---

## 🤖 deepseek-subagent

`deepseek-subagent` 是一个 **Codex-only** 的 DeepSeek 子 Agent 集成 Skill。

固定调用链：

```text
spawn_agent(agent_type="DeepSeek")
    → opencode-go-bridge (127.0.0.1)
    → OpenCode Go
    → deepseek-v4-flash
```

当前版本重点包括：

- Windows + Codex 安装、诊断、修复、禁用和卸载；
- 本地 Responses/SSE 兼容桥与工具调用续接；
- 本地 bridge token 与上游 OpenCode Go Key 分离；
- `status` / `doctor --e2e` / repair / token rotation；
- manifest / transaction / compare-and-swap，尽量只修改 Skill 自有配置；
- DeepSeek 子 Agent 默认持久化；
- 已完成的 Agent 视为 idle/reusable，优先 `send_input`；
- `shutdown` 时优先 `resume_agent`；
- 未经用户明确决定，不主动 `close_agent` 或创建替代者；
- `not_found`、严重上下文污染或重复运行失败时，先报告真实生命周期状态，再由用户决定是否建立继任者。

### 数据边界

使用 DeepSeek 路由时，完成任务所需的提示词、上下文和源码会经本机桥转发到外部 OpenCode Go 服务。仓库中只保留示例凭据文件，不保存真实 Key 或运行时 token。

---

## 🎯 适用场景

| 场景 | 推荐 Skill |
| :--- | :--- |
| 创建/重构 Verilog RTL | `rtl-style` |
| AXIS / CDC / FSM / pipeline / timing 风险评审 | `rtl-style` |
| Vivado timing 报告映射回 RTL 架构 | `rtl-style` |
| FPGA 串口、寄存器、协议调试上位机 | `py-hosttool` |
| PySide6 仪表盘、协议工作台、日志与打包 | `py-hosttool` |
| Codex 调用 DeepSeek 子 Agent | `deepseek-subagent` |
| 长期项目子 Agent 的复用与生命周期控制 | `deepseek-subagent` |

---

## 📁 仓库结构

```text
sopc-skill/
├── README.md
├── rtl-style/
│   ├── SKILL.md
│   ├── agents/
│   ├── scripts/
│   └── references/
├── py-hosttool/
│   ├── SKILL.md
│   ├── references/
│   ├── assets/
│   │   ├── template/
│   │   └── reference_projects/
│   ├── scripts/
│   ├── manifest.txt
│   └── review_questions.md
└── deepseek-subagent/
    ├── SKILL.md
    ├── VERSION
    ├── agents/
    ├── evals/
    ├── references/
    ├── runtime/
    ├── scripts/
    ├── tests/
    └── .local/
        ├── README.txt
        └── opencode-go.key.example
```

---

## 🚀 使用方式

将需要的 Skill 目录安装/复制到支持自定义 Skill 的 Codex / Agent 环境中。每个 Skill 以自己的 `SKILL.md` 为入口，并根据任务按需加载 `references/`、执行 `scripts/` 或使用 `assets/`。

对于工程二次定制，优先在项目自身上下文中补充：

- 权威源码与文档路径；
- RTL 命名、时钟、复位、延迟与接口约定；
- AXIS / AXI-Lite / DDR / DMA 等接口契约；
- 上位机协议、寄存器表、串口参数和目标设备；
- 本地验证命令和可接受的验证边界；
- Agent 角色、Provider、数据边界和代码提交要求。

不要把项目特有事实硬编码回通用 Skill，除非这些规则确实应当成为所有后续项目的共同约束。

---

## 🔐 安全与隐私

### deepseek-subagent

以下真实运行文件不得提交：

```text
deepseek-subagent/.local/opencode-go.key
deepseek-subagent/.local/local-bridge-token.txt
deepseek-subagent/.local/local-bridge-token-state.json
```

仓库仅保留：

```text
deepseek-subagent/.local/opencode-go.key.example
deepseek-subagent/.local/README.txt
```

### 通用原则

- 不提交 API Key、Token、私钥或机器专有凭据；
- 不提交 `__pycache__`、日志、临时文件和用户环境绝对路径；
- `py-hosttool` 的参考工程用于方法和资产复用，不应把具体项目业务事实视为新项目默认配置；
- 外部模型/Provider 会产生数据出域时，应在 Skill 中明确说明真实数据边界。

---

## ✅ 项目定位

这个仓库不是完整 FPGA 工程，也不是通用 prompt 合集，而是一组**工程型 AI Skill**：

- `rtl-style` 把 RTL 编码、注释、Vivado inference 与 timing 风险固化成规则、references 和静态 checker；
- `py-hosttool` 把板卡调试上位机的 UI、串口、协议、日志、测试和打包经验固化成模板与工作流；
- `deepseek-subagent` 把 Codex → DeepSeek 的路由、诊断、安全边界和子 Agent 生命周期固化成可维护集成。

目标是让 AI 在真实 SoPC 开发中**少猜、少破坏、少重复探索，更容易复核和持续迭代**。
