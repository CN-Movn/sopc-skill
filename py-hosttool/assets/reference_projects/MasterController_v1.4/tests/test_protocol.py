from protocol import *
from gui import ConsoleWindow, status_color_for_line
from arq_register_map import (
    ARQ_IP_RX_SCHEDULER, ARQ_IP_RX_WRAPPER, ARQ_IP_TX_SCHEDULER,
    ARQ_IP_TX_WRAPPER, ARQ_OP_READ_REG, ARQ_OP_WRITE_REG,
    RX_SCHEDULER_REGISTERS, RX_WRAPPER_REGISTERS,
    TX_SCHEDULER_REGISTERS, TX_WRAPPER_REGISTERS,
    find_register, registers_for_operation, registers_for_target,
)
import pytest


def test_inner_command_90_matches_matlab_layout():
    frame = create_inner_command_frame(0x90, 0x00, 0x01, 0x01)
    assert frame == bytes.fromhex("EB 90 90 00 01 01 " + "55 " * 13 + "E3 55 AA")


def test_inner_mac_and_arq_validation():
    frame = create_inner_command_frame(0x93, device_id=7, mac_address="00:25:1F:D2:00:00")
    assert frame[3:10] == bytes.fromhex("00 25 1F D2 00 00 07")


def _assert_inner_checksum(frame: bytes):
    assert len(frame) == 22
    assert frame[:2] == bytes.fromhex("EB 90")
    assert frame[13:19] == bytes([0x55] * 6)
    assert frame[20:22] == bytes.fromhex("55 AA")
    assert frame[19] == sum(frame[2:19]) & 0xFF


def test_arq_write_reg_layout_and_checksum():
    frame = create_inner_command_frame(
        0x94, device_id=0x01, arq_target=0x03, arq_operation=0x01,
        arq_offset=0x0004, arq_value=0x12345678,
    )
    assert frame[3:19] == bytes.fromhex(
        "00 01 01 03 00 04 12 34 56 78 55 55 55 55 55 55"
    )
    _assert_inner_checksum(frame)


def test_arq_register_tables_match_vitis_counts_and_target_mapping():
    assert len(TX_WRAPPER_REGISTERS) == 36
    assert len(RX_WRAPPER_REGISTERS) == 21
    assert len(TX_SCHEDULER_REGISTERS) == 23
    assert len(RX_SCHEDULER_REGISTERS) == 23
    assert registers_for_target(ARQ_IP_TX_WRAPPER) is TX_WRAPPER_REGISTERS
    assert registers_for_target(ARQ_IP_RX_WRAPPER) is RX_WRAPPER_REGISTERS
    assert registers_for_target(ARQ_IP_TX_SCHEDULER) is TX_SCHEDULER_REGISTERS
    assert registers_for_target(ARQ_IP_RX_SCHEDULER) is RX_SCHEDULER_REGISTERS
    for registers in (
        TX_WRAPPER_REGISTERS, RX_WRAPPER_REGISTERS,
        TX_SCHEDULER_REGISTERS, RX_SCHEDULER_REGISTERS,
    ):
        assert len({register.offset for register in registers}) == len(registers)


def test_arq_register_access_filters_match_vitis_rules():
    tx_read = registers_for_operation(ARQ_IP_TX_WRAPPER, ARQ_OP_READ_REG)
    tx_write = registers_for_operation(ARQ_IP_TX_WRAPPER, ARQ_OP_WRITE_REG)
    rx_write = registers_for_operation(ARQ_IP_RX_WRAPPER, ARQ_OP_WRITE_REG)
    scheduler_read = registers_for_operation(ARQ_IP_TX_SCHEDULER,
                                             ARQ_OP_READ_REG)
    scheduler_write = registers_for_operation(ARQ_IP_TX_SCHEDULER,
                                              ARQ_OP_WRITE_REG)
    assert len(tx_read) == 36
    assert all(reg.access not in ("WO", "PULSE") for reg in tx_read)
    assert [reg.name for reg in tx_write] == [
        "LEGACY_FRAME_SHADOW_ALIAS", "CFG_PAYLOAD_SHADOW",
        "CFG_FRAME_SHADOW", "CFG_APPLY_STATUS",
    ]
    assert [reg.name for reg in rx_write] == [
        "CFG_PAYLOAD_SHADOW", "CFG_FRAME_SHADOW", "CFG_APPLY_STATUS",
    ]
    assert len(scheduler_read) == 22
    assert all(reg.access not in ("WO", "PULSE") for reg in scheduler_read)
    assert {reg.name for reg in scheduler_write} == {
        "SCRATCH", "COMMAND", "STATUS", "ERROR_STATUS", "DEBUG_SELECT",
        "CFG_PAYLOAD_SHADOW", "CFG_FRAME_SHADOW", "CFG_APPLY_STATUS",
    }


