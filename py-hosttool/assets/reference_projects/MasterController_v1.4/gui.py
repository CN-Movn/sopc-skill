from __future__ import annotations

import sys
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QLineF, QRectF, QTimer, Qt
from PySide6.QtGui import (
    QColor, QCursor, QPainter, QPainterPath, QPen, QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPlainTextEdit,
    QPushButton, QRadioButton, QScrollBar, QSpinBox, QSplitter, QTextEdit,
    QToolButton, QVBoxLayout, QWidget,
)
from serial.tools import list_ports

from arq_register_map import (
    ARQ_IP_ALL, ARQ_OP_DUMP_ALL, ARQ_OP_READ_REG, ARQ_OP_WRITE_REG,
    find_register, registers_for_operation,
)
from protocol import (
    MASTER_HEADER, analyze_command, analyze_report, bytes_to_hex,
    create_inner_command_frame, extract_protocol_frames, extract_report_frames,
    hex_to_bytes, parse_hex_byte, wrap_master_frame,
)
from serial_worker import SerialSettings, SerialWorker


STATUS_TAG_COLORS = {
    "[OK]": "#188038",
    "[WARN]": "#e37400",
    "[ERROR]": "#d93025",
    "[INFO]": "#5f6368",
}


def status_color_for_line(line: str) -> str | None:
    """Return the color for a Vitis health tag without changing its text."""
    # If a diagnostic line contains more than one tag, show its most severe
    # state instead of depending on tag order.
    for tag in ("[ERROR]", "[WARN]", "[OK]", "[INFO]"):
        if tag in line:
            return STATUS_TAG_COLORS[tag]
    return None


