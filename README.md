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
| [`deepseek-subagent`](./deepseek-subagent) | **v1.4.3** | Codex 子 Agent 成本高、重复扫代码、生命周期不可控 | 固定 DeepSeek 路由、可诊断本地桥、长期 Agent 复用、用户掌握关闭/替换权 |

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

[`rtl-style`](./rtl-style) 面向 **Vivado / Verilog RTL 创建、修改和评审**。

它关注的不是“这段 RTL 看起来能不能工作”，而是更接近 FPGA 工程真正关心的问题：

- 组合锥是否过深；
- priority / large fan-in 是否会形成关键路径；
- high-fanout 控制是否会拖累实现；
- AXI4-Stream ready/valid 是否正确；
- pipeline 是否真的切断关键路径，而不是只移动寄存器；
- RAM / DSP 是否按预期 inference；
- CDC / reset 是否存在结构性风险；
- sideband、数据和状态在加 pipeline 后是否仍然对齐。

### 🌟 这套 Skill 的差异点

**1. 中文注释不是“建议”，而是 Hard Contract**

模块功能、时钟复位、接口、数据流、延迟、CDC、时序设计和边界条件都有明确最低要求；复杂 FSM、handshake、counter、CDC、pipeline 等也要求说明设计意图。

**2. Timing by Construction：时序问题尽量在编码前解决**

不是等综合后看到 WNS 再救火，而是在写 RTL 前先做 Timing Design Gate：识别深组合路径、priority、fan-in/fanout、ready chain、RAM/DSP、entry scan、CDC 和 pipeline 需求。

**3. AMD 官方方法论不是摆在 reference 里吃灰**

Skill 把 UG949 / UG901 / UG906 中与 RTL 结构、综合推断、时序分析有关的内容提炼成可执行规则，并与实际 review/checker 形成闭环。

**4. 能确定性检查的规则交给脚本**

`scripts/check_rtl_style.py` 会检查中文模块头、复杂 always 注释、明显 priority 链、ready 组合链等静态风险。它不是 Vivado 的替代品，而是一个低成本 preflight。

**5. Progressive Disclosure，减少上下文浪费**

`SKILL.md` 只保留必须执行的核心 contract；详细方法、反例和官方依据下沉到 `references/`，按任务复杂度加载。

### 适合这些任务

- 新建 Verilog RTL 模块；
- AXI4-Stream 数据通路；
- FSM / scheduler / arbitration；
- counter / statistics / packet builder；
- RAM / DSP / pipeline 结构修改；
- timing 违例前置规避和静态评审；
- CDC / reset / handshake 检查；
- 对现有 RTL 做工程化 review。

---

## 🖥️ py-hosttool —— 把成熟上位机设计语言真正复用起来

[`py-hosttool`](./py-hosttool) 面向 **FPGA / SoPC / 嵌入式板卡的 PySide6 上位机开发**。

它不是从网上总结一套“Python GUI 最佳实践”，而是从两个已经实际开发、反复调整过的上位机项目中提炼：

- `ArqMinSystem_v1.1`
- `MasterController_v1.4`

目标不是让每个新项目重新搭一套 GUI，而是把成熟的窗口、布局、串口、日志、线程和交付资产直接变成下一套工具的起点。

### 🌟 这套 Skill 的差异点

**1. 复用的是“设计语言”，不是只抄几个控件**

保留成熟的无边框窗口体系，包括：

- 固定 / 置顶；
- 最小化；
- 最大化 / 还原；
- 关闭；
- Windows 原生边缘缩放；
- 自定义标题栏与窗口状态同步。

因此新工具不需要每次从 `QMainWindow` 默认壳子重新摸索。

**2. 已经沉淀两类典型布局**

- **设备诊断仪表盘**：适合状态、统计、性能、告警和诊断；
- **协议串口工作台**：适合命令、帧解析、日志、周期发送和联调。

Agent 可以先判断项目属于哪一类，再复用对应框架，而不是把所有功能堆到一个窗口里。

**3. 串口不是 demo 级 `serial.read()`**

成熟资产包括：

- 串口参数区；
- RX 绿色 / TX 蓝色日志；
- 动态 HEX 排版；
- 滚动保持；
- 发送区；
- 周期发送状态机；
- 子串口窗口；
- `QThread + command queue` 的 pySerial 所有权模型。

重点是把串口线程所有权、UI 更新和业务协议拆开，避免后期越改越乱。

**4. Skill 内直接保留 starter template 和 reference projects**

不是只有说明文档。`assets/` 里保留可以直接作为新项目骨架的模板，以及成熟工程的参考资产，方便 AI 对照复用真实实现。

**5. 明确“可复用资产”和“必须替换的业务语义”**

窗口框架、布局、日志、线程、串口工作流可以复用；寄存器、协议帧、命令字、业务状态机必须根据新项目重新定义，避免把旧工程协议机械复制进新工具。

### 适合这些任务

- 新建 FPGA/嵌入式板卡调试上位机；
- 从串口 demo 升级到可长期维护的 GUI；
- 复用成熟窗口标题栏和布局；
- 做设备状态/吞吐/错误统计仪表盘；
- 做协议帧收发与日志工作台；
- 用 PyInstaller 打包交付；
- 审查现有 PySide6 项目的线程、串口和结构边界。

---

## 🤖 deepseek-subagent —— 让 Codex 拥有可持续复用的低成本子 Agent

