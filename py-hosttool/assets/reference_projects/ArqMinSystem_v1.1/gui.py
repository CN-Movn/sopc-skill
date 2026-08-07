"""Main window for the ARQ dual-node diagnostic instrument."""
from __future__ import annotations

import sys
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
import re
import struct

from PySide6.QtCore import QEvent, QLineF, QRectF, Qt
from PySide6.QtGui import (QCloseEvent, QColor, QCursor, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QMessageBox, QPushButton,
                               QPlainTextEdit, QSpinBox, QSplitter,
                               QStatusBar, QTabWidget, QToolButton,
                               QVBoxLayout, QWidget)
from serial.tools import list_ports

from client import McpClient
from performance import PerformanceModel
from performance_view import PerformancePage
from protocol import MessageType, Opcode, Status, Target
from registers import ALICE_INSTANCES, BOB_INSTANCES, INSTANCE_BY_TARGET
from serial_worker import SerialSettings, SerialWorker
from services import ControlService, DiagnosticService
from widgets import ProtocolStrip, RegisterCard
from workflows import WorkflowService


# AXI UARTLite is fixed at this baud rate in the current BSP.
UART_BAUDRATE = 115200


def spin(minimum: int, maximum: int, value: int) -> QSpinBox:
    box = QSpinBox()
    box.setRange(minimum, maximum)
    box.setValue(value)
    box.setMinimumWidth(108)
    return box


def integer(text: str) -> int:
    return int(text.strip(), 0)


