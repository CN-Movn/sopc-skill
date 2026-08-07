"""Protocol-compatible implementation of the MATLAB MasterController helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from arq_register_map import find_register

INNER_HEADER, INNER_TAIL = b"\xEB\x90", b"\x55\xAA"
MASTER_HEADER, MASTER_TAIL = b"\xEB\x91", b"\x55\xBB"
REPORT_LENGTH = 134

ARQ_TARGET_NAMES = {
    0x01: "TX Wrapper",
    0x02: "RX Wrapper",
    0x03: "TX Scheduler",
    0x04: "RX Scheduler",
    0xFF: "ALL",
}
ARQ_OPERATION_NAMES = {
    0x01: "WRITE_REG",
    0x02: "READ_REG",
    0x03: "DUMP_ALL",
}


def hex_to_bytes(text: str) -> bytes:
    clean = "".join(ch for ch in str(text) if ch in "0123456789abcdefABCDEF")
    if len(clean) % 2:
        raise ValueError("十六进制字符串长度为奇数，无法转换为字节。")
    return bytes.fromhex(clean)


def bytes_to_hex(data: bytes | bytearray, separator: str = " ") -> str:
    return separator.join(f"{value:02X}" for value in data)


def parse_hex_byte(value: str, field: str = "字段") -> int:
    value = str(value).strip().upper().removeprefix("0X")
    try:
        number = int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{field}不是合法的十六进制值：{value}") from exc
    if not 0 <= number <= 0xFF:
        raise ValueError(f"{field}超出 1 字节范围：{value}")
    return number


def create_inner_command_frame(command_code: int, msg_id: int = 0, device_id: int = 1,
                               work_mode: int = 0, mac_address: str = "", *,
                               arq_target: int = 0x01,
                               arq_operation: int = 0x02,
                               arq_offset: int = 0,
                               arq_value: int = 0) -> bytes:
    """Mirror create_inner_command_frame.m exactly."""
    fields = bytearray([0x55] * 16)
    if command_code == 0x90:
        fields[0:3] = bytes((msg_id, device_id, work_mode))
    elif command_code in (0x91, 0x92):
        fields[0:2] = bytes((msg_id, device_id))
    elif command_code == 0x93:
        mac = hex_to_bytes(mac_address)
        if len(mac) != 6:
            raise ValueError("MAC地址格式不正确，应为 XX:XX:XX:XX:XX:XX")
        fields[0:7] = mac + bytes((device_id,))
    elif command_code == 0x94:
        if arq_target not in ARQ_TARGET_NAMES:
            raise ValueError(f"ARQ TargetIP不正确：0x{arq_target:02X}")
        if arq_operation not in ARQ_OPERATION_NAMES:
            raise ValueError(f"ARQ Operation不正确：0x{arq_operation:02X}")
        if not 0 <= msg_id <= 0xFF:
            raise ValueError("ARQ MsgID超出1字节范围")
        if msg_id != 0:
            raise ValueError("ARQ消息 ID必须为00")
        if not 0 <= device_id <= 0xFF:
            raise ValueError("ARQ DeviceID超出1字节范围")
        if not 0 <= arq_offset <= 0xFFFF:
            raise ValueError("ARQ寄存器偏移超出16位范围")
        if not 0 <= arq_value <= 0xFFFFFFFF:
            raise ValueError("ARQ寄存器值超出32位范围")
        if arq_target == 0xFF and arq_operation != 0x03:
            raise ValueError("ARQ TargetIP=ALL只允许与DUMP_ALL组合")

        if arq_operation == 0x02 and arq_value != 0:
            raise ValueError("ARQ READ_REG的寄存器值必须为0")
        if arq_operation == 0x03 and (arq_offset != 0 or arq_value != 0):
            raise ValueError("ARQ DUMP_ALL的寄存器偏移和值必须为0")

        # Keep the common command-frame convention: every unused data byte
        # remains 0x55.  ARQ only occupies bytes 3..12 of the 16-byte field.
        fields[0] = msg_id
        fields[1] = device_id
        fields[2] = arq_operation
        fields[3] = arq_target
        fields[4:6] = arq_offset.to_bytes(2, "big")
        fields[6:10] = arq_value.to_bytes(4, "big")
    else:
        raise ValueError(f"不支持的指令码: 0x{command_code:02X}")
    body = bytes((command_code,)) + bytes(fields)
    return INNER_HEADER + body + bytes((sum(body) & 0xFF,)) + INNER_TAIL


def wrap_master_frame(outer_code: int | str, payload: bytes) -> bytes:
    if isinstance(outer_code, str):
        outer_code = parse_hex_byte(outer_code, "主控外层指令码")
    if not 0 <= outer_code <= 0xFF:
        raise ValueError("主控外层指令码必须在 0x00 到 0xFF 之间")
    payload = bytes(payload)
    if len(payload) > 0xFFFF:
        raise ValueError("主控外层负载长度不能超过 65535 字节")
    length = len(payload).to_bytes(2, "big")
    checksum = (outer_code + sum(length) + sum(payload)) & 0xFF
    return MASTER_HEADER + bytes((outer_code,)) + length + payload + bytes((checksum,)) + MASTER_TAIL


@dataclass(frozen=True)
class MasterFrame:
    outer_code: int
    payload: bytes
    frame_bytes: bytes
    start_index: int  # MATLAB-compatible, starts at 1
    length: int


def unwrap_master_frame(data: bytes) -> MasterFrame:
    data = bytes(data)
    if len(data) < 8:
        raise ValueError("输入数据长度不足，无法构成主控外层帧")
    for start in range(len(data) - 1):
        if data[start:start + 2] != MASTER_HEADER or start + 8 > len(data):
            continue
        outer, high, low = data[start + 2:start + 5]
        length = high * 256 + low
        end = start + length + 8
        if end > len(data):
            continue
        frame = data[start:end]
        if frame[-2:] != MASTER_TAIL:
            continue
        expected = (outer + high + low + sum(frame[5:-3])) & 0xFF
        if frame[-3] != expected:
            raise ValueError(f"主控外层帧校验和错误，计算值 0x{expected:02X}，实际值 0x{frame[-3]:02X}")
        return MasterFrame(outer, frame[5:-3], frame, start + 1, len(frame))
    raise ValueError("找到 EB91 帧头，但未找到完整且合法的主控外层帧")


def _valid_direct_report(frame: bytes) -> bool:
    return len(frame) == REPORT_LENGTH and frame[:2] == INNER_HEADER and frame[-2:] == INNER_TAIL and frame[131] == (sum(frame[2:131]) & 0xFF)


@dataclass(frozen=True)
class ExtractedReport:
    mode: str
    raw_bytes: bytes
    analysis_bytes: bytes


def extract_protocol_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Extract complete wire frames for serial logging without splitting reads.

    Recognizes the MATLAB application's 22-byte command frames, 134-byte
    reports and variable-length ``EB 91`` master frames.  A possible partial
    frame is retained verbatim for the next serial callback.
    """
    data, frames, index, incomplete = bytes(buffer), [], 0, None
    while index <= len(data) - 2:
        header = data[index:index + 2]
        if header == MASTER_HEADER:
            if index + 8 > len(data):
                incomplete = index
                break
            length = int.from_bytes(data[index + 3:index + 5], "big")
            end = index + length + 8
            if end > len(data):
                incomplete = index
                break
            candidate = data[index:end]
            try:
                unwrap_master_frame(candidate)
            except ValueError:
                index += 1
            else:
                frames.append(candidate)
                index = end
        elif header == INNER_HEADER:
            if index + 3 > len(data):
                incomplete = index
                break
            # 90..94 are command frames; other identifiers use the fixed
            # 134-byte telemetry-report layout in the MATLAB project.
            frame_length = 22 if data[index + 2] in (0x90, 0x91, 0x92, 0x93, 0x94) else REPORT_LENGTH
            end = index + frame_length
            if end > len(data):
                incomplete = index
                break
            candidate = data[index:end]
            valid = (candidate[-2:] == INNER_TAIL and
                     candidate[-3] == (sum(candidate[2:-3]) & 0xFF))
            if valid:
                frames.append(candidate)
                index = end
            else:
                index += 1
        else:
            index += 1

    if incomplete is not None:
        return frames, data[incomplete:]
    # Preserve one trailing EB as a possible header across a read boundary.
    return frames, data[-1:] if data.endswith(b"\xEB") else b""


