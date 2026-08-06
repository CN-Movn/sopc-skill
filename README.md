# sopc-skill ⚙️

> 面向 SoPC / FPGA / RTL / MATLAB 上位机与 Codex 多 Agent 开发流程的自定义 AI Skill 集合

<div align="center">

  <h3>让 AI 更懂 FPGA 工程，也更懂工程协作边界</h3>

  <p>
    一组沉淀自真实 SoPC 开发流程的 AI 协作规范与工具，用于约束 ChatGPT、Codex 和其他 AI coding agent 在 RTL 编写、代码评审、MATLAB 调试工具开发及 Codex 子 Agent 集成中的行为边界。
  </p>

  <p>
    <img src="https://img.shields.io/badge/Domain-SoPC%20%2F%20FPGA-blue?style=flat-square" alt="Domain">
    <img src="https://img.shields.io/badge/RTL-Verilog-green?style=flat-square" alt="RTL">
    <img src="https://img.shields.io/badge/Tool-MATLAB-orange?style=flat-square" alt="MATLAB">
    <img src="https://img.shields.io/badge/Agent-Codex%20%2F%20DeepSeek-purple?style=flat-square" alt="Agent">
    <img src="https://img.shields.io/badge/Status-Active-lightgrey?style=flat-square" alt="Status">
  </p>

  <p>
    <a href="#-项目简介">项目简介</a> •
    <a href="#-核心技能">核心技能</a> •
    <a href="#-适用场景">适用场景</a> •
    <a href="#-仓库结构">仓库结构</a> •
    <a href="#-使用方式">使用方式</a> •
    <a href="#-安全与隐私边界">安全与隐私边界</a> •
    <a href="#-交流与改进">交流与改进</a>
  </p>

</div>

---

## 📖 项目简介

`sopc-skill` 是一个面向 **SoPC / FPGA / 嵌入式板卡调试与 AI coding agent 协作** 的自定义 Skill 仓库。

这个仓库来自我个人在 SoPC 开发过程中的实际使用经验，主要用于沉淀一组“让 AI 更适合参与硬件工程开发”的规则、模板和集成工具。

在真实 FPGA / SoPC 项目中，AI 不只是要“能写代码”，还要尽量做到：

- 不乱补工程事实；
- 不破坏已验证链路；
- 不随意改接口边界；
- 理解 AXIS、CDC、pipeline、timing closure 等硬件开发风险；
- 生成的 Verilog RTL 更适合综合、仿真、评审和上板；
- 生成的 MATLAB GUI 工具更适合长期调试和交付；
- 正确区分父 Agent、子 Agent、模型 Provider 与外部服务；
- 没有实际运行过仿真、综合、打包或端到端测试时，不假装已经验证通过。

这个仓库的目标，就是把这些工程习惯整理成可复用、可维护的 AI Skill。

---

## ✨ 核心技能

目前包含三个自定义 Skill：

| Skill | 方向 | 说明 |
| :--- | :--- | :--- |
| [`rtl-style`](./rtl-style) | Verilog RTL | 面向 Vivado / Vitis FPGA 工程的 RTL 编码与评审规范 |
| [`matlab-toolkit`](./matlab-toolkit) | MATLAB GUI | 面向 FPGA / SoPC 板卡调试的 MATLAB 上位机工具开发规范 |
| [`deepseek-subagent`](./deepseek-subagent) | Codex 子 Agent | 通过本地兼容桥为 Codex 提供固定的 DeepSeek 子 Agent 能力 |

---

## 🧠 rtl-style

`rtl-style` 用于约束 AI 在编写、修改或评审 Verilog RTL 时的行为。

它重点关注的不是“写出一段看起来能跑的代码”，而是让 AI 尽量生成更加工程化的 RTL：

- 可综合；
- 可仿真；
- 可评审；
- 风格统一；
- 对 AXI4-Stream 友好；
- 对 CDC 风险敏感；
- 对 pipeline 和 timing closure 友好；
- 默认使用中文注释说明关键设计意图。

### 典型使用场景

- 创建 Verilog RTL 模块；
- 重构已有 RTL；
- 编写 AXI4-Stream 数据通路；
- 修改 packet / frame builder；
- 处理 FSM、计数器、状态统计；
- 检查 ready / valid 握手；
- 增加 pipeline；
- 排查 timing 风险；
- 检查 CDC 和复位边界；
- 让 AI 评审 RTL 风格和潜在问题。

### 重点约束

- 默认使用 Verilog-2001；
- 使用 ``default_nettype none``；
- 不依赖隐式 wire；
- 不推断 latch；
- 不制造组合环；
- 不跨多个 `always` 块驱动同一个 `reg`；
- AXIS 在 `tvalid && !tready` 时必须保持 payload 稳定；
- CDC 不能直接跨时钟采样多 bit 总线；
- 不声称未实际运行过的仿真、综合、实现或时序检查已经通过。