def test_arq_write_masks_match_vitis_whitelist():
    assert find_register(ARQ_IP_TX_WRAPPER, 0x000).write_mask == 0x0000FFFF
    expected = {
        0x004: 0xFFFFFFFF,
        0x008: 0x00000007,
        0x00C: 0x00000020,
        0x018: 0x00000003,
        0x020: 0x000000F7,
        0x080: 0x0000FFFF,
        0x084: 0x0000FFFF,
        0x088: 0x00000009,
    }
    assert {
        register.offset: register.write_mask
        for register in registers_for_operation(ARQ_IP_TX_SCHEDULER,
                                                 ARQ_OP_WRITE_REG)
    } == expected
    assert find_register(ARQ_IP_RX_SCHEDULER, 0x018).write_mask == 0x00000023
    for target in (
        ARQ_IP_TX_WRAPPER, ARQ_IP_RX_WRAPPER,
        ARQ_IP_TX_SCHEDULER, ARQ_IP_RX_SCHEDULER,
    ):
        assert find_register(target, 0x080).access == "RW"
        assert find_register(target, 0x084).access == "RW"
        assert find_register(target, 0x088).access == "CONTROL/STATUS"
        assert find_register(target, 0x08C).access == "RO"
        assert find_register(target, 0x090).access == "RO"


def test_arq_rejects_nonzero_msg_id():
    with pytest.raises(ValueError, match="消息 ID必须为00"):
        create_inner_command_frame(
            0x94, msg_id=1, device_id=1,
            arq_target=ARQ_IP_TX_SCHEDULER,
            arq_operation=ARQ_OP_READ_REG, arq_offset=0x004,
        )


def test_arq_selected_register_offset_is_encoded_at_bytes_7_and_8():
    scratch = find_register(ARQ_IP_TX_SCHEDULER, 0x004)
    assert scratch is not None and scratch.name == "SCRATCH"
    frame = create_inner_command_frame(
        0x94, msg_id=0, device_id=1, arq_target=ARQ_IP_TX_SCHEDULER,
        arq_operation=ARQ_OP_WRITE_REG, arq_offset=scratch.offset,
        arq_value=0x12345678,
    )
    assert frame[7:9] == scratch.offset.to_bytes(2, "big")


def test_arq_read_reg_uses_only_offset_and_checksum():
    frame = create_inner_command_frame(
        0x94, device_id=0x01, arq_target=0x04, arq_operation=0x02,
        arq_offset=0x0020, arq_value=0,
    )
    assert frame[3:9] == bytes.fromhex("00 01 02 04 00 20")
    assert frame[9:13] == bytes(4)
    _assert_inner_checksum(frame)


def test_arq_single_ip_dump_uses_zero_fields_and_checksum():
    frame = create_inner_command_frame(
        0x94, device_id=0x01, arq_target=0x02, arq_operation=0x03,
        arq_offset=0, arq_value=0,
    )
    assert frame[3:7] == bytes.fromhex("00 01 03 02")
    assert frame[7:13] == bytes(6)
    _assert_inner_checksum(frame)


