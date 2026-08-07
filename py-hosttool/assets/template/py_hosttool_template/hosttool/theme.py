"""Shared design tokens and QSS derived from the reference host tools."""

COLORS = {
    "text": "#202124",
    "muted": "#59636e",
    "canvas": "#f4f6f8",
    "titlebar": "#f3f6f9",
    "panel": "#ffffff",
    "line": "#cbd2d9",
    "line_soft": "#d7dce2",
    "frame": "#85898f",
    "accent": "#0b57d0",
    "success": "#188038",
    "warning": "#e37400",
    "error": "#d93025",
    "close_hover": "#e81123",
    "stale": "#98a1aa",
}

APP_QSS = f"""
QMainWindow {{ background: transparent; color: {COLORS['text']}; }}
QWidget {{ color: {COLORS['text']}; }}
QStatusBar {{ background: transparent; border: none; }}
QGroupBox {{
    font-weight: 600;
    border: 1px solid {COLORS['line']};
    border-radius: 6px;
    margin-top: 8px;
    background: {COLORS['panel']};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 9px; padding: 0 4px; }}
QPushButton {{ min-height: 25px; padding: 2px 10px; }}
QLineEdit, QSpinBox, QComboBox {{ min-height: 24px; }}
QTabWidget::pane {{
    border: 1px solid {COLORS['line']};
    border-radius: 6px;
    background: #eef1f4;
}}
QPlainTextEdit, QTextEdit {{ background: white; }}
QLabel[muted="true"] {{ color: {COLORS['muted']}; }}
QGroupBox[severity="error"] {{ border: 2px solid {COLORS['error']}; }}
QGroupBox[severity="notice"] {{ border: 2px solid #e0a800; }}
QGroupBox[severity="stale"] {{ border: 2px solid {COLORS['stale']}; }}
"""