---

## 🛠️ matlab-toolkit

`matlab-toolkit` 用于指导 AI 开发和维护 MATLAB GUI 上位机调试工具。

它主要服务于 FPGA / SoPC / 嵌入式板卡调试中的这些任务：

- 串口调试；
- 协议帧生成；
- 协议帧解析；
- 寄存器读写；
- 遥测信息解析；
- RX / TX 彩色日志；
- 周期发送；
- MATLAB GUI 工具打包；
- MATLAB Runtime 交付说明。

### 设计目标

`matlab-toolkit` 希望避免 AI 把上位机工具写成一次性脚本，而是尽量让工具具备：

- 清晰的 GUI 结构；
- 明确的协议边界；
- 可复用的串口收发逻辑；
- 可读的 RX / TX 日志；
- 可维护的解析函数；
- 清楚的打包边界；
- 对实际 MATLAB 运行环境的诚实表述。

### 重点约束

- 通道能力可以复用，业务协议必须替换；
- 不机械复用旧工程协议；
- 不把 EXE、RuntimeInstaller、`dist`、cache、中间构建文件放进 Skill；
- 没有 MATLAB 环境时，只能做结构检查和静态检查；
- 不假装 GUI、串口通信或打包已经验证通过。

---

## 🤖 deepseek-subagent

`deepseek-subagent` 是一个 **仅面向 Codex** 的子 Agent 集成 Skill。它通过本机兼容桥，把 Codex 的原生子 Agent 调用固定路由到 OpenCode Go 上的 `deepseek-v4-flash`：

```text
spawn_agent(agent_type="DeepSeek")
    → opencode-go-bridge
    → OpenCode Go
    → deepseek-v4-flash
```

该 Skill 不强制父 Agent 一定委派任务，也不规定固定子 Agent 数量。是否使用零个、一个或多个 DeepSeek 子 Agent，由用户指令和任务本身决定。

### 主要能力

- 安装、修复、禁用和卸载 Codex DeepSeek 子 Agent 路由；
- 管理 `DeepSeek.toml`、模型目录和 Skill 自有 Codex 配置字段；
- 在 `127.0.0.1` 上运行本地兼容桥；
- 转换 Codex Responses / SSE、工具调用和多轮上下文；
- 区分本地桥 token、上游 API Key、Cloudflare/WAF、网络和服务错误；
- 提供 `status`、`doctor --e2e` 和 token 轮换等诊断命令；
- 使用事务、manifest 和 compare-and-swap 方式保护非 Skill 所有的 Codex 配置。

### 使用边界

- 目标平台为 Windows + Codex；
- DeepSeek 子 Agent 为文本模型，图片和截图应由父 Agent 读取后转述；
- 真实 OpenCode Go Key 必须由用户在本机手动创建；
- 使用该路由时，发送给 DeepSeek 的提示词、上下文和必要源码会被转发至外部 OpenCode Go 服务；
- 用户明确要求使用 DeepSeek 或配置其 Key，视为对该次外部转发的授权；
- 不应把其他默认/GPT 子 Agent 描述成 DeepSeek，也不应在 DeepSeek 失败时静默替换。

---

## 🎯 适用场景

这个仓库适合这些场景：

| 场景 | 说明 |
| :--- | :--- |
| AI 生成 RTL | 让 AI 按更适合 FPGA 工程的方式写 Verilog |
| RTL 代码评审 | 检查 AXIS、CDC、FSM、pipeline、timing 风险 |
| SoPC 数据通路开发 | 约束 AI 不随意破坏接口、时钟域和已验证链路 |
| MATLAB 调试工具开发 | 生成更适合板卡调试的 GUI 上位机 |
| Codex 多 Agent 协作 | 为 Codex 提供可诊断、可卸载的 DeepSeek 子 Agent 路由 |
| Agent 协作规范沉淀 | 把个人工程经验固化成可复用 Skill |
| 团队风格统一 | 给团队内部 AI coding agent 提供统一行为边界 |

---

## 📁 仓库结构

