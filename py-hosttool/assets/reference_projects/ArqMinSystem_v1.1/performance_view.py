"""Compact Qt view for locally calculated ARQ link performance."""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QGridLayout, QGroupBox, QHeaderView,
                               QLabel, QSizePolicy, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from diagnostics import Severity
from performance import (PHYSICAL_LABEL, RX_DELIVERY_LABEL, LinkMetrics,
                         Metric, PerformanceModel)


COLOURS = {
    Severity.NORMAL: "#e8f8ed",
    Severity.NOTICE: "#fff6d5",
    Severity.ERROR: "#ffe4e4",
    Severity.STALE: "#eef0f3",
}


class MetricTile(QFrame):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("metricTile")
        self.setFixedHeight(52)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 4, 7, 4)
        layout.setSpacing(0)
        self.title = QLabel(title)
        self.title.setStyleSheet("color:#59636e;font-size:11px")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Preferred)
        self.value = QLabel("--")
        self.value.setStyleSheet("font-weight:700;font-size:11px")
        self.value.setWordWrap(True)
        self.value.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Preferred)
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        self.set_metric(Metric(None, "--", Severity.STALE))

    def set_metric(self, metric: Metric) -> None:
        self.value.setText(metric.text)
        self.setStyleSheet(
            "QFrame#metricTile{border:1px solid #d7dce2;border-radius:5px;"
            f"background:{COLOURS[metric.severity]};}}")
        self.setToolTip("估算值：由现有帧计数与生效长度换算"
                        if metric.estimated else "精确值：由现有累计计数差分计算")


class TrendChart(QWidget):
    """不依赖第三方绘图库的60秒交付/物理帧吞吐趋势图。"""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self.points: tuple[tuple[float, float, float], ...] = ()
        self.setMinimumHeight(135)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setToolTip(f"绿色：{RX_DELIVERY_LABEL}（最终AXI-Stream握手字节）；"
                        f"蓝色：{PHYSICAL_LABEL}。")

    def set_points(self, points: tuple[tuple[float, float, float], ...]) -> None:
        self.points = points
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.setPen(QPen(QColor("#cbd2d9"), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 5, 5)
        plot = QRectF(46, 24, max(10, self.width() - 58), max(10, self.height() - 45))
        painter.setPen(QColor("#202124"))
        painter.drawText(9, 17, self.title)
        legend_colour = QColor("#7a848e")
        font_metrics = painter.fontMetrics()
        green_width = font_metrics.horizontalAdvance(RX_DELIVERY_LABEL)
        blue_width = font_metrics.horizontalAdvance(PHYSICAL_LABEL)
        legend_width = 16 + 20 + green_width + 24 + 16 + 20 + blue_width
        legend_x = max(8, self.width() - legend_width - 8)
        painter.setPen(QColor("#16863b"))
        painter.drawLine(legend_x, 13, legend_x + 16, 13)
        painter.setPen(legend_colour)
        painter.drawText(legend_x + 20, 17, RX_DELIVERY_LABEL)
        blue_x = legend_x + 16 + 20 + green_width + 24
        painter.setPen(QColor("#0b57d0"))
        painter.drawLine(blue_x, 13, blue_x + 16, 13)
        painter.setPen(legend_colour)
        painter.drawText(blue_x + 20, 17, PHYSICAL_LABEL)
        painter.setPen(QPen(QColor("#e2e6ea"), 1))
        for index in range(4):
            y = plot.top() + plot.height() * index / 3
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        if len(self.points) < 2:
            painter.setPen(QColor("#7a848e"))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "等待连续采样")
            return
        end = self.points[-1][0]
        start = max(end - 60.0, self.points[0][0])
        maximum = max(max(goodput, physical) for _, goodput, physical in self.points)
        maximum = max(maximum, 1.0)
        painter.setPen(QColor("#59636e"))
        painter.drawText(4, int(plot.top() + 4), self._rate(maximum))
        painter.drawText(13, int(plot.bottom()), "0")
        painter.drawText(int(plot.left()), self.height() - 4, "-60s")
        painter.drawText(int(plot.right() - 18), self.height() - 4, "0s")
        self._line(painter, plot, start, end, maximum, 1, QColor("#16863b"))
        self._line(painter, plot, start, end, maximum, 2, QColor("#0b57d0"))

    def _line(self, painter: QPainter, plot: QRectF, start: float, end: float,
              maximum: float, field: int, colour: QColor) -> None:
        if end <= start:
            return
        painter.setPen(QPen(colour, 1.7))
        prior = None
        for point in self.points:
            x = plot.left() + (point[0] - start) / (end - start) * plot.width()
            y = plot.bottom() - point[field] / maximum * plot.height()
            current = QPointF(x, y)
            if prior is not None:
                painter.drawLine(prior, current)
            prior = current

    @staticmethod
    def _rate(bits: float) -> str:
        if bits >= 1e9:
            return f"{bits / 1e9:.2f}G"
        if bits >= 1e6:
            return f"{bits / 1e6:.1f}M"
        if bits >= 1e3:
            return f"{bits / 1e3:.1f}k"
        return f"{bits:.0f}"