class WindowControlButton(QToolButton):
    """不依赖系统字体的标题栏矢量按钮。"""

    def __init__(self, control: str, tooltip: str, parent=None) -> None:
        super().__init__(parent)
        self.control = control
        self.setToolTip(tooltip)
        self.setFixedSize(46, 35)
        self.setAutoRaise(True)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colour = QColor("#0b57d0" if self.control == "pin" and self.isChecked()
                        else "#202124")
        if self.control == "close" and self.underMouse():
            colour = QColor("#ffffff")
        painter.setPen(QPen(colour, 1.55, Qt.PenStyle.SolidLine,
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
            painter.drawLine(QLineF(cx - 5, cy - 2.5, cx - 3, cy - 2.5))
            painter.drawLine(QLineF(cx - 5, cy - 2.5, cx - 5, cy + 5))
            painter.drawLine(QLineF(cx - 5, cy + 5, cx + 2.5, cy + 5))
        elif self.control == "close":
            painter.drawLine(QLineF(cx - 4.5, cy - 4.5, cx + 4.5, cy + 4.5))
            painter.drawLine(QLineF(cx + 4.5, cy - 4.5, cx - 4.5, cy + 4.5))
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
    """带置顶、最小化、最大化和关闭按钮的自定义标题栏。"""

    def __init__(self, window: "HostToolWindow") -> None:
        super().__init__(window)
        self._window = window
        self.setFixedHeight(36)
        self.setObjectName("windowTitleBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(0)
        icon = QLabel("▣")
        icon.setFixedWidth(26)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label = QLabel(window.windowTitle())
        layout.addWidget(icon)
        layout.addWidget(self.title_label)
        layout.addStretch(1)
        self.pin_button = WindowControlButton("pin", "窗口置顶", self)
        self.pin_button.setCheckable(True)
        self.minimize_button = WindowControlButton("minimize", "最小化", self)
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button = WindowControlButton("maximize", "最大化", self)
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button = WindowControlButton("close", "关闭", self)
        self.close_button.setObjectName("closeButton")
        self.close_button.clicked.connect(window.close)
        layout.addWidget(self.pin_button)
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)
        self.set_maximized_style(False)

    def set_maximized_style(self, maximized: bool) -> None:
        radius = 0 if maximized else 4
        self.setStyleSheet(f"""
            QWidget#windowTitleBar {{
                background:#f3f6f9; border-bottom:1px solid #d7dce2;
                border-top-left-radius:{radius}px;
                border-top-right-radius:{radius}px;
            }}
            QToolButton {{ border:none; background:transparent; }}
            QToolButton:hover {{ background:#e3e7eb; }}
            QToolButton:checked {{ background:#d7e8ff; }}
            QToolButton#closeButton {{ border-top-right-radius:{radius}px; }}
            QToolButton#closeButton:hover {{ background:#e81123; }}
        """)

    def set_pin_state(self, enabled: bool) -> None:
        self.pin_button.blockSignals(True)
        self.pin_button.setChecked(enabled)
        self.pin_button.setToolTip("取消窗口置顶" if enabled else "窗口置顶")
        self.pin_button.blockSignals(False)

    def toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.update_maximize_icon()

    def update_maximize_icon(self) -> None:
        maximized = self._window.isMaximized()
        self.maximize_button.set_control("restore" if maximized else "maximize")
        self.maximize_button.setToolTip("还原" if maximized else "最大化")
        self.set_maximized_style(maximized)

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


class HostToolWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("ARQ系统诊断工具")
        self.resize(1680, 980)
        self.worker = SerialWorker(self)
        self.client = McpClient(self.worker, self)
        self.diagnostics = DiagnosticService(self.client, self)
        self.controls = ControlService(self.client, self)
        self.workflows = WorkflowService(self.client, self)
        self.performance = PerformanceModel()
        self.cards: dict[int, RegisterCard] = {}
        self._operation_entries: list[str] = []
        self._connected = False
        self._connection_generation = 0
        self._handshake_ok = False
        self._configuration_sync_pending = False
        self._frame_syncing = False
        self._always_on_top = False
        self._build_ui()
        self._wire()
        self.worker.start()
        self.refresh_ports()

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("windowFrame")
        self.window_frame = central
        outer = QVBoxLayout(central)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        self.title_bar = WindowTitleBar(self)
        self.pin_button = self.title_bar.pin_button
        content = QWidget()
        root = QHBoxLayout(content)
        root.setContentsMargins(10, 8, 10, 6)
        root.setSpacing(0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        left = self._left_panel()
        right = self._right_panel()
        left.setMinimumWidth(640)
        right.setMinimumWidth(800)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes((660, 1000))
        root.addWidget(splitter)
        outer.addWidget(self.title_bar)
        outer.addWidget(content, 1)
        status = QStatusBar()
        status.setSizeGripEnabled(False)
        self.app_status = status
        self.connection_state = QLabel("串口未连接")
        self.protocol_state = QLabel("协议离线")
        self.refresh_state = QLabel("刷新已停止")
        status.addWidget(QLabel("ARQ Diagnostic v1.1"), 1)
        status.addPermanentWidget(self.connection_state)
        status.addPermanentWidget(self.protocol_state)
        status.addPermanentWidget(self.refresh_state)
        outer.addWidget(status)
        self.setCentralWidget(central)
        self.setStyleSheet("""
            QMainWindow { background:transparent; }
            QStatusBar { background:transparent; border:none; }
            QGroupBox { font-weight:600; border:1px solid #cbd2d9; border-radius:6px;
                        margin-top:8px; background:white; }
            QGroupBox::title { subcontrol-origin:margin; left:9px; padding:0 4px; }
            QPushButton { min-height:25px; padding:2px 10px; }
            QLineEdit, QSpinBox, QComboBox { min-height:24px; }
            QGroupBox[severity="error"] { border:2px solid #dc3545; }
            QGroupBox[severity="notice"] { border:2px solid #e0a800; }
            QGroupBox[severity="stale"] { border:2px solid #98a1aa; }
            QTabWidget#controlTabs::pane, QTabWidget#diagnosticTabs::pane {
                border:1px solid #cbd2d9; border-radius:6px;
                background:#eef1f4; }
        """)
        self._update_window_frame_style()

    def _update_window_frame_style(self) -> None:
        maximized = self.isMaximized()
        radius = 0 if maximized else 6
        border = 0 if maximized else 1
        margin = 0 if maximized else 1
        self.window_frame.layout().setContentsMargins(margin, margin,
                                                       margin, margin)
        self.window_frame.setStyleSheet(f"""
            QWidget#windowFrame {{
                background:#f4f6f8;
                border:{border}px solid #85898f;
                border-radius:{radius}px;
            }}
        """)
        self.title_bar.set_maximized_style(maximized)

    def _left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        layout.addWidget(self._connection_group())
        self.control_tabs = QTabWidget()
        self.control_tabs.setObjectName("controlTabs")
        self.control_tabs.addTab(self._common_page(), "一键操作")
        self.control_tabs.addTab(self._control_page(self._global_group()),
                                 "全局配置")
        self.control_tabs.addTab(self._control_page(self._link_group()),
                                 "双向ARQ链路")
        self.control_tabs.addTab(self._control_page(self._channel_group()),
                                 "故障注入")
        self.control_tabs.addTab(self._control_page(self._source_group()),
                                 "测试源")
        layout.addWidget(self.control_tabs, 1)
        layout.addWidget(self._log_group())
        panel.setMinimumWidth(640)
        return panel

    @staticmethod
    def _control_page(content: QWidget) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(content)
        layout.addStretch()
        return body

    def _connection_group(self) -> QGroupBox:
        box = QGroupBox("连接与刷新")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self.port = QComboBox()
        self.connect_button = QPushButton("连接")
        self.port_refresh = QPushButton("刷新串口")
        self.refresh_button = QPushButton("开始刷新")
        self.period = spin(500, 10000, 1000)
        self.period.setSuffix(" ms / 节点")

        connection_buttons = QWidget()
        connection_layout = QHBoxLayout(connection_buttons)
        connection_layout.setContentsMargins(0, 0, 0, 0)
        connection_layout.setSpacing(8)
        connection_layout.addWidget(self.connect_button, 1)
        connection_layout.addWidget(self.port_refresh, 1)

        grid.addWidget(QLabel("串口"), 0, 0)
        grid.addWidget(self.port, 0, 1)
        grid.addWidget(connection_buttons, 0, 2)
        grid.addWidget(QLabel("刷新周期"), 1, 0)
        grid.addWidget(self.period, 1, 1)
        grid.addWidget(self.refresh_button, 1, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        return box

    def _global_group(self) -> QGroupBox:
        box = QGroupBox("全局配置")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self.payload_length = spin(8, 2024, 1752)
        self.physical_length = spin(32, 2048, 1776)
        self.payload_length.setSingleStep(8)
        self.physical_length.setSingleStep(8)
        self.physical_length.setReadOnly(True)
        self.physical_length.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.physical_length.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.payload_length.valueChanged.connect(self._sync_physical_length)
        apply_button = QPushButton("应用帧配置")
        clear_stats = QPushButton("清统计")
        clear_errors = QPushButton("清错误")
        soft_reset = QPushButton("Scheduler软复位")
        apply_button.clicked.connect(lambda: self.controls.set_frame(
            self.payload_length.value(), self.payload_length.value() + 24))
        clear_stats.clicked.connect(self.controls.clear_stats)
        clear_errors.clicked.connect(self.controls.clear_errors)
        soft_reset.clicked.connect(self._confirm_soft_reset)
        grid.addWidget(QLabel("有效载荷长度"),0,0); grid.addWidget(self.payload_length,0,1)
        grid.addWidget(QLabel("物理帧长度"),0,2); grid.addWidget(self.physical_length,0,3)
        grid.addWidget(apply_button,1,0,1,2)
        grid.addWidget(soft_reset,1,2,1,2)
        grid.addWidget(clear_stats,2,0,1,2)
        grid.addWidget(clear_errors,2,2,1,2)
        grid.setColumnStretch(1, 1); grid.setColumnStretch(3, 1)
        return box

    def _sync_physical_length(self, payload: int) -> None:
        if self._frame_syncing:
            return
        physical = payload + 24
        if 32 <= physical <= 2048:
            self._frame_syncing = True
            self.physical_length.setValue(physical)
            self._frame_syncing = False

    def _link_group(self) -> QGroupBox:
        box = QGroupBox("双向ARQ链路")
        layout = QHBoxLayout(box)
        self.link_controls = {}
        for title, target in (("A2B", Target.A2B_LINK), ("B2A", Target.B2A_LINK)):
            child = QGroupBox(title)
            form = QFormLayout(child)
            enable = QCheckBox("启用 ARQ"); timeout = spin(1, 0x7FFFFFFF, 100000)
            retry = spin(0, 65535, 3); sequence = QLineEdit("0x00000000")
            apply_button = QPushButton("应用")
            apply_button.clicked.connect(lambda _=False, t=target, e=enable,
                                                 to=timeout, r=retry, s=sequence:
                self._set_link(t, e, to, r, s))
            form.addRow(enable); form.addRow("超时周期", timeout)
            form.addRow("最大重试次数", retry); form.addRow("初始序列号", sequence)
            form.addRow(apply_button)
            self.link_controls[int(target)] = (enable, timeout, retry, sequence)
            layout.addWidget(child)
        return box

    def _channel_group(self) -> QGroupBox:
        box = QGroupBox("故障注入")
        layout = QHBoxLayout(box)
        self.channel_controls = {}
        for title, target in (("A2B", Target.A2B_CHANNEL), ("B2A", Target.B2A_CHANNEL)):
            child = QGroupBox(title)
            form = QFormLayout(child)
            enable = QCheckBox("启用"); bypass = QCheckBox("旁路"); bypass.setChecked(True)
            continuous = QCheckBox("连续注错"); force = QCheckBox("至少翻转1 bit")
            flags = QWidget(); flags_l = QHBoxLayout(flags); flags_l.setContentsMargins(0,0,0,0)
            flags_l.addWidget(enable); flags_l.addWidget(bypass); flags_l.addWidget(continuous); flags_l.addWidget(force)
            threshold = spin(0, 65535, 0); flips = spin(1, 8, 1); seed = QLineEdit("0x00000001")
            apply_button = QPushButton("应用"); arm = QPushButton("触发单次注错")
            buttons = QWidget(); buttons_l=QHBoxLayout(buttons); buttons_l.setContentsMargins(0,0,0,0)
            buttons_l.addWidget(apply_button); buttons_l.addWidget(arm)
            apply_button.clicked.connect(lambda _=False,t=target,items=(enable,bypass,continuous,force,threshold,flips,seed): self._set_channel(t,items))
            arm.clicked.connect(lambda _=False,t=target:self.controls.control("触发单次注错",t,2))
            form.addRow(flags); form.addRow("注错阈值",threshold); form.addRow("最大翻转位数",flips)
            form.addRow("随机种子",seed); form.addRow(buttons)
            self.channel_controls[int(target)] = (enable,bypass,continuous,force,threshold,flips,seed)
            layout.addWidget(child)
        return box

    def _source_group(self) -> QGroupBox:
        box = QGroupBox("测试源")
        layout = QHBoxLayout(box)
        self.source_controls = {}
        modes = ("0 停止", "1 满帧", "2 固定长度", "3 边界长度",
                 "4 随机短帧", "5 混合", "6 交替极值", "7 FULL_THEN_TAIL")
        for title, target in (("Alice",Target.ALICE_SOURCE),("Bob",Target.BOB_SOURCE)):
            child=QGroupBox(title); form=QFormLayout(child)
            mode=QComboBox(); mode.addItems(modes); length=spin(8,1752,1752)
            length.setSingleStep(8)
            gap=spin(0,0x7FFFFFFF,1000); limit=spin(0,0x7FFFFFFF,0)
            seed=QLineEdit("0x00000001"); mix=spin(1,0x7FFFFFFF,16)
            apply_button=QPushButton("应用"); start=QPushButton("启动"); stop=QPushButton("停止")
            buttons=QWidget(); bl=QHBoxLayout(buttons); bl.setContentsMargins(0,0,0,0)
            bl.addWidget(apply_button); bl.addWidget(start); bl.addWidget(stop)
            items=(mode,length,gap,limit,seed,mix)
            apply_button.clicked.connect(lambda _=False,t=target,it=items:self._set_source(t,it))
            start.clicked.connect(lambda _=False,t=target:self.controls.control("启动测试源",t,1))
            stop.clicked.connect(lambda _=False,t=target:self.controls.control("停止测试源",t,2))
            form.addRow("发送模式",mode); form.addRow("帧长度",length)
            form.addRow("帧间隔",gap); form.addRow("帧数限制",limit); form.addRow("随机种子",seed)
            form.addRow("混合模式周期",mix); form.addRow(buttons)
            self.source_controls[int(target)]=items; layout.addWidget(child)
        return box

    def _common_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        link = QGroupBox("双向ARQ自动流程")
        link_grid = QGridLayout(link)
        self.quick_link_grid = link_grid
        self.quick_timeout = spin(1, 0x7FFFFFFF, 100000)
        self.quick_retry = spin(0, 65535, 3)
        self.quick_sequence = QLineEdit("0x00000000")
        self.quick_arq_on = QPushButton("一键启动双向ARQ并启动双源")
        self.quick_arq_off = QPushButton("一键关闭ARQ并恢复直通基线")
        self.quick_clear = QPushButton("一键清统计和错误")
        self.quick_arq_on.setStyleSheet("font-weight:700;color:#087f23")
        link_timeout_label = QLabel("超时周期")
        link_retry_label = QLabel("最大重试次数")
        link_sequence_label = QLabel("初始序列号")
        link_grid.addWidget(link_timeout_label, 0, 0)
        link_grid.addWidget(self.quick_timeout, 0, 1)
        link_grid.addWidget(self.quick_arq_on, 0, 2)
        link_grid.addWidget(link_retry_label, 1, 0)
        link_grid.addWidget(self.quick_retry, 1, 1)
        link_grid.addWidget(self.quick_arq_off, 1, 2)
        link_grid.addWidget(link_sequence_label, 2, 0)
        link_grid.addWidget(self.quick_sequence, 2, 1)
        link_grid.addWidget(self.quick_clear, 2, 2)
        link_grid.setVerticalSpacing(5)
        link_grid.setHorizontalSpacing(10)
        link_grid.setColumnStretch(1, 1)
        link_grid.setColumnStretch(2, 1)

        fault = QGroupBox("双向故障注入自动流程")
        fault_grid = QGridLayout(fault)
        self.quick_fault_grid = fault_grid
        self.quick_threshold = spin(0, 65535, 66)
        self.quick_flips = spin(1, 8, 1)
        self.quick_seed = QLineEdit("0x00000001")
        self.quick_arm = QPushButton("双向单次注错")
        self.quick_continuous = QPushButton("启动双向连续注错")
        self.quick_bypass = QPushButton("停止注错并恢复旁路")
        fault_threshold_label = QLabel("注错阈值")
        fault_flips_label = QLabel("最大翻转位数")
        fault_seed_label = QLabel("基础随机种子")
        fault_grid.addWidget(fault_threshold_label, 0, 0)
        fault_grid.addWidget(self.quick_threshold, 0, 1)
        fault_grid.addWidget(self.quick_arm, 0, 2)
        fault_grid.addWidget(fault_flips_label, 1, 0)
        fault_grid.addWidget(self.quick_flips, 1, 1)
        fault_grid.addWidget(self.quick_continuous, 1, 2)
        fault_grid.addWidget(fault_seed_label, 2, 0)
        fault_grid.addWidget(self.quick_seed, 2, 1)
        fault_grid.addWidget(self.quick_bypass, 2, 2)
        fault_grid.setVerticalSpacing(5)
        fault_grid.setHorizontalSpacing(10)
        fault_grid.setColumnStretch(1, 1)
        fault_grid.setColumnStretch(2, 1)

        # 两个独立Grid必须共享相同的列最小宽度，否则Qt会按各自内容
        # 分别计算列宽，导致上下两个自动流程区域的输入框与按钮错位。
        flow_labels = (link_timeout_label, link_retry_label,
                       link_sequence_label, fault_threshold_label,
                       fault_flips_label, fault_seed_label)
        label_width = max(item.sizeHint().width() for item in flow_labels)
        flow_buttons = (self.quick_arq_on, self.quick_arq_off, self.quick_clear,
                        self.quick_arm, self.quick_continuous,
                        self.quick_bypass)
        button_width = max(item.sizeHint().width() for item in flow_buttons)
        for grid in (link_grid, fault_grid):
            grid.setColumnMinimumWidth(0, label_width)
            grid.setColumnMinimumWidth(2, button_width)

        note = QLabel(
            "自动流程会停止双源、执行Scheduler软复位、确认两个TX发送事务排空、"
            "依次应用A2B/B2A配置并重新启动双源。任何步骤失败立即停止；"
            "RX的预分配请求不视为真实忙碌，安全窗口由Vitis精确门控；"
            "诊断刷新可以保持开启。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#59636e;padding:5px")
        self.quick_cancel = QPushButton("取消当前流程")
        self.quick_cancel.setEnabled(False)

        self.workflow_buttons = (self.quick_arq_on, self.quick_arq_off,
                                 self.quick_clear,
                                 self.quick_arm, self.quick_continuous,
                                 self.quick_bypass)
        self.quick_arq_on.clicked.connect(self._quick_enable_arq)
        self.quick_arq_off.clicked.connect(self._quick_disable_arq)
        self.quick_clear.clicked.connect(self.workflows.clear_stats_and_errors)
        self.quick_arm.clicked.connect(self._quick_arm_once)
        self.quick_continuous.clicked.connect(self._quick_continuous_faults)
        self.quick_bypass.clicked.connect(self._quick_bypass_faults)
        self.quick_cancel.clicked.connect(self.workflows.cancel)

        layout.addWidget(link)
        layout.addWidget(fault)
        layout.addWidget(note)
        layout.addWidget(self.quick_cancel)
        layout.addStretch()
        return page

    def _right_panel(self) -> QWidget:
        body = QWidget(); layout = QVBoxLayout(body); layout.setContentsMargins(0,0,0,0)
        title = QLabel("关键寄存器实时诊断")
        title.setStyleSheet("font-size:18px;font-weight:700;padding:3px")
        subtitle = QLabel("每个节点1秒刷新一次；Alice/Bob相位错开约0.5秒。仅展示链路、ARQ、错误、资源和关键快照。")
        subtitle.setStyleSheet("color:#59636e")
        layout.addWidget(title); layout.addWidget(subtitle)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("diagnosticTabs")
        self.node_page = QWidget()
        node_layout = QVBoxLayout(self.node_page)
        node_layout.setContentsMargins(8, 8, 8, 8)
        self.node_tabs = QTabWidget()
        self.node_tabs.setObjectName("nodeDiagnosticTabs")
        self.alice_node_page = self._node_page(ALICE_INSTANCES)
        self.bob_node_page = self._node_page(BOB_INSTANCES)
        self.node_tabs.addTab(self.alice_node_page, "Alice 节点")
        self.node_tabs.addTab(self.bob_node_page, "Bob 节点")
        node_layout.addWidget(self.node_tabs)
        self.tabs.addTab(self.node_page, "节点实时诊断")
        self.performance_page = PerformancePage(self.performance)
        self.tabs.addTab(self.performance_page, "链路性能与质量")
        layout.addWidget(self.tabs, 1)
        return body

    def _log_group(self) -> QGroupBox:
        box = QGroupBox("日志")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 9, 8, 7)
        layout.setSpacing(5)
        self.operation_log = QPlainTextEdit()
        self.operation_log.setReadOnly(True)
        self.operation_log.setMaximumBlockCount(3000)
        self.operation_log.setMinimumHeight(160)
        self.operation_log.setMaximumHeight(220)
        self.operation_log.setPlaceholderText("手动命令和一键流程的下发顺序、响应及等待状态将在这里记录。")

        self.protocol_strip = ProtocolStrip()

        export_box = QWidget()
        export_layout = QVBoxLayout(export_box)
        export_layout.setContentsMargins(0, 0, 0, 0)
        export_layout.setSpacing(5)

        address_row = QWidget()
        address_layout = QHBoxLayout(address_row)
        address_layout.setContentsMargins(0, 0, 0, 0)
        address_layout.setSpacing(6)
        self.export_path = QLineEdit()
        self.export_path.setPlaceholderText("导出地址")
        # 兼容原有操作日志导出代码和测试；两类日志共用同一地址输入框。
        self.operation_export_path = self.export_path
        address_layout.addWidget(QLabel("导出地址"))
        address_layout.addWidget(self.export_path, 1)

        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(6)
        clear_button = QPushButton("清空操作日志")
        operation_export_button = QPushButton("导出操作日志")
        diagnostic_export_button = QPushButton("导出诊断日志")
        clear_button.clicked.connect(self._clear_operation_log)
        operation_export_button.clicked.connect(self._export_operation_log)
        diagnostic_export_button.clicked.connect(self._export_diagnostic_log)
        for button in (clear_button, operation_export_button,
                       diagnostic_export_button):
            button_layout.addWidget(button, 1)

        layout.addWidget(self.operation_log)
        layout.addWidget(self.protocol_strip)
        export_layout.addWidget(address_row)
        export_layout.addWidget(button_row)
        layout.addWidget(export_box)
        return box

    def _node_page(self, instances) -> QWidget:
        body=QWidget(); grid=QGridLayout(body); grid.setContentsMargins(8,8,8,8); grid.setSpacing(8)
        for index,instance in enumerate(instances):
            card=RegisterCard(instance); self.cards[int(instance.target)]=card
            grid.addWidget(card,index//2,index%2)
        for row in range(3): grid.setRowStretch(row, 1)
        for column in range(2): grid.setColumnStretch(column, 1)
        return body

    def _wire(self) -> None:
        self.port_refresh.clicked.connect(self.refresh_ports)
        self.connect_button.clicked.connect(self._toggle_connection)
        self.refresh_button.clicked.connect(self._toggle_refresh)
        self.pin_button.toggled.connect(self._set_always_on_top)
        self.worker.opened.connect(self._opened)
        self.worker.closed.connect(self._closed)
        self.worker.error.connect(self._error)
        self.client.frame_received.connect(self._frame_received)
        self.client.protocol_online.connect(self._protocol_online_changed)
        self.diagnostics.instance_updated.connect(self._registers_updated)
        self.diagnostics.instance_failed.connect(self._registers_failed)
        self.diagnostics.protocol_updated.connect(self.protocol_strip.update_values)
        self.diagnostics.cycle_updated.connect(lambda side,_:self.refresh_state.setText(f"{side}节点已刷新"))
        self.controls.issued.connect(self._append_operation)
        self.controls.completed.connect(self._operation_done)
        self.workflows.log.connect(self._append_operation)
        self.workflows.finished.connect(self._workflow_finished)
        self.workflows.running_changed.connect(self._workflow_running_changed)

    def _set_always_on_top(self, enabled: bool) -> None:
        """切换窗口置顶，并保留Qt重建原生窗口前的显示状态。"""
        visible = self.isVisible()
        state = self.windowState()
        self._always_on_top = bool(enabled)
        self.title_bar.set_pin_state(enabled)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if visible:
            self.show()
            self.setWindowState(state)
            if not state & Qt.WindowState.WindowMinimized:
                self.raise_()
                self.activateWindow()

    def _protocol_online_changed(self, online: bool) -> None:
        if not online:
            self.protocol_state.setText("协议离线")
        elif self._handshake_ok:
            self.protocol_state.setText("协议在线")
        else:
            self.protocol_state.setText("协议握手中")

    def refresh_ports(self) -> None:
        current = self.port.currentText()

        def port_key(device: str) -> tuple[int, int | str]:
            match = re.fullmatch(r"COM(\d+)", device, re.IGNORECASE)
            return (0, int(match.group(1))) if match else (1, device.casefold())

        devices = sorted((item.device for item in list_ports.comports()),
                         key=port_key)
        self.port.clear()
        self.port.addItems(devices)
        preferred = current if current in devices else "COM4"
        index = self.port.findText(preferred, Qt.MatchFlag.MatchFixedString)
        if index >= 0:
            self.port.setCurrentIndex(index)

    def _toggle_connection(self) -> None:
        if self._connected:
            self.diagnostics.stop(); self.worker.close_port()
        elif self.port.currentText():
            self.worker.open_port(SerialSettings(self.port.currentText(), UART_BAUDRATE))

    def _opened(self, port: str) -> None:
        self._connection_generation += 1
        generation = self._connection_generation
        self.diagnostics.stop()
        self.performance.reset()
        self._configuration_sync_pending = False
        self.performance.set_period(self.period.value())
        self._connected=True; self._handshake_ok=False
        self.connection_state.setText(f"{port} 已连接")
        self.connect_button.setText("断开")
        self.refresh_button.setText("开始刷新")
        self.refresh_state.setText("正在握手，尚未开始刷新")
        self.protocol_state.setText("协议握手中")
        self._handshake_step(generation, 0)

    def _handshake_step(self, generation: int, index: int) -> None:
        steps = ((Opcode.PING, b"", "PING"),
                 (Opcode.GET_INFO, b"", "GET_INFO"),
                 (Opcode.GET_CAPABILITY, b"", "GET_CAPABILITY"),
                 (Opcode.GET_HEALTH, b"", "GET_HEALTH"))
        if generation != self._connection_generation or not self._connected:
            return
        if index >= len(steps):
            payload = struct.pack("<BBHI", 0, 1, 1000, 0)
            self.client.submit(
                Target.SYSTEM, Opcode.SET_REPORT_CONFIG, payload,
                lambda frame, error:self._report_configured(
                    generation, frame, error), priority=0,
                timeout_ms=900, retries=2)
            return
        opcode, payload, name = steps[index]
        def done(frame, error) -> None:
            if generation != self._connection_generation or not self._connected:
                return
            if error or frame is None:
                self._handshake_failed(name, error or "无响应")
                return
            if frame.status_code != Status.OK:
                status = frame.status.name if frame.status else f"0x{frame.status_code:04X}"
                self._handshake_failed(name, status)
                return
            if opcode == Opcode.GET_INFO:
                if (len(frame.data) < 20 or
                        struct.unpack_from("<I", frame.data)[0] != 0x4D435001 or
                        struct.unpack_from("<H", frame.data, 4)[0] != 1):
                    self._handshake_failed(name, "设备标识或响应长度不匹配")
                    return
            elif opcode == Opcode.GET_CAPABILITY:
                if (len(frame.data) < 8 or
                        struct.unpack_from("<I", frame.data)[0] == 0 or
                        struct.unpack_from("<I", frame.data, 4)[0] != 0):
                    self._handshake_failed(name, "能力字不合法")
                    return
            elif opcode == Opcode.GET_HEALTH:
                if len(frame.data) < 4 or struct.unpack_from("<I", frame.data)[0] != 0:
                    self._handshake_failed(name, "硬件健康掩码非零")
                    return
            self._handshake_step(generation, index + 1)
        self.client.submit(Target.SYSTEM, opcode, payload, done,
                           priority=0, timeout_ms=900, retries=2)

    def _handshake_failed(self, name: str, detail: str) -> None:
        self._handshake_ok = False
        self.protocol_state.setText("协议握手失败")
        self.refresh_state.setText("握手失败，未开始刷新")
        self.app_status.showMessage(f"{name} 握手失败：{detail}", 8000)
        self._append_operation(f"握手失败 | {name} | {detail}")
        QMessageBox.warning(self, "设备兼容性检查失败",
                            f"{name}\n{detail}\n\n未开始寄存器刷新。")

    def _report_configured(self, generation: int, frame, error) -> None:
        if generation != self._connection_generation or not self._connected:
            return
        if error:
            self.app_status.showMessage(
                f"Report配置未确认，将继续尝试寄存器刷新：{error}", 6000)
            self._handshake_ok = True
            self._configuration_sync_pending = True
            self.performance.set_period(self.period.value())
            self.diagnostics.start(self.period.value())
            self.refresh_button.setText("停止刷新")
            self.refresh_state.setText("等待Alice节点数据…")
            return
        elif frame is not None and frame.status_code != Status.OK:
            self.app_status.showMessage(
                f"Report配置返回0x{(frame.status_code or 0):04X}，将继续刷新", 6000)
            self._handshake_ok = True
            self._configuration_sync_pending = True
            self.performance.set_period(self.period.value())
            self.diagnostics.start(self.period.value())
            self.refresh_button.setText("停止刷新")
            self.refresh_state.setText("等待Alice节点数据…")
            return
        self._handshake_ok = True
        self.protocol_state.setText("协议在线")
        self._begin_hardware_sync(generation, 0)

    def _begin_hardware_sync(self, generation: int, index: int) -> None:
        steps = ((Target.A2B_LINK, "link"), (Target.B2A_LINK, "link"),
                 (Target.A2B_CHANNEL, "channel"), (Target.B2A_CHANNEL, "channel"),
                 (Target.ALICE_SOURCE, "source"), (Target.BOB_SOURCE, "source"))
        if generation != self._connection_generation or not self._connected:
            return
        if index >= len(steps):
            self._configuration_sync_pending = True
            self.performance.set_period(self.period.value())
            self.diagnostics.start(self.period.value())
            self.refresh_button.setText("停止刷新")
            self.refresh_state.setText("等待Alice节点数据…")
            return
        target, kind = steps[index]
        def done(frame, error) -> None:
            if generation != self._connection_generation or not self._connected:
                return
            if error or frame is None or frame.status_code != Status.OK:
                self._append_operation(
                    f"配置同步失败 | target=0x{int(target):04X} | "
                    f"{error or (frame.status.name if frame and frame.status else '无响应')}")
            elif kind == "link" and len(frame.data) >= 12:
                self._apply_link_config(int(target), frame.data)
            elif kind == "channel" and len(frame.data) >= 12:
                self._apply_channel_config(int(target), frame.data)
            elif kind == "source" and len(frame.data) >= 24:
                self._apply_source_config(int(target), frame.data)
            self._begin_hardware_sync(generation, index + 1)
        self.client.submit(target, Opcode.GET_CONFIG, b"", done,
                           priority=0, timeout_ms=900, retries=1)

    def _apply_link_config(self, target: int, data: bytes) -> None:
        controls = self.link_controls.get(target)
        if controls is None:
            return
        enable, timeout, retry, sequence = controls
        enable.setChecked(bool(data[0]))
        retry.setValue(struct.unpack_from("<H", data, 2)[0])
        timeout.setValue(struct.unpack_from("<I", data, 4)[0])
        sequence.setText(f"0x{struct.unpack_from('<I', data, 8)[0]:08X}")

    def _apply_channel_config(self, target: int, data: bytes) -> None:
        controls = self.channel_controls.get(target)
        if controls is None:
            return
        enable, bypass, continuous, force, threshold, flips, seed = controls
        enable.setChecked(bool(data[0])); bypass.setChecked(bool(data[1]))
        continuous.setChecked(bool(data[2])); force.setChecked(bool(data[3]))
        threshold.setValue(struct.unpack_from("<H", data, 4)[0])
        flips.setValue(max(1, min(8, data[6])))
        seed.setText(f"0x{struct.unpack_from('<I', data, 8)[0]:08X}")

    def _apply_source_config(self, target: int, data: bytes) -> None:
        controls = self.source_controls.get(target)
        if controls is None:
            return
        mode, length, gap, limit, seed, mix = controls
        mode_value = data[0]
        if 0 <= mode_value < mode.count():
            mode.setCurrentIndex(mode_value)
        length.setValue(max(8, min(1752, struct.unpack_from("<I", data, 4)[0])))
        gap.setValue(struct.unpack_from("<I", data, 8)[0])
        limit.setValue(struct.unpack_from("<I", data, 12)[0])
        seed.setText(f"0x{struct.unpack_from('<I', data, 16)[0]:08X}")
        mix.setValue(max(1, struct.unpack_from("<I", data, 20)[0]))

    def _frame_received(self, frame) -> None:
        if frame.message_type != MessageType.EVENT:
            return
        if frame.opcode == 0x8001:
            value = struct.unpack_from("<I", frame.payload, 4)[0] if len(frame.payload) >= 8 else 0
            text = f"硬件Fatal Event：mask=0x{value:08X}"
            self._append_operation(f"EVENT {text}")
            self.protocol_state.setText("存在Fatal")
            self.app_status.showMessage(text, 12000)
            QMessageBox.critical(self, "硬件Fatal事件", text)

    def _closed(self) -> None:
        self._connection_generation += 1
        self.diagnostics.stop()
        self.performance.reset()
        self._connected=False; self._handshake_ok=False
        self._configuration_sync_pending=False
        self.connection_state.setText("串口未连接")
        self.connect_button.setText("连接"); self.refresh_button.setText("开始刷新")
        self.refresh_state.setText("刷新已停止")

    def _error(self, text: str) -> None:
        self.app_status.showMessage(text,5000)

    def _toggle_refresh(self) -> None:
        if self.diagnostics.running:
            self.diagnostics.stop(); self.refresh_button.setText("开始刷新")
            self.refresh_state.setText("刷新已停止")
        elif self._connected and self._handshake_ok:
            self.performance.set_period(self.period.value())
            self.diagnostics.start(self.period.value()); self.refresh_button.setText("停止刷新")
            self.refresh_state.setText("等待Alice节点数据…")
        elif self._connected:
            self.refresh_state.setText("握手未完成，无法开始刷新")

    def _registers_updated(self, target: int, values: dict) -> None:
        if target in self.cards:self.cards[target].update_values(values)
        if self._configuration_sync_pending and target in {
            int(Target.ALICE_TX_WRAPPER), int(Target.BOB_TX_WRAPPER),
        }:
            spec = INSTANCE_BY_TARGET.get(target)
            if spec is not None:
                names = {item.name: item.key for item in spec.registers}
                payload_key = names.get("ACTIVE_MAX_PAYLOAD")
                frame_key = names.get("ACTIVE_FRAME_BYTES")
                if payload_key in values and frame_key in values:
                    payload = values[payload_key]
                    physical = values[frame_key]
                    if 8 <= payload <= 2024 and physical == payload + 24:
                        self._frame_syncing = True
                        self.payload_length.setValue(payload)
                        self.physical_length.setValue(physical)
                        self._frame_syncing = False
                        self._configuration_sync_pending = False
        self.performance.ingest(target, values)
        self.performance_page.refresh()

    def _registers_failed(self, target: int, text: str) -> None:
        if target in self.cards:self.cards[target].mark_failed(text)
        self.performance.mark_failed(target)
        self.performance_page.refresh()

    def _operation_done(self, label: str, ok: bool, detail: str) -> None:
        self._append_operation(f"RX {label} | {'OK' if ok else 'FAIL'} | {detail}")
        if ok and label in {"全局帧配置", "链路配置", "清统计", "Scheduler软复位"}:
            # These operations can clear/rebase hardware counters or change
            # the frame/ARQ operating point.  Drop only the cross-operation
            # rate interval; retain the trend history on screen.
            self.performance.invalidate_baselines()
        self.app_status.showMessage(f"{label}：{detail}",6000)
        if not ok: QMessageBox.warning(self,"操作失败",f"{label}\n{detail}")

    def _append_operation(self, text: str) -> None:
        line = f"[{datetime.now():%H:%M:%S.%f}"[:-3] + f"] {text}"
        self._operation_entries.append(line)
        self.operation_log.appendPlainText(line)

    def _clear_operation_log(self) -> None:
        self._operation_entries.clear()
        self.operation_log.clear()

    def _workflow_running_changed(self, running: bool) -> None:
        for button in self.workflow_buttons:
            button.setEnabled(not running)
        self.quick_cancel.setEnabled(running)
        for index in range(1, self.control_tabs.count()):
            self.control_tabs.setTabEnabled(index, not running)

    def _workflow_finished(self, name: str, ok: bool, detail: str) -> None:
        if ok:
            # ARQ enable/disable workflows include source quiesce, scheduler
            # reset and link re-apply.  Their first post-workflow sample must
            # not be differenced against the pre-workflow counter epoch.  The
            # existing trend remains visible while the new epoch starts.
            self.performance.invalidate_baselines()
        self.app_status.showMessage(f"{name}：{detail}", 8000)
        if not ok and detail != "用户取消":
            QMessageBox.warning(self, "自动流程失败", f"{name}\n{detail}\n\n详见操作日志。")

    def _quick_link_values(self) -> tuple[int, int, int] | None:
        try:
            return (self.quick_timeout.value(), self.quick_retry.value(),
                    integer(self.quick_sequence.text()))
        except ValueError:
            QMessageBox.warning(self, "输入错误", "初始序列号必须是十进制或0x十六进制整数。")
            return None

    def _quick_fault_values(self) -> tuple[int, int, int] | None:
        try:
            return (self.quick_threshold.value(), self.quick_flips.value(),
                    integer(self.quick_seed.text()))
        except ValueError:
            QMessageBox.warning(self, "输入错误", "随机种子必须是十进制或0x十六进制整数。")
            return None

    def _quick_enable_arq(self) -> None:
        values = self._quick_link_values()
        if values:
            self.workflows.enable_bidirectional_arq(*values)

    def _quick_disable_arq(self) -> None:
        values = self._quick_link_values()
        fault = self._quick_fault_values()
        if values and fault:
            self.workflows.disable_bidirectional_arq(*values, seed=fault[2])

    def _quick_arm_once(self) -> None:
        values = self._quick_fault_values()
        if values:
            self.workflows.arm_bidirectional_once(values[1], values[2])

    def _quick_continuous_faults(self) -> None:
        values = self._quick_fault_values()
        if values:
            self.workflows.enable_continuous_faults(*values)

    def _quick_bypass_faults(self) -> None:
        values = self._quick_fault_values()
        if values:
            self.workflows.bypass_faults(values[2])

    def _export_operation_log(self) -> None:
        raw_path = self.operation_export_path.text().strip().strip('"')
        if not raw_path:
            QMessageBox.warning(self, "导出失败", "请输入操作日志导出地址。")
            return
        try:
            requested = Path(raw_path).expanduser()
            destination = (requested if requested.suffix.lower() in (".log", ".txt")
                           else requested / datetime.now().strftime(
                               "ARQ_Operations_%Y%m%d_%H%M%S.log"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            header = ("ARQ系统操作日志\n"
                      f"导出时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
                      f"串口: {self.port.currentText() or '未连接'}\n\n")
            destination.write_text(header + "\n".join(self._operation_entries) + "\n",
                                   encoding="utf-8")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导出失败", f"无法写入操作日志：\n{exc}")
            return
        self.app_status.showMessage(f"操作日志已导出：{destination}", 8000)

    def _export_diagnostic_log(self) -> None:
        raw_path = self.export_path.text().strip().strip('"')
        if not raw_path:
            QMessageBox.warning(self, "导出失败", "请输入导出地址。")
            return
        try:
            requested = Path(raw_path).expanduser()
            if requested.suffix.lower() in (".log", ".txt"):
                destination = requested
            else:
                destination = requested / datetime.now().strftime(
                    "ARQ_Diagnostic_%Y%m%d_%H%M%S.log")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(self._diagnostic_log_text(), encoding="utf-8")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导出失败", f"无法写入诊断日志：\n{exc}")
            return
        self.app_status.showMessage(f"诊断日志已导出：{destination}", 8000)

    def _diagnostic_log_text(self) -> str:
        timestamps = [card.updated_at for card in self.cards.values()
                      if card.updated_at is not None]
        if timestamps:
            first_sample = min(timestamps)
            last_sample = max(timestamps)
            span_ms = (last_sample - first_sample).total_seconds() * 1000.0
            capture_range = (
                f"{first_sample:%H:%M:%S.%f}"[:-3] + " ~ " +
                f"{last_sample:%H:%M:%S.%f}"[:-3])
        else:
            span_ms = 0.0
            capture_range = "尚未采集"
        lines = [
            "ARQ系统诊断日志",
            f"导出时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"串口: {self.port.currentText() or '未连接'}",
            f"波特率: {UART_BAUDRATE}",
            "采集模型: 非原子最近值集合（各IP顺序采集）",
            f"有效卡片: {len(timestamps)}/{len(self.cards)}",
            f"采集时间范围: {capture_range}",
            f"跨卡片时间跨度: {span_ms:.3f} ms",
            "说明: 跨IP累计计数可能因采集时刻不同出现一帧级偏差；"
            "严格守恒判断应使用同一硬件快照或停止数据源后采集。",
            "",
        ]
        for target, card in self.cards.items():
            lines.append(f"[{card.instance.title}] Target=0x{target:04X}")
            lines.append(f"采集状态: {card.state.text()}")
            for spec in sorted(card.instance.registers, key=lambda item: item.key):
                row = card.rows[spec.key]
                lines.append(
                    f"{spec.offset_text} | {spec.name} | {spec.chinese} | "
                    f"{row.text(1)} | {row.text(2)} | {spec.access.value}")
            lines.append("")
        lines.append("[Vitis / MCP 通信]")
        if self.protocol_strip.values:
            for key, value in self.protocol_strip.values.items():
                lines.append(f"{key}: {value}")
        else:
            lines.append("尚未采集")
        lines.append("")
        lines.append(self.performance.log_summary())
        lines.append("")
        return "\n".join(lines)

    def _set_link(self,target,enable,timeout,retry,sequence) -> None:
        try:self.controls.set_link(target,enable.isChecked(),timeout.value(),retry.value(),integer(sequence.text()))
        except ValueError:QMessageBox.warning(self,"输入错误","初始序列号必须是十进制或0x十六进制整数")

    def _set_channel(self,target,items) -> None:
        e,b,c,f,t,m,s=items
        try:self.controls.set_channel(target,e.isChecked(),b.isChecked(),c.isChecked(),f.isChecked(),t.value(),m.value(),integer(s.text()))
        except ValueError:QMessageBox.warning(self,"输入错误","随机种子必须是十进制或0x十六进制整数")

    def _set_source(self,target,items) -> None:
        mode,length,gap,limit,seed,mix=items
        try:self.controls.set_source(target,mode.currentIndex(),length.value(),gap.value(),limit.value(),integer(seed.text()),mix.value())
        except ValueError:QMessageBox.warning(self,"输入错误","随机种子必须是十进制或0x十六进制整数")

    def _confirm_soft_reset(self) -> None:
        if QMessageBox.question(self,"确认Scheduler软复位","将软复位四个Scheduler，是否继续？") == QMessageBox.StandardButton.Yes:
            self.controls.soft_reset()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if (event.type() == QEvent.Type.WindowStateChange and
                hasattr(self, "title_bar")):
            self.title_bar.update_maximize_icon()
            self._update_window_frame_style()

    def nativeEvent(self, event_type, message):
        """为无边框窗口恢复Windows四边及四角缩放。"""
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
                        return True, 13
                    if top and right:
                        return True, 14
                    if bottom and left:
                        return True, 16
                    if bottom and right:
                        return True, 17
                    if left:
                        return True, 10
                    if right:
                        return True, 11
                    if top:
                        return True, 12
                    if bottom:
                        return True, 15
        return super().nativeEvent(event_type, message)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.workflows.cancel(); self.diagnostics.stop(); self.worker.shutdown(); event.accept()
