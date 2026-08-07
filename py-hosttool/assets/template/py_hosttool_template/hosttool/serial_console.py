"""Reusable serial settings, RX/TX log, sender, periodic send and child window.

The interaction details intentionally preserve the mature reference
serial-console behavior: gray log headers, RX green / TX blue payloads, dynamic
HEX wrapping based on the actual viewport, scroll-position preservation, and an
explicit send/stop periodic-send state machine.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
    QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)

from .config import APP_NAME, DEFAULT_BAUDRATE
from .serial_worker import SerialSettings, SerialWorker, available_ports
from .theme import COLORS
from .window_chrome import FramelessWindow, WindowGroup


STATUS_TAG_COLORS = {
    "[OK]": COLORS["success"],
    "[WARN]": COLORS["warning"],
    "[ERROR]": COLORS["error"],
    "[INFO]": COLORS["muted"],
}

# Preserve the validated serial-log semantics rather than remapping RX/TX
# onto the generic theme accent/success tokens.
LOG_HEADER_COLOR = "#72777f"
LOG_RX_COLOR = "#237a32"
LOG_TX_COLOR = "#1565c0"
LOG_ERROR_COLOR = "#b71c1c"
LOG_INFO_COLOR = "#5f6368"


def status_color_for_line(line: str) -> str | None:
    for tag in ("[ERROR]", "[WARN]", "[OK]", "[INFO]"):
        if tag in line:
            return STATUS_TAG_COLORS[tag]
    return None


def parse_hex_text(text: str) -> bytes:
    compact = "".join(text.split())
    if not compact:
        return b""
    if len(compact) % 2:
        raise ValueError("HEX 数据必须包含偶数个十六进制字符。")
    try:
        return bytes.fromhex(compact)
    except ValueError as exc:
        raise ValueError("HEX 数据包含非法字符。") from exc


def format_hex(data: bytes) -> str:
    """Return stable one-line HEX for exports/tests; GUI wrapping is dynamic."""
    return data.hex(" ").upper()


class PortComboBox(QComboBox):
    def __init__(self, refresh_callback, parent=None) -> None:
        super().__init__(parent)
        self._refresh_callback = refresh_callback

    def showPopup(self) -> None:
        self._refresh_callback()
        super().showPopup()


class SerialConsolePanel(QWidget):
    status_changed = Signal(str)
    counts_changed = Signal(int, int, int)
    request_child = Signal()

    def __init__(self, *, allow_child: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.allow_child = allow_child
        self.worker = SerialWorker(self)
        self.worker.received.connect(self._received)
        self.worker.sent.connect(self._sent)
        self.worker.opened.connect(self._opened)
        self.worker.closed.connect(self._closed)
        self.worker.error.connect(self._serial_error)
        self.worker.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._periodic_send)
        self.rx_count = self.tx_count = self.frame_count = 0
        self._connected = False
        # (timestamp, kind, plaintext, hex_bytes_if_rendered_as_hex)
        self._log_entries: list[tuple[str, str, str, bytes | None]] = []
        self._build()
        self.refresh_ports(silent=True)
        self.log("INFO", "工具已启动，等待操作。")

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        controls = QWidget()
        controls.setMinimumWidth(235)
        controls.setMaximumWidth(260)
        control_layout = QVBoxLayout(controls)
        control_layout.setContentsMargins(4, 4, 4, 4)
        self._serial_controls(control_layout)
        outer.addWidget(controls)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 2, 2, 2)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFontFamily("Consolas")
        # HEX rows are explicitly laid out from viewport metrics. Qt must not
        # wrap those rows a second time.
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.log_view.setPlaceholderText("接收日志")
        right_layout.addWidget(self.log_view, 3)

        send_group = QGroupBox("数据发送")
        send_layout = QVBoxLayout(send_group)
        send_layout.setContentsMargins(7, 6, 7, 6)
        send_layout.setSpacing(4)
        self.send_text = QPlainTextEdit()
        self.send_text.setFixedHeight(76)
        self.send_text.setPlaceholderText("发送 HEX 或 ASCII 数据")
        send_layout.addWidget(self.send_text)
        actions = QGridLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setHorizontalSpacing(5)
        actions.setVerticalSpacing(0)
        for column, (text, callback) in enumerate((
            ("发送", self.send_input),
            ("清空发送区", self.send_text.clear),
            ("清空日志", self.clear_log),
            ("保存日志", self.save_log),
        )):
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button, 0, column)
            if column == 0:
                self.send_btn = button
        send_layout.addLayout(actions)
        right_layout.addWidget(send_group)
        outer.addWidget(right, 1)

    def _serial_controls(self, layout: QVBoxLayout) -> None:
        settings = QGroupBox("串口参数")
        form = QFormLayout(settings)
        form.setContentsMargins(7, 7, 7, 7)
        form.setHorizontalSpacing(6)
        form.setVerticalSpacing(3)
        self.port = PortComboBox(self.refresh_ports)
        self.baud = QComboBox()
        self.baud.addItems([str(value) for value in (
            9600, 19200, 38400, 57600, 115200, 230400, 460800, 500000,
            921600, 1000000, 1500000, 2000000, 3000000,
        )])
        self.baud.setCurrentText(str(DEFAULT_BAUDRATE))
        self.databits = QComboBox(); self.databits.addItems(["8", "7", "6", "5"])
        self.stopbits = QComboBox(); self.stopbits.addItems(["1", "1.5", "2"])
        self.parity = QComboBox(); self.parity.addItems(["NONE", "ODD", "EVEN"])
        self.flow = QCheckBox("启用硬件流控")
        for label, widget in (("串口", self.port), ("波特率", self.baud),
                              ("数据位", self.databits), ("停止位", self.stopbits),
                              ("校验位", self.parity)):
            widget.setMaximumWidth(155)
            form.addRow(label, widget)
        form.addRow(self.flow)
        open_area = QWidget()
        open_layout = QHBoxLayout(open_area)
        open_layout.setContentsMargins(0, 2, 0, 0)
        self.connection_lamp = QLabel()
        self.connection_lamp.setFixedSize(30, 30)
        self._set_connection_lamp(False)
        buttons = QVBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(3)
        self.open_button = QPushButton("打开串口")
        self.open_button.clicked.connect(self.toggle_connection)
        buttons.addWidget(self.open_button)
        if self.allow_child:
            child = QPushButton("打开新串口")
            child.clicked.connect(self.request_child.emit)
            buttons.addWidget(child)
        open_layout.addWidget(self.connection_lamp, 0, Qt.AlignmentFlag.AlignVCenter)
        open_layout.addLayout(buttons, 1)
        form.addRow(open_area)
        layout.addWidget(settings)

        receive = QGroupBox("接收设置")
        receive_layout = QVBoxLayout(receive)
        receive_layout.setContentsMargins(7, 5, 7, 5)
        receive_layout.setSpacing(2)
        self.rx_ascii, self.rx_hex = QRadioButton("ASCII"), QRadioButton("HEX")
        self.rx_hex.setChecked(True)
        row = QHBoxLayout(); row.addWidget(self.rx_ascii); row.addWidget(self.rx_hex); row.addStretch(1)
        receive_layout.addLayout(row)
        layout.addWidget(receive)

        transmit = QGroupBox("发送设置")
        transmit_layout = QVBoxLayout(transmit)
        transmit_layout.setContentsMargins(7, 5, 7, 5)
        transmit_layout.setSpacing(2)
        self.tx_ascii, self.tx_hex = QRadioButton("ASCII"), QRadioButton("HEX")
        self.tx_hex.setChecked(True)
        row = QHBoxLayout(); row.addWidget(self.tx_ascii); row.addWidget(self.tx_hex); row.addStretch(1)
        transmit_layout.addLayout(row)
        self.periodic = QCheckBox("周期发送")
        self.period_ms = QSpinBox(); self.period_ms.setRange(1, 3600000); self.period_ms.setValue(1000)
        self.period_ms.setMaximumWidth(90)
        periodic_row = QHBoxLayout()
        periodic_row.addWidget(self.periodic); periodic_row.addWidget(self.period_ms)
        periodic_row.addWidget(QLabel("ms")); periodic_row.addStretch(1)
        transmit_layout.addLayout(periodic_row)
        # This checkbox selects periodic mode. It does not start the timer by
        # itself; the Send button starts/stops the active periodic session.
        self.periodic.toggled.connect(self._periodic_toggled)
        layout.addWidget(transmit)
        layout.addStretch(1)

    def refresh_ports(self, silent: bool = False) -> None:
        current = self.port.currentText()
        devices = available_ports()
        self.port.blockSignals(True)
        self.port.clear()
        self.port.addItems(devices)
        if current in devices:
            self.port.setCurrentText(current)
        self.port.blockSignals(False)
        if not silent:
            self.status_changed.emit("串口列表已刷新" if devices else "未检测到可用串口")

    def toggle_connection(self) -> None:
        if self._connected:
            self.worker.close_port()
            return
        if not self.port.currentText():
            QMessageBox.warning(self, "串口错误", "未检测到可用串口，请连接设备后重试。")
            return
        parity = {"NONE": "N", "ODD": "O", "EVEN": "E"}[self.parity.currentText()]
        settings = SerialSettings(
            port=self.port.currentText(),
            baudrate=int(self.baud.currentText()),
            bytesize=int(self.databits.currentText()),
            stopbits=float(self.stopbits.currentText()),
            parity=parity,
            rtscts=self.flow.isChecked(),
        )
        self.worker.open_port(settings)

    def _opened(self, port: str) -> None:
        self._connected = True
        self.open_button.setText("关闭串口")
        self._set_connection_lamp(True)
        self.status_changed.emit(f"串口 {port} 已打开")
        self.log("INFO", f"串口 {port} 已打开")

    def _closed(self) -> None:
        self._connected = False
        self._stop_periodic()
        self.open_button.setText("打开串口")
        self._set_connection_lamp(False)
        self.status_changed.emit("串口已关闭")
        self.log("INFO", "串口已关闭")

    def _set_connection_lamp(self, connected: bool) -> None:
        color = "#2e9b4c" if connected else "#c74343"
        self.connection_lamp.setToolTip("串口已打开" if connected else "串口已关闭")
        self.connection_lamp.setStyleSheet(
            f"background-color:{color};border:1px solid #737373;border-radius:15px;"
        )

    def _build_send_payload(self) -> bytes:
        text = self.send_text.toPlainText()
        return text.encode("utf-8") if self.tx_ascii.isChecked() else parse_hex_text(text)

    def send_input(self) -> None:
        # When a periodic session is active, the same button is the explicit
        # Stop action, matching the validated reference behavior.
        if self.timer.isActive():
            self._stop_periodic()
            return
        if not self._connected:
            QMessageBox.warning(self, "串口错误", "串口未打开，请先打开串口。")
            return
        try:
            payload = self._build_send_payload()
            if not payload:
                raise ValueError("发送区为空。")
        except ValueError as exc:
            QMessageBox.warning(self, "发送格式错误", str(exc))
            return

        if self.periodic.isChecked():
            # First frame is sent immediately; only subsequent frames wait for
            # the configured interval.
            self.worker.send(payload)
            self._start_periodic()
        else:
            self.worker.send(payload)

    def _sent(self, data: bytes) -> None:
        self.tx_count += len(data)
        self.frame_count += 1
        text = data.decode("utf-8", errors="replace")
        self._append_log_entry("TX", text, data if self.tx_hex.isChecked() else None)
        self.counts_changed.emit(self.rx_count, self.tx_count, self.frame_count)

    def _received(self, data: bytes) -> None:
        self.rx_count += len(data)
        text = data.decode("utf-8", errors="replace")
        self._append_log_entry("RX", text, data if self.rx_hex.isChecked() else None)
        self.counts_changed.emit(self.rx_count, self.tx_count, self.frame_count)

    def _periodic_toggled(self, enabled: bool) -> None:
        if not enabled and self.timer.isActive():
            self._stop_periodic()

    def _start_periodic(self) -> None:
        self.timer.start(self.period_ms.value())
        self.send_btn.setText("停止发送")
        self.status_changed.emit("周期发送已启动")
        self.log("INFO", f"周期发送已启动，周期 {self.period_ms.value()} ms。")

    def _stop_periodic(self) -> None:
        was_active = self.timer.isActive()
        self.timer.stop()
        if hasattr(self, "send_btn"):
            self.send_btn.setText("发送")
        if was_active:
            self.status_changed.emit("周期发送已停止")
            self.log("INFO", "周期发送已停止。")

    def _periodic_send(self) -> None:
        if not self._connected:
            self._stop_periodic()
            return
        try:
            payload = self._build_send_payload()
            if not payload:
                raise ValueError("发送区为空。")
        except ValueError as exc:
            self._stop_periodic()
            self.log("ERROR", f"周期发送失败：{exc}")
            return
        self.worker.send(payload)

    def _serial_error(self, text: str) -> None:
        # A communication error invalidates an active periodic session and the
        # connected-state presentation. A later open command safely closes any
        # stale Serial object before reopening it.
        self._stop_periodic()
        self._connected = False
        self.open_button.setText("打开串口")
        self._set_connection_lamp(False)
        self.status_changed.emit(text)
        self.log("ERROR", text)

    def log(self, level: str, text: str) -> None:
        self._append_log_entry(level, text, None)

    def _format_hex_frame(self, data: bytes) -> str:
        """Fit each HEX row to the actual log viewport and current font."""
        metrics = self.log_view.fontMetrics()
        margin = int(self.log_view.document().documentMargin())
        usable_width = max(1, self.log_view.viewport().width() - 2 * margin - 4)
        lines: list[str] = []
        row: list[str] = []
        for byte in data:
            token = f"{byte:02X}"
            candidate = "  ".join((*row, token))
            if row and metrics.horizontalAdvance(candidate) > usable_width:
                lines.append("  ".join(row))
                row = [token]
            else:
                row.append(token)
        if row:
            lines.append("  ".join(row))
        return "\n".join(lines)

    @staticmethod
    def _insert_status_text(cursor: QTextCursor, text: str,
                            default_format: QTextCharFormat) -> None:
        for line in text.splitlines(keepends=True):
            color = status_color_for_line(line)
            if color is None:
                cursor.insertText(line, default_format)
                continue
            line_format = QTextCharFormat(default_format)
            line_format.setForeground(QColor(color))
            cursor.insertText(line, line_format)

    def _append_log_entry(self, kind: str, text: str,
                          hex_data: bytes | None, stamp: str | None = None) -> None:
        stamp = stamp or datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log_entries.append((stamp, kind, text, bytes(hex_data) if hex_data is not None else None))
        if len(self._log_entries) > 5000:
            del self._log_entries[:500]
            self._rerender_log()
            return

        bar = self.log_view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 2
        self._render_log_entry(stamp, kind, text, hex_data)
        if at_bottom:
            self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def _render_log_entry(self, stamp: str, kind: str, text: str,
                          hex_data: bytes | None) -> None:
        data_color = {
            "RX": LOG_RX_COLOR,
            "TX": LOG_TX_COLOR,
            "ERROR": LOG_ERROR_COLOR,
            "WARN": COLORS["warning"],
            "INFO": LOG_INFO_COLOR,
        }.get(kind, COLORS["text"])
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        header_format = QTextCharFormat()
        data_format = QTextCharFormat()
        header_format.setForeground(QColor(LOG_HEADER_COLOR))
        data_format.setForeground(QColor(data_color))
        cursor.insertText(f"[{stamp}] {kind}\n", header_format)
        if hex_data is not None:
            cursor.insertText(f"{self._format_hex_frame(hex_data)}\n\n", data_format)
        else:
            self._insert_status_text(cursor, f"{text}\n\n", data_format)
        self.log_view.setTextCursor(cursor)

    def _rerender_log(self) -> None:
        if not self._log_entries:
            return
        bar = self.log_view.verticalScrollBar()
        old_value = bar.value()
        was_at_bottom = bar.value() >= bar.maximum() - 2
        entries = list(self._log_entries)
        self.log_view.clear()
        for stamp, kind, text, hex_data in entries:
            self._render_log_entry(stamp, kind, text, hex_data)
        bar.setValue(bar.maximum() if was_at_bottom else min(old_value, bar.maximum()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._log_entries:
            QTimer.singleShot(0, self._rerender_log)

    def clear_log(self) -> None:
        self._log_entries.clear()
        self.log_view.clear()

    def reset_counts(self) -> None:
        self.rx_count = self.tx_count = self.frame_count = 0
        self.counts_changed.emit(0, 0, 0)

    def save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "保存串口日志", "serial_console_log.txt", "Text (*.txt)")
        if not path:
            return
        lines = [f"{APP_NAME} 串口日志", f"导出时间: {datetime.now():%Y-%m-%d %H:%M:%S}", ""]
        for stamp, kind, text, hex_data in self._log_entries:
            payload = format_hex(hex_data) if hex_data is not None else text
            lines.extend((f"[{stamp}] {kind}", payload, ""))
        try:
            Path(path).write_text("\n".join(lines), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    def shutdown(self) -> None:
        self._stop_periodic()
        self.worker.shutdown()


class SerialConsoleWindow(FramelessWindow):
    def __init__(self, *, group: WindowGroup, parent=None) -> None:
        super().__init__("串口子窗口", group=group)
        self.resize(752, 796)
        self.setMinimumSize(640, 580)
        box = QGroupBox("串口调试与日志")
        layout = QVBoxLayout(box)
        self.panel = SerialConsolePanel(allow_child=False)
        layout.addWidget(self.panel)
        self.content_layout.addWidget(box)

    def closeEvent(self, event) -> None:
        self.panel.shutdown()
        super().closeEvent(event)
