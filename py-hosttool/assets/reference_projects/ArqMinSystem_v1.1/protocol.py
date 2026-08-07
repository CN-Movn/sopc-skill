"""SPM-MCP v1 protocol and ARQ diagnostic bulk-register extension."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct

SOF = b"\xA5\x5A"
VERSION = 1
MAX_PAYLOAD = 512
HEADER = struct.Struct("<BBBBHHHH")


class MessageType(IntEnum):
    REQUEST = 1
    RESPONSE = 2
    REPORT = 3
    EVENT = 4


class Target(IntEnum):
    SYSTEM = 0x0000
    ALICE = 0x0100
    BOB = 0x0101
    ALICE_TX_SCHEDULER = 0x0200
    BOB_TX_SCHEDULER = 0x0201
    ALICE_TX_WRAPPER = 0x0300
    BOB_TX_WRAPPER = 0x0301
    ALICE_RX_SCHEDULER = 0x0400
    BOB_RX_SCHEDULER = 0x0401
    ALICE_RX_WRAPPER = 0x0500
    BOB_RX_WRAPPER = 0x0501
    ALICE_SOURCE = 0x0600
    BOB_SOURCE = 0x0601
    A2B_CHANNEL = 0x0700
    B2A_CHANNEL = 0x0701
    A2B_LINK = 0x0800
    B2A_LINK = 0x0801


class Opcode(IntEnum):
    PING = 0x0001
    GET_INFO = 0x0002
    GET_CAPABILITY = 0x0003
    GET_HEALTH = 0x0004
    GET_CONFIG = 0x0100
    SET_CONFIG = 0x0101
    GET_STATUS = 0x0102
    CONTROL = 0x0103
    CLEAR_STATS = 0x0104
    CLEAR_ERRORS = 0x0105
    GET_SNAPSHOT = 0x0106
    GET_REPORT_CONFIG = 0x0200
    SET_REPORT_CONFIG = 0x0201
    GET_REPORT_NOW = 0x0202
    READ_REGISTER = 0x0300
    DUMP_SLOT = 0x0301
    GET_PROTOCOL_STATS = 0x0302
    READ_REG_BLOCK = 0x0303


class Status(IntEnum):
    OK = 0
    BAD_FRAME = 1
    BAD_VERSION = 2
    BAD_TARGET = 3
    BAD_OPCODE = 4
    BAD_LENGTH = 5
    RANGE_ERROR = 6
    BUSY = 7
    TIMEOUT = 8
    HW_MISMATCH = 9
    APPLY_REJECT = 10
    UNSUPPORTED = 11
    INTERNAL_ERROR = 12
    PARTIAL_SUCCESS = 13


@dataclass(frozen=True)
class McpFrame:
    message_type: MessageType
    flags: int
    sequence: int
    target: int
    opcode: int
    payload: bytes

    @property
    def status_code(self) -> int | None:
        return struct.unpack_from("<H", self.payload)[0] if self.message_type == MessageType.RESPONSE and len(self.payload) >= 4 else None

    @property
    def status(self) -> Status | None:
        code = self.status_code
        if code is None:
            return None
        try:
            return Status(code)
        except ValueError:
            return None

    @property
    def detail(self) -> int | None:
        return struct.unpack_from("<H", self.payload, 2)[0] if self.message_type == MessageType.RESPONSE and len(self.payload) >= 4 else None

    @property
    def data(self) -> bytes:
        return self.payload[4:] if self.message_type == MessageType.RESPONSE and len(self.payload) >= 4 else b""


@dataclass(frozen=True)
class RegisterFragment:
    capture_id: int
    index: int
    count: int
    total_items: int
    values: dict[int, int]


class FrameStream:
    def __init__(self) -> None:
        self.buffer = b""

    def push(self, chunk: bytes) -> list[bytes]:
        self.buffer += chunk
        frames, self.buffer = extract_frames(self.buffer)
        return frames


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def encode_frame(message_type: MessageType, sequence: int, target: int,
                 opcode: int, payload: bytes = b"", flags: int = 0) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload exceeds MCP limit")
    header = HEADER.pack(VERSION, int(message_type), flags & 0xFF, 0,
                         sequence & 0xFFFF, target & 0xFFFF,
                         opcode & 0xFFFF, len(payload))
    body = header + payload
    return SOF + body + struct.pack("<H", crc16_ccitt(body))


def build_command(sequence: int, target: Target | int, opcode: Opcode | int,
                  payload: bytes = b"") -> bytes:
    return encode_frame(MessageType.REQUEST, sequence, int(target), int(opcode), payload)


def decode_frame(raw: bytes) -> McpFrame:
    if len(raw) < 16 or not raw.startswith(SOF):
        raise ValueError("MCP帧头缺失或帧不完整")
    version, kind, flags, _reserved, sequence, target, opcode, length = HEADER.unpack_from(raw, 2)
    if version != VERSION:
        raise ValueError(f"不支持的MCP版本：{version}")
    if length > MAX_PAYLOAD or len(raw) != 16 + length:
        raise ValueError("MCP长度字段不匹配")
    if crc16_ccitt(raw[2:-2]) != struct.unpack_from("<H", raw, len(raw) - 2)[0]:
        raise ValueError("MCP CRC错误")
    return McpFrame(MessageType(kind), flags, sequence, target, opcode, raw[14:-2])


def extract_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    frames: list[bytes] = []
    while buffer:
        start = buffer.find(SOF)
        if start < 0:
            return frames, buffer[-1:] if buffer.endswith(SOF[:1]) else b""
        buffer = buffer[start:]
        if len(buffer) < 14:
            return frames, buffer
        length = struct.unpack_from("<H", buffer, 12)[0]
        if length > MAX_PAYLOAD:
            buffer = buffer[1:]
            continue
        wire_length = 16 + length
        if len(buffer) < wire_length:
            return frames, buffer
        candidate = buffer[:wire_length]
        try:
            decode_frame(candidate)
        except (ValueError, KeyError):
            buffer = buffer[1:]
            continue
        frames.append(candidate)
        buffer = buffer[wire_length:]
    return frames, b""


def parse_register_fragment(frame: McpFrame) -> RegisterFragment:
    if frame.message_type != MessageType.RESPONSE or frame.opcode != Opcode.READ_REG_BLOCK:
        raise ValueError("不是批量寄存器响应")
    if frame.status_code != Status.OK:
        raise ValueError(f"批量读取失败：status={frame.status_code}, detail={frame.detail}")
    data = frame.data
    if len(data) < 8:
        raise ValueError("批量寄存器响应头不完整")
    capture_id, index, count, total_items, item_count = struct.unpack_from("<HBBHH", data)
    if len(data) != 8 + item_count * 6:
        raise ValueError("批量寄存器条目长度不匹配")
    values: dict[int, int] = {}
    offset = 8
    for _ in range(item_count):
        key, value = struct.unpack_from("<HI", data, offset)
        values[key] = value
        offset += 6
    return RegisterFragment(capture_id, index, count, total_items, values)


PROTOCOL_STAT_NAMES = (
    "frames_ok", "crc_errors", "version_errors", "length_errors",
    "type_errors", "request_drops", "uart_errors", "response_drops",
    "event_drops", "report_drops",
)


def parse_protocol_stats(frame: McpFrame) -> dict[str, int]:
    if frame.status_code != Status.OK or frame.opcode != Opcode.GET_PROTOCOL_STATS:
        raise ValueError("协议统计响应失败")
    if len(frame.data) != 40:
        raise ValueError("协议统计长度不匹配")
    return dict(zip(PROTOCOL_STAT_NAMES, struct.unpack("<10I", frame.data), strict=True))


def status_text(frame: McpFrame) -> str:
    if frame.status_code is None:
        return "响应格式错误"
    try:
        return Status(frame.status_code).name
    except ValueError:
        return f"UNKNOWN_0x{frame.status_code:04X}"
