<div align="center">

# sopc-skill ⚙️

### 让 AI 更懂 FPGA 工程，也更懂工具开发与 Agent 协作边界

面向真实 SoPC / FPGA / 嵌入式开发流程沉淀的自定义 AI Skill 集合。

不是让 AI “多写一点代码”，而是把**真实工程里反复验证过的方法、约束、模板、检查脚本和协作边界**固化下来，让 ChatGPT / Codex 等 coding agent 更稳定地参与工程开发。

<p>
  <img src="https://img.shields.io/badge/Domain-SoPC%20%2F%20FPGA-4c78a8?style=flat-square" alt="Domain">
  <img src="https://img.shields.io/badge/RTL-Vivado%20%2F%20Verilog-2f855a?style=flat-square" alt="RTL">
  <img src="https://img.shields.io/badge/HostTool-PySide6-7b61ff?style=flat-square" alt="Host Tool">
  <img src="https://img.shields.io/badge/Agent-Codex%20%2F%20DeepSeek-a35ac7?style=flat-square" alt="Agent">
  <img src="https://img.shields.io/badge/Status-Active-555?style=flat-square" alt="Status">
</p>

<p>
  <a href="#-为什么有这个仓库">为什么有这个仓库</a> ·
  <a href="#-当前-skill">当前 Skill</a> ·
  <a href="#-rtl-style--让-ai-写出更像工程代码的-rtl">rtl-style</a> ·
  <a href="#-py-hosttool--把成熟上位机设计语言真正复用起来">py-hosttool</a> ·
  <a href="#-deepseek-subagent--让-codex-拥有可持续复用的低成本子-agent">deepseek-subagent</a> ·
  <a href="#-使用方式">使用方式</a> ·
  <a href="#-安全与边界">安全与边界</a>
</p>

</div>

---

## ✨ 为什么有这个仓库

`sopc-skill` 面向 **FPGA / SoPC / 嵌入式板卡开发与 AI coding agent 协作**。

真实硬件工程里，真正昂贵的从来不只是“把代码写出来”，而是：

- 不虚构协议、寄存器、时钟、复位和性能事实；
- 不破坏已经验证过的接口和链路；
- 知道 AXI4-Stream、CDC、pipeline、RAM/DSP inference、timing closure 真正危险在哪里；
- 写出来的 RTL 不只是“语法正确”，还要更容易综合、评审、验证和上板；
- 上位机不能只是一次性脚本，要有稳定的窗口、日志、串口、线程和交付结构；
- 多 Agent 不能只会“新建—用完—关闭”，还要考虑上下文复用、成本和长期协作；
- 没有实际跑过仿真、综合、实现、GUI、串口或端到端测试时，不能假装已经验证通过。

这个仓库做的事情，就是把这些经验变成 AI 可以稳定执行的 **Skill、reference、模板、脚本和运行时能力**。

> **核心目标：让 AI 从“会生成代码”更进一步，变成“更懂工程约束、更懂复用、更懂边界的协作工具”。**

---

## 🚀 当前 Skill

| Skill | 当前版本 | 主要解决什么问题 | 核心优势 |
| :--- | :---: | :--- | :--- |
| [`rtl-style`](./rtl-style) | **v2.2.1** | AI 写 RTL 容易只顾功能、忽略时序/CDC/可维护性 | 中文注释硬约束、Timing by Construction、AMD 官方方法论、静态 checker、RTL 模板 |
| [`py-hosttool`](./py-hosttool) | **v1.2.1** | Python 上位机项目经常从零搭壳、重复踩 GUI/串口/线程坑 | 复用成熟 PySide6 设计语言、完整模板、参考工程、串口资产、窗口框架与交付流程 |
| [`deepseek-subagent`](./deepseek-subagent) | **v1.7.2** | Codex 子 Agent 成本高、重复扫代码、跨进程连续性容易被误判 | 固定 DeepSeek 路由、V1 fail-closed、进程内复用、持久 handoff 与 successor 连续性 |

三者分别对应一个真实工程链条：

```text
RTL / FPGA 设计
      ↓
上位机 / 调试工具
      ↓
AI 多 Agent 协作与成本控制
```

它们不是彼此孤立的 Prompt，而是尽量把一套真实工程工作方式沉淀成可复用资产。

---

