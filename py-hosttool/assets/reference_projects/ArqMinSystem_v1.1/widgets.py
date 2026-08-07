"""Reusable compact widgets for the ARQ diagnostic view."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QHeaderView,
                               QSizePolicy)

from diagnostics import COLORS, Severity, diagnose
from registers import InstanceSpec


class RegisterCard(QGroupBox):
    def __init__(self, instance: InstanceSpec, parent=None) -> None:
        super().__init__(instance.title, parent)
        self.instance = instance
        self.values: dict[int, int] = {}
        self.updated_at: datetime | None = None
        self._worst_severity = Severity.STALE
        self.rows: dict[int, QTreeWidgetItem] = {}
        self.state = QLabel("尚未采集")
        self.state.setProperty("muted", True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(("诊断项", "当前值", "状态"))
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setMinimumSectionSize(70)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(1, 108)
        header.resizeSection(2, 128)
        header.setStretchLastSection(False)
        for spec in sorted(instance.registers, key=lambda item: item.key):
            row = QTreeWidgetItem(self.tree, (spec.chinese, "--", "未读取"))
            detail = (f"{spec.name}\n{spec.offset_text}\n分组：{spec.group.value}\n"
                      f"访问属性：{spec.access.value}")
            row.setToolTip(0, detail)
            self.rows[spec.key] = row
        top = QHBoxLayout()
        top.addWidget(self.state)
        top.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.addLayout(top)
        layout.addWidget(self.tree)
        self.setMinimumHeight(240)

    def update_values(self, values: dict[int, int]) -> None:
        self.updated_at = datetime.now()
        now = self.updated_at.strftime("%H:%M:%S.%f")[:-3]
        previous = self.values
        self.values = dict(values)
        worst = Severity.NORMAL
        # A transport gap is stale data, not a hardware error. Keep genuine
        # Error/Fatal rows dominant when one fragment or one sample is absent.
        order = {Severity.NORMAL: 0, Severity.STALE: 1,
                 Severity.NOTICE: 2, Severity.ERROR: 3}
        for spec in self.instance.registers:
            row = self.rows[spec.key]
            if spec.key not in values:
                self._paint(row, Severity.STALE)
                row.setText(1, "--")
                row.setText(2, "本批缺失")
                if order[Severity.STALE] > order[worst]:
                    worst = Severity.STALE
                continue
            value = values[spec.key]
            result = diagnose(spec, value, previous.get(spec.key))
            row.setText(1, f"0x{value:08X}")
            row.setText(2, result.text)
            row.setToolTip(1, f"{spec.name}\n{spec.offset_text}\n原始值：0x{value:08X}\n"
                               f"访问属性：{spec.access.value}\n更新时间：{now}\n{result.text}")
            self._paint(row, result.severity)
            if order[result.severity] > order[worst]:
                worst = result.severity
        self.state.setText(f"{now} · {len(values)} 项")
        self._worst_severity = worst
        self.setProperty("severity", worst.value)
        self.style().unpolish(self)
        self.style().polish(self)

    def mark_failed(self, text: str) -> None:
        self.state.setText(f"采集失败：{text}")
        severity = (Severity.ERROR if self._worst_severity == Severity.ERROR
                    else Severity.STALE)
        self.setProperty("severity", severity.value)
        self.style().unpolish(self)
        self.style().polish(self)

    @staticmethod
    def _paint(item: QTreeWidgetItem, severity: Severity) -> None:
        colour = QBrush(QColor(*COLORS[severity]))
        for column in range(3):
            item.setBackground(column, colour)


class ProtocolStrip(QGroupBox):
    def __init__(self, parent=None) -> None:
        super().__init__("Vitis / MCP 通信")
        self.values: dict[str, int] = {}
        self.labels: dict[str, QLabel] = {}
        names = (("frames_ok", "有效帧"), ("crc_errors", "CRC"),
                 ("request_drops", "请求丢弃"), ("uart_errors", "UART"),
                 ("response_drops", "响应丢弃"), ("event_drops", "事件丢弃"))
        layout = QHBoxLayout(self)
        layout.setSpacing(4)
        for key, title in names:
            label = QLabel(f"{title}: --")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Preferred)
            self.labels[key] = label
            layout.addWidget(label, 1)

    def update_values(self, values: dict[str, int]) -> None:
        self.values = dict(values)
        for key, label in self.labels.items():
            label.setText(f"{label.text().split(':')[0]}: {values.get(key, 0)}")
