#!/usr/bin/env python3
"""Lightweight static checker for rtl-style generated/modified Verilog.

This is intentionally a heuristic preflight, not a replacement for Vivado synthesis,
report_design_analysis, report_timing, CDC analysis, or simulation.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
MODULE_RE = re.compile(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)\b", re.M)
REQUIRED_HEADER_FIELDS = (
    "模块名称",
    "功能说明",
    "时钟复位",
    "接口说明",
    "数据流与延迟",
    "CDC 说明",
    "时序设计",
    "边界说明",
)


def iter_verilog_paths(items: Iterable[str]) -> list[Path]:
    result: list[Path] = []
    for item in items:
        path = Path(item)
        if path.is_dir():
            result.extend(sorted(path.rglob("*.v")))
        elif path.suffix.lower() == ".v" and path.exists():
            result.append(path)
    # Preserve order while removing duplicates.
    return list(dict.fromkeys(p.resolve() for p in result))


def line_no(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def preceding_comment_window(text: str, module_offset: int, max_lines: int = 30) -> str:
    prefix_lines = text[:module_offset].splitlines()
    return "\n".join(prefix_lines[-max_lines:])


def extract_always_blocks(lines: list[str]) -> list[tuple[int, str]]:
    blocks: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        if not re.search(r"\balways\s*@", lines[i]):
            i += 1
            continue

        start = i
        buf = [lines[i]]
        depth = lines[i].count("begin") - lines[i].count("end")

        # Single-statement always without begin/end: keep a small block.
        if depth <= 0 and ";" in lines[i]:
            blocks.append((start + 1, lines[i]))
            i += 1
            continue

        i += 1
        while i < len(lines):
            buf.append(lines[i])
            depth += lines[i].count("begin") - lines[i].count("end")
            if depth <= 0 and re.search(r"\bend\b", lines[i]):
                break
            i += 1
        blocks.append((start + 1, "\n".join(buf)))
        i += 1
    return blocks


def comment_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if "//" in line and CHINESE_RE.search(line)]


def analyze_file(path: Path) -> tuple[list[str], list[str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    errors: list[str] = []
    warnings: list[str] = []

    if "`default_nettype none" not in text:
        errors.append("缺少 `default_nettype none`。")
    if "`default_nettype wire" not in text:
        warnings.append("文件末尾未看到 `default_nettype wire` 恢复。")

    modules = list(MODULE_RE.finditer(text))
    if not modules:
        warnings.append("未检测到 module 声明。")
    for match in modules:
        name = match.group(1)
        header = preceding_comment_window(text, match.start())
        missing = [field for field in REQUIRED_HEADER_FIELDS if field not in header]
        if missing:
            errors.append(
                f"module {name} 顶部中文说明块缺少字段: {', '.join(missing)}。"
            )
        if not CHINESE_RE.search(header):
            errors.append(f"module {name} 顶部未检测到中文注释。")

    # Require at least some Chinese intent comments beyond the module header for nontrivial files.
    chinese_comments = comment_lines(text)
    if len(lines) >= 80 and len(chinese_comments) < 10:
        warnings.append(
            f"文件 {len(lines)} 行，但仅检测到 {len(chinese_comments)} 行中文注释；"
            "请确认 FSM/握手/计数/流水/CDC 等非平凡逻辑均有意图说明。"
        )

    # Nontrivial always blocks must have a nearby Chinese intent comment.
    always_blocks = extract_always_blocks(lines)
    for start_line, block in always_blocks:
        block_lines = block.splitlines()
        nontrivial = len(block_lines) >= 6 or bool(re.search(r"\b(if|case[xz]?)\b", block))
        if nontrivial:
            before = "\n".join(lines[max(0, start_line - 4): start_line - 1])
            first_part = "\n".join(block_lines[:3])
            if not CHINESE_RE.search(before + "\n" + first_part):
                errors.append(
                    f"L{start_line}: 非平凡 always 块附近缺少中文意图注释。"
                )

    # FSM/case selection should carry a nearby Chinese explanation, not only syntax.
    for idx, line in enumerate(lines, 1):
        if re.search(r"\bcase[xz]?\s*\(", re.sub(r"//.*", "", line)):
            before = "\n".join(lines[max(0, idx - 4): idx])
            if not CHINESE_RE.search(before):
                warnings.append(
                    f"L{idx}: case/FSM 选择附近未检测到中文说明；请解释状态/选择语义和 priority 假设。[UG901-P1/UG901-F1]"
                )

    # Heuristic timing-risk checks inside always blocks.
    for start_line, block in always_blocks:
        is_comb = bool(re.search(r"always\s*@\s*\(\s*\*\s*\)|always\s*@\s*\*", block))
        if not is_comb:
            continue

        else_if_count = len(re.findall(r"\belse\s+if\b", block))
        ternary_count = block.count("?")
        case_count = len(re.findall(r"\bcase[xz]?\s*\(", block))
        compare_count = len(re.findall(r"==|!=|<=|>=|(?<!<)<(?![<=])|(?<!>)>(?![>=])", block))
        arithmetic_count = len(re.findall(r"(?<!\+)[+](?!\+)|(?<!-)-(?!-)|\*", block))

        if else_if_count >= 4:
            warnings.append(
                f"L{start_line}: 组合块含 {else_if_count} 个 else-if；"
                "确认是否真的需要 priority，避免长 priority mux。[UG901-P1/UG949-T2]"
            )
        if ternary_count >= 3:
            warnings.append(
                f"L{start_line}: 组合块含 {ternary_count} 个三目运算符；"
                "检查是否形成嵌套 mux/深组合锥。[UG949-T1/T2]"
            )

        complexity_classes = sum(
            [
                else_if_count > 0 or case_count > 0,
                compare_count >= 2,
                arithmetic_count >= 2,
                ternary_count > 0,
            ]
        )
        if complexity_classes >= 3 and len(block.splitlines()) >= 18:
            warnings.append(
                f"L{start_line}: 同一组合块同时包含多类 decode/比较/算术/mux 操作；"
                "按 register-to-register 路径审查，必要时拆成平衡 pipeline。[UG949-T1/T2, UG906-A1]"
            )

    # Direct ready-to-ready dependencies are often long backpressure paths.
    for idx, line in enumerate(lines, 1):
        stripped = re.sub(r"//.*", "", line)
        m = re.search(r"\bassign\s+([A-Za-z_][A-Za-z0-9_$]*tready)\s*=\s*(.+);", stripped)
        if m:
            lhs, rhs = m.group(1), m.group(2)
            ready_refs = re.findall(r"\b[A-Za-z_][A-Za-z0-9_$]*tready\b", rhs)
            if any(ref != lhs for ref in ready_refs):
                warnings.append(
                    f"L{idx}: {lhs} 组合依赖另一个 tready；检查是否形成跨模块长 ready 链。[UG949-T3]"
                )

    # Nested/long single-line ternary is easy to miss in review.
    for idx, line in enumerate(lines, 1):
        code = re.sub(r"//.*", "", line)
        if code.count("?") >= 2:
            warnings.append(
                f"L{idx}: 单行存在嵌套三目选择；优先确认是否能用平行 case/predecode 表达。[UG901-P1]"
            )

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Static preflight for rtl-style Verilog comments and common timing-risk structures."
    )
    parser.add_argument("paths", nargs="+", help="Verilog files or directories to inspect")
    parser.add_argument(
        "--warnings-as-errors",
        action="store_true",
        help="return nonzero when timing-risk warnings are present",
    )
    args = parser.parse_args()

    files = iter_verilog_paths(args.paths)
    if not files:
        print("rtl-style check: no .v files found", file=sys.stderr)
        return 2

    error_count = 0
    warning_count = 0
    for path in files:
        errors, warnings = analyze_file(path)
        if errors or warnings:
            print(f"\n[{path}]")
        for msg in errors:
            error_count += 1
            print(f"  ERROR: {msg}")
        for msg in warnings:
            warning_count += 1
            print(f"  WARN : {msg}")

    print(
        f"\nrtl-style check summary: files={len(files)}, errors={error_count}, warnings={warning_count}"
    )
    if error_count:
        return 1
    if args.warnings_as_errors and warning_count:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