class LinkPanel(QGroupBox):
    SUMMARY_NAMES = ("业务源吞吐", RX_DELIVERY_LABEL, PHYSICAL_LABEL, "重传率",
                     "有效载荷效率", "人工注入误码率（BER）", "有效命中率", "接收丢帧率",
                     "注错恢复率", "反馈闭合", "终结失败", "重传吞吐")

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(title, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(6)
        self.state = QLabel("等待连续采样")
        self.state.setStyleSheet("color:#59636e")
        self.state.setWordWrap(False)
        self.state.setFixedHeight(22)
        self.state.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Preferred)
        layout.addWidget(self.state)
        tile_grid = QGridLayout()
        tile_grid.setSpacing(5)
        self.tiles = {name: MetricTile(name) for name in self.SUMMARY_NAMES}
        for index, name in enumerate(self.SUMMARY_NAMES):
            tile_grid.addWidget(self.tiles[name], index // 3, index % 3)
        for column in range(3):
            tile_grid.setColumnStretch(column, 1)
        layout.addLayout(tile_grid)
        self.table = QTableWidget(6, 5)
        self.table.setHorizontalHeaderLabels(("链路节点", "吞吐", "帧率", "质量", "压力"))
        self.table.setVerticalHeaderLabels(("测试源", "TX Scheduler", "TX Wrapper",
                                            "Analog Channel", "RX Wrapper", "RX Scheduler"))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        table_font = self.table.font()
        table_font.setPointSizeF(max(8.5, table_font.pointSizeF() - 0.5))
        self.table.setFont(table_font)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, 150)
        for column in range(1, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.table.setFixedHeight(304)
        layout.addWidget(self.table)

    def update_metrics(self, metrics: LinkMetrics) -> None:
        summary = dict(metrics.summary)
        for name, tile in self.tiles.items():
            tile.set_metric(summary.get(name, Metric(None, "--", Severity.STALE)))
        if metrics.sampled_at is None:
            self.state.setText("尚未形成同一IP的连续采样")
        else:
            stamp = metrics.sampled_at.strftime("%H:%M:%S.%f")[:-3]
            self.state.setText(
                f"{stamp}{' · 数据过期/不完整' if metrics.stale else ''}")
        for row, stage in enumerate(metrics.stages):
            values = (stage.name, stage.throughput, stage.frame_rate,
                      stage.quality, stage.pressure)
            for column, value in enumerate(values):
                if column == 0:
                    item = QTableWidgetItem(value)
                    severity = Severity.NORMAL
                    estimated = False
                else:
                    item = QTableWidgetItem(value.text)
                    severity = value.severity
                    estimated = value.estimated
                item.setBackground(QColor(COLOURS[severity]))
                item.setToolTip(value if column == 0 else value.text)
                if estimated:
                    item.setToolTip(item.toolTip() + "\n估算值；未增加硬件读取")
                self.table.setItem(row, column, item)


class PerformancePage(QWidget):
    def __init__(self, model: PerformanceModel, parent=None) -> None:
        super().__init__(parent)
        self.model = model
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        self.panels = {
            "A2B": LinkPanel("A→B 链路"),
            "B2A": LinkPanel("B→A 链路"),
        }
        self.charts = {
            "A2B": TrendChart("A→B 最近60秒吞吐趋势"),
            "B2A": TrendChart("B→A 最近60秒吞吐趋势"),
        }
        self.direction_tabs = QTabWidget()
        self.direction_tabs.setObjectName("performanceDirectionTabs")
        self.direction_pages = {}
        for name, tab_title in (("A2B", "A→B 性能"), ("B2A", "B→A 性能")):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(6, 7, 6, 6)
            page_layout.setSpacing(7)
            page_layout.addWidget(self.panels[name])
            page_layout.addWidget(self.charts[name], 1)
            self.direction_pages[name] = page
            self.direction_tabs.addTab(page, tab_title)
        layout.addWidget(self.direction_tabs)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def refresh(self) -> None:
        now = time.monotonic()
        for name in ("A2B", "B2A"):
            self.panels[name].update_metrics(self.model.link_metrics(name, now))
            self.charts[name].set_points(self.model.history_points(name))
