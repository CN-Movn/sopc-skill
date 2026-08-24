"""Protocol-workbench shell with the reusable serial console on the right."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from .config import APP_NAME, APP_VERSION, DEFAULT_LAYOUT
from .dashboard_window import DashboardWindow
from .serial_console import SerialConsolePanel, SerialConsoleWindow
from .window_chrome import FramelessWindow


class WorkbenchWindow(FramelessWindow):
    def __init__(self) -> None:
        super().__init__(f"{APP_NAME} {APP_VERSION}")
        self.resize(1330, 796)
        self.setMinimumSize(1080, 580)
        self.child_windows: list[SerialConsoleWindow] = []
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        left.setFixedWidth(535)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._business_panel(), 3)
        left_layout.addWidget(self._result_panel(), 4)

        serial_box = QGroupBox("串口调试与日志")
        serial_layout = QVBoxLayout(serial_box)
        serial_layout.setContentsMargins(7, 7, 7, 7)
        self.serial_panel = SerialConsolePanel()
        self.serial_panel.request_child.connect(self.open_child)
        self.serial_panel.status_changed.connect(self._set_status)
        self.serial_panel.counts_changed.connect(self._set_counts)
        serial_layout.addWidget(self.serial_panel)

        splitter.addWidget(left)
        splitter.addWidget(serial_box)
        splitter.setSizes([535, 770])
        self.content_layout.addWidget(splitter, 1)
        self._build_status_bar()

    def _business_panel(self) -> QGroupBox:
        box = QGroupBox("业务指令与参数")
        layout = QVBoxLayout(box)
        note = QLabel("在此实现项目协议字段、寄存器白名单和指令生成。不要沿用模板占位逻辑。")
        note.setWordWrap(True)
        note.setProperty("muted", True)
        self.command_text = QPlainTextEdit()
        self.command_text.setPlaceholderText("生成或输入业务指令")
        self.command_text.setFixedHeight(92)
        buttons = QHBoxLayout()
        for text in ("生成", "发送到串口", "分析", "复制", "清空"):
            button = QPushButton(text)
            if text == "清空":
                button.clicked.connect(self.command_text.clear)
            elif text == "发送到串口":
                button.clicked.connect(self._send_command)
            buttons.addWidget(button, 1)
        layout.addWidget(note)
        layout.addStretch(1)
        layout.addWidget(self.command_text)
        layout.addLayout(buttons)
        return box

    def _result_panel(self) -> QGroupBox:
        box = QGroupBox("上报/结果分析")
        layout = QVBoxLayout(box)
        self.report_text = QPlainTextEdit()
        self.report_text.setPlaceholderText("输入或从协议层提取上报帧")
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("解析结果")
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.report_text)
        splitter.addWidget(self.result_text)
        splitter.setSizes([200, 340])
        layout.addWidget(splitter)
        return box

    def _build_status_bar(self) -> None:
        bar = QGridLayout()
        self.version_label = QLabel(f"{APP_NAME} {APP_VERSION}")
        self.rx_label = QLabel("RX: 0")
        self.tx_label = QLabel("TX: 0")
        self.frame_label = QLabel("TX 次数: 0")
        self.status_label = QLabel("状态: 就绪")
        reset = QPushButton("复位计数")
        reset.clicked.connect(self.serial_panel.reset_counts)
        for index, widget in enumerate((self.version_label, self.rx_label,
                                        self.tx_label, self.frame_label)):
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bar.addWidget(widget, 0, index)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar.addWidget(self.status_label, 0, 4)
        bar.addWidget(reset, 0, 5)
        for column in range(4):
            bar.setColumnStretch(column, 1)
        bar.setColumnStretch(4, 3)
        self.content_layout.addLayout(bar)

    def _send_command(self) -> None:
        self.serial_panel.send_text.setPlainText(self.command_text.toPlainText())
        self.serial_panel.send_input()

    def _set_status(self, text: str) -> None:
        self.status_label.setText(f"状态: {text}")

    def _set_counts(self, rx: int, tx: int, tx_operations: int) -> None:
        self.rx_label.setText(f"RX: {rx}")
        self.tx_label.setText(f"TX: {tx}")
        self.frame_label.setText(f"TX 次数: {tx_operations}")

    def open_child(self) -> None:
        child = SerialConsoleWindow(group=self.window_group, parent=self)
        self.child_windows.append(child)
        child.destroyed.connect(lambda *_args, item=child: self._remove_child(item))
        child.show()

    def _remove_child(self, child: SerialConsoleWindow) -> None:
        if child in self.child_windows:
            self.child_windows.remove(child)

    def closeEvent(self, event) -> None:
        for child in list(self.child_windows):
            if not child.close():
                event.ignore()
                return
        if not self.serial_panel.shutdown():
            event.ignore()
            return
        super().closeEvent(event)


MainWindow = DashboardWindow if DEFAULT_LAYOUT == "dashboard" else WorkbenchWindow