## 🧠 rtl-style —— 让 AI 写出更像工程代码的 RTL

[`rtl-style`](./rtl-style) 面向 **Vivado / Verilog RTL 创建、修改和评审**，重点不是“语法能过”，而是让 AI 更早考虑时序、CDC、握手、资源推断和可维护性。

### 🌟 核心卖点

- **中文注释 Hard Contract**：模块头、FSM、handshake、CDC、pipeline 等关键结构必须说明设计意图。
- **Timing by Construction**：编码前识别深组合、priority、fan-in/fanout、ready chain、RAM/DSP 和 pipeline 风险，而不是等 WNS 出问题再补救。
- **AMD 官方方法论落地**：把 UG949 / UG901 / UG906 中与 RTL 结构、综合推断、时序分析相关的原则转成可执行规则。
- **Checker + RTL 模板**：`check_rtl_style.py` 做低成本静态 preflight，并提供 AXIS registered stage、module skeleton 等可直接复用模板。
- **Progressive Disclosure**：核心约束留在 `SKILL.md`，详细依据和反例按需加载，减少上下文浪费。

适合 AXI4-Stream、FSM / scheduler、RAM / DSP / pipeline、CDC / reset，以及现有 RTL 的工程化 review。

---

## 🖥️ py-hosttool —— 把成熟上位机设计语言真正复用起来

[`py-hosttool`](./py-hosttool) 面向 **FPGA / SoPC / 嵌入式板卡的 PySide6 上位机开发**，核心资产来自 `ArqMinSystem_v1.1` 与 `MasterController_v1.4` 两套实际工程，而不是从零拼 GUI demo。

### 🌟 核心卖点

- **成熟窗口框架**：复用无边框标题栏、置顶、最小化、最大化 / 还原、原生边缘缩放等完整桌面交互。
- **两类现成布局**：设备诊断仪表盘 + 协议串口工作台，可直接作为新工具的信息架构起点。
- **工程级串口资产**：彩色 RX/TX 日志、动态 HEX、滚动保持、周期发送、子串口窗口，以及 `QThread + command queue` 的 pySerial 所有权模型。
- **模板与真实参考工程一起提供**：`assets/template/` 可直接起项目，`reference_projects/` 用于追溯成熟实现。
- **复用边界明确**：窗口、布局、日志和线程框架可以继承；寄存器、协议帧、命令字和业务状态机必须按新项目重建。

适合板卡调试工具、设备诊断 / 性能仪表盘、协议收发工作台，以及现有 PySide6 项目的结构化改造。

---

## 🤖 deepseek-subagent —— 让 Codex 拥有可持续复用的低成本子 Agent

[`deepseek-subagent`](./deepseek-subagent) 是一个 **仅面向 Codex** 的 DeepSeek 子 Agent 集成 Skill，通过本机桥固定路由到 OpenCode Go 的 `deepseek-v4-flash`。

```text
spawn_agent(agent_type="DeepSeek")
    ↓
opencode-go-bridge
    ↓
OpenCode Go / deepseek-v4-flash
```

### 🌟 核心卖点

- **进程内复用 + 持久交接**：同一 Codex 进程内优先通过 `send_input` 继续使用已熟悉项目的子 Agent；完整退出、Windows 重启、`shutdown` 或 `not_found` 后，不伪称恢复旧 Agent，而是通过 `.local/handoffs` 中的可验证事实让 successor 接续工作。
- **V1 fail-closed**：首次 DeepSeek 操作前通过 `prepare --json` 校验并准备 `multi_agent_v1` transport；条件不满足时明确失败，不静默回落到不兼容路径。
- **逐轮可验证 continuity**：每轮子 Agent 工作都绑定稳定 role / scope / project root，并要求追加 handoff record；父 Agent 校验新增记录和 marker 后才接受结果。
- **关闭 / 替换权属于用户**：任务完成、暂时 idle、容量压力、进程退出或 `not_found` 都不会自动触发 `close_agent`；需要 successor 或关闭旧 Agent 时由用户决定。
- **低成本固定路由**：把适合扫描、review、局部实现和重复工作的任务稳定交给 DeepSeek，降低长期多 Agent 协作成本。
- **正式可诊断运行时**：支持 `prepare`、setup / repair / disable / uninstall、`status`、`doctor --e2e`、transport、bridge、credential、Agent roster 与 handoff 管理。
- **数据边界明确**：本机桥会把完成任务所需的提示词、上下文和源码转发到外部 OpenCode Go 服务，私有代码使用前应确认边界。

