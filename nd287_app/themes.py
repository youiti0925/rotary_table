# -*- coding: utf-8 -*-
"""画面の見た目（テーマ）と文字サイズ

Qtの Fusion スタイルをベースに、QSS（Qtのスタイルシート）で配色を変える。
追加ライブラリは不要で、exe化してもそのまま動く。文字サイズはアプリの
フォント（ポイント）で制御するので、QSSでは font-size を固定しない。

使い方:
    from .themes import apply_theme, apply_font
    apply_theme(app, "ライト・ブルー")
    apply_font(app, 11)
"""

from PySide6 import QtGui

# 候補テーマの一覧（設定のプルダウンに出す順）
THEME_NAMES = [
    "ライト・ブルー",
    "ネイビー・コーポレート",
    "ティール・フラット",
    "ダーク・スレート",
    "システム",
]

DEFAULT_THEME = "ネイビー・コーポレート"
DEFAULT_FONT_PT = 7


def _light(accent, accent_hover, accent_press, bg, panel, border,
           header_bg, header_fg, text, muted, primary_fg="#ffffff", pad="6px 12px"):
    sel_fg = "#ffffff"
    return f"""
QMainWindow, QDialog, QWidget {{ background: {bg}; color: {text}; }}
QLabel, QCheckBox, QRadioButton {{ background: transparent; color: {text}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QPlainTextEdit,
QTextEdit, QAbstractSpinBox {{
    background: {panel}; border: 1px solid {border}; border-radius: 6px;
    padding: {pad}; color: {text};
    selection-background-color: {accent}; selection-color: {sel_fg};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus, QAbstractSpinBox:focus {{ border: 1px solid {accent}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {panel}; border: 1px solid {border}; outline: none;
    selection-background-color: {accent}; selection-color: {sel_fg};
}}

QPushButton {{
    background: {panel}; border: 1px solid {border}; border-radius: 6px;
    padding: {pad}; color: {text};
}}
QPushButton:hover {{ background: {accent_hover}; border-color: {accent}; }}
QPushButton:pressed {{ background: {accent_press}; }}
QPushButton:disabled {{ color: {muted}; background: {bg}; border-color: {border}; }}

/* 主要アクション（取込開始・自動測定）は塗りつぶしで目立たせる */
QPushButton#primary {{
    background: {accent}; color: {primary_fg}; border: none; border-radius: 7px;
    padding: 9px 14px; font-weight: bold;
}}
QPushButton#primary:hover {{ background: {accent_press}; }}
QPushButton#primary:disabled {{ background: {border}; color: {muted}; }}

QGroupBox {{
    background: {panel}; border: 1px solid {border}; border-radius: 8px;
    margin-top: 12px; padding: 8px 8px 6px 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 10px; padding: 1px 7px; color: {accent}; font-weight: bold;
}}

QToolBar {{ background: {header_bg}; border: none; spacing: 4px; padding: 5px 8px; }}
QToolBar::separator {{ background: {border}; width: 1px; margin: 4px 6px; }}
QToolBar QPushButton {{ background: transparent; border: 1px solid transparent;
    border-radius: 6px; padding: 6px 12px; color: {header_fg}; }}
QToolBar QPushButton:hover {{ background: {accent_hover}; border-color: {accent};
    color: {text}; }}
QToolBar QPushButton:pressed {{ background: {accent_press}; }}
QToolBar QLabel {{ color: {header_fg}; }}
QToolBar QComboBox {{ color: {text}; }}

QTabWidget::pane {{ border: 1px solid {border}; border-radius: 8px; top: -1px;
    background: {panel}; }}
QTabBar::tab {{ background: {bg}; color: {muted}; padding: 7px 18px;
    margin-right: 3px; border-top-left-radius: 7px; border-top-right-radius: 7px; }}
QTabBar::tab:selected {{ background: {panel}; color: {accent}; font-weight: bold;
    border: 1px solid {border}; border-bottom: 2px solid {accent}; }}

QTableWidget, QTableView {{
    background: {panel}; alternate-background-color: {bg};
    gridline-color: {border}; border: 1px solid {border}; border-radius: 8px;
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background: {accent}; color: {sel_fg}; }}
QHeaderView::section {{
    background: {header_bg}; color: {header_fg}; padding: 6px; border: none;
    border-right: 1px solid {border}; border-bottom: 1px solid {border};
    font-weight: bold;
}}
QTableCornerButton::section {{ background: {header_bg}; border: none; }}

QStatusBar {{ background: {header_bg}; color: {header_fg}; }}
QStatusBar QPushButton {{ padding: 3px 10px; }}
QToolTip {{ background: {text}; color: {bg}; border: none; padding: 5px 7px; }}
QMenu {{ background: {panel}; border: 1px solid {border}; }}
QMenu::item:selected {{ background: {accent}; color: {sel_fg}; }}
QScrollArea {{ background: transparent; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 6px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {accent}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 6px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
"""


THEMES = {
    "システム": "",
    "ライト・ブルー": _light(
        accent="#2563eb", accent_hover="#eef2ff", accent_press="#1d4ed8",
        bg="#eef1f6", panel="#ffffff", border="#d4dae3",
        header_bg="#ffffff", header_fg="#334155", text="#1f2933", muted="#94a3b8"),
    "ネイビー・コーポレート": _light(
        accent="#1e3a8a", accent_hover="#e6ebf5", accent_press="#172e6e",
        bg="#e9edf2", panel="#ffffff", border="#c8d0db",
        header_bg="#1e3a8a", header_fg="#ffffff", text="#1f2933", muted="#8a97a8"),
    "ティール・フラット": _light(
        accent="#0d9488", accent_hover="#e3f4f1", accent_press="#0b7d72",
        bg="#eef6f4", panel="#ffffff", border="#c5d8d4",
        header_bg="#ffffff", header_fg="#14302b", text="#14302b", muted="#7d9b95",
        pad="8px 14px"),
    "ダーク・スレート": _light(
        accent="#38bdf8", accent_hover="#1e3a52", accent_press="#0ea5e9",
        bg="#0f172a", panel="#1e293b", border="#334155",
        header_bg="#172033", header_fg="#cbd5e1", text="#e2e8f0", muted="#7c8aa0",
        primary_fg="#0b1220"),
}


def apply_theme(app, name):
    """アプリ全体にテーマを適用する。未知の名前は「システム」扱い。"""
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    app.setStyleSheet(THEMES.get(name, ""))
    return name in THEMES


def apply_font(app, point_size):
    """アプリ全体の基準文字サイズ[pt]を変える（フォント書体はそのまま）。"""
    try:
        pt = int(point_size)
    except (TypeError, ValueError):
        pt = DEFAULT_FONT_PT
    pt = max(7, min(pt, 22))
    font = app.font()
    font.setPointSize(pt)
    app.setFont(font)
    return pt
