import csv
import time

from hosttool.recording import CaptureEntry, CaptureRecorder, CaptureReplay


def test_capture_is_lossless_and_replay_can_filter_direction(tmp_path):
    recorder = CaptureRecorder(tmp_path, prefix="session", queue_size=8)
    sent_at = time.time()
    entries = [
        CaptureEntry(sent_at, "TX", "COM1", b"\x00\xff\x10", {"name": "命令"}),
        CaptureEntry(sent_at + 0.001, "RX", "COM1", b"\x7f\x80", {"valid": True}),
    ]
    for entry in entries:
        assert recorder.put(entry)
    assert recorder.close()
    assert recorder.error is None

    replayed = list(CaptureReplay(recorder.jsonl_path).entries())
    assert replayed == entries
    assert list(CaptureReplay(recorder.jsonl_path).entries("RX")) == [entries[1]]

    with recorder.csv_path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.reader(source))
    assert rows[0] == ["时间", "方向", "通道", "原始数据_HEX", "解析_JSON", "错误"]
    assert len(rows) == 3
    assert rows[1][3] == "00 FF 10"


def test_capture_rejects_queue_overflow_explicitly(tmp_path):
    recorder = CaptureRecorder(tmp_path, queue_size=1)
    entry = CaptureEntry(time.time(), "RX", "CH0", b"\x00")
    recorder._stop.set()
    recorder._thread.join(2)
    assert not recorder._thread.is_alive()
    recorder._queue.put_nowait(entry)
    assert recorder.put(entry) is False
    assert recorder.error is not None
    assert "队列已满" in recorder.error