def extract_report_frames(buffer: bytes) -> tuple[list[ExtractedReport], bytes]:
    """Extract valid complete reports from a continuous input stream, retaining partial data."""
    data, result, i, processed_end, incomplete = bytes(buffer), [], 0, 0, None
    while i <= len(data) - 2:
        if data[i:i + 2] == MASTER_HEADER:
            if i + 8 > len(data): incomplete = i; break
            size = int.from_bytes(data[i + 3:i + 5], "big") + 8
            if i + size > len(data): incomplete = i; break
            candidate = data[i:i + size]
            try:
                parsed = unwrap_master_frame(candidate)
                if _valid_direct_report(parsed.payload):
                    result.append(ExtractedReport("master", parsed.payload, candidate))
                processed_end, i = i + size, i + size
                continue
            except ValueError:
                pass
        elif data[i:i + 2] == INNER_HEADER:
            if i + REPORT_LENGTH > len(data): incomplete = i; break
            candidate = data[i:i + REPORT_LENGTH]
            if _valid_direct_report(candidate):
                result.append(ExtractedReport("direct", candidate, candidate))
                processed_end, i = i + REPORT_LENGTH, i + REPORT_LENGTH
                continue
        i += 1
    if incomplete is not None: remaining = data[incomplete:]
    elif processed_end: remaining = data[processed_end:]
    else: remaining = data[-256:]
    return result, remaining


