"""Frameless Windows shell with vector controls and synchronized pinning."""
from __future__ import annotations

import sys
import weakref
from ctypes import wintypes

from PySide6.QtCore import QEvent, QLineF, QRectF, Qt
from PySide6.QtGui import QColor, QCloseEvent, QCursor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .theme import APP_QSS, COLORS


class WindowGroup:
    """Share always-on-top state between a main window and child windows."""

    def __init__(self) -> None:
        self.enabled = False
        self.members: weakref.WeakSet[FramelessWindow] = weakref.WeakSet()

    def add(self, window: "FramelessWindow") -> None:
        self.members.add(window)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        active = QApplication.activeWindow()
        for member in list(self.members):
            member._apply_always_on_top(self.enabled)
        if active in self.members and active is not None:
            active.raise_()
            active.activateWindow()


class WindowControlButton(QToolButton):
    """Vector title-bar button independent of system symbol fonts."""

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
        color = QColor(COLORS["accent"] if self.control == "pin" and self.isChecked()
                       else COLORS["text"])
        if self.control == "close" and self.underMouse():
            color = QColor("#ffffff")
        painter.setPen(QPen(color, 1.55, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy = self.width() / 2.0, self.height() / 2.0
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
            path.moveTo(cx - 4.5, cy - 5); path.lineTo(cx + 4.5, cy - 5)
            path.moveTo(cx - 2.5, cy - 5); path.lineTo(cx - 2.5, cy - 1.5)
            path.moveTo(cx + 2.5, cy - 5); path.lineTo(cx + 2.5, cy - 1.5)
            path.moveTo(cx - 4, cy - 1.5); path.lineTo(cx + 4, cy - 1.5)
            path.moveTo(cx - 2.5, cy - 1.5); path.lineTo(cx, cy + 2)
            path.lineTo(cx + 2.5, cy - 1.5)
            path.moveTo(cx, cy + 2); path.lineTo(cx, cy + 6)
            painter.drawPath(path)

    def set_control(self, control: str) -> None:
        if self.control != control:
            self.control = control
            self.update()


class WindowTitleBar(QWidget):
    def __init__(self, window: "FramelessWindow") -> None:
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
        self.pin_button.toggled.connect(window._set_pin_group)
        self.minimize_button = WindowControlButton("minimize", "最小化", self)
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button = WindowControlButton("maximize", "最大化", self)
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button = WindowControlButton("close", "关闭", self)
        self.close_button.setObjectName("closeButton")
        self.close_button.clicked.connect(window.close)
        for button in (self.pin_button, self.minimize_button,
                       self.maximize_button, self.close_button):
            layout.addWidget(button)
        self.set_maximized_style(False)

    def set_maximized_style(self, maximized: bool) -> None:
        radius = 0 if maximized else 4
        self.setStyleSheet(f"""
            QWidget#windowTitleBar {{
                background: {COLORS['titlebar']};
                border-bottom: 1px solid {COLORS['line_soft']};
                border-top-left-radius: {radius}px;
                border-top-right-radius: {radius}px;
            }}
            QToolButton {{ border: none; background: transparent; }}
            QToolButton:hover {{ background: #e3e7eb; }}
            QToolButton:checked {{ background: #d7e8ff; }}
            QToolButton#closeButton {{ border-top-right-radius: {radius}px; }}
            QToolButton#closeButton:hover {{ background: {COLORS['close_hover']}; }}
        """)

    def set_pin_state(self, enabled: bool) -> None:
        self.pin_button.blockSignals(True)
        self.pin_button.setChecked(enabled)
        self.pin_button.setToolTip("取消窗口置顶" if enabled else "窗口置顶")
        self.pin_button.blockSignals(False)

    def toggle_maximized(self) -> None:
        self._window.showNormal() if self._window.isMaximized() else self._window.showMaximized()
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


class FramelessWindow(QMainWindow):
    """Base class that owns the proven custom frame behavior."""

    def __init__(self, title: str, *, group: WindowGroup | None = None) -> None:
        super().__init__()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle(title)
        self.window_group = group or WindowGroup()
        self.window_group.add(self)
        self._always_on_top = self.window_group.enabled

        self.window_frame = QWidget(self)
        self.window_frame.setObjectName("windowFrame")
        self.setCentralWidget(self.window_frame)
        self.outer_layout = QVBoxLayout(self.window_frame)
        self.outer_layout.setContentsMargins(1, 1, 1, 1)
        self.outer_layout.setSpacing(0)
        self.title_bar = WindowTitleBar(self)
        self.outer_layout.addWidget(self.title_bar)
        self.content_host = QWidget(self.window_frame)
        self.content_layout = QVBoxLayout(self.content_host)
        self.content_layout.setContentsMargins(12, 10, 12, 8)
        self.outer_layout.addWidget(self.content_host, 1)
        self.setStyleSheet(APP_QSS)
        self.title_bar.set_pin_state(self._always_on_top)
        if self._always_on_top:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._update_window_frame_style()

    def _set_pin_group(self, enabled: bool) -> None:
        self.window_group.set_enabled(enabled)

    def _apply_always_on_top(self, enabled: bool) -> None:
        visible = self.isVisible()
        state = self.windowState()
        self._always_on_top = enabled
        self.title_bar.set_pin_state(enabled)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if visible:
            self.show()
            self.setWindowState(state)

    def _update_window_frame_style(self) -> None:
        maximized = self.isMaximized()
        radius = 0 if maximized else 5
        border = 0 if maximized else 1
        margin = 0 if maximized else 1
        self.outer_layout.setContentsMargins(margin, margin, margin, margin)
        self.window_frame.setStyleSheet(f"""
            QWidget#windowFrame {{
                background: {COLORS['canvas']};
                border: {border}px solid {COLORS['frame']};
                border-radius: {radius}px;
            }}
        """)
        self.title_bar.set_maximized_style(maximized)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.title_bar.update_maximize_icon()
            self._update_window_frame_style()

    def nativeEvent(self, event_type, message):
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
                    if top and left: return True, 13
                    if top and right: return True, 14
                    if bottom and left: return True, 16
                    if bottom and right: return True, 17
                    if left: return True, 10
                    if right: return True, 11
                    if top: return True, 12
                    if bottom: return True, 15
        return super().nativeEvent(event_type, message)

    def closeEvent(self, event: QCloseEvent) -> None:
        event.accept()
