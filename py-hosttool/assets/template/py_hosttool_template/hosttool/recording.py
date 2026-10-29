"""Bounded background capture recording and read-only JSONL replay."""
from __future__ import annotations

import csv
import json
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Literal


CAPTURE_QUEUE_SIZE = 4096
CAPTURE_FLUSH_INTERVAL = 0.5


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class CaptureEntry:
    """One lossless capture event; payload bytes remain the source of truth."""

    timestamp: float
    direction: Literal["RX", "TX"]
    channel: str
    payload: bytes
    decoded: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def __post_init__(self) -> None:
        if self.direction not in ("RX", "TX"):
            raise ValueError("direction must be RX or TX")
        if not self.channel:
            raise ValueError("channel must not be empty")
        if not isinstance(self.payload, bytes):
            raise TypeError("payload must be bytes")

    def to_record(self) -> dict[str, Any]:
        return {
            "kind": "capture",
            "timestamp": self.timestamp,
            "direction": self.direction,
            "channel": self.channel,
            "payload_hex": self.payload.hex(),
            "decoded": self.decoded,
            "error": self.error,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "CaptureEntry":
        if record.get("kind") != "capture":
            raise ValueError("unsupported capture record kind")
        try:
            timestamp = float(record["timestamp"])
            direction = record["direction"]
            channel = str(record["channel"])
            payload = bytes.fromhex(record["payload_hex"])
            decoded = record.get("decoded", {})
            error = record.get("error", "")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid capture record") from exc
        if not isinstance(decoded, dict) or not isinstance(error, str):
            raise ValueError("invalid capture metadata")
        return cls(timestamp, direction, channel, payload, decoded, error)


class CaptureRecorder:
    """Write JSONL plus a human-readable CSV without blocking the GUI thread."""

    def __init__(
        self,
        directory: Path | str,
        *,
        prefix: str = "capture",
        queue_size: int = CAPTURE_QUEUE_SIZE,
    ) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.jsonl_path = self.directory / f"{prefix}_{stamp}.jsonl"
        self.csv_path = self.directory / f"{prefix}_{stamp}.csv"
        self._queue: queue.Queue[CaptureEntry] = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._error_lock = threading.Lock()
        self._error: str | None = None
        self._thread = threading.Thread(
            target=self._run, name="hosttool-capture-recorder", daemon=False
        )
        self._thread.start()

    @property
    def error(self) -> str | None:
        with self._error_lock:
            return self._error

    def _set_error(self, message: str) -> None:
        with self._error_lock:
            if self._error is None:
                self._error = message

    def put(self, entry: CaptureEntry) -> bool:
        if not isinstance(entry, CaptureEntry):
            raise TypeError("entry must be CaptureEntry")
        if self.error:
            return False
        try:
            self._queue.put_nowait(entry)
        except queue.Full:
            self._set_error("记录队列已满；捕获不完整")
            return False
        return True

    def _run(self) -> None:
        try:
            with self.jsonl_path.open("w", encoding="utf-8", newline="\n") as source, \
                    self.csv_path.open("w", encoding="utf-8-sig", newline="") as view:
                writer = csv.writer(view)
                writer.writerow(
                    ["时间", "方向", "通道", "原始数据_HEX", "解析_JSON", "错误"]
                )
                last_flush = time.monotonic()
                while not self._stop.is_set() or not self._queue.empty():
                    try:
                        entry = self._queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    record = entry.to_record()
                    source.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                    writer.writerow(
                        [
                            _timestamp(entry.timestamp),
                            entry.direction,
                            entry.channel,
                            entry.payload.hex(" ").upper(),
                            json.dumps(entry.decoded, ensure_ascii=False, separators=(",", ":")),
                            entry.error,
                        ]
                    )
                    if time.monotonic() - last_flush >= CAPTURE_FLUSH_INTERVAL:
                        source.flush()
                        view.flush()
                        last_flush = time.monotonic()
        except Exception as exc:
            self._set_error(f"记录写入失败：{exc}")

    def close(self, timeout: float = 3.0) -> bool:
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)
        if self._thread.is_alive():
            self._set_error("记录线程未在限定时间内停止")
            return False
        return self.error is None


class CaptureReplay:
    """Read-only JSONL replay with an optional direction filter."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def entries(
        self, direction: Literal["RX", "TX"] | None = None
    ) -> Iterator[CaptureEntry]:
        if direction not in (None, "RX", "TX"):
            raise ValueError("direction must be RX, TX or None")
        with self.path.open(encoding="utf-8") as source:
            for line_number, raw in enumerate(source, 1):
                try:
                    record = json.loads(raw)
                    entry = CaptureEntry.from_record(record)
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"invalid capture record at line {line_number}"
                    ) from exc
                if direction is None or entry.direction == direction:
                    yield entry
