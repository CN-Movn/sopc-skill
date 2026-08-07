from hosttool.serial_console import format_hex, parse_hex_text, status_color_for_line
from hosttool.serial_worker import port_sort_key


def test_hex_helpers():
    assert parse_hex_text("AA 55\n01") == bytes.fromhex("AA 55 01")
    assert format_hex(bytes.fromhex("AA 55 01")) == "AA 55 01"


def test_port_order():
    assert port_sort_key("COM4") < port_sort_key("COM12") < port_sort_key("USB0")


def test_status_severity_priority():
    assert status_color_for_line("[OK] recovered [ERROR] fatal") == "#d93025"
