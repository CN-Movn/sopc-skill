"""Central register interpretation and four-colour diagnostic policy."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from registers import RegisterSpec


class Severity(StrEnum):
    NORMAL = "normal"
    NOTICE = "notice"
    ERROR = "error"
    STALE = "stale"


@dataclass(frozen=True)
class Diagnosis:
    severity: Severity
    text: str


def diagnose(spec: RegisterSpec, value: int, previous: int | None = None) -> Diagnosis:
    rule = spec.rule
    if rule == "id":
        good = spec.expected is None or value == spec.expected
        return Diagnosis(Severity.NORMAL if good else Severity.ERROR,
                         "版本匹配" if good else f"ID/版本不匹配，期望 0x{spec.expected:08X}")
    if rule in ("fatal", "failure", "sticky_error"):
        return Diagnosis(Severity.NORMAL if value == 0 else Severity.ERROR,
                         "无异常" if value == 0 else "存在不可忽略的错误状态")
    if rule == "apply":
        if value & 0x04 or value & 0x10:
            return Diagnosis(Severity.ERROR, "Apply Reject/错误")
        if value & 0x03:
            return Diagnosis(Severity.NOTICE, "Apply Busy/Pending")
        return Diagnosis(Severity.NORMAL, "Apply空闲")
    if rule in ("arq_status", "scheduler_status"):
        if value & 0x80000000 or value & 0x40:
            return Diagnosis(Severity.ERROR, "会话失败或Fatal")
        if value & 0x3F:
            return Diagnosis(Severity.NORMAL, "链路事务活动中")
        return Diagnosis(Severity.NORMAL, "空闲/正常")
    if rule == "tx_failure_stats":
        remote_receive = (value >> 24) & 0xFF
        duplicate_suppressed = (value >> 16) & 0xFF
        session_failed = (value >> 8) & 0xFF
        retry_exhausted = value & 0xFF
        if session_failed or retry_exhausted:
            return Diagnosis(
                Severity.ERROR,
                f"Retry耗尽 {retry_exhausted}，Session失败 {session_failed}")
        if duplicate_suppressed:
            return Diagnosis(
                Severity.NOTICE,
                f"重复抑制 {duplicate_suppressed}，反馈接收 {remote_receive}")
        return Diagnosis(Severity.NORMAL, f"无终结失败，反馈接收 {remote_receive}")
    if rule == "tx_retx_stats":
        fields = (
            ("重传Issue", value & 0xFF),
            ("重传命令", (value >> 8) & 0xFF),
            ("Late ACK取消", (value >> 16) & 0xFF),
            ("Late ACK延后", (value >> 24) & 0xFF),
        )
        if previous is not None:
            previous_fields = (
                previous & 0xFF,
                (previous >> 8) & 0xFF,
                (previous >> 16) & 0xFF,
                (previous >> 24) & 0xFF,
            )
            increases = [f"{name} +{current - old}"
                         for (name, current), old in zip(fields, previous_fields,
                                                         strict=True)
                         if current > old]
            if increases:
                return Diagnosis(Severity.NOTICE, "；".join(increases))
        nonzero = [f"{name} {current}" for name, current in fields if current]
        return Diagnosis(Severity.NOTICE if nonzero else Severity.NORMAL,
                         "累计：" + "，".join(nonzero) if nonzero
                         else "无重传或Late ACK事件")
    if rule == "format_detail":
        length_errors = (value >> 16) & 0xFFFF
        type_errors = value & 0xFFFF
        increases: list[str] = []
        if previous is not None:
            previous_length = (previous >> 16) & 0xFFFF
            previous_type = previous & 0xFFFF
            if length_errors > previous_length:
                increases.append(f"长度错误 +{length_errors - previous_length}")
            if type_errors > previous_type:
                increases.append(f"类型错误 +{type_errors - previous_type}")
        if increases:
            return Diagnosis(Severity.ERROR, "；".join(increases))
        if length_errors or type_errors:
            return Diagnosis(Severity.NOTICE,
                             f"长度错误累计 {length_errors}；"
                             f"类型错误累计 {type_errors}")
        return Diagnosis(Severity.NORMAL, "无类型/长度错误")
    if rule == "tail_last_len":
        tail_errors = (value >> 16) & 0xFFFF
        last_payload_len = value & 0xFFFF
        if previous is not None:
            previous_tail_errors = (previous >> 16) & 0xFFFF
            if tail_errors > previous_tail_errors:
                return Diagnosis(
                    Severity.ERROR,
                    f"Tail错误 +{tail_errors - previous_tail_errors}；"
                    f"最近Payload {last_payload_len}字节")
        if tail_errors:
            return Diagnosis(Severity.NOTICE,
                             f"Tail错误累计 {tail_errors}；"
                             f"最近Payload {last_payload_len}字节")
        return Diagnosis(Severity.NORMAL,
                         f"无Tail错误；最近Payload {last_payload_len}字节")
    if rule in ("error_counter", "recoverable_counter"):
        if previous is not None and value > previous:
            return Diagnosis(Severity.ERROR if rule == "error_counter" else Severity.NOTICE,
                             f"本轮增加 {value - previous}")
        return Diagnosis(Severity.NOTICE if value else Severity.NORMAL,
                         "累计非零，当前未增长" if value else "无累计异常")
    if rule in ("activity", "parser", "resource"):
        return Diagnosis(Severity.NORMAL,
                         "活动/占用（正常）" if value else "空闲")
    return Diagnosis(Severity.NORMAL, "正常")


COLORS = {
    Severity.NORMAL: (232, 248, 237),
    Severity.NOTICE: (255, 246, 213),
    Severity.ERROR: (255, 228, 228),
    Severity.STALE: (238, 240, 243),
}
