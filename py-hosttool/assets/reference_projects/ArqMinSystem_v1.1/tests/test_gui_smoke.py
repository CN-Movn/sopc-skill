import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

from gui import HostToolWindow
import gui
from diagnostics import Severity
from performance import Metric
from performance_view import LinkPanel, TrendChart


def test_window_constructs_and_worker_stops():
    app = QApplication.instance() or QApplication([])
    window = HostToolWindow()
    assert window.tabs.count() == 2
    assert [window.tabs.tabText(index) for index in range(2)] == [
        "节点实时诊断", "链路性能与质量"]
    assert window.node_tabs.count() == 2
    assert [window.node_tabs.tabText(index) for index in range(2)] == [
        "Alice 节点", "Bob 节点"]
    assert window.node_tabs.widget(0) is window.alice_node_page
    assert window.node_tabs.widget(1) is window.bob_node_page
    performance_page = window.tabs.widget(1)
    assert performance_page.direction_tabs.count() == 2
    assert [performance_page.direction_tabs.tabText(index)
            for index in range(2)] == ["A→B 性能", "B→A 性能"]
    assert (performance_page.direction_tabs.widget(0) is
            performance_page.direction_pages["A2B"])
    assert (performance_page.direction_tabs.widget(1) is
            performance_page.direction_pages["B2A"])
    assert len(window.cards) == 12
    assert not isinstance(window.node_tabs.widget(0), QScrollArea)
    assert not isinstance(window.node_tabs.widget(1), QScrollArea)
    assert window.control_tabs.count() == 5
    assert [window.control_tabs.tabText(index) for index in range(5)] == [
        "一键操作", "全局配置", "双向ARQ链路", "故障注入", "测试源"]
    assert window.operation_export_path is window.export_path
    assert window.export_path.placeholderText() == "导出地址"
    button_texts = {button.text() for button in window.findChildren(QPushButton)}
    assert {"清空操作日志", "导出操作日志", "导出诊断日志"} <= button_texts
    assert (window.quick_link_grid.columnMinimumWidth(0) ==
            window.quick_fault_grid.columnMinimumWidth(0))
    assert (window.quick_link_grid.columnMinimumWidth(2) ==
            window.quick_fault_grid.columnMinimumWidth(2))
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert window.window_frame.objectName() == "windowFrame"
    assert "border-radius:6px" in window.window_frame.styleSheet()
    assert window.app_status.parent() is window.window_frame
    assert [window.title_bar.pin_button.control,
            window.title_bar.minimize_button.control,
            window.title_bar.maximize_button.control,
            window.title_bar.close_button.control] == [
                "pin", "minimize", "maximize", "close"]
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.pin_button.setChecked(True)
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert window.pin_button.toolTip() == "取消窗口置顶"
    window.pin_button.setChecked(False)
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert window.pin_button.toolTip() == "窗口置顶"
    window.close()
    app.processEvents()


def test_diagnostic_log_export(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = HostToolWindow()
    window.export_path.setText(str(tmp_path))
    window._export_diagnostic_log()
    exported = list(tmp_path.glob("ARQ_Diagnostic_*.log"))
    assert len(exported) == 1
    text = exported[0].read_text(encoding="utf-8")
    assert "ARQ系统诊断日志" in text
    assert "TX Scheduler" in text
    assert "Vitis / MCP 通信" in text
    assert "采集模型: 非原子最近值集合" in text
    assert "跨卡片时间跨度:" in text
    assert "[链路性能与质量]" in text
    assert "A→B:" in text
    window.close()
    app.processEvents()


def test_operation_log_records_and_exports(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = HostToolWindow()
    window._append_operation("TX 单元测试命令")
    window._append_operation("RX 单元测试命令 | OK")
    window.operation_export_path.setText(str(tmp_path))
    window._export_operation_log()
    exported = list(tmp_path.glob("ARQ_Operations_*.log"))
    assert len(exported) == 1
    text = exported[0].read_text(encoding="utf-8")
    assert "ARQ系统操作日志" in text
    assert "TX 单元测试命令" in text
    assert "RX 单元测试命令 | OK" in text
    window.close()
    app.processEvents()


def test_stale_port_initialization_cannot_start_new_connection(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = HostToolWindow()
    starts = []
    monkeypatch.setattr(window.diagnostics, "start",
                        lambda period: starts.append(period))
    window._connected = True
    window._connection_generation = 4
    window._report_configured(3, None, "COM5旧事务超时")
    assert starts == []
    window._report_configured(4, None, "COM4初始化响应丢失")
    assert starts == [window.period.value()]
    window.close()
    app.processEvents()


def test_ports_use_numeric_order_and_default_to_com4(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(gui.list_ports, "comports", lambda: [
        SimpleNamespace(device="COM10"), SimpleNamespace(device="COM4"),
        SimpleNamespace(device="COM2"), SimpleNamespace(device="COM5"),
    ])
    window = HostToolWindow()
    assert [window.port.itemText(i) for i in range(window.port.count())] == [
        "COM2", "COM4", "COM5", "COM10"]
    assert window.port.currentText() == "COM4"
    window.close()
    app.processEvents()


def test_performance_summary_columns_do_not_jump_with_long_feedback():
    app = QApplication.instance() or QApplication([])
    panel = LinkPanel("A→B 链路")
    panel.resize(680, 720)
    panel.show()
    app.processEvents()
    before = {name: tile.width() for name, tile in panel.tiles.items()}
    panel.tiles["反馈闭合"].set_metric(Metric(
        None, "生成 27828.3 帧/秒\n路由 26199.8 / 接收 26001.4 帧/秒",
        Severity.NORMAL, True))
    app.processEvents()
    after = {name: tile.width() for name, tile in panel.tiles.items()}
    assert before == after
    assert max(after.values()) - min(after.values()) <= 1
    assert panel.table.columnWidth(0) == 150
    assert panel.table.wordWrap()
    assert panel.state.height() == 22
    assert not panel.state.wordWrap()
    chart = TrendChart("A→B 最近60秒吞吐趋势")
    assert "绿色" in chart.toolTip() and "蓝色" in chart.toolTip()
    assert "RX下游交付有效吞吐" in chart.toolTip()
    assert "物理帧吞吐（含DATA/ACK/NACK）" in chart.toolTip()
    panel.close()
    app.processEvents()
