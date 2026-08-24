---
name: rtl-style
description: Verilog RTL coding and review discipline for AMD/Xilinx Vivado FPGA work, with mandatory Chinese module/header comments and timing-friendly RTL structure. Use when creating, modifying, refactoring, or reviewing synthesizable Verilog, especially AXI4-Stream datapaths, FSMs, counters, packet/video processing, FIFO or RAM wrappers, CDC logic, high-speed control paths, or code that must avoid deep combinational cones, high fanout, priority chains, and timing-closure regressions.
---

# RTL Style

Use this skill for synthesizable Verilog RTL. Generated RTL must be functionally correct, documented in Chinese, predictable for Vivado inference, and timing-friendly by construction.

Default to Verilog-2001. Do not introduce SystemVerilog unless the user explicitly requests it or the surrounding project already uses it.

## Mandatory reference routing

Before writing or materially modifying RTL:

1. Always read `references/comment_contract.md` for the mandatory Chinese-comment contract.
2. For nontrivial datapaths or control logic, AXI4-Stream/backpressure, CDC, RAM/DSP, arbitration/scheduler logic, wide mux/reduction/entry scans, or any timing-sensitive change, read `references/timing_by_construction.md` and complete its **Timing Design Gate** before final coding. Simple local edits such as field renames, small register changes, or straightforward counters may rely on the concise timing hard contract in this file without loading the full timing reference.
3. For high-speed datapaths, arbitration/scheduler logic, RAM/DSP, wide mux/reduction, AXIS backpressure, reset/control-set, or other timing-sensitive structures, read `references/amd_ug949_rtl_methodology.md`.
4. When Verilog syntax affects synthesis inference (priority/case, RAM, DSP, FSM, reset/set/CE, width/sign), read `references/amd_ug901_synthesis_coding.md`.
5. When diagnosing existing Vivado timing evidence, read `references/amd_ug906_timing_analysis.md`.
6. Use `references/official_source_verification.md` for AMD/Xilinx source authenticity and version metadata. Do not invent document claims.
7. For a new ordinary module, start from `references/verilog_module_skeleton.v`. For an AXI4-Stream registered boundary, also use `references/axis_registered_stage_template.v`.
8. Before delivery, apply `references/rtl_review_checklist.md`. Run `scripts/check_rtl_style.py` on every new or materially changed `.v`/`.sv` file when Python and the files are available; if it cannot run, state the reason instead of silently skipping it.

Use `references/amd_xilinx_official_guidance.md` as the compact index for `[UG949-*]`, `[UG901-*]`, and `[UG906-*]` rule IDs.

## Priority order

When rules compete:

1. Preserve the user's stated protocol, interface, reset, latency, and functional behavior.
2. Preserve deliberate conventions already integrated into the project.
3. Prevent CDC/protocol violations, latches, combinational loops, and multi-driver registers.
4. Avoid preventable timing hazards in RTL architecture.
5. Follow formatting and naming preferences.

Do not silently add observable latency. If latency is not fixed and a high-speed path is nontrivial, prefer an architecture that genuinely shortens the register-to-register combinational path.

## Hard contract: synthesizable RTL

- Generate synthesizable design RTL; keep simulation-only constructs in testbenches.
- Use ``default_nettype none`` and restore ``default_nettype wire`` at the end of standalone source files.
- Do not infer latches, create combinational loops, or assign one `reg` from multiple `always` blocks.
- Use nonblocking assignments (`<=`) in sequential logic and blocking assignments (`=`) in combinational logic.
- Give combinational next-state/output logic complete defaults and give each `case` a `default` unless a verified local convention deliberately replaces it.
- Replace unexplained magic numbers with named `parameter` or `localparam` values.
- Do not gate fabric clocks as a substitute for clock-enable logic.
- Do not directly sample signals across clock domains.
- Make arithmetic width and signedness intentional. `[UG901-V1]`
- Do not claim simulation, synthesis, implementation, timing closure, CDC, or hardware validation passed unless those checks were actually run.

## Hard contract: Chinese comments

Chinese comments are mandatory, not style polish.

- Every new RTL module, and every materially rewritten existing module, must contain the full Chinese header defined in `references/comment_contract.md`.
- Add Chinese intent comments around nontrivial FSM/control, handshake/backpressure, counter boundaries, exception/drop paths, CDC, RAM/FIFO behavior, pipeline stages, and timing-specific implementation choices.
- Keep identifiers in English. Comments must explain intent, boundaries, invariants, latency, or hardware consequences rather than translate syntax line by line.
- Keep comments proportional to design risk. Do not add comments to meet a line count or density target, repeat the same statement around every assignment, or narrate obvious syntax.

Missing required header fields or leaving nontrivial timing/CDC/control logic undocumented makes the module incomplete.

## Hard contract: Timing by Construction

Treat timing as an RTL architecture concern, not a post-implementation cleanup step.