def _u(data: bytes) -> int: return int.from_bytes(data, "big")


def analyze_command(data: bytes, mode: str = "direct") -> str:
    if mode == "master":
        outer = unwrap_master_frame(data)
        prefix = f"========= 主控外层解析结果 =========\n外层指令码:\t0x{outer.outer_code:02X}\n负载长度:\t{len(outer.payload)} 字节\n外层起始偏移:\t{outer.start_index}\n说明:\t\t已按主控外层解包，并继续解析通信板内层指令帧\n===================================\n\n"
        return prefix + analyze_command(outer.payload)
    if len(data) < 22: return "错误: 数据长度不足 22 字节"
    d, code, field, check = bytes(data), data[2], data[3:19], data[19]
    names = {0x90: "工作设置", 0x91: "遥测开关", 0x92: "唤醒帧开关", 0x93: "目标MAC地址设置", 0x94: "ARQ寄存器调试"}
    out = []
    if d[:2] != INNER_HEADER: out.append(f"警告: 帧头不匹配，应为 0xEB90，实际为 0x{d[0]:02X}{d[1]:02X}\n")
    if d[20:22] != INNER_TAIL: out.append(f"警告: 帧尾不匹配，应为 0x55AA，实际为 0x{d[20]:02X}{d[21]:02X}\n")
    calc = sum(d[2:19]) & 0xFF
    if check != calc: out.append(f"警告: 校验和错误，计算值为 0x{calc:02X}，实际值为 0x{check:02X}\n")
    out.append(f"========= 测控指令传输帧解析结果 =========\n\n1. 帧基本信息：\n   帧头:\t\t0x{d[0]:02X}{d[1]:02X}\n   指令码:\t0x{code:02X} ({names.get(code, '未知指令')})\n   校验和:\t0x{check:02X} (计算值: 0x{calc:02X})\n   帧尾:\t\t0x{d[20]:02X}{d[21]:02X}\n\n2. 指令详细信息：\n")
    if code == 0x90: out.append(f"   指令类型:\t工作设置\n   消息ID:\t\t0x{field[0]:02X}\n   设备ID:\t\t0x{field[1]:02X}\n   工作模式:\t0x{field[2]:02X} ({'外部通信模式' if field[2] == 0 else '内部自检模式' if field[2] == 1 else '未知模式'})\n")
    elif code in (0x91, 0x92):
        action = '开启' if field[0] == 0xE5 else '关闭' if field[0] == 0xEA else '未知状态'
        target = '遥测' if code == 0x91 else '唤醒帧'
        out.append(f"   指令类型:\t{names[code]}\n   消息ID:\t\t0x{field[0]:02X} ({action}{target if action != '未知状态' else ''})\n   设备ID:\t\t0x{field[1]:02X}\n")
    elif code == 0x93: out.append(f"   指令类型:\t目标MAC地址设置\n   MAC地址:\t{bytes_to_hex(field[:6], ':')}\n   设备ID:\t\t0x{field[6]:02X}\n")
    elif code == 0x94:
        operation, target = field[2], field[3]
        offset = int.from_bytes(field[4:6], "big")
        value = int.from_bytes(field[6:10], "big")
        unused = field[10:16]
        register = find_register(target, offset) if operation in (0x01, 0x02) else None
        if register is not None:
            register_details = (
                f"   寄存器名称:\t{register.name}\n"
                f"   访问属性:\t{register.access}\n"
                f"   所属分类:\t{register.section}\n"
            )
        elif operation == 0x03:
            register_details = (
                "   寄存器名称:\tDUMP_ALL无需选择寄存器\n"
                "   访问属性:\tN/A\n"
                "   所属分类:\tN/A\n"
            )
        else:
            register_details = (
                "   寄存器名称:\t未知或未列入白名单\n"
                "   访问属性:\t未知或未列入白名单\n"
                "   所属分类:\t未知或未列入白名单\n"
            )
        out.append(
            f"   指令类型:\tARQ寄存器调试\n"
            f"   消息ID:\t\t0x{field[0]:02X}\n"
            f"   设备ID:\t\t0x{field[1]:02X}\n"
            f"   TargetIP:\t0x{target:02X} ({ARQ_TARGET_NAMES.get(target, '未知目标')})\n"
            f"   Operation:\t0x{operation:02X} ({ARQ_OPERATION_NAMES.get(operation, '未知操作')})\n"
            f"   寄存器偏移:\t0x{offset:04X}\n"
            f"{register_details}"
            f"   寄存器值:\t0x{value:08X}\n"
            f"   未使用字节:\t{bytes_to_hex(unused)} ({'正确' if unused == bytes([0x55] * 6) else '错误'})\n"
        )
    else: out.append(f"   指令类型:\t未知指令\n   数据域:\t\t{bytes_to_hex(field)}\n")
    return "".join(out) + "\n======================================\n"