class WindowControlButton(QToolButton):
    """Uniform vector title-bar icon independent of the system font."""

    def __init__(self, control: str, tooltip: str, parent=None):
        super().__init__(parent)
        self.control = control
        self.setToolTip(tooltip)
        self.setFixedSize(46, 35)
        self.setAutoRaise(True)

    def paintEvent(self, event) -> None:
        # Let the stylesheet draw hover/checked/close backgrounds first.
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        color = QColor("#202124")
        if self.control == "pin" and self.isChecked():
            color = QColor("#0b57d0")
        if self.control == "close" and self.underMouse():
            color = QColor("#ffffff")
        painter.setPen(QPen(color, 1.55, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        cx = self.width() / 2.0
        cy = self.height() / 2.0
        if self.control == "minimize":
            painter.drawLine(QLineF(cx - 5, cy + 1, cx + 5, cy + 1))
        elif self.control == "maximize":
            painter.drawRect(QRectF(cx - 5, cy - 5, 10, 10))
        elif self.control == "restore":
            painter.drawRect(QRectF(cx - 3, cy - 5, 8, 8))
            painter.drawLine(QLineF(cx - 5, cy - 2.5,
                                    cx - 3, cy - 2.5))
            painter.drawLine(QLineF(cx - 5, cy - 2.5,
                                    cx - 5, cy + 5))
            painter.drawLine(QLineF(cx - 5, cy + 5,
                                    cx + 2.5, cy + 5))
        elif self.control == "close":
            painter.drawLine(QLineF(cx - 4.5, cy - 4.5,
                                    cx + 4.5, cy + 4.5))
            painter.drawLine(QLineF(cx + 4.5, cy - 4.5,
                                    cx - 4.5, cy + 4.5))
        elif self.control == "pin":
            path = QPainterPath()
            path.moveTo(cx - 4.5, cy - 5)
            path.lineTo(cx + 4.5, cy - 5)
            path.moveTo(cx - 2.5, cy - 5)
            path.lineTo(cx - 2.5, cy - 1.5)
            path.moveTo(cx + 2.5, cy - 5)
            path.lineTo(cx + 2.5, cy - 1.5)
            path.moveTo(cx - 4, cy - 1.5)
            path.lineTo(cx + 4, cy - 1.5)
            path.moveTo(cx - 2.5, cy - 1.5)
            path.lineTo(cx, cy + 2)
            path.lineTo(cx + 2.5, cy - 1.5)
            path.moveTo(cx, cy + 2)
            path.lineTo(cx, cy + 6)
            painter.drawPath(path)

    def set_control(self, control: str) -> None:
        if self.control != control:
            self.control = control
            self.update()


class WindowTitleBar(QWidget):
    """Compact title bar with a group-synchronized always-on-top button."""

    def __init__(self, window: "ConsoleWindow"):
        super().__init__(window)
        self._window = window
        self.setFixedHeight(36)
        self.setObjectName("windowTitleBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(0)

        self.icon_label = QLabel("▣")
        self.icon_label.setFixedWidth(26)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label = QLabel(window.windowTitle())
        layout.addWidget(self.icon_label)
        layout.addWidget(self.title_label)
        layout.addStretch(1)

        self.pin_button = self._button("pin", "置顶所有串口窗口")
        self.pin_button.setCheckable(True)
        self.pin_button.toggled.connect(window._set_pin_group)
        self.minimize_button = self._button("minimize", "最小化")
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button = self._button("maximize", "最大化")
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button = self._button("close", "关闭")
        self.close_button.setObjectName("closeButton")
        self.close_button.clicked.connect(window.close)

        layout.addWidget(self.pin_button)
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)

        self.set_maximized_style(False)

    def set_maximized_style(self, maximized: bool) -> None:
        top_radius = 0 if maximized else 4
        self.setStyleSheet(f"""
            QWidget#windowTitleBar {{
                background: #f3f6f9;
                border-bottom: 1px solid #d7dce2;
                border-top-left-radius: {top_radius}px;
                border-top-right-radius: {top_radius}px;
            }}
            QToolButton {{
                border: none;
                background: transparent;
                color: #202124;
            }}
            QToolButton:hover {{ background: #e3e7eb; }}
            QToolButton:checked {{ background: #d7e8ff; color: #0b57d0; }}
            QToolButton#closeButton {{
                border-top-right-radius: {top_radius}px;
            }}
            QToolButton#closeButton:hover {{
                background: #e81123;
                color: white;
            }}
        """)

    def _button(self, control: str, tooltip: str) -> WindowControlButton:
        return WindowControlButton(control, tooltip, self)

    def set_pin_state(self, enabled: bool) -> None:
        self.pin_button.blockSignals(True)
        self.pin_button.setChecked(enabled)
        self.pin_button.setToolTip(
            "取消所有串口窗口置顶" if enabled else "置顶所有串口窗口"
        )
        self.pin_button.blockSignals(False)

    def toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.update_maximize_icon()

    def update_maximize_icon(self) -> None:
        maximized = self._window.isMaximized()
        self.maximize_button.set_control(
            "restore" if maximized else "maximize"
        )
        self.maximize_button.setToolTip("还原" if maximized else "最大化")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None:
                handle.startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class PortComboBox(QComboBox):
    """Refresh the port list immediately before the user opens it."""

    def __init__(self, refresh_callback, parent=None):
        super().__init__(parent)
        self._refresh_callback = refresh_callback

    def showPopup(self) -> None:
        self._refresh_callback()
        super().showPopup()


class ConsoleWindow(QMainWindow):
    """Serial console, optionally combined with command/report protocol tools."""

    def __init__(self, title: str = "串口子窗口", parent=None, protocol: bool = False):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle(title)
        # Keep the tool compact by default; all major regions still resize
        # through Qt layouts when the user enlarges the window.
        # The protocol tools retain their prior width; only the serial side
        # was compacted, so reduce the main window instead of stretching left.
        self.resize(1330 if protocol else 752, 796)
        self.setMinimumSize(1080 if protocol else 640, 580)
        self.protocol = protocol
        self.child_windows: list[ConsoleWindow] = []
        if isinstance(parent, ConsoleWindow):
            self._pin_group_owner = parent._pin_group_owner
            self._always_on_top = self._pin_group_owner._always_on_top
        else:
            self._pin_group_owner = self
            self._always_on_top = False
            self._pin_members: list[ConsoleWindow] = []
        self._pin_group_owner._pin_members.append(self)
        self.worker = SerialWorker(self)
        self.rx_report_buffer = b""
        self.rx_log_buffer = b""
        self.last_report = b""
        self.last_mode = "direct"
        self.rx_count = self.tx_count = self.frame_count = 0
        self._log_entries: list[tuple[str, str, bytes | None, str]] = []
        self._report_result_initialized = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._periodic_send)
        self.worker.received.connect(self._received)
        self.worker.sent.connect(self._sent)
        self.worker.opened.connect(lambda port: self._connected(True, f"串口 {port} 已打开"))
        self.worker.closed.connect(lambda: self._connected(False, "串口已关闭"))
        self.worker.error.connect(self._serial_error)
        self._build()
        self.title_bar.set_pin_state(self._always_on_top)
        if self._always_on_top:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.worker.start()
        self.refresh_ports()
        self.log("INFO", "工具已启动，等待操作。")

    def _set_pin_group(self, enabled: bool) -> None:
        """Apply one pin state to the main window and every serial child."""
        owner = self._pin_group_owner
        owner._always_on_top = bool(enabled)
        members = list(owner._pin_members)
        active_window = QApplication.activeWindow()
        for member in members:
            member._apply_always_on_top(owner._always_on_top)
        if active_window in members and active_window is not None:
            active_window.raise_()
            active_window.activateWindow()

    def _apply_always_on_top(self, enabled: bool) -> None:
        visible = self.isVisible()
        state = self.windowState()
        self._always_on_top = enabled
        self.title_bar.set_pin_state(enabled)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if visible:
            # Changing a top-level window flag recreates the native window.
            # Restore visibility and its minimized/maximized state afterwards.
            self.show()
            self.setWindowState(state)

    def _build(self) -> None:
        root = QWidget(self)
        root.setObjectName("windowFrame")
        self.window_frame = root
        self.setCentralWidget(root)
        outer_layout = QVBoxLayout(root)
        outer_layout.setContentsMargins(1, 1, 1, 1)
        outer_layout.setSpacing(0)
        self.title_bar = WindowTitleBar(self)
        outer_layout.addWidget(self.title_bar)

        content = QWidget(root)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 10, 12, 8)
        outer_layout.addWidget(content, 1)

        if self.protocol:
            main_splitter = QSplitter(Qt.Orientation.Horizontal)
            left, right = QWidget(), QWidget()
            left_layout, right_layout = QVBoxLayout(left), QVBoxLayout(right)
            left_layout.setContentsMargins(0, 0, 0, 0)
            right_layout.setContentsMargins(0, 0, 0, 0)
            self._command_panel(left_layout)
            self._report_panel(left_layout)
            self._serial_panel(right_layout)
            # Preserve the established command/report panel width. Extra
            # horizontal room from user resizing belongs to the serial side.
            left.setFixedWidth(535)
            main_splitter.addWidget(left)
            main_splitter.addWidget(right)
            main_splitter.setSizes([535, 770])
            main_splitter.setChildrenCollapsible(False)
            layout.addWidget(main_splitter, 1)
        else:
            self._serial_panel(layout)

        self._build_status_bar(layout)
        self._update_window_frame_style()

    def _update_window_frame_style(self) -> None:
        maximized = self.isMaximized()
        radius = 0 if maximized else 5
        self.window_frame.setStyleSheet(f"""
            QWidget#windowFrame {{
                background: #f5f5f5;
                border: 1px solid #85898f;
                border-radius: {radius}px;
            }}
        """)
        self.title_bar.set_maximized_style(maximized)

    def _command_panel(self, layout: QVBoxLayout) -> None:
        box = QGroupBox("指令帧生成与分析")
        layout.addWidget(box, 3)
        panel = QVBoxLayout(box)
        panel.setContentsMargins(7, 7, 7, 6)
        panel.setSpacing(5)
        params = QGridLayout()
        params.setHorizontalSpacing(8)
        params.setVerticalSpacing(5)
        params.setColumnMinimumWidth(0, 76)
        params.setColumnMinimumWidth(2, 76)
        params.setColumnStretch(1, 1)
        params.setColumnStretch(3, 1)
        self.code = QComboBox()
        self.code.addItems(["90 - 工作模式", "91 - 遥测开关", "92 - 唤醒帧开关", "93 - 目标 MAC 设置", "94 - ARQ 寄存器调试"])
        self.code.currentIndexChanged.connect(self._command_mode)
        self.mac, self.msg, self.device = QLineEdit("00:25:1F:D2:00:00"), QLineEdit("00"), QLineEdit("01")
        self.work = QComboBox(); self.work.addItems(["00 - 外部通信", "01 - 内部自检"])
        self.master = QCheckBox("主控模式 (EB91 外层)")
        self.extra = QComboBox(); self.extra.addItems(["E5 - 开启", "EA - 关闭"])
        self.arq_target = QComboBox(); self.arq_target.addItems([
            "01 - TX Wrapper", "02 - RX Wrapper", "03 - TX Scheduler",
            "04 - RX Scheduler", "FF - ALL",
        ])
        self.arq_operation = QComboBox(); self.arq_operation.addItems([
            "01 - WRITE_REG", "02 - READ_REG", "03 - DUMP_ALL",
        ])
        self.arq_operation.setCurrentIndex(1)
        self.arq_offset = QComboBox()
        self.arq_offset.setEditable(False)
        self.arq_offset.setMaxVisibleItems(20)
        self.arq_offset.view().setMinimumWidth(420)
        self.arq_value = QLineEdit("00000000")
        self.arq_operation.currentIndexChanged.connect(self._arq_mode)
        self.arq_target.currentIndexChanged.connect(self._arq_target_changed)
        for field in (self.code, self.mac, self.msg, self.device, self.work,
                      self.extra, self.arq_target, self.arq_operation,
                      self.arq_offset, self.arq_value):
            field.setMaximumWidth(185)
        self.msg_label = QLabel("消息 ID")
        rows = (((QLabel("指令码"), self.code),
                 (QLabel("目标 MAC"), self.mac)),
                ((self.msg_label, self.msg),
                 (QLabel("设备 ID"), self.device)),
                ((QLabel("工作模式"), self.work),
                 (QLabel("开关"), self.extra)),
                ((QLabel("目标 IP"), self.arq_target),
                 (QLabel("操作"), self.arq_operation)),
                ((QLabel("寄存器偏移"), self.arq_offset),
                 (QLabel("寄存器值"), self.arq_value)),
                ((QLabel(""), QWidget()),
                 (QLabel(""), self.master)))
        for row, row_fields in enumerate(rows):
            for pair, (label, widget) in enumerate(row_fields):
                column = pair * 2
                params.addWidget(label, row, column)
                params.addWidget(widget, row, column + 1)
        panel.addLayout(params)
        self.command_text = QPlainTextEdit()
        self.command_text.setPlaceholderText("生成的 HEX 指令帧")
        self.command_text.setFixedHeight(58)
        self.command_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        panel.addWidget(self.command_text)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        for text, callback in (("生成", self.generate), ("发送到串口", self.send_command), ("分析", self.analyze_command), ("复制", lambda: self._clipboard(self.command_text.toPlainText())), ("清空", self.command_text.clear)):
            button = QPushButton(text); button.clicked.connect(callback); buttons.addWidget(button, 1)
        panel.addLayout(buttons)
        self._command_mode()

    def _report_panel(self, layout: QVBoxLayout) -> None:
        box = QGroupBox("上报帧分析（134 字节）")
        layout.addWidget(box, 4)
        report_layout = QVBoxLayout(box)
        report_layout.setContentsMargins(7, 7, 7, 6)
        report_layout.setSpacing(4)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.report_text = QPlainTextEdit()
        self.report_text.setPlaceholderText("最新上报帧（HEX）")
        self.report_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.report_result = QPlainTextEdit()
        self.report_result.setReadOnly(True)
        self.report_result.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        splitter.addWidget(self.report_text)
        splitter.addWidget(self.report_result)
        # The top half now has enough height for a complete 134-byte HEX frame.
        splitter.setSizes([200, 340])
        splitter.setChildrenCollapsible(False)
        report_layout.addWidget(splitter, 1)
        buttons = QHBoxLayout()
        for text, callback in (("从串口提取", self.extract_latest), ("分析", self.analyze_report), ("复制结果", lambda: self._clipboard(self.report_result.toPlainText())), ("清空", self._clear_report)):
            button = QPushButton(text); button.clicked.connect(callback); buttons.addWidget(button)
        report_layout.addLayout(buttons)

    def _serial_panel(self, layout: QVBoxLayout) -> None:
        box = QGroupBox("串口调试与日志")
        layout.addWidget(box, 1)
        outer = QHBoxLayout(box)
        outer.setSpacing(10)
        controls = QWidget()
        # This matches the label-plus-field width used by the command panel.
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
        # HEX rows are explicitly laid out below; Qt must not wrap one of
        # those rows a second time and leave stray bytes on their own line.
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
        for column, (text, callback) in enumerate((("发送", self.send_input), ("清空发送区", self.send_text.clear), ("清空日志", self._clear_log), ("保存日志", self.save_log))):
            button = QPushButton(text); button.clicked.connect(callback); actions.addWidget(button, 0, column)
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
        self.baud = QComboBox(); self.baud.addItems([str(x) for x in (9600, 19200, 38400, 57600, 115200, 230400, 460800, 500000, 921600, 1000000, 1500000, 2000000, 3000000)]); self.baud.setCurrentText("115200")
        self.databits = QComboBox(); self.databits.addItems(["8", "7", "6", "5"])
        self.stopbits = QComboBox(); self.stopbits.addItems(["1", "1.5", "2"])
        self.parity = QComboBox(); self.parity.addItems(["NONE", "ODD", "EVEN"])
        self.flow = QCheckBox("启用硬件流控")
        for label, widget in (("串口", self.port), ("波特率", self.baud), ("数据位", self.databits), ("停止位", self.stopbits), ("校验位", self.parity)):
            widget.setMaximumWidth(155)
            form.addRow(label, widget)
        form.addRow(self.flow)
        open_area = QWidget()
        open_layout = QHBoxLayout(open_area)
        open_layout.setContentsMargins(0, 2, 0, 0)
        self.connection_lamp = QLabel()
        self.connection_lamp.setFixedSize(30, 30)
        self.connection_lamp.setToolTip("串口已关闭")
        self._set_connection_lamp(False)
        button_stack = QVBoxLayout()
        button_stack.setContentsMargins(0, 0, 0, 0)
        button_stack.setSpacing(3)
        self.open_btn, child = QPushButton("打开串口"), QPushButton("打开新串口")
        self.new_serial_btn = child
        self.open_btn.clicked.connect(self.toggle)
        child.clicked.connect(self.open_child)
        button_stack.addWidget(self.open_btn)
        button_stack.addWidget(child)
        open_layout.addWidget(self.connection_lamp, 0, Qt.AlignmentFlag.AlignVCenter)
        open_layout.addLayout(button_stack, 1)
        form.addRow(open_area)
        layout.addWidget(settings)

        receive = QGroupBox("接收设置")
        receive_layout = QVBoxLayout(receive)
        receive_layout.setContentsMargins(7, 5, 7, 5)
        receive_layout.setSpacing(2)
        self.rx_ascii, self.rx_hex = QRadioButton("ASCII"), QRadioButton("HEX")
        self.rx_hex.setChecked(True)
        receive_modes = QHBoxLayout()
        receive_modes.addWidget(self.rx_ascii); receive_modes.addWidget(self.rx_hex); receive_modes.addStretch(1)
        receive_layout.addLayout(receive_modes)
        layout.addWidget(receive)

        transmit = QGroupBox("发送设置")
        transmit_layout = QVBoxLayout(transmit)
        transmit_layout.setContentsMargins(7, 5, 7, 5)
        transmit_layout.setSpacing(2)
        self.tx_ascii, self.tx_hex = QRadioButton("ASCII"), QRadioButton("HEX")
        self.tx_hex.setChecked(True)
        transmit_modes = QHBoxLayout()
        transmit_modes.addWidget(self.tx_ascii); transmit_modes.addWidget(self.tx_hex); transmit_modes.addStretch(1)
        self.periodic = QCheckBox("周期发送")
        self.period_ms = QSpinBox(); self.period_ms.setRange(1, 3600000); self.period_ms.setValue(1000); self.period_ms.setMaximumWidth(90)
        periodic_row = QHBoxLayout()
        periodic_row.addWidget(self.periodic)
        periodic_row.addWidget(self.period_ms)
        periodic_row.addWidget(QLabel("ms"))
        periodic_row.addStretch(1)
        transmit_layout.addLayout(transmit_modes)
        transmit_layout.addLayout(periodic_row)
        self.periodic.toggled.connect(self._periodic_toggled)
        layout.addWidget(transmit)
        layout.addStretch(1)

    def _build_status_bar(self, layout: QVBoxLayout) -> None:
        bar = QGridLayout()
        self.version_label, self.rx_label, self.tx_label, self.frame_label = QLabel("MasterController v1.4"), QLabel("RX: 0"), QLabel("TX: 0"), QLabel("帧数: 0")
        self.status_label = QLabel("状态: 就绪")
        reset = QPushButton("复位计数"); reset.clicked.connect(self._reset)
        for index, widget in enumerate((self.version_label, self.rx_label, self.tx_label, self.frame_label)):
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bar.addWidget(widget, 0, index)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar.addWidget(self.status_label, 0, 4)
        bar.addWidget(reset, 0, 5)
        for column in range(4): bar.setColumnStretch(column, 1)
        bar.setColumnStretch(4, 3)
        layout.addLayout(bar)

    def _command_mode(self) -> None:
        code = self.code.currentText()[:2]
        self.mac.setEnabled(code == "93"); self.work.setEnabled(code == "90")
        self.extra.setEnabled(code in ("91", "92"))
        is_arq = code == "94"
        # MsgID is a common field shared by the legacy commands and 0x94.
        # The current ARQ protocol accepts only the common MSG_ID value 0x00.
        if is_arq:
            self.msg.setText("00")
        self.msg.setReadOnly(is_arq)
        for widget in (self.arq_target, self.arq_operation,
                       self.arq_offset, self.arq_value):
            widget.setEnabled(is_arq)
        self._arq_mode()

    def _arq_target_changed(self) -> None:
        if self.arq_target.currentText().startswith("FF"):
            self.arq_operation.setCurrentIndex(2)
        self._refresh_arq_registers()

    def _refresh_arq_registers(self) -> None:
        """Rebuild the offset choices from the Vitis register whitelist."""
        self.arq_offset.clear()
        if not self.code.currentText().startswith("94"):
            self.arq_offset.addItem("仅ARQ寄存器调试使用", None)
            self.arq_offset.setEnabled(False)
            return

        target = self._byte(self.arq_target.currentText(), "目标 IP")
        operation = self._byte(self.arq_operation.currentText(), "操作")
        if operation == ARQ_OP_DUMP_ALL or target == ARQ_IP_ALL:
            self.arq_offset.addItem("DUMP_ALL无需选择寄存器", 0)
            self.arq_offset.setEnabled(False)
            return

        registers = registers_for_operation(target, operation)
        if not registers:
            access = "可写" if operation == ARQ_OP_WRITE_REG else "可读"
            self.arq_offset.addItem(f"当前目标没有{access}寄存器", None)
            self.arq_offset.setEnabled(False)
            return

        for register in registers:
            self.arq_offset.addItem(register.display_text, register.offset)
        self.arq_offset.setEnabled(True)

    def _arq_mode(self) -> None:
        is_arq = self.code.currentText().startswith("94")
        operation = self.arq_operation.currentText()[:2]
        if is_arq and operation != "03" and self.arq_target.currentText().startswith("FF"):
            self.arq_target.setCurrentIndex(0)
        is_write = operation == "01"
        self.arq_value.setEnabled(is_arq and is_write)
        if is_arq and not is_write:
            self.arq_value.setText("00000000")
        if is_arq and operation == "03":
            self.arq_value.setText("00000000")
        self._refresh_arq_registers()

    @staticmethod
    def _hex_value(text: str, bits: int, name: str) -> int:
        clean = text.strip().upper().removeprefix("0X")
        try:
            value = int(clean, 16)
        except ValueError as exc:
            raise ValueError(f"{name}不是合法的十六进制值：{text}") from exc
        if not 0 <= value < (1 << bits):
            raise ValueError(f"{name}超出{bits}位范围：{text}")
        return value

    def open_child(self) -> None:
        child = ConsoleWindow(parent=self)
        child.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.child_windows.append(child)
        child.destroyed.connect(lambda: self.child_windows.remove(child) if child in self.child_windows else None)
        child.show()

    @staticmethod
    def _byte(text: str, name: str) -> int: return parse_hex_byte(text[:2], name)

    def generate(self) -> None:
        try:
            code = self._byte(self.code.currentText(), "指令码")
            msg = self._byte(self.msg.text(), "消息 ID")
            if code in (0x91, 0x92): msg = self._byte(self.extra.currentText(), "开关")
            arq_args = {}
            if code == 0x94:
                operation = self._byte(self.arq_operation.currentText(), "操作")
                offset = 0
                if operation != ARQ_OP_DUMP_ALL:
                    selected_offset = self.arq_offset.currentData()
                    if selected_offset is None:
                        access = "可写" if operation == ARQ_OP_WRITE_REG else "可读"
                        raise ValueError(f"当前目标没有{access}寄存器，无法生成指令")
                    offset = int(selected_offset)
                if operation == ARQ_OP_WRITE_REG:
                    target = self._byte(self.arq_target.currentText(), "目标 IP")
                    value = self._hex_value(self.arq_value.text(), 32, "寄存器值")
                    register = find_register(target, offset)
                    if register is None:
                        raise ValueError("所选寄存器未列入当前目标的白名单")
                    invalid_bits = value & ((~register.write_mask) & 0xFFFFFFFF)
                    if invalid_bits:
                        raise ValueError(
                            f"{register.name}写入值0x{value:08X}包含保留位；"
                            f"合法写掩码为0x{register.write_mask:08X}，"
                            f"非法置位为0x{invalid_bits:08X}"
                        )
                arq_args = {
                    "arq_target": self._byte(self.arq_target.currentText(), "目标 IP"),
                    "arq_operation": operation,
                    "arq_offset": offset,
                    "arq_value": self._hex_value(self.arq_value.text(), 32, "寄存器值") if operation == ARQ_OP_WRITE_REG else 0,
                }
            frame = create_inner_command_frame(
                code, msg, self._byte(self.device.text(), "设备 ID"),
                self._byte(self.work.currentText(), "工作模式"), self.mac.text(),
                **arq_args,
            )
            self.command_text.setPlainText(bytes_to_hex(wrap_master_frame(0x90, frame) if self.master.isChecked() else frame))
            self.status("指令帧已生成")
        except ValueError as exc: self._error(str(exc))

    def analyze_command(self) -> None:
        try:
            text = self.command_text.toPlainText(); self.log("INFO", analyze_command(hex_to_bytes(text), "master" if text.replace(" ", "").upper().startswith("EB91") else "direct"))
        except ValueError as exc: self._error(f"分析指令帧失败：{exc}")

    def send_command(self) -> None:
        try:
            data = hex_to_bytes(self.command_text.toPlainText())
            # Copy first so the generated frame remains available even if the
            # serial worker subsequently reports that the port is closed.
            self.send_text.setPlainText(bytes_to_hex(data))
            self.send_bytes(data)
        except ValueError as exc: self._error(str(exc))

    def analyze_report(self) -> None:
        try:
            data = hex_to_bytes(self.report_text.toPlainText())
            self._set_report_result(analyze_report(data, "master" if data.startswith(MASTER_HEADER) else "direct"))
        except ValueError as exc: self._error(f"分析上报帧失败：{exc}")

    def extract_latest(self) -> None:
        if self.last_report: self.report_text.setPlainText(bytes_to_hex(self.last_report))
        else: self._error("接收缓存中尚未识别到完整上报帧。")

    def _clear_report(self) -> None:
        self.report_text.clear()
        self.report_result.clear()
        self._report_result_initialized = False
    def _clipboard(self, text: str) -> None: QApplication.clipboard().setText(text)

    def refresh_ports(self) -> None:
        ports = sorted((port.device for port in list_ports.comports()), key=self._port_sort_key)
        self.port.clear()
        if ports:
            self.port.addItems(ports)
            self.port.setCurrentIndex(0)
            self.status("串口列表已刷新")
        else:
            self.status("未检测到可用串口")

    @staticmethod
    def _port_sort_key(port: str) -> tuple[int, str]:
        upper = port.upper()
        if upper.startswith("COM") and upper[3:].isdigit():
            return int(upper[3:]), upper
        return 2**31 - 1, upper

    def toggle(self) -> None:
        if self.open_btn.text() == "关闭串口": self.worker.close_port(); return
        if not self.port.currentText():
            self._error("未检测到可用串口，请连接设备后刷新。")
            return
        parity = {"NONE": "N", "ODD": "O", "EVEN": "E"}[self.parity.currentText()]
        self.worker.open(SerialSettings(self.port.currentText(), int(self.baud.currentText()), int(self.databits.currentText()), float(self.stopbits.currentText()), parity, self.flow.isChecked()))

    def _connected(self, connected: bool, message: str) -> None:
        self.open_btn.setText("关闭串口" if connected else "打开串口")
        self._set_connection_lamp(connected)
        if not connected:
            self._stop_periodic()
        self.status(message); self.log("INFO", message)

    def _set_connection_lamp(self, connected: bool) -> None:
        color, text = ("#2e9b4c", "串口已打开") if connected else ("#c74343", "串口已关闭")
        self.connection_lamp.setStyleSheet(f"background-color: {color}; border: 1px solid #737373; border-radius: 15px;")
        self.connection_lamp.setToolTip(text)

    def send_input(self) -> None:
        try:
            if self.timer.isActive():
                self._stop_periodic()
                return
            data = self._build_send_payload()
            if self.periodic.isChecked():
                if self.open_btn.text() != "关闭串口":
                    self._error("串口未打开，请先打开串口。")
                    return
                self.send_bytes(data)  # The first periodic transmission is immediate.
                self._start_periodic()
            else:
                self.send_bytes(data)
        except ValueError as exc: self._error(str(exc))

    def _build_send_payload(self) -> bytes:
        return hex_to_bytes(self.send_text.toPlainText()) if self.tx_hex.isChecked() else self.send_text.toPlainText().encode("utf-8")

    def send_bytes(self, data: bytes) -> None:
        if not data: self._error("发送内容为空。"); return
        self.worker.send(data)

    def _sent(self, data: bytes) -> None:
        self.tx_count += len(data); self.update_counts()
        self.log("TX", data.decode("utf-8", errors="replace"), data if self.tx_hex.isChecked() else None)

    def _received(self, data: bytes) -> None:
        """Accumulate read chunks; log each completed protocol frame exactly once."""
        self.rx_count += len(data)
        self.rx_report_buffer += data
        # An incoming EB (or a prior partial frame) is protocol data.  Do not
        # log serial.read chunks until extraction has a complete wire frame.
        protocol_candidate = bool(self.rx_log_buffer) or b"\xEB" in data
        if protocol_candidate:
            self.rx_log_buffer += data
            frames, self.rx_log_buffer = extract_protocol_frames(self.rx_log_buffer)
            for frame in frames:
                self.log("RX", frame.decode("utf-8", errors="replace"), frame if self.rx_hex.isChecked() else None)
        else:
            # Preserve ordinary ASCII-console behavior for traffic that is not
            # part of this project's binary protocol.
            self.log("RX", data.decode("utf-8", errors="replace"), data if self.rx_hex.isChecked() else None)

        reports, self.rx_report_buffer = extract_report_frames(self.rx_report_buffer)
        for report in reports:
            self.last_report, self.last_mode = report.raw_bytes, report.mode
            self.frame_count += 1
            if self.protocol:
                self.report_text.setPlainText(bytes_to_hex(report.raw_bytes))
                self._set_report_result(analyze_report(report.analysis_bytes, report.mode))
        self.update_counts()

    def _set_report_result(self, text: str) -> None:
        """Keep the reader's position unless they were following the bottom."""
        bar = self.report_result.verticalScrollBar()
        if not self._report_result_initialized:
            self.report_result.setPlainText(text)
            bar.setValue(bar.minimum())
            self._report_result_initialized = True
            return
        old_value, was_at_bottom = bar.value(), bar.value() >= bar.maximum() - 2
        self.report_result.setPlainText(text)
        bar.setValue(bar.maximum() if was_at_bottom else min(old_value, bar.maximum()))

    def _format_hex_frame(self, data: bytes) -> str:
        """Fit each data line to the actual log viewport and current font."""
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

    def _periodic_send(self) -> None:
        try:
            self.send_bytes(self._build_send_payload())
        except ValueError as exc:
            self._error(f"周期发送失败：{exc}")

    def _periodic_toggled(self, enabled: bool) -> None:
        if not enabled and self.timer.isActive():
            self._stop_periodic()

    def _start_periodic(self) -> None:
        self.timer.start(self.period_ms.value())
        self.send_btn.setText("停止发送")
        self.status("周期发送已启动")
        self.log("INFO", f"周期发送已启动，周期 {self.period_ms.value()} ms。")

    def _stop_periodic(self) -> None:
        was_active = self.timer.isActive()
        self.timer.stop()
        self.send_btn.setText("发送")
        if was_active:
            self.status("周期发送已停止")
            self.log("INFO", "周期发送已停止。")

    def status(self, text: str) -> None: self.status_label.setText("状态: " + text)

    def update_counts(self) -> None:
        self.rx_label.setText(f"RX: {self.rx_count}")
        self.tx_label.setText(f"TX: {self.tx_count}")
        self.frame_label.setText(f"帧数: {self.frame_count}")

    def _reset(self) -> None: self.rx_count = self.tx_count = self.frame_count = 0; self.update_counts()

    def _error(self, text: str) -> None:
        self.status(text)
        self.log("ERROR", text)

    def _serial_error(self, text: str) -> None:
        self._stop_periodic()
        self._set_connection_lamp(False)
        self._error(text)

    def log(self, kind: str, text: str, hex_data: bytes | None = None) -> None:
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log_entries.append((kind, text, bytes(hex_data) if hex_data is not None else None, stamp))
        if len(self._log_entries) > 600:
            self._log_entries = self._log_entries[-600:]
        self._append_log_entry(kind, text, hex_data, stamp)

    def _append_log_entry(self, kind: str, text: str, hex_data: bytes | None, stamp: str) -> None:
        bar = self.log_view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 2
        data_color = {"RX": "#237a32", "TX": "#1565c0", "ERROR": "#b71c1c", "INFO": "#5f6368"}.get(kind, "#333333")
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        header_format, data_format = QTextCharFormat(), QTextCharFormat()
        header_format.setForeground(QColor("#72777f"))
        data_format.setForeground(QColor(data_color))
        cursor.insertText(f"[{stamp}] {kind}\n", header_format)
        if hex_data is not None:
            cursor.insertText(f"{self._format_hex_frame(hex_data)}\n\n", data_format)
        else:
            self._insert_status_text(cursor, f"{text}\n\n", data_format)
        self.log_view.setTextCursor(cursor)
        if at_bottom: self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    @staticmethod
    def _insert_status_text(cursor: QTextCursor, text: str,
                            default_format: QTextCharFormat) -> None:
        """Color tagged plaintext line-by-line while preserving raw content."""
        for line in text.splitlines(keepends=True):
            color = status_color_for_line(line)
            if color is None:
                cursor.insertText(line, default_format)
                continue
            line_format = QTextCharFormat(default_format)
            line_format.setForeground(QColor(color))
            cursor.insertText(line, line_format)

    def _rerender_log(self) -> None:
        if not self._log_entries:
            return
        bar = self.log_view.verticalScrollBar()
        old_value, was_at_bottom = bar.value(), bar.value() >= bar.maximum() - 2
        self.log_view.clear()
        for kind, text, hex_data, stamp in self._log_entries:
            self._append_log_entry(kind, text, hex_data, stamp)
        bar.setValue(bar.maximum() if was_at_bottom else min(old_value, bar.maximum()))

    def _clear_log(self) -> None:
        self._log_entries.clear()
        self.log_view.clear()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._log_entries:
            QTimer.singleShot(0, self._rerender_log)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if (event.type() == QEvent.Type.WindowStateChange and
                hasattr(self, "title_bar")):
            self.title_bar.update_maximize_icon()
            self._update_window_frame_style()

    def nativeEvent(self, event_type, message):
        """Restore native edge resizing for the custom Windows title bar."""
        if sys.platform == "win32" and not self.isMaximized():
            try:
                msg = wintypes.MSG.from_address(int(message))
            except (AttributeError, TypeError, ValueError):
                pass
            else:
                if msg.message == 0x0084:  # WM_NCHITTEST
                    point = self.mapFromGlobal(QCursor.pos())
                    border = 7
                    left = point.x() < border
                    right = point.x() >= self.width() - border
                    top = point.y() < border
                    bottom = point.y() >= self.height() - border
                    if top and left:
                        return True, 13  # HTTOPLEFT
                    if top and right:
                        return True, 14  # HTTOPRIGHT
                    if bottom and left:
                        return True, 16  # HTBOTTOMLEFT
                    if bottom and right:
                        return True, 17  # HTBOTTOMRIGHT
                    if left:
                        return True, 10  # HTLEFT
                    if right:
                        return True, 11  # HTRIGHT
                    if top:
                        return True, 12  # HTTOP
                    if bottom:
                        return True, 15  # HTBOTTOM
        return super().nativeEvent(event_type, message)

    def save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "保存串口日志", "serial_console_log.txt", "Text (*.txt)")
        if path: Path(path).write_text(self.log_view.toPlainText(), encoding="utf-8")

    def closeEvent(self, event) -> None:
        owner = self._pin_group_owner
        if self in owner._pin_members:
            owner._pin_members.remove(self)
        self._stop_periodic(); self.worker.shutdown(); event.accept()


class MasterControllerWindow(ConsoleWindow):
    def __init__(self): super().__init__("MasterController v1.4", protocol=True)