def test_arq_all_dump_and_all_operation_constraint():
    frame = create_inner_command_frame(
        0x94, device_id=0x01, arq_target=0xFF, arq_operation=0x03,
    )
    assert frame[3:7] == bytes.fromhex("00 01 03 FF")
    _assert_inner_checksum(frame)
    with pytest.raises(ValueError, match="ALL"):
        create_inner_command_frame(
            0x94, device_id=0x01, arq_target=0xFF, arq_operation=0x02,
        )
    with pytest.raises(ValueError, match="READ_REG"):
        create_inner_command_frame(
            0x94, device_id=0x01, arq_target=0x03, arq_operation=0x02,
            arq_offset=0x0004, arq_value=1,
        )
    with pytest.raises(ValueError, match="DUMP_ALL"):
        create_inner_command_frame(
            0x94, device_id=0x01, arq_target=0x03, arq_operation=0x03,
            arq_offset=0x0004,
        )


def test_master_roundtrip_and_big_endian_length():
    payload = bytes(range(22)); wrapped = wrap_master_frame("90", payload); result = unwrap_master_frame(b"noise" + wrapped)
    assert wrapped[3:5] == b"\x00\x16" and result.payload == payload and result.outer_code == 0x90 and result.start_index == 6


def _report() -> bytes:
    d = bytearray(134); d[:3] = b"\xEB\x90\xEA"; d[3:131] = bytes(range(128)); d[131] = sum(d[2:131]) & 0xff; d[132:] = b"\x55\xAA"; return bytes(d)


def test_report_extraction_handles_fragmented_direct_and_master_streams():
    direct = _report(); master = wrap_master_frame(0x90, direct)
    frames, remainder = extract_report_frames(b"junk" + direct[:50]); assert not frames and remainder == direct[:50]
    frames, remainder = extract_report_frames(remainder + direct[50:] + master)
    assert [f.mode for f in frames] == ["direct", "master"] and remainder == b""


def test_protocol_log_extraction_emits_only_complete_frames_per_serial_segments():
    report = _report()
    command = create_inner_command_frame(0x91, 0xE5, 1)
    first, remaining = extract_protocol_frames(report[:1])
    assert first == [] and remaining == report[:1]
    second, remaining = extract_protocol_frames(remaining + report[1:83])
    assert second == [] and remaining == report[:83]
    third, remaining = extract_protocol_frames(remaining + report[83:] + command)
    assert third == [report, command]
    assert remaining == b""


def test_analyzers_produce_expected_semantics():
    command = create_inner_command_frame(0x91, 0xEA, 1)
    assert "关闭遥测" in analyze_command(command)
    arq = create_inner_command_frame(
        0x94, device_id=0x01, arq_target=0x03, arq_operation=0x01,
        arq_offset=0x0004, arq_value=0x12345678,
    )
    analysis = analyze_command(arq)
    assert "TX Scheduler" in analysis and "WRITE_REG" in analysis
    assert "消息ID:\t\t0x00" in analysis
    assert "0x12345678" in analysis and "未使用字节:" in analysis
    assert "55 55 55 55 55 55 (正确)" in analysis
    assert "寄存器名称:\tSCRATCH" in analysis
    assert "访问属性:\tRW" in analysis
    assert "所属分类:\tConfig" in analysis
    assert "遥测上报帧解析结果" in analyze_report(_report())


def test_arq_analyzer_marks_unknown_register_without_inventing_metadata():
    frame = create_inner_command_frame(
        0x94, msg_id=0, device_id=1, arq_target=ARQ_IP_TX_SCHEDULER,
        arq_operation=ARQ_OP_READ_REG, arq_offset=0xFFC, arq_value=0,
    )
    analysis = analyze_command(frame)
    assert "未知或未列入白名单" in analysis


def test_gui_display_helper_keeps_port_order():
    assert ConsoleWindow._port_sort_key("COM4") < ConsoleWindow._port_sort_key("COM12")
    assert ConsoleWindow._port_sort_key("COM12") < ConsoleWindow._port_sort_key("USB0")


def test_status_color_selection_handles_mixed_plaintext_lines():
    assert status_color_for_line("[OK] Scheduler idle") == "#188038"
    assert status_color_for_line("prefix [WARN] pending") == "#e37400"
    assert status_color_for_line("[ERROR] reject") == "#d93025"
    assert status_color_for_line("[INFO] counter = 1") == "#5f6368"
    assert status_color_for_line("ordinary serial output") is None
    assert status_color_for_line("[OK] recovered, [ERROR] still fatal") == "#d93025"