def analyze_report(data: bytes, mode: str = "direct") -> str:
    if mode == "master":
        outer = unwrap_master_frame(data)
        prefix = f"========= 主控外层解析结果 =========\n外层指令码:\t0x{outer.outer_code:02X}\n负载长度:\t{len(outer.payload)} 字节\n外层起始偏移:\t{outer.start_index}\n说明:\t\t已按主控外层解包，并继续解析通信板内层上报帧\n===================================\n\n"
        return prefix + analyze_report(outer.payload)
    d = bytes(data)
    if len(d) < REPORT_LENGTH: return "错误: 数据长度不足134字节"
    field, telemetry = d[3:131], d[8:131]
    get = lambda start, end: _u(telemetry[start:end])
    signed16 = lambda start: int.from_bytes(telemetry[start:start + 2], "big", signed=True)
    checksum = sum(d[2:131]) & 0xFF
    power_temp = lambda x: ((1200 - (x / 4095) * 3300) / 2) - 273.15
    fpga_temp = lambda x: x / 65536 * 503.975 - 273.15
    lines = ["========= 遥测上报帧解析结果 (V2.0 帧结构) =========\n\n",
             "1. 帧基本信息：\n", f"   帧头:\t\t0x{d[0]:02X}{d[1]:02X}\n", f"   标识码:\t0x{d[2]:02X}\n",
             f"   校验和:\t0x{d[131]:02X} (计算值: 0x{checksum:02X} - {'正确' if d[131] == checksum else '错误'})\n", f"   帧尾:\t\t0x{d[132]:02X}{d[133]:02X}\n\n",
             "2. 数据域元信息：\n", f"   消息ID:\t\t0x{field[0]:02X}\n", f"   设备ID:\t\t0x{field[1]:02X}\n", f"   帧序号:\t\t{field[2]}\n\n",
             "3. 数字量遥测内容：\n", f"   系统心跳:\t\t{telemetry[0]} ({'有' if telemetry[0] == 1 else '无'})\n", f"   工作模式:\t\t{telemetry[1]} ({'内部自检' if telemetry[1] == 1 else '外部通信'})\n", f"   激光帧同步状态:\t{telemetry[2]} ({'已锁定' if telemetry[2] == 1 else '未锁定'})\n",
             "\n--- 激光与网口统计 ---\n", f"   激光发送/接收帧数:\t{get(3,11)} / {get(11,19)}\n", f"   网口发送/接收帧数:\t{get(35,39)} / {get(39,43)}\n", f"   激光接收总比特数/s:\t{get(19,27)}\n", f"   激光接收误比特数/s:\t{get(27,35)}\n",
             "\n--- 速率与功率 ---\n", f"   发送/接收速率:\t\t{get(51,54)} Mbps / {get(54,57)} Mbps\n"]
    for label, a, b in (("网口SFP0",43,45),("光口SFP1",47,49)):
        tx, rx = get(a,a+2), get(b,b+2)
        lines.append(f"   {label} 发/收光功率:\t{tx * .0001:.4f} mW / {rx * .0001:.4f} mW (原始值: {tx} / {rx})\n")
    ptemps = [get(x, x + 2) for x in (97, 99, 101)]
    fpga, sfp0, sfp1 = get(103,105), signed16(105) / 256, signed16(107) / 256
    lines += ["\n--- 字节统计与误码 ---\n", f"   发送成功/失败字节数:\t{get(73,81)} / {get(81,89)}\n", f"   接收字节数:\t\t{get(89,97)}\n", f"   发送/接收误码数/s:\t{get(57,65)} / {get(65,73)}\n", "\n--- 温度与地址 ---\n",
              f"   电源温度 (1/2/3):\t{power_temp(ptemps[0]):.2f}°C, {power_temp(ptemps[1]):.2f}°C, {power_temp(ptemps[2]):.2f}°C (原始值: {ptemps[0]}, {ptemps[1]}, {ptemps[2]})\n", f"   FPGA 温度:\t\t{fpga_temp(fpga):.2f}°C (原始值: {fpga})\n", f"   网口SFP0 温度:\t\t{sfp0:.2f}°C\n", f"   光口SFP1 温度:\t\t{sfp1:.2f}°C\n", f"   MAC地址:\t\t{bytes_to_hex(telemetry[109:115], ':')}\n"]
    bits, errors = get(19,27), get(65,73)
    if bits: lines.append(f"\n   计算误码率 (BER):\t\t{errors / bits:.6e}\n")
    return "".join(lines) + "\n===========================================================\n"
