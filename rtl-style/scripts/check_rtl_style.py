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
ALWAYS_RE = re.compile(r"\balways\s*@|\balways_(?:comb|ff|latch)\b")
VERILOG_SUFFIXES = {".v", ".sv"}
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
            result.extend(
                sorted(
                    candidate
                    for candidate in path.rglob("*")
                    if candidate.is_file() and candidate.suffix.lower() in VERILOG_SUFFIXES
                )
            )
        elif path.suffix.lower() in VERILOG_SUFFIXES and path.exists():
            result.append(path)
    # Preserve order while removing duplicates.
    return list(dict.fromkeys(p.resolve() for p in result))


def mask_comments_and_strings(text: str) -> str:
    """Mask non-code text while preserving byte offsets and line breaks.

    This is deliberately a small lexical guard for heuristics, not a Verilog parser.
    """

    output: list[str] = []
    state = "code"
    index = 0
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""

        if state == "code":
            if char == "/" and following == "/":
                output.extend((" ", " "))
                index += 2
                state = "line_comment"
                continue
            if char == "/" and following == "*":
                output.extend((" ", " "))
                index += 2
                state = "block_comment"
                continue
            if char == '"':
                output.append(" ")
                index += 1
                state = "string"
                continue
            output.append(char)
            index += 1
            continue

        if state == "line_comment":
            output.append("\n" if char == "\n" else " ")
            index += 1
            if char == "\n":
                state = "code"
            continue

        if state == "block_comment":
            if char == "*" and following == "/":
                output.extend((" ", " "))
                index += 2
                state = "code"
                continue
            output.append("\n" if char == "\n" else " ")
            index += 1
            continue

        # String contents cannot contain RTL structure. Preserve escaped/newline
        # positions so diagnostics still use the original source line numbers.
        if char == "\\" and following:
            output.append(" ")
            output.append("\n" if following == "\n" else " ")
            index += 2
            continue
        output.append("\n" if char == "\n" else " ")
        index += 1
        if char == '"':
            state = "code"

    return "".join(output)


def preceding_comment_window(text: str, module_offset: int, max_lines: int = 30) -> str:
    """Return the nearest module header comment, allowing blank/directive lines."""

    prefix_lines = text[:module_offset].splitlines()
    collected: list[str] = []
    saw_comment = False
    in_block_comment = False
    for line in reversed(prefix_lines[-max_lines:]):
        stripped = line.strip()
        is_line_comment = stripped.startswith("//")
        if "*/" in stripped:
            in_block_comment = True
        is_comment = is_line_comment or in_block_comment
        if "/*" in stripped and in_block_comment:
            in_block_comment = False

        if is_comment:
            collected.append(line)
            saw_comment = True
            continue
        if not stripped or (stripped.startswith("`") and not saw_comment):
            continue
        if stripped.startswith("`") and saw_comment:
            continue
        break
    return "\n".join(reversed(collected))


def keyword_count(line: str, keyword: str) -> int:
    return len(re.findall(rf"\b{keyword}\b", line))


def extract_always_blocks(
    lines: list[str], code_lines: list[str]
) -> list[tuple[int, str, str]]:
    blocks: list[tuple[int, str, str]] = []
    i = 0
    while i < len(lines):
        if not ALWAYS_RE.search(code_lines[i]):
            i += 1
            continue

        start = i
        buf = [lines[i]]
        code_buf = [code_lines[i]]
        depth = keyword_count(code_lines[i], "begin") - keyword_count(code_lines[i], "end")
        entered_begin = keyword_count(code_lines[i], "begin") > 0

        if not entered_begin and ";" in code_lines[i]:
            blocks.append((start + 1, lines[i], code_lines[i]))
            i += 1
            continue
        i += 1
        while i < len(lines):
            buf.append(lines[i])
            code_buf.append(code_lines[i])
            begins = keyword_count(code_lines[i], "begin")
            ends = keyword_count(code_lines[i], "end")
            entered_begin = entered_begin or begins > 0
            depth += begins - ends
            if entered_begin and depth <= 0 and ends:
                break
            if not entered_begin and ";" in code_lines[i]:
                break
            i += 1
        blocks.append((start + 1, "\n".join(buf), "\n".join(code_buf)))
        i += 1
    return blocks


