---
name: rtl-style
description: Verilog RTL coding style and review discipline for Vivado/Vitis FPGA work. Use when creating, modifying, refactoring, or reviewing synthesizable Verilog modules, especially AXI4-Stream datapaths, counters, FSMs, register control, FIFO wrappers, video stream processing, packet processing, CDC fixes, timing-friendly rewrites, or RTL style cleanup.
---

# RTL Style

Use this skill when writing or changing Verilog RTL. Optimize for synthesizable, maintainable RTL that is friendly to Vivado synthesis, implementation, timing closure, simulation, and code review.

Default to Verilog-2001 RTL. Do not use SystemVerilog features unless the user explicitly asks for SystemVerilog.

When creating a new ordinary module, read `references/verilog_module_skeleton.v`. When creating an AXI4-Stream registered stage or skid-buffer-like block, read `references/axis_registered_stage_template.v`.

## Hard Rules

- Generate synthesizable Verilog RTL, not simulation-only constructs in design modules.
- Use ``default_nettype none`` and never rely on implicit wires.
- Do not infer latches.
- Do not create combinational loops.
- Do not assign the same `reg` from multiple `always` blocks.
- Use nonblocking assignments (`<=`) in sequential logic.
- Use blocking assignments (`=`) in combinational logic.
- Give combinational logic default assignments before conditional logic.
- Give every `case` a `default`.
- Do not use unexplained magic numbers; name them as `parameter` or `localparam`.
- Do not directly sample signals across clock domains.
- Do not gate clocks in RTL as a substitute for clock enable logic.
- For AXI4-Stream, when `tvalid=1` and `tready=0`, keep `tdata`, `tkeep`, `tlast`, `tuser`, and `tvalid` stable.
- If Vivado, xsim, Verilator, testbench runs, synthesis, implementation, or timing checks were not actually executed, do not claim they passed.

## Preferred Rules

- Prefer three-block FSMs for nontrivial control.
- Prefer active-low synchronous reset named `rst_n` unless local style differs.
- Prefer lowercase snake_case for modules, files, ports, and signals unless local style differs.
- Add pipeline registers for long combinational paths, wide datapaths, or complex ready/valid backpressure logic.
- Isolate debug/status logic from the main datapath unless it is timing-safe.
- Keep simple modules simple; do not force FSM, FIFO, pipeline, or debug framework into simple logic.

## AXI4-Stream

For AXIS modules:

- Define fire signals, for example `s_axis_fire = s_axis_tvalid && s_axis_tready`.
- Keep all output payload and qualifier signals stable while `m_axis_tvalid && !m_axis_tready`.
- If backpressure is unsupported, say so clearly.
- Prefer explicit skid buffers or registered ready paths when backpressure logic becomes long or timing-sensitive.
- Use register slices, skid buffers, or pipeline stages to cut long ready paths.

## CDC And Reset

Make clock domains explicit. Single-bit CDC uses synchronizers; pulse CDC uses pulse or toggle synchronizers; multi-bit CDC uses async FIFO or handshake. Never directly cross-sample a multi-bit bus.

## 中文注释规范

Use Chinese comments in Verilog code by default. Keep identifiers in English. Comments should explain module function, clock/reset assumptions, interface semantics, FSM states, counter boundaries, exception handling, CDC, pipeline, and timing-sensitive logic.

## Delivery Format

Use Chinese by default for explanations. For modules involving AXIS, CDC, FIFO, multiple clocks, high-speed datapaths, complex FSMs, or pipeline changes, include functional summary, assumptions, risks, complete Verilog code or patch, and suggested simulation scenarios.
