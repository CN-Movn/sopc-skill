# UG949 工程化提炼：RTL Architecture / Timing by Construction

来源：AMD **UltraFast Design Methodology Guide for FPGAs and SoCs (UG949)**。

当前核验基线：2026.1 English，2026-06-23。

官方入口：
https://docs.amd.com/r/en-US/ug949-vivado-design-methodology

本文不是官方原文复制，而是把 UG949 中与“写 RTL 时就避免时序/资源问题”直接相关的内容转换成 Coding Agent 可执行规则。每条规则都有稳定 ID，供 `SKILL.md`、`timing_by_construction.md` 和静态检查器交叉引用。

---


## 目录

- [UG949-T1：把 pipeline 当成架构，而不是补丁](#ug949-t1把-pipeline-当成架构而不是补丁)
- [UG949-T2：识别 large fan-in / deep cone](#ug949-t2识别-large-fan-in--deep-cone)
- [UG949-T3：在逻辑/层级边界注册 datapath](#ug949-t3在逻辑层级边界注册-datapath)
- [UG949-T4：高 fanout 要早处理，但不要盲目复制](#ug949-t4高-fanout-要早处理但不要盲目复制)
- [UG949-T5：RAM 读路径默认考虑输出寄存](#ug949-t5ram-读路径默认考虑输出寄存)
- [UG949-T6：DSP / arithmetic 要利用内部 pipeline](#ug949-t6dsp--arithmetic-要利用内部-pipeline)
- [UG949-T7：Reset 是 timing/resource 架构的一部分](#ug949-t7reset-是-timingresource-架构的一部分)
- [UG949-T8：RTL 阶段就使用 methodology 思维](#ug949-t8rtl-阶段就使用-methodology-思维)
- [UG949 快速审查表](#ug949-快速审查表)

## UG949-T1：把 pipeline 当成架构，而不是补丁

官方相关章节：

- Pipelining Considerations  
  https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Pipelining-Considerations
- Check Inferred Logic  
  https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Check-Inferred-Logic
- Maximizing Impact Early in the Development Cycle  
  https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Maximizing-Impact-Early-in-the-Development-Cycle

### 官方方法论核心

UG949 明确指出：长 datapath 含多级逻辑时，把逻辑分布到多个 clock cycle 可提高可达到的时钟频率和吞吐，代价是 latency 与流水管理；同时，在 RTL/综合阶段做结构改变比到了实现阶段再反复调 directive 更有影响力。

`Check Inferred Logic` 进一步点名需要关注额外流水的结构：

- large fan-in logic cone；
- block RAM 无输出寄存器；
- arithmetic 没有适当流水；
- 大 XOR function；
- 因物理位置导致的长 route。

### Agent 强制动作

在写一个高频或吞吐型模块前，先写出寄存级，而不是先写一个巨大的 `always @(*)`：

```text
输入/握手寄存
    -> 预译码 / 基础条件
    -> 仲裁 / 索引 / 算术
    -> RAM/DSP 或核心处理
    -> 输出选择 / 输出寄存
```

如果一个周期同时出现 3 类以上相互依赖的复杂操作（decode / priority / mux / arithmetic / compare / reduction / handshake），必须主动审查是否拆级。

### 禁止的误解

- “Vivado 会优化，所以先一拍写完再说”不是合格理由。
- “多加一个寄存器”不等于有效 pipeline；必须真正切断最差组合工作。
- AMD 没给统一 LUT 层级阈值，因此不能伪造“超过 4 层一定失败”之类硬结论。

---

## UG949-T2：识别 large fan-in / deep cone

官方章节：
https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Check-Inferred-Logic

### 典型危险 RTL

```text
所有 slot 条件
  -> 多路 AND/OR
  -> priority select
  -> encoded index
  -> 宽 mux
  -> compare
  -> state/valid 更新
```

或者：

```verilog
if (cond0 && a0 && b0)
    sel = 0;
else if (cond1 && a1 && b1)
    sel = 1;
else if (...)
    ...
```

这些结构常把 large fan-in、priority mux、宽选择叠在一条寄存器到寄存器路径上。

### 推荐结构

根据功能语义选择：

- candidate bitmap -> register -> encoder；
- 分组 reduction/tree；
- 分层仲裁；
- free/ready FIFO 或 pointer，避免每拍扫描所有 entry；
- predecode 后寄存，再做选择；
- 把宽比较与最终 mux 分开。

**关键：不要只把同一逻辑换一种更短语法。** `for`、function、长 ternary、长 if-chain 如果综合出来仍是同一深锥，就没有解决结构问题。

---

## UG949-T3：在逻辑/层级边界注册 datapath

官方章节：
https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Register-Data-Paths-at-Logical-Boundaries

UG949 建议注册 hierarchy boundary 的输出，并视情况注册输入，使 critical path 尽量包含在单个模块或逻辑边界内。这样更容易分析、修复，也减少跨层级优化导致的可追踪性损失。

### Agent 应用

对高频 datapath：

- 模块边界优先输出寄存；
- 如果接口 latency 允许，输入也可先寄存；
- AXIS 跨大功能模块时优先使用 register slice / skid buffer；
- 不要为了“模块化”制造 `module A combinational -> module B combinational -> module C combinational` 的长跨层级路径。

### 边界

不能为了遵守本规则偷偷改变用户已经冻结的可见 latency、命令先后或 packet boundary。若新增 latency，必须同步平衡 valid / metadata / tlast / tkeep / control。

---

## UG949-T4：高 fanout 要早处理，但不要盲目复制

官方相关章节：

- High Fanouts in Critical Paths  
  https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/High-Fanouts-in-Critical-Paths
- Resets  
  https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Resets

UG949 指出 high fanout 的危险程度取决于目标频率和路径结构，不能定义一个通用固定阈值；同时建议综合后使用 `report_high_fanout_nets` 监控。

### RTL 阶段先做什么

- 不让全局 `mode/flush/enable/debug` 无意义驱动整个 datapath；
- 把状态派生控制局部化；
- 跨大模块边界时寄存控制；
- debug/status 旁路采样，避免重新参与主路径；
- reset 只作用于真正需要确定初始化的控制状态。

### 不要做什么

- 不要手工复制 synchronizer 第一/第二级寄存器来“降 fanout”；
- 不要仅凭 fanout 数字就做大规模 RTL 重构；必须结合 timing path 与布局信息。

---

## UG949-T5：RAM 读路径默认考虑输出寄存

官方章节：
https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Performance-Considerations-When-Implementing-RAM

UG949 强调 memory 的实现类型会直接影响频率和功耗。对高性能 RAM 路径，核心工程原则是不要让 RAM 本身的访问延迟后面继续挂一大串组合逻辑。

### 推荐结构

```text
地址/控制生成
    -> address/control register（大 bank/长控制路径时）
    -> RAM
    -> RAM output register
    -> 后续解析 / mux / compare
```

### 危险结构

```text
复杂地址 -> BRAM -> 宽 mux -> compare -> priority -> 目标寄存器
```

### Agent 检查

写 RAM wrapper / descriptor table / packet buffer 时必须说明：

- 期望 infer BRAM / URAM / LUTRAM 还是不限定；
- read latency；
- 输出是否寄存；
- RAM read data 后同拍还做了哪些组合操作；
- reset 是否会破坏 memory inference。

---

## UG949-T6：DSP / arithmetic 要利用内部 pipeline

官方章节：
https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Coding-for-Optimal-DSP-and-Arithmetic-Inference

AMD 明确说明 DSP block 是高度流水化资源，并建议打算映射到 DSP48 的代码充分流水以利用内部寄存级。同时，不必要的 set 会妨碍某些 DSP48 寄存器映射。

### Agent 应用

- 乘法/MAC/宽加法链先考虑目标是否 DSP；
- latency 允许时把 arithmetic 拆成与 DSP 内部寄存结构相匹配的级；
- 不要 multiplier -> adder -> comparator -> mux 全塞一拍；
- 位宽只保留业务真正需要的精度，避免无意义扩大 arithmetic cone；
- 不要为 datapath 每级机械增加 set/reset。

---

## UG949-T7：Reset 是 timing/resource 架构的一部分

官方章节：

- Resets  
  https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Resets
- Reset and Clock Enable Precedence  
  https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Reset-and-Clock-Enable-Precedence
- Reducing Control Sets  
  https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Reducing-Control-Sets

UG949 明确指出 reset 会显著影响最大频率、面积和功耗，并可能改变 SRL / RAM / DSP 等资源的推断。AMD 还明确建议在同时存在 reset 与 clock enable 的同步块中，先写 reset，再写 enable；否则 reset 可能被推入 data path 并增加逻辑。

### Agent 应用

- 已有工程：保持外部 reset polarity/语义，不擅自改接口；
- 内部 datapath：优先 reset `valid/state/pointer`，无效数据位不必全部 reset；
- 大数组、shift pipeline、RAM data 不做“习惯性清零”；
- reset + CE：reset 优先；
- 新高性能 block 无历史约束时，内部控制尽量保持简单、统一的 control set。

### 一个重要区分

“减少 reset”不等于“删除功能必须的 reset”。协议状态机、FIFO pointer、有效位、跨域 reset release 等仍必须按功能需要设计。

---

## UG949-T8：RTL 阶段就使用 methodology 思维

官方章节：

- Using the UltraFast Design Methodology DRCs  
  https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Using-the-UltraFast-Design-Methodology-DRCs
- Running Report Methodology  
  https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Running-Report-Methodology

Vivado 的 `report_methodology` 能在 RTL、综合后、实现后分别检查不同类型的方法论问题。UG949 推荐在多个阶段执行并处理 Critical Warning / Warning。

### 本 Skill 的默认边界

本 Skill 默认**不主动启动 Vivado**。Coding Agent 应：

1. 先用 `scripts/check_rtl_style.py` 做轻量静态预检；
2. 告知用户后续可在 Vivado 中运行 `report_methodology`；
3. 用户明确要求工具验证时，再执行项目允许的 Vivado 检查。

---

## UG949 快速审查表

写 RTL 前问：

- 这条 register-to-register path 做了几类依赖操作？ `[UG949-T1/T2]`
- 是否存在每拍全表扫描、large reduction 或 long priority？ `[UG949-T2]`
- 功能边界是否有合理寄存？ `[UG949-T3]`
- reset/mode/enable 是否成为全局高 fanout？ `[UG949-T4/T7]`
- RAM 输出后是否继续大规模组合？ `[UG949-T5]`
- DSP/算术是否真正利用 pipeline？ `[UG949-T6]`
- 有没有为了初始化方便而 reset 整个 datapath？ `[UG949-T7]`