[`deepseek-subagent`](./deepseek-subagent) 是一个 **仅面向 Codex** 的 DeepSeek 子 Agent 集成 Skill。

它通过本机兼容桥把 Codex 原生子 Agent 调用固定路由到 OpenCode Go 上的 `deepseek-v4-flash`：

```text
spawn_agent(agent_type="DeepSeek")
    ↓
opencode-go-bridge   (127.0.0.1)
    ↓
OpenCode Go
    ↓
deepseek-v4-flash
```

### 🌟 这套 Skill 的差异点

**1. 目标不是“多开几个 Agent”，而是降低长期协作成本**

大型工程第一次探索最贵：扫目录、找权威文件、理解模块关系、建立术语、识别风险。真正节省 token 的方式，是让已经熟悉工程的子 Agent 尽可能复用，而不是每轮都新建一个重新探索。

**2. v1.4.3 默认把每个 DeepSeek 子 Agent 视为持久化助手**

任务完成只代表 idle，不代表应该释放：

```text
同范围新任务
    → 优先 send_input

Agent shutdown
    → 优先 resume_agent

not_found / 无法恢复
    → 报告用户并等待是否创建继任者
```

主 Agent 不再因为“这轮任务结束了”就擅自 `close_agent`。

**3. 关闭和替换权交给用户**

即使出现容量压力、上下文污染或 Agent 看似暂时用不到，也不能悄悄关闭或替换长期助手。这样项目上下文不会因为主 Agent 的一次生命周期判断被轻易丢掉。

**4. 本地桥是可安装、可诊断、可修复、可卸载的正式运行时**

包含：

- Codex Agent / provider 配置管理；
- localhost token；
- Responses / SSE 转换；
- tool call；
- 多轮上下文；
- `status` / `doctor --e2e`；
- setup / repair / disable / uninstall；
- 配置事务、manifest 和 compare-and-swap 保护。

**5. 对外部数据边界保持明确**

DeepSeek 路由经本机桥连接 OpenCode Go，因此发送给子 Agent 的提示词、上下文和完成任务所需的源码会被转发到外部服务。Skill 会明确区分本地桥 token、上游 Key、网络/WAF 和模型服务错误，不把这些边界混在一起。

### 适合这些任务

- 给大型工程建立长期 RTL / Vivado / Vitis / Python 助手；
- 让便宜模型承担代码扫描、review、局部实现和重复性工作；
- 避免同一个项目被不同子 Agent 反复从头探索；
- 在 Codex 中建立可诊断的 DeepSeek 子 Agent 路由；
- 对 Agent 生命周期、继任和上下文连续性做更严格控制。

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
  └─ 让多个长期子 Agent 分担扫描、评审、修改和专项任务
```

例如：

- 主 Agent 负责总体架构判断和跨工程决策；
- DeepSeek RTL 助手长期负责某一组 RTL / timing 问题；
- `rtl-style` 约束它的代码和 review 标准；
- Python 助手按 `py-hosttool` 的设计语言开发对应上位机；
- 已经熟悉项目的子 Agent 后续继续复用，而不是每轮重新建立上下文。

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
Use $deepseek-subagent to configure and diagnose the Codex DeepSeek child-agent route, then reuse existing DeepSeek project assistants when possible.
```

对于包含 scripts / assets / runtime 的 Skill，建议使用完整目录，不要只复制 `SKILL.md`。

---

## 🔐 安全与边界

这个仓库强调一个原则：**没有验证过的事情，不要声称验证过。**

- 没跑 Vivado 仿真 / 综合 / 实现，不声称 RTL 已通过；
- 没启动 GUI / 串口 / PyInstaller，不声称上位机已完成端到端验证；
- 静态 checker 只代表 preflight，不替代实际工具链；
- reference project 是复用资产，不代表业务协议可以直接照搬；
- DeepSeek 子 Agent 通过外部 OpenCode Go 服务处理任务，私有源码使用前应确认数据边界；
- 真实 API Key、bridge token、状态文件和本机缓存不应进入仓库。

`deepseek-subagent` 的真实本地凭据文件均由 `.gitignore` 保护，仓库只保留示例和说明。

---

## 🧭 设计原则

这个仓库持续遵循几条原则：

1. **工程事实优先于 AI 猜测** —— 信息不足时指出缺口，不自动补关键条件；
2. **已经验证的链路优先保护** —— 修改应尽量局部、可回退、可复核；
3. **先解决结构问题，再解决表面症状** —— RTL 先看架构与 timing，GUI 先看分层与线程，Agent 先看生命周期和上下文；
4. **把确定性工作交给脚本** —— checker / validator 能做的，不让模型每次重新猜；
5. **把成熟资产真正留下来** —— 模板、参考工程、设计语言和工作流都应该能够直接复用；
6. **尽量节省上下文和 token** —— progressive disclosure、长期 Agent 复用、精炼交接优先；
7. **对验证边界保持诚实** —— 静态分析、模型判断和真实工具链验证必须明确区分。

---

<div align="center">

### ⭐ 这个仓库适合谁？

正在尝试把 **ChatGPT / Codex / AI Agent 真正接入 FPGA、SoPC、嵌入式板卡开发流程**，
而不是只把 AI 当成代码补全工具的人。

如果这些 Skill 对你的工作有帮助，欢迎 Star、Fork，并根据自己的工程继续扩展。

**让 AI 少一点“看起来能跑”，多一点“真正懂工程”。**

</div>
