# Timing by Construction：从 RTL 结构上避免时序问题

核心原则：**先控制 register-to-register 组合逻辑锥的形状，再依赖 Vivado 优化。**

本文件是执行层规则。官方依据已经拆成三份深度参考：

- `amd_ug949_rtl_methodology.md`：设计阶段如何主动避免 timing/resource 风险；
- `amd_ug901_synthesis_coding.md`：Verilog 写法如何影响 Vivado inference；
- `amd_ug906_timing_analysis.md`：以后如何用 Vivado 报告证明问题并回到 RTL。

规则 ID 例如 `[UG949-T2]`、`[UG901-P1]`、`[UG906-A1]` 可直接回查上述文档。

---


## 目录

- [0. 写代码前先过 Timing Design Gate](#0-写代码前先过-timing-design-gate)
- [1. 什么叫“深逻辑锥”](#1-什么叫深逻辑锥-ug949-t1t2-ug906-a1a2)
- [2. Pipeline 要切真正的 critical cone](#2-pipeline-要切真正的-critical-cone-ug949-t1)
- [3. Priority chain](#3-priority-chain先问业务真的需要优先级吗-ug901-p1-ug949-t2)
- [4. Large fan-in](#4-large-fan-in不要每拍把全世界归约成一个信号-ug949-t2)
- [5. High fanout](#5-high-fanout先减少-load再谈-replication-ug949-t4-ug906-a3)
- [6. AXI4-Stream ready/valid](#6-axi4-stream-readyvalid-ug949-t3)
- [7. RAM/BRAM/URAM](#7-rambramuram-ug949-t5-ug901-m1)
- [8. DSP / Arithmetic](#8-dsp--arithmetic-ug949-t6-ug901-d1v1)
- [9. Reset / Control Set](#9-reset--control-set-ug949-t7-ug901-r1)
- [10. Static Review Red Flags](#10-static-review-red-flags启发式不是-amd-sign-off-阈值)
- [11. Vivado 证据闭环](#11-vivado-证据闭环-ug906-a1a6)
- [12. 修改已有代码时绝对不能随意改变](#12-修改已有代码时绝对不能随意改变)

## 0. 写代码前先过 Timing Design Gate

任何高频 datapath、AXIS、RAM/FIFO wrapper、scheduler、仲裁、packet/video pipeline、宽算术或多 entry 管理模块，在开始最终编码前先回答：

1. **寄存级是什么？** 输入到输出计划几拍？
2. **最可能的关键组合锥是什么？** decode / priority / mux / arithmetic / compare / reduction / ready？
3. **有没有每拍扫描全部 entry 的结构？** 能否用 bitmap、pointer、FIFO、tree 或分级处理？
4. **RAM/DSP 前后寄存在哪里？** `[UG949-T5/T6]`
5. **ready/backpressure 会不会组合穿越多个功能块？** `[UG949-T3]`
6. **reset/mode/flush/enable 是否驱动过多 datapath？** `[UG949-T4/T7]`
7. **新增 pipeline 是否改变外部可见 latency？** 如果会，必须先尊重用户接口合同。

若目标频率未知，不要虚构 MHz；但也不能因此把所有功能挤进一个周期。

---

## 1. 什么叫“深逻辑锥” `[UG949-T1/T2] [UG906-A1/A2]`

不是按 Verilog 行数判断，而是看一个寄存器到另一个寄存器之间是否存在多级**相互依赖**的组合工作。例如：

```text
state decode
  -> 多条件候选生成
  -> priority 仲裁
  -> 宽字段 mux
  -> 加法/减法
  -> 宽比较
  -> 再次 mux
  -> 输出寄存器
```

Vivado 可以做逻辑优化，但相互依赖的工作不可能全部变成真正并行。

### 生成代码时的红旗

- 很长的 `if / else if / else if ...`；
- 多层嵌套 `?:`；
- 一个 `always @(*)` 同时做 decode、算术、比较、选择和握手；
- 一个输出依赖大量独立条件的 AND/OR；
- 大宽度可变索引/可变移位后继续做算术或比较；
- 宽 checksum/CRC/XOR reduction 一拍完成后继续参与控制；
- `tready` 从下游一路组合传播到多个上游模块；
- RAM 地址生成 -> RAM 输出 -> 复杂逻辑 -> 同拍寄存；
- state + counter compare + payload select + handshake 全塞一拍。

这些不是“看到就一定 timing fail”，但 Agent 必须主动审查和说明。

---

## 2. Pipeline 要切真正的 critical cone `[UG949-T1]`

如果接口允许增加内部延迟，先按硬件责任划 stage：

```text
Stage 0: 输入握手/寄存
Stage 1: 字段预译码、基础比较
Stage 2: 仲裁/索引/算术
Stage 3: RAM/DSP 或核心处理
Stage 4: 最终 mux / 输出寄存
```

目标不是 pipeline 越多越好，而是每级的组合工作相对单一、平衡。

### 不好的假流水

```text
复杂逻辑 A+B+C+D -> reg -> 简单连线 -> reg
```

### 更好的拆法

```text
A+B -> reg -> C+D -> reg
```

如果新增 pipeline，必须一起平衡：

- valid；
- metadata；
- ID/sequence；
- tlast/tkeep；
- error flag；
- command/feedback relation。

不能只 pipeline data。

---

## 3. Priority chain：先问“业务真的需要优先级吗？” `[UG901-P1] [UG949-T2]`

长 `if/else-if` 或按顺序匹配的选择通常会表达 priority semantics。

### 真需要 priority

例如固定优先级仲裁、异常覆盖顺序。此时：

- 中文注释说明为什么前面的条件必须压过后面；
- 分支很多时考虑两级/分组仲裁；
- 高速路径必要时把 candidate 生成与 winner 编码拆拍。

### 不需要 priority

如果只是“从多个互斥源中选一个”或“任意 free slot 均可”，优先：

- predecode；
- one-hot；
- parallel selection；
- bitmap + encoder；
- queue/pointer 代替每拍扫描。

### synthesis attribute 边界

UG901 提到 `parallel_case`，但本 Skill **禁止 Agent 机械贴属性**。只有真实互斥/覆盖关系已被设计保证，并且项目约定允许时才使用。优先让 RTL 自己正确表达语义。

---

## 4. Large fan-in：不要每拍把全世界归约成一个信号 `[UG949-T2]`

常见来源：

- `all_ready = cond0 && cond1 && ... && condN`；
- 多错误位汇总；
- 多 descriptor/slot 的 ready/free scan；
- 宽总线全零/全一判断；
- 多路状态共同决定一个 enable；
- 大 XOR/reduction。

优先方案：

- 局部 precompute 后寄存；
- 分组 tree/reduction；
- candidate bitmap；
- 分阶段 scan；
- FIFO / encoded pointer 取代“每拍看所有 entry”。

不要把同一硬件复杂度仅改写成 function/for-loop 就认为优化完成。

---

## 5. High fanout：先减少 load，再谈 replication `[UG949-T4] [UG906-A3]`

常见 high-fanout candidate：

- reset；
- mode；
- global enable；
- flush；
- state-derived enable；
- debug/status control。

RTL 阶段优先：

- 去掉不需要的 load；
- 局部派生控制；
- 功能边界寄存；
- debug/status 不反向参与主 datapath；
- reset 只覆盖需要确定状态的寄存器。

真正 fanout 是否成为 timing 问题，要让用户用：

```tcl
report_high_fanout_nets
```

验证。不要手工复制 CDC synchronizer 寄存器。

---

## 6. AXI4-Stream ready/valid `[UG949-T3]`

- 明确定义 `fire = valid && ready`；
- `m_axis_tvalid && !m_axis_tready` 时 payload/qualifier/valid 必须稳定；
- 不让 `tready` 组合穿透多个大功能模块；
- 使用 register slice / skid buffer / registered-ready boundary 切断 backpressure 路径；
- 若模块明确不支持 backpressure，必须写进接口合同，不能静默丢数。

### 重点

数据路径已经 pipeline 并不代表 AXIS 路径安全。高速串联系统经常最后爆在：

```text
A.ready <- B.ready <- C.ready <- D.ready
```

所以 ready chain 是单独的 timing review item。

---

## 7. RAM/BRAM/URAM `[UG949-T5] [UG901-M1]`

写 memory 前先确定：

- 端口类型；
- read/write semantics；
- read latency；
- 输出寄存；
- read-during-write；
- reset/initialization；
- 预期 inference。

高频优先结构：

```text
地址/控制寄存 -> RAM -> 输出寄存 -> 后处理
```

避免：

```text
复杂地址 -> RAM -> 宽 mux -> compare -> priority -> 目标寄存
```

大 memory array 不要为了“清零方便”机械 reset，否则可能破坏 BRAM/URAM inference。

---

## 8. DSP / Arithmetic `[UG949-T6] [UG901-D1/V1]`

- 明确 operand signed/unsigned；
- 明确全精度结果宽度与有意截断；
- 不需要的高位不要无意义扩大后续逻辑；
- 目标为 DSP 时充分利用 pipeline；
- multiplier -> add -> compare -> mux 不要默认一拍；
- 避免无必要 set/reset 破坏专用资源寄存器利用。

特别审查：

- 宽 counter + compare；
- address arithmetic；
- checksum/CRC；
- variable shift；
- mixed-sign expression。

---

## 9. Reset / Control Set `[UG949-T7] [UG901-R1]`

- 已有工程保持 reset polarity/语义；
- 内部 datapath 优先 reset `valid/state/pointer`；
- stale data 被 valid 屏蔽时，不必 reset 每一位数据；
- 同步块同时有 reset + enable 时，reset 先判断；
- RAM、DSP、SRL 目标逻辑避免不必要 set/reset；
- reset 本身也是 high-fanout control，应限制范围。

不要把“减少 reset”误解成“状态机/FIFO pointer 也不用 reset”。功能必须的初始化不能删除。

---

## 10. Static Review Red Flags：启发式，不是 AMD sign-off 阈值

AMD 没有规定一个与器件/频率无关的“最大 LUT 层级”。因此以下只作为 Agent 的预警门槛：

- 一个周期内出现 **3 类以上相互依赖复杂操作** -> 默认拆级审查；
- **4 个以上真实顺序 priority branch** -> 检查分组/流水/架构替代；
- `ready` 跨多个功能模块组合传播 -> 默认评估切断；
- 大规模 entry scan / reduction / wide variable mux -> 默认评估 tree/encoder/queue/pipeline；
- RAM/DSP 高速路径没有利用输出/内部寄存 -> 默认风险；
- 大数据总线全部 reset -> 默认检查是否只需 valid reset。

`scripts/check_rtl_style.py` 的 warning 基于这些启发式，不替代 Vivado。

---

## 11. Vivado 证据闭环 `[UG906-A1~A6]`

如果用户后续发现 timing violation，优先看：

```tcl
report_methodology
report_timing_summary
report_design_analysis
report_high_fanout_nets
```

分析顺序：

```text
logic levels 高?
  -> 回 RTL 看 priority/mux/reduction/pipeline
fanout 高?
  -> 看 control locality/load
net delay 高?
  -> 看 placement/region/congestion/route
RAM/DSP path?
  -> 看 inference 和内部/输出寄存
```

不要在没有 timing evidence 时武断声称“这就是某 N 层 LUT”；也不要在明显深锥存在时以“还没跑 Vivado”为理由不做前置结构优化。

---

## 12. 修改已有代码时绝对不能随意改变

- AXIS packet boundary；
- command/feedback 顺序；
- 可见 latency；
- counter 的精确 fire 边界；
- CDC protocol；
- RAM read latency contract；
- flush/pulse 的边沿语义；
- error/drop 的原子边界。

如果 timing 需要新增 latency，必须连同所有伴随控制信号一起调整并明确说明新增几拍。