### ⚠️ 启动顺序（重要）

`deepseek-subagent` 的 V1 配置必须在 **Codex 创建新 Task 之前**生效。Windows / Codex 完整重启后，推荐按下面的顺序启动：

```text
双击 deepseek-subagent/双击运行prepare.cmd
        ↓
确认 prepare 成功
        ↓
启动 / 回到 Codex
        ↓
创建新的对话 / Task
        ↓
再创建 DeepSeek 子 Agent
```

- `prepare` 会校验并准备 `multi_agent_v1` transport，同时检查 / 恢复本地 bridge。
- 已经初始化为 **Multi-Agent V2** 的当前 Task **不能原地切回 V1**；即使 `prepare` 修复成功，也只能对之后创建的新 Task 生效。
- 如果安全门禁提示当前 Task 已是 V2，先确认 `prepare` 成功，再创建一个新的 Codex Task 继续即可，不需要重新安装 Skill。
- 同一个已经正常工作的 V1 Task 内无需每轮重复执行 `prepare`；关键是完整重启后，在新 Task 创建前先运行一次。

适合大型 RTL / Vivado / Vitis / Python 工程中的专项助手、代码探索、review 和低成本多 Agent 协作。

---

## 🧩 三个 Skill 如何组合

在一个完整的 SoPC / FPGA 项目中，可以形成这样的协作方式：

```text
rtl-style
  └─ 约束 RTL 创建 / 修改 / review
       ↓
py-hosttool
  └─ 快速构建稳定的调试与诊断上位机
       ↓
deepseek-subagent
  └─ 让多个可持续协作的子 Agent 分担扫描、评审、修改和专项任务
```

例如：

- 主 Agent 负责总体架构判断和跨工程决策；
- DeepSeek RTL 助手长期负责某一组 RTL / timing 问题；
- `rtl-style` 约束它的代码和 review 标准；
- Python 助手按 `py-hosttool` 的设计语言开发对应上位机；
- 当前 Codex 进程内继续复用已经熟悉项目的子 Agent；跨重启则让 successor 读取持久 handoff 中的已验证事实，而不是假装恢复旧运行态。

这也是这个仓库真正想解决的问题：**不只是给 AI 一套 prompt，而是给 AI 一套更接近真实工程团队的工作方式。**

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
│
├── py-hosttool/
│   ├── SKILL.md
│   ├── references/
│   ├── scripts/
│   ├── assets/
│   │   ├── template/
│   │   └── reference_projects/
│   └── ...
│
└── deepseek-subagent/
    ├── SKILL.md
    ├── VERSION
    ├── 双击运行prepare.cmd
    ├── agents/
    ├── evals/
    ├── references/
    ├── runtime/
    ├── scripts/
    └── tests/
```

---

## 🚀 使用方式

将需要的目录作为自定义 Skill 引入 ChatGPT / Codex，或在项目中直接调用对应 Skill。

示例：

```text
Use $rtl-style to review this AXI4-Stream scheduler and identify timing-by-construction risks before I run Vivado.
```

```text
Use $py-hosttool to build a PySide6 diagnostic host tool for this FPGA board, reusing the mature frameless window and serial workbench assets.
```

```text
Use $deepseek-subagent to prepare the Codex DeepSeek V1 route, reuse current-process children when possible, and preserve verified project continuity through durable handoffs.
```

对于包含 scripts / assets / runtime 的 Skill，建议使用完整目录，不要只复制 `SKILL.md`。

---

## 🔐 安全与边界

这个仓库强调一个原则：**没有验证过的事情，不要声称验证过。**

- 没跑 Vivado 仿真 / 综合 / 实现，不声称 RTL 已通过；
- 没启动 GUI / 串口 / PyInstaller，不声称上位机已完成端到端验证；
- 静态 checker 只代表 preflight，不替代实际工具链；
- reference project 是复用资产，不代表业务协议可以直接照搬；
- `deepseek-subagent/.local` 中的真实 credential、bridge/runtime 状态、Agent roster 与 handoff 属于本机持久数据，不应提交到仓库。
