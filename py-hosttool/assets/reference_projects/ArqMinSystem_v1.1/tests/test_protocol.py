import struct

from protocol import (McpFrame, MessageType, Opcode, Status, Target,
                      build_command, decode_frame, encode_frame,
                      parse_register_fragment)


def test_frame_round_trip():
    wire = build_command(7, Target.A2B_LINK, Opcode.GET_CONFIG, b"abc")
    frame = decode_frame(wire)
    assert frame.sequence == 7
    assert frame.target == Target.A2B_LINK
    assert frame.payload == b"abc"


def test_register_fragment_parse():
    data = struct.pack("<HBBHH", 9, 0, 1, 2, 2)
    data += struct.pack("<HIHI", 0x0C4, 1, 0x8034, 2)
    payload = struct.pack("<HH", Status.OK, 0) + data
    raw = encode_frame(MessageType.RESPONSE, 3, Target.ALICE_TX_SCHEDULER,
                       Opcode.READ_REG_BLOCK, payload)
    fragment = parse_register_fragment(decode_frame(raw))
    assert fragment.capture_id == 9
    assert fragment.values == {0x0C4: 1, 0x8034: 2}
