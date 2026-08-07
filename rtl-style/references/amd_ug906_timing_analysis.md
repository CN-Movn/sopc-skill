# UG906 工程化提炼：从 Vivado Timing 证据回到 RTL

来源：AMD **Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906)**。

当前核验基线：2026.1 English，2026-06-23。

官方入口：
https://docs.amd.com/r/en-US/ug906-vivado-design-analysis

UG906 不是“怎么写 Verilog”的编码规范，而是告诉工程师如何从 Vivado 的 timing/logic/physical 证据判断问题到底来自哪里。对 Coding Agent 来说，它的价值是：**不要凭感觉说某段 RTL 一定时序差；要知道以后用什么报告证明或证伪。**

---


## 目录

- [UG906-A1：Logic Level Distribution](#ug906-a1用-logic-level-distribution-找深逻辑路径)
- [UG906-A2：高 logic level 反查 RTL](#ug906-a2高-logic-level-时必须反查-rtl-结构)
- [UG906-A3：High Fanout Net Report](#ug906-a3用-high-fanout-net-report-证明-fanout-问题)
- [UG906-A4：区分 logic delay 与 routing/physical delay](#ug906-a4区分-logic-delay-与-routingphysical-delay)
- [UG906-A5：Timing 结论依赖正确约束](#ug906-a5timing-结论必须建立在正确约束上)
- [UG906-A6：报告形成闭环](#ug906-a6报告是闭环不是生成-rtl-的前置依赖)
- [Timing violation 时的 Agent 输出格式](#当用户给出-timing-violation-时的-agent-输出格式)

## UG906-A1：用 Logic Level Distribution 找深逻辑路径

官方章节：
https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/Timing-Path-Characteristics-Report

`report_design_analysis` 的 Timing mode 可以查看最差 setup path 的特征，并可生成 Logic Level Distribution。当前 2026.1 文档明确支持按 logic level 分布分析，并提供 `-logic_level_distribution` 相关命令行能力。

### 对 RTL Agent 的意义

当静态审查看到：

- decode -> priority -> mux -> arithmetic -> compare；
- 多层 ternary；
- 大 entry scan；
- RAM output 后长组合；

不要写“这一定有 12 层 LUT”。正确说法是：

> 这是深组合锥风险结构；如果用户运行 Vivado，应通过 `report_design_analysis` / logic-level distribution 检查它是否真实进入 worst path。

### 交付建议

如果用户已经给了 timing report：

- 记录 startpoint / endpoint；
- 看 logic levels；
- 区分 cell delay 与 net delay；
- 判断路径是否跨 hierarchy / SLR / RAM / DSP；
- 再决定是 RTL 拆级、降低 fanout、换推断结构还是物理优化。

---

## UG906-A2：高 logic level 时必须反查 RTL 结构

官方章节：
https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/Using-the-Elaborated-View-to-Optimize-the-RTL

UG906 明确指出：分析 `report_timing`、`report_timing_summary` 或 `report_design_analysis` 时，应查看 critical path 是否可以通过修改 RTL、综合属性或综合选项更高效地映射；**尤其是 logic levels 很多的路径**，会给 implementation tool 带来压力并限制整体性能。

### Agent 判断顺序

1. 路径功能真的需要这么深吗？
2. 是无意 priority / 宽 mux / reduction 造成的吗？
3. 能否 predecode、tree、pipeline？
4. 是否因为层级边界没有寄存？
5. 是否 RAM/DSP 没有使用内部/输出寄存？
6. 如果逻辑层级不高但 delay 仍大，再看 fanout / route / placement。

### 禁止的做法

看到 setup violation 就直接建议：

- “换 implementation strategy”；
- “多跑几个 seed”；
- “加 false path”；
- “降频”；

这些都不能替代先判断 RTL 结构是否低效。

---

## UG906-A3：用 High Fanout Net Report 证明 fanout 问题

官方章节：

- Report High Fanout Nets  
  https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/Report-High-Fanout-Nets
- Generating the High Fanout Net Report  
  https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/Generating-the-High-Fanout-Net-Report

`report_high_fanout_nets` 可在综合后、布局后、布线后运行。报告包含 fanout、driver type、load types，布局后还可以包含 clock region / SLR 信息。

### Agent 使用边界

静态代码里看到 `reset/mode/flush/enable` 驱动大量寄存器，只能标记 **high-fanout candidate**。

真正判断其是否影响 timing：

```tcl
report_high_fanout_nets -name hfn_1
```

必要时结合 timing 信息看 worst slack。

### 为什么不能手工乱复制

fanout 问题通常同时包含逻辑分布与物理路由。RTL 手工复制 driver 可能：

- 破坏 CDC 结构；
- 增加 control set；
- 让功能/约束更复杂；
- 反而限制 Vivado 的物理优化。

所以优先从“减少不必要 load、局部化控制、寄存边界”入手。

---

## UG906-A4：区分 logic delay 与 routing/physical delay

UG906 的 timing path characteristics 不只看 logic levels；路径特征还用于判断 delay 来源。

### RTL Agent 应避免的错误归因

- logic levels 很少但 net delay 巨大：不一定是“代码逻辑太深”；可能是 fanout、跨区域、拥塞或 placement。
- logic levels 很多：即使 routing 看起来正常，也应优先回 RTL 检查结构。
- RAM/DSP 路径：要看 primitive 与前后寄存关系，不要只数 Verilog 运算符。

### 推荐诊断树

```text
setup fail
  |
  +-- logic levels 高? -- yes --> RTL mapping / priority / mux / pipeline
  |
  +-- fanout 高? ------- yes --> control locality / load reduction / HFN report
  |
  +-- net delay 高? ---- yes --> placement / region / congestion / physical path
  |
  +-- RAM/DSP? --------- yes --> internal/output register + inference review
```

---

## UG906-A5：Timing 结论必须建立在正确约束上

UG906 是 timing analysis / closure guide，任何 WNS/TNS 与 path 分析都依赖时钟和 timing constraints 正确。

因此 Agent 不得：

- 在没有确认 clock/constraint 的情况下把某条“未约束路径”当真实 signoff；
- 用 `set_false_path` 掩盖功能同步路径；
- 用 multicycle path 当作修 timing 的通用手段；
- 把“综合后 timing 看起来好”直接等同于 route 后 closure。

本 Skill 默认不修改 XDC，除非用户明确要求并提供真实时钟/接口事实。

---

## UG906-A6：报告是闭环，不是生成 RTL 的前置依赖

对 Coding Agent 的正确闭环：

```text
RTL 结构预防
  -> 静态 style/timing-risk review
  -> 用户仿真
  -> synthesis / report_methodology
  -> implementation
  -> report_timing / report_design_analysis / report_high_fanout_nets
  -> 若失败，再将证据映射回 RTL
```

不要反过来认为“没有 Vivado report 就可以随便写深组合逻辑”。UG949 负责前置方法论，UG906 负责后置证据。

---

## 当用户给出 timing violation 时的 Agent 输出格式

建议按以下结构分析：

1. **路径现象**：startpoint、endpoint、WNS、logic level、fanout/route 特征。
2. **RTL 原因树**：priority / mux / wide compare / RAM / DSP / high fanout / ready chain / CDC 等。
3. **最小修改方案**：不先大改架构，优先切真正 critical cone。
4. **协议/latency 风险**：新增 pipeline 对 valid/metadata/packet boundary 的影响。
5. **复验命令/报告**：用户应重新看哪些 Vivado report。

这使“时序优化”从经验猜测变成可验证工程闭环。
