# RTL 交付前检查清单

生成或修改非平凡 RTL 后逐项检查。高频模块在编码前还应先执行 `timing_by_construction.md` 的 Timing Design Gate。

## A. 注释与可维护性

- [ ] 模块顶部有中文：模块名称、功能、时钟复位、接口、延迟/数据流、CDC、时序设计、边界说明。
- [ ] 新增 FSM / pipeline / handshake / counter / CDC / RAM/FIFO 逻辑有中文意图注释。
- [ ] 注释描述“为什么/边界/硬件后果”，而不是逐行翻译。
- [ ] 注释与当前实现一致。

## B. 组合逻辑锥 `[UG949-T1/T2] [UG906-A1/A2]`

逐个主要寄存器输出 -> 目标寄存器检查：

- [ ] 是否同拍串了 decode -> priority/mux -> arithmetic -> compare -> mux？
- [ ] 是否存在大 fan-in 条件或大规模 entry scan？
- [ ] 是否存在长 `else if` / nested ternary，且 priority 并非协议必须？
- [ ] 是否存在大宽度 variable mux / shift / XOR 后继续深加工？
- [ ] pipeline 是否真正切开了复杂逻辑，而不是在简单位置堆寄存器？

发现以上结构时：能保持接口契约则拆级；不能改 latency 则在交付说明中明确标为 timing risk。

## C. AXI4-Stream `[UG949-T3]`

- [ ] `fire = valid && ready` 语义清晰。
- [ ] `valid && !ready` 时输出 data/keep/last/user/valid 稳定。
- [ ] 没有明显长 `ready` 组合链穿过多个 block。
- [ ] pipeline/skid 后所有 sideband 与 payload 延迟一致。
- [ ] packet/flush/tlast 边界没有因流水增加 off-by-one。

## D. FSM / priority / counters `[UG901-P1/F1/V1]`

- [ ] priority 是功能需求，而不是编码习惯造成的。
- [ ] 非 priority 多路选择优先使用 predecode/one-hot/bitmap/parallel 结构。
- [ ] 未机械添加 `parallel_case` 等 synthesis attribute；若使用，有真实互斥/覆盖依据。
- [ ] counter 的 `<`, `<=`, `== LAST`, `+1` 边界逐项推演。
- [ ] timeout/retry/length counter 无 off-by-one。
- [ ] arithmetic width/sign/截断是有意设计。
- [ ] state decode 没有直接形成高 fanout 深组合控制树。

## E. RAM / DSP / resource inference `[UG949-T5/T6] [UG901-M1/D1]`

- [ ] RAM 端口、同步读、read-during-write、read latency 与已有合同一致。
- [ ] 高频 RAM 输出有合理寄存级。
- [ ] 大 RAM 的地址/控制路径没有一拍做过多复杂运算。
- [ ] 没有因为机械 reset 整个数组而破坏 RAM/SRL/DSP 推断。
- [ ] multiplier / MAC / 宽 arithmetic 在需要高 Fmax 时已考虑 DSP/流水。
- [ ] 不需要的 operand/result 高位已避免无意义扩大逻辑锥。

## F. Reset / CDC / fanout `[UG949-T4/T7] [UG901-R1]`

- [ ] reset 范围尽可能小，datapath data 寄存器不做无意义 reset。
- [ ] reset 与 clock enable 同时存在时，reset 优先编码。
- [ ] RAM/DSP/SRL 目标逻辑没有无必要 set/reset。
- [ ] multi-bit CDC 没有直接双触发器逐位同步。
- [ ] synchronizer 之后的高 fanout 分发没有破坏同步链。
- [ ] debug/status 不反向进入主通路形成新 critical cone。

## G. Vivado 证据闭环 `[UG906-A1~A6]`

- [ ] 如果只做静态审查，只说“静态检查”。
- [ ] 未运行 Vivado 时，不说“时序已收敛”。
- [ ] 若用户提供 timing report，依据 startpoint/endpoint、logic levels、cell/net delay、fanout/route 分析，不只看 WNS。
- [ ] high fanout 只在静态阶段称为 candidate；实际影响用 `report_high_fanout_nets` 验证。
- [ ] deep cone 只在静态阶段称为 risk；实际 logic level 用 `report_design_analysis` / logic-level distribution 验证。
- [ ] 不用 false path / multicycle / implementation directive 掩盖没有解释清楚的 RTL 结构问题。

## H. 功能验证与交付证据

- [ ] 已确认本次修改的功能合同、不变量、可见 latency、吞吐和 reset 行为。
- [ ] 已运行 `check_rtl_style.py`；若未运行，交付说明中写明原因。
- [ ] 已查找并优先复用项目现有 lint、testbench、仿真或回归入口，没有用静态 checker 代替功能验证。
- [ ] 针对改动覆盖 reset、连续事务、停顿/恢复、边界值和异常路径；AXIS 还覆盖 backpressure 下 payload/sideband 稳定及 `tlast` 边界。
- [ ] counter/FSM/RAM/FIFO/DSP/CDC 按适用范围验证 off-by-one、非法/恢复状态、读写延迟或冲突语义、位宽/符号以及跨域事件不会丢失/重复；静态或普通仿真不能证明的 CDC 性质明确保留为验证边界。
- [ ] 交付结果逐层标明实际完成的 checker、lint/elaboration、simulation、synthesis、implementation、timing、CDC 和硬件验证，不把较低层级结果升级表述。
