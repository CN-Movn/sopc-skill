# UG901 工程化提炼：Vivado Synthesis Coding / Inference

来源：AMD **Vivado Design Suite User Guide: Synthesis (UG901)**。

当前核验基线：2026.1 English，2026-07-08。

官方入口：
https://docs.amd.com/r/en-US/2026.1/ug901-vivado-synthesis/Synthesis-Methodology

UG901 的重点是“某种 HDL 写法会让 Vivado 推断出什么”。本 Skill 只提炼对 Verilog RTL 代码生成直接有价值的部分。

---


## 目录

- [UG901-S1：写 RTL 时必须思考 inference](#ug901-s1写-rtl-时必须思考-inference)
- [UG901-P1：避免无意的 Priority Processing](#ug901-p1避免无意的-priority-processing)
- [UG901-R1：Reset / Set / Clock Enable](#ug901-r1reset--set--clock-enable-写法直接影响-primitive-映射)
- [UG901-M1：RAM inference](#ug901-m1ram-inference-要从模板和-latency-契约出发)
- [UG901-D1：DSP/Multiplier](#ug901-d1dspmultiplier-的位宽和实现方式要明确)
- [UG901-F1：FSM inference](#ug901-f1fsm-要让-synthesis-看得懂)
- [UG901-L1：RTL Linter](#ug901-l1使用-rtl-linter-作为-vivado-侧静态检查)
- [UG901-V1：位宽 / 符号](#ug901-v1位宽--符号必须显式审查)
- [UG901 快速审查表](#ug901-快速审查表)

## UG901-S1：写 RTL 时必须思考 inference

官方 2026.1 UG901 的 HDL Coding Techniques 覆盖：

- registers/latches；
- multipliers / DSP；
- RAM inference；
- FSM；
- Verilog procedural / combinational / sequential constructs；
- synthesis attributes；
- RTL Linter。

### Agent 原则

任何“语法上正确”的 Verilog 都不等于“硬件结构合适”。生成时至少判断：

- 这个选择会 infer priority mux 还是 parallel mux？
- 这个 array 会 infer BRAM/URAM/LUTRAM 还是大量 FF？
- 这个 multiply/add 是否可能用 DSP？
- reset/set/enable 会不会阻止专用资源寄存器映射？
- variable index/shift 会不会产生大 mux/barrel logic？

---

## UG901-P1：避免无意的 Priority Processing

官方章节：

- 2026.1 文档导航明确包含 `Avoiding Priority Processing`；
- 官方可访问章节：  
  https://docs.amd.com/r/en-US/ug901-vivado-synthesis/Avoiding-Priority-Processing

UG901 明确讨论如何避免不需要的 priority processing。

### Skill 对官方建议的安全化解释

UG901 示例提到 `parallel_case` attribute，但 Coding Agent **不能机械贴属性**。原因：属性必须与真实互斥/覆盖语义一致，否则会造成仿真/综合或维护风险。

因此本 Skill 的默认优先级是：

1. 先用 RTL 结构真实表达互斥关系；
2. 可用 predecode / one-hot / case 明确平行选择；
3. 只有项目已有规范、并能证明 exclusivity 时才考虑 synthesis attribute。

### 什么时候 priority 是合理的

- 中断优先级；
- 仲裁器明确要求固定 priority；
- “first match wins” 是协议定义；
- 错误处理有明确覆盖关系。

此时必须在中文注释写明 priority 顺序的功能原因。

### 什么时候是 accidental priority

```verilog
if (slot_free[0]) idx = 0;
else if (slot_free[1]) idx = 1;
else if (slot_free[2]) idx = 2;
...
```

如果只是“找任意空 slot”，却没有业务要求 slot0 永远优先，这就是需要重新审查的结构。

---

## UG901-R1：Reset / Set / Clock Enable 写法直接影响 primitive 映射

官方相关内容：UG901 HDL Coding Techniques -> Flip-Flops and Registers Control Signals -> Coding Guidelines。2026.1 文档导航仍包含该章节；历史/跨版本官方内容明确强调：

- 避免不必要 asynchronous set/reset；
- 专用 RAM/DSP 的 sequential resource 对控制语义有限制；
- 不必要 set/reset 会造成次优映射；
- control signal polarity / control set 会影响实现。

### Skill 规则

- 已有工程接口不擅自改 reset polarity；
- 内部高性能 datapath 不机械 reset 所有数据寄存器；
- 只复位 valid/state/pointer 使 stale data 无效；
- 不写同时 set + reset 的复杂 FF 语义，除非项目确实需要并已验证；
- 对 RAM/DSP 目标逻辑，优先使用专用资源能直接支持的寄存/控制模式。

此规则与 UG949-T7 配合使用。

---

## UG901-M1：RAM inference 要从模板和 latency 契约出发

UG901 的 HDL Coding Techniques 包含 RAM HDL Coding Techniques、UltraRAM coding templates、single/dual-port RAM inference 等章节。

### Agent 规则

写 inferred memory 时不要随意“创新语法”。先确认：

- 单口 / simple dual port / true dual port；
- synchronous read 还是其他语义；
- read-during-write 行为；
- byte enable；
- 初始化方式；
- 输出 latency；
- reset 是否针对 output register，而不是整片 memory。

如果项目已有成熟 RAM wrapper/template，优先复用该 inference pattern。

### Timing 结合

RAM inference 正确只是第一步。高频路径还要按 `[UG949-T5]` 检查输出寄存和后级组合锥。

---

## UG901-D1：DSP/Multiplier 的位宽和实现方式要明确

官方章节：

- Multipliers  
  https://docs.amd.com/r/en-US/ug901-vivado-synthesis/Multipliers
- Multipliers Implementation  
  https://docs.amd.com/r/en-US/ug901-vivado-synthesis/Multipliers-Implementation

UG901 当前文档说明 multiplier 可以映射到 slice logic 或 DSP，选择与 operand size / performance 有关；如果不需要全部 MSB，应减小 operand width。

### Agent 规则

- 明确 signed/unsigned；
- 明确结果全精度位宽；
- 不依赖 Verilog 隐式 width/sign 规则做关键算术；
- 不需要的高位不要扩大后续比较/加法 cone；
- 对高频 DSP path 配合 `[UG949-T6]` 做充分流水。

---

## UG901-F1：FSM 要让 synthesis 看得懂

官方章节：

- FSM Components  
  https://docs.amd.com/r/en-US/ug901-vivado-synthesis/FSM-Components
- FSM Reporting  
  https://docs.amd.com/r/en-US/ug901-vivado-synthesis/FSM-Reporting

Vivado 会识别 FSM 并报告其 state encoding。

### Agent 规则

- FSM 状态寄存器单一驱动；
- next-state 有完整 default；
- 状态条件不要无理由叠加复杂 datapath 计算；
- 大量 state decode 同拍驱动宽 mux 时，按 `[UG949-T1/T2]` 审查；
- 不为了“优化”随意强制 FSM encoding，除非项目/时序证据要求。

---

## UG901-L1：使用 RTL Linter 作为 Vivado 侧静态检查

官方章节：

- Running the Linter  
  https://docs.amd.com/r/en-US/ug901-vivado-synthesis/Running-the-Linter
- List of Linter Rules  
  https://docs.amd.com/r/en-US/ug901-vivado-synthesis/List-of-Linter-Rules

2026.1 UG901 给出的命令示例：

```tcl
synth_design -top <top_level> -part <part> -lint
```

RTL Linter 可检查算术溢出、mixed-sign、shift overflow、未赋值/未使用 bit、多重赋值等合法但危险的 RTL pattern。

### 与 Skill 自带 checker 的区别

`scripts/check_rtl_style.py`：

- 不需要 Vivado；
- 重点查中文注释合同和本项目积累的 timing-risk pattern；
- 只是 heuristic preflight。

Vivado RTL Linter：

- 由 AMD synthesis frontend 理解 RTL；
- 能检查更多语言/推断问题；
- 需要 Vivado 和真实 top/part。

默认不要因为用了 Skill 就自动启动 Vivado；用户明确要求工具检查时再运行。

---

## UG901-V1：位宽 / 符号必须显式审查

结合 UG901 当前 RTL Linter 的 `Arithmetic overflow`、`Operands have mixed signs`、`Shifter overflow` 等规则，本 Skill 强化：

- arithmetic operand 位宽必须有意识；
- signed/unsigned 混合不得依赖“感觉”；
- counter 上限、`+ 1'b1`、乘法结果、地址计算要明确目标宽度；
- variable shift 的 shift amount 要限制范围；
- 截断必须是业务有意行为，并在关键路径附近注释。

这不仅是功能正确性，也避免无意义的大位宽逻辑拖累 timing/resource。

---

## UG901 快速审查表

- long `if/else-if` 是否真有 priority？ `[UG901-P1]`
- reset/set/CE 是否会妨碍 RAM/DSP/FF 映射？ `[UG901-R1]`
- inferred RAM 端口/读写/latency 是否匹配已验证模板？ `[UG901-M1]`
- multiplier width/sign/implementation 是否明确？ `[UG901-D1/V1]`
- FSM 是否被 Vivado 清晰识别，而非散落的隐式状态逻辑？ `[UG901-F1]`
- 是否值得让用户额外跑 Vivado RTL Linter？ `[UG901-L1]`