Before final coding, identify the register-stage plan, likely deepest combinational cone, large fan-in/entry-scan candidates, high-fanout controls, RAM/DSP boundaries, AXIS backpressure path, CDC boundaries, and sideband/valid signals that must remain latency-aligned. For a nontrivial change, make these decisions reviewable with a concise pre-coding design summary; do not leave them as unstated assumptions. If target frequency is unknown, do not invent one; still avoid obviously deep single-cycle structures. `[UG949-T1/T2]`

The following conclusions are mandatory; detailed rationale, examples, and alternatives live in `references/timing_by_construction.md`:

- Break dependent decode/priority/mux/arithmetic/compare/reduction work across meaningful register stages when latency permits; a pipeline stage must actually reduce critical combinational work. `[UG949-T1/T2]`
- Do not create accidental priority chains. If priority is required, make and document it explicitly; otherwise prefer structures matching parallel semantics. `[UG901-P1]`
- Treat large fan-in, wide reductions/equality checks, every-cycle scans, and high-fanout controls as timing-risk structures that require deliberate architecture. `[UG949-T2/T4]`
- Prefer registered logical boundaries on high-speed datapaths when the interface contract permits. `[UG949-T3]`
- Do not let AXI4-Stream `tready` form a long combinational chain through multiple functional blocks; use a register slice, skid buffer, or registered-ready boundary when needed. `[UG949-T3]`
- For high-speed inferred RAM/BRAM/URAM and DSP arithmetic, preserve known-good inference structures and place registers/pipeline stages where the target primitive can use them effectively. `[UG949-T5/T6] [UG901-M1/D1]`
- Preserve established reset semantics, but avoid needless datapath resets/control sets that hurt inference or timing. `[UG949-T7] [UG901-R1]`

Static red flags are preventive heuristics, not AMD sign-off thresholds. Actual timing conclusions require correctly constrained Vivado evidence.

## CDC contract

Make clock domains explicit.

- Single-bit level CDC: synchronizer chain.
- Pulse/event CDC: pulse/toggle synchronizer or handshake according to event rate.
- Multi-bit payload CDC: asynchronous FIFO, stable-data handshake, or another coherent CDC structure.
- Do not directly cross-sample a multi-bit bus.
- Apply synchronizer attributes only according to the local Vivado/project convention.

## Workflow

1. **Inspect the local contract.** Determine clocks, reset, interfaces, latency, throughput, CDC boundaries, functional invariants, and existing verification entry points from supplied code/project; do not invent missing protocol facts.
2. **Run the Timing Design Gate.** Use `references/timing_by_construction.md` before coding a nontrivial/high-speed path, and record the concise design summary it requires.
3. **Choose inference intentionally.** Consult the relevant UG949/UG901 distilled reference rather than relying on software-like HDL intuition.
4. **Write RTL with the Chinese comment contract.** Keep comments synchronized with the final implementation.
5. **Run the static preflight.** Apply `references/rtl_review_checklist.md` and run `scripts/check_rtl_style.py` on changed RTL when the local environment permits. Treat its findings as prompts for review, not parser or timing results.
6. **Pass the functional verification gate.** Reuse existing project lint, testbench, or simulation entry points when they are available, relevant, and within task scope. Exercise the changed contract and boundary cases; if execution is unavailable, list the unrun scenarios and do not claim functional verification.
7. **Escalate verification proportionally.** Do not launch expensive Vivado synthesis/implementation or long simulations merely because the Skill was invoked. Run them when the user requests them or the task's acceptance criteria require them, using the real top, part, constraints, and project flow.
8. **When Vivado evidence exists, diagnose with UG906.** Separate logic-depth, fanout, routing/physical-delay, and constraint problems before recommending RTL changes.
9. **Report verification honestly.** Distinguish static review, lightweight checker, lint/elaboration, simulation, synthesis, implementation, timing analysis, CDC analysis, and hardware validation.

## Vivado evidence boundary

When the user provides or asks for Vivado analysis, useful reports can include:

```tcl
report_methodology
report_timing_summary
report_design_analysis
report_high_fanout_nets
```

Vivado 2026.1 UG901 also documents an RTL Linter through `synth_design ... -lint`; this skill does not launch Vivado by default.

Do not invent WNS, TNS, logic levels, fanout impact, or timing closure.

## Delivery

Use Chinese by default for explanations. For AXIS, CDC, FIFO/RAM, multiple clocks, high-speed datapaths, complex FSMs, or pipeline changes, include:

- 功能与接口摘要；
- 时钟/复位/CDC 假设；
- 流水级与可见延迟；
- 主动规避的时序风险及相关 `[UG949-*] / [UG901-*]` 规则；
- 完整 Verilog 代码或明确 patch；
- 建议的仿真场景；
- 实际运行的检查、命令/入口和结果；
- 未运行或无法运行的验证层级及剩余风险；
- 若未运行 Vivado，明确写“未验证综合/实现/时序”。
