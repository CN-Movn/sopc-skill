"""Reusable instrument-dashboard shell for device control and diagnostics."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSplitter, QStatusBar, QTabWidget, QVBoxLayout,
    QWidget,
)

from .config import APP_NAME, APP_VERSION
from .window_chrome import FramelessWindow


class DashboardWindow(FramelessWindow):
    def __init__(self) -> None:
        super().__init__(f"{APP_NAME} {APP_VERSION}")
        self.resize(1680, 980)
        self.setMinimumSize(1460, 720)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        left, right = self._left_panel(), self._right_panel()
        left.setMinimumWidth(640)
        right.setMinimumWidth(800)
        splitter.addWidget(left); splitter.addWidget(right)
        splitter.setStretchFactor(0, 0); splitter.setStretchFactor(1, 1)
        splitter.setSizes([660, 1000])
        self.content_layout.addWidget(splitter, 1)
        status = QStatusBar()
        status.setSizeGripEnabled(False)
        status.addWidget(QLabel(f"{APP_NAME} {APP_VERSION}"), 1)
        status.addPermanentWidget(QLabel("设备未连接"))
        status.addPermanentWidget(QLabel("协议离线"))
        status.addPermanentWidget(QLabel("刷新已停止"))
        self.outer_layout.addWidget(status)

    def _left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        connection = QGroupBox("设备连接")
        form = QFormLayout(connection)
        form.addRow("通道", QLineEdit("按项目实现"))
        row = QHBoxLayout()
        row.addWidget(QPushButton("刷新")); row.addWidget(QPushButton("连接")); row.addWidget(QPushButton("开始刷新"))
        form.addRow(row)
        layout.addWidget(connection)
        tabs = QTabWidget()
        tabs.addTab(self._action_page(), "一键操作")
        tabs.addTab(self._placeholder_page("全局配置"), "全局配置")
        tabs.addTab(self._placeholder_page("链路/通道配置"), "链路配置")
        layout.addWidget(tabs, 1)
        logs = QGroupBox("操作日志")
        log_layout = QVBoxLayout(logs)
        log = QPlainTextEdit(); log.setReadOnly(True); log.setMaximumHeight(220)
        log.setPlaceholderText("手动操作、自动流程和错误将在这里记录")
        log_layout.addWidget(log)
        layout.addWidget(logs)
        return panel

    def _action_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page)
        first = QGroupBox("设备自动流程")
        grid = QGridLayout(first)
        for row, text in enumerate(("启动系统", "停止系统", "清统计与错误")):
            grid.addWidget(QLabel(f"步骤 {row + 1}"), row, 0)
            grid.addWidget(QLineEdit("按项目填写参数"), row, 1)
            grid.addWidget(QPushButton(text), row, 2)
        layout.addWidget(first)
        note = QLabel("自动流程应具备前置条件、等待安全窗口、回读确认、失败停止和取消路径。")
        note.setWordWrap(True); note.setProperty("muted", True)
        layout.addWidget(note); layout.addStretch(1)
        return page

    @staticmethod
    def _placeholder_page(title: str) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page)
        box = QGroupBox(title); box_layout = QVBoxLayout(box)
        label = QLabel("按项目协议与寄存器事实实现，不要保留来源工程业务字段。")
        label.setWordWrap(True); label.setProperty("muted", True)
        box_layout.addWidget(label); box_layout.addStretch(1)
        layout.addWidget(box); layout.addStretch(1)
        return page

    def _right_panel(self) -> QWidget:
        body = QWidget(); layout = QVBoxLayout(body); layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("设备实时诊断")
        title.setStyleSheet("font-size:18px;font-weight:700;padding:3px")
        subtitle = QLabel("使用对称节点页、诊断卡片、性能指标和趋势图组织实时信息。")
        subtitle.setProperty("muted", True)
        tabs = QTabWidget()
        tabs.addTab(self._card_grid(), "实时诊断")
        tabs.addTab(self._placeholder_page("性能与质量"), "性能与质量")
        layout.addWidget(title); layout.addWidget(subtitle); layout.addWidget(tabs, 1)
        return body

    @staticmethod
    def _card_grid() -> QWidget:
        page = QWidget(); grid = QGridLayout(page); grid.setSpacing(8)
        for index in range(6):
            box = QGroupBox(f"诊断卡片 {index + 1}")
            layout = QVBoxLayout(box)
            state = QLabel("尚未采集"); state.setProperty("muted", True)
            detail = QPlainTextEdit(); detail.setReadOnly(True); detail.setPlainText("关键状态\n当前值\n诊断结论")
            layout.addWidget(state); layout.addWidget(detail)
            grid.addWidget(box, index // 2, index % 2)
        for row in range(3): grid.setRowStretch(row, 1)
        for column in range(2): grid.setColumnStretch(column, 1)
        return page