def analyze_file(path: Path) -> tuple[list[str], list[str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    code_text = mask_comments_and_strings(text)
    lines = text.splitlines()
    code_lines = code_text.splitlines()
    errors: list[str] = []
    warnings: list[str] = []

    if "`default_nettype none" not in code_text:
        errors.append("缺少 `default_nettype none`。")
    if "`default_nettype wire" not in code_text:
        warnings.append("未检测到 `default_nettype wire` 恢复指令。")

    modules = list(MODULE_RE.finditer(code_text))
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

    # Check structural coverage, never a comment-line count or density quota.
    always_blocks = extract_always_blocks(lines, code_lines)
    for start_line, block, code_block in always_blocks:
        block_lines = block.splitlines()
        nontrivial = len(block_lines) >= 6 or bool(re.search(r"\b(if|case[xz]?)\b", code_block))
        if nontrivial:
            before = "\n".join(lines[max(0, start_line - 4): start_line - 1])
            first_part = "\n".join(block_lines[:3])
            if not CHINESE_RE.search(before + "\n" + first_part):
                errors.append(
                    f"L{start_line}: 非平凡 always 块附近缺少中文意图注释。"
                )

    # FSM/case selection should carry a nearby Chinese explanation, not only syntax.
    for idx, code_line in enumerate(code_lines, 1):
        if re.search(r"\bcase[xz]?\s*\(", code_line):
            before = "\n".join(lines[max(0, idx - 4): idx])
            if not CHINESE_RE.search(before):
                warnings.append(
                    f"L{idx}: case/FSM 选择附近未检测到中文说明；请解释状态/选择语义和 priority 假设。[UG901-P1/UG901-F1]"
                )

    # Heuristic timing-risk checks inside always blocks.
    for start_line, _block, code_block in always_blocks:
        is_comb = bool(
            re.search(r"always\s*@\s*\(\s*\*\s*\)|always\s*@\s*\*", code_block)
            or re.search(r"\balways_comb\b", code_block)
        )
        if not is_comb:
            continue

        else_if_count = len(re.findall(r"\belse\s+if\b", code_block))
        ternary_count = code_block.count("?")
        case_count = len(re.findall(r"\bcase[xz]?\s*\(", code_block))
        compare_count = len(re.findall(r"==|!=|<=|>=|(?<!<)<(?![<=])|(?<!>)>(?![>=])", code_block))
        arithmetic_count = len(re.findall(r"(?<!\+)[+](?!\+)|(?<!-)-(?!-)|\*", code_block))

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
        if complexity_classes >= 3 and len(code_block.splitlines()) >= 18:
            warnings.append(
                f"L{start_line}: 同一组合块同时包含多类 decode/比较/算术/mux 操作；"
                "按 register-to-register 路径审查，必要时拆成平衡 pipeline。[UG949-T1/T2, UG906-A1]"
            )

    # Direct ready-to-ready dependencies are often long backpressure paths.
    for idx, code_line in enumerate(code_lines, 1):
        m = re.search(r"\bassign\s+([A-Za-z_][A-Za-z0-9_$]*tready)\s*=\s*(.+);", code_line)
        if m:
            lhs, rhs = m.group(1), m.group(2)
            ready_refs = re.findall(r"\b[A-Za-z_][A-Za-z0-9_$]*tready\b", rhs)
            if any(ref != lhs for ref in ready_refs):
                warnings.append(
                    f"L{idx}: {lhs} 组合依赖另一个 tready；检查是否形成跨模块长 ready 链。[UG949-T3]"
                )

    # Nested/long single-line ternary is easy to miss in review.
    for idx, code_line in enumerate(code_lines, 1):
        if code_line.count("?") >= 2:
            warnings.append(
                f"L{idx}: 单行存在嵌套三目选择；优先确认是否能用平行 case/predecode 表达。[UG901-P1]"
            )

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Static preflight for rtl-style Verilog/SystemVerilog comments and common timing-risk structures."
    )
    parser.add_argument("paths", nargs="+", help=".v/.sv files or directories to inspect")
    parser.add_argument(
        "--warnings-as-errors",
        action="store_true",
        help="return nonzero when timing-risk warnings are present",
    )
    args = parser.parse_args()

    files = iter_verilog_paths(args.paths)
    if not files:
        print("rtl-style check: no .v/.sv files found", file=sys.stderr)
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
