# AMD/Xilinx 官方方法论导航

本文件是官方资料路由页，不再重复堆砌所有结论。

首先阅读 `official_source_verification.md` 确认三份文档的真实来源、版本与核验边界，然后按任务进入对应深度参考。

## 1. UG949 - 设计阶段主动避免 timing/resource 问题

深度提炼：`amd_ug949_rtl_methodology.md`

官方文档：AMD *UltraFast Design Methodology Guide for FPGAs and SoCs (UG949)*，当前核验 2026.1。

重点 rule ID：

- `UG949-T1`：pipeline 是架构，不是 post-route 补丁；
- `UG949-T2`：large fan-in / deep combinational cone；
- `UG949-T3`：register logical boundaries；
- `UG949-T4`：high fanout；
- `UG949-T5`：RAM performance / output register；
- `UG949-T6`：DSP/arithmetic pipeline；
- `UG949-T7`：reset/control set；
- `UG949-T8`：methodology DRC 闭环。

## 2. UG901 - HDL 写法与 Vivado inference

深度提炼：`amd_ug901_synthesis_coding.md`

官方文档：AMD *Vivado Design Suite User Guide: Synthesis (UG901)*，当前核验 2026.1。

重点 rule ID：

- `UG901-S1`：代码即硬件推断；
- `UG901-P1`：避免无意 priority processing；
- `UG901-R1`：reset/set/CE 与 primitive mapping；
- `UG901-M1`：RAM inference；
- `UG901-D1`：DSP/multiplier；
- `UG901-F1`：FSM inference；
- `UG901-L1`：Vivado RTL Linter；
- `UG901-V1`：位宽/符号审查。

## 3. UG906 - 用 timing 证据回到 RTL

深度提炼：`amd_ug906_timing_analysis.md`

官方文档：AMD *Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906)*，当前核验 2026.1。

重点 rule ID：

- `UG906-A1`：Logic Level Distribution；
- `UG906-A2`：高 logic level 反查 RTL；
- `UG906-A3`：High Fanout Net Report；
- `UG906-A4`：logic delay vs route/physical delay；
- `UG906-A5`：timing 结论依赖正确约束；
- `UG906-A6`：RTL -> report -> RTL 的验证闭环。

## 4. 官方规则与 Skill 启发式边界

AMD 官方没有给所有器件、频率、路径统一规定“超过 N 层 LUT / N 个 if 就失败”。

因此：

- `timing_by_construction.md` 中的“3 类复杂操作”“4 个 priority branch”等是 **Agent 静态预警启发式**；
- 真正 timing sign-off 必须来自正确约束下的 Vivado synthesis/implementation timing report；
- Skill 可以在 RTL 阶段主动规避明显结构风险，但不能虚构 WNS、logic level 或 closure 结果。