```text
sopc-skill/
├── README.md
├── rtl-style/
│   ├── SKILL.md
│   ├── agents/
│   │   └── openai.yaml
│   └── references/
│       ├── axis_registered_stage_template.v
│       └── verilog_module_skeleton.v
├── matlab-toolkit/
│   ├── SKILL.md
│   ├── README.md
│   ├── CHANGELOG.md
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

### 方式一：作为 ChatGPT / Codex Skill 使用

将对应目录作为自定义 Skill 引入：

```text
rtl-style/
matlab-toolkit/
deepseek-subagent/
```

例如：

```text
Use $rtl-style to review this AXI4-Stream packet builder and suggest timing-friendly improvements.
```

```text
Use $matlab-toolkit to create a MATLAB GUI serial debug tool for this FPGA board protocol.
```

```text
Use $deepseek-subagent to diagnose the Codex DeepSeek child-agent route, then use agent_type="DeepSeek" when the task justifies delegation.
```

### 方式二：直接复制 SKILL.md

如果工具链暂时不支持完整 Skill 目录，可以直接复制 `rtl-style/SKILL.md` 或 `matlab-toolkit/SKILL.md` 到自己的 Agent instruction 中。

`deepseek-subagent` 包含运行时代码、脚本、测试和本地桥，不能只复制 `SKILL.md`；应安装完整目录。

### 方式三：按项目二次定制

建议根据自己的工程继续补充：

- 本地工程目录；
- RTL 命名规范；
- AXIS / AXI-Lite / DDR / DMA 接口约定；
- 时钟和复位约定；
- 仿真命令；
- Vivado 综合 / 实现流程；
- MATLAB 工具协议字段；
- 板卡调试流程；
- Agent 模型、外部 Provider 和数据边界；
- 代码提交和验收标准。

---

## 🔐 安全与隐私边界

仓库中只保留凭据示例文件，不包含真实 OpenCode Go Key 或本地桥 token。

`deepseek-subagent` 的真实本地文件包括：

```text
.local/opencode-go.key
.local/local-bridge-token.txt
.local/local-bridge-token-state.json
```

这些文件均已加入忽略规则，不应被提交、打包、打印、同步或写入日志。仓库只保留：

```text
.local/opencode-go.key.example
.local/README.txt
```

需要特别注意：使用 DeepSeek 子 Agent 时，任务提示词、上下文和完成任务所需的源码会经本机桥发送到 OpenCode Go。使用私有、敏感或受组织策略约束的源码前，应确认相应的数据处理边界。

---

## ✅ 我希望这些 Skill 解决什么问题

### 对 RTL

- 减少“看起来能跑但不利于时序”的 RTL；
- 减少 AXIS ready / valid 写错；
- 减少隐式 wire、latch、组合环；
- 减少 CDC 风险被忽略；
- 减少 debug 逻辑影响主数据通路；
- 让 AI 在写代码时主动考虑 pipeline 和 timing。

### 对 MATLAB 工具

- 减少一次性脚本；
- 减少协议解析散落各处；
- 减少 GUI 逻辑和通信逻辑混在一起；
- 减少打包产物污染源码目录；
- 让 AI 更清楚地区分“结构检查”和“实际运行验证”。

### 对多 Agent 协作

- 区分父 Agent、DeepSeek 子 Agent、Provider 和本地兼容桥；
- 避免遗漏 `agent_type` 导致路由到错误模型；
- 避免为了展示多 Agent 而无意义拆分任务；
- 让安装、诊断、修复、禁用和卸载具备明确边界；
- 避免真实 Key、token 或私有源码流向不明确。

### 对 AI 协作

- 减少 AI 乱改边界；
- 减少 AI 没验证却说验证通过；
- 减少“过度设计”和“瞎补细节”；
- 让 AI 更像一个工程协作者，而不是单纯代码生成器。

---

## 🗺️ 后续计划

后续可能继续补充：

- 更完整的 MATLAB GUI 模板；
- 更多 AXIS / FIFO / CDC RTL reference；
- packet builder / frame parser 示例；
- Vivado timing-friendly rewrite 案例；
- SoPC 最小系统、DDR、AXI-Lite、HP 口数据通路协作规范；
- 板卡 bring-up 和 debug prompt 模板；
- DeepSeek 子 Agent 的 Windows CI、兼容性验证和升级说明；
- 面向 Codex 的项目交接模板。

---

## 🤝 交流与改进

这个仓库来自个人 SoPC / FPGA 开发和 AI coding agent 协作中的实际经验，目前仍在持续调整。

如果你也在做：

- FPGA；
- SoPC；
- Vivado / Vitis；
- Verilog RTL；
- AXI4-Stream；
- 板卡调试；
- MATLAB 上位机工具；
- Codex / DeepSeek 多 Agent；
- AI coding agent 工程化使用；

欢迎交流、提 issue、fork 或提出改进建议。

也欢迎根据自己的团队习惯，把这些 Skill 改造成适合自己项目的版本。

---

## 📌 说明

本仓库不是完整 SoPC 工程，也不是可直接运行的板卡工程。

它更像是一个：

> AI + FPGA / SoPC 工程经验与 Agent 工具沉淀仓库

目标是帮助 AI 在参与硬件开发和工程协作时更加稳健、保守、透明和工程化。
