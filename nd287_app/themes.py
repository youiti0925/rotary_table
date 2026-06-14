# -*- coding: utf-8 -*-
"""画面の見た目（テーマ）

Qtの Fusion スタイルをベースに、QSS（Qtのスタイルシート）で配色を変える。
追加ライブラリは不要で、exe化してもそのまま動く。

使い方:
    from .themes import apply_theme
    apply_theme(app, "ライト・ブルー")

THEME_NAMES に候補名が並ぶ。"システム" は QSS なし（OS既定の見た目）。
"""

# 候補テーマの一覧（設定のプルダウンに出す順）
THEME_NAMES = [
    "システム",
    "ライト・ブルー",
    "ネイビー・コーポレート",
    "ティール・フラット",
    "ダーク・スレート",
]

DEFAULT_THEME = "システム"


def _light(accent, accent_hover, accent_press, bg, panel, border,
           header_bg, header_fg, text, muted, pad="5px 12px", font_px=13,
           header_row_dark=False):
    sel_fg = "#ffffff"
    head_border = header_bg if header_row_dark else border
    return f"""
* {{ font-size: {font_px}px; }}
QMainWindow, QDialog, QWidget {{ background: {bg}; color: {text}; }}
QLabel, QCheckBox, QRadioButton {{ background: transparent; color: {text}; }}
QLabel[muted="true"] {{ color: {muted}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QPlainTextEdit,
QTextEdit, QAbstractSpinBox {{
    background: {panel}; border: 1px solid {border}; border-radius: 6px;
    padding: {pad}; color: {text};
    selection-background-color: {accent}; selection-color: {sel_fg};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus, QAbstractSpinBox:focus {{ border: 1px solid {accent}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {panel}; border: 1px solid {border};
    selection-background-color: {accent}; selection-color: {sel_fg};
}}

QPushButton {{
    background: {panel}; border: 1px solid {border}; border-radius: 6px;
    padding: {pad}; color: {text}; min-height: 16px;
}}
QPushButton:hover {{ background: {accent_hover}; border-color: {accent}; }}
QPushButton:pressed {{ background: {accent_press}; }}
QPushButton:disabled {{ color: {muted}; background: {bg}; border-color: {border}; }}
QPushButton:default {{ border: 1px solid {accent}; }}

QTabWidget::pane {{ border: 1px solid {border}; border-radius: 6px; top: -1px; }}
QTabBar::tab {{
    background: {bg}; color: {muted}; padding: 6px 16px; margin-right: 2px;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}}
QTabBar::tab:selected {{ background: {panel}; color: {accent}; font-weight: bold;
    border: 1px solid {border}; border-bottom: 2px solid {accent}; }}

QTableWidget, QTableView {{
    background: {panel}; alternate-background-color: {bg};
    gridline-color: {border}; border: 1px solid {border}; border-radius: 6px;
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background: {accent}; color: {sel_fg}; }}
QHeaderView::section {{
    background: {header_bg}; color: {header_fg}; padding: 5px;
    border: none; border-right: 1px solid {head_border};
    border-bottom: 1px solid {head_border}; font-weight: bold;
}}
QTableCornerButton::section {{ background: {header_bg}; border: none; }}

QGroupBox {{ border: 1px solid {border}; border-radius: 6px; margin-top: 8px;
    padding: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px;
    color: {accent}; font-weight: bold; }}

QStatusBar {{ background: {header_bg}; color: {header_fg if header_row_dark else muted}; }}
QToolTip {{ background: {text}; color: {bg}; border: none; padding: 4px 6px; }}
QMenu {{ background: {panel}; border: 1px solid {border}; }}
QMenu::item:selected {{ background: {accent}; color: {sel_fg}; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 5px;
    min-height: 26px; }}
QScrollBar::handle:vertical:hover {{ background: {accent}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 5px;
    min-width: 26px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
"""


def _dark():
    return _light(
        accent="#38bdf8", accent_hover="#1e3a52", accent_press="#0e2233",
        bg="#0f172a", panel="#1e293b", border="#334155",
        header_bg="#1e293b", header_fg="#93c5fd", text="#e2e8f0",
        muted="#94a3b8", header_row_dark=True,
    )


THEMES = {
    "システム": "",
    "ライト・ブルー": _light(
        accent="#2563eb", accent_hover="#eef2ff", accent_press="#dbe4ff",
        bg="#f5f7fa", panel="#ffffff", border="#cbd5e1",
        header_bg="#eef2f7", header_fg="#334155", text="#1f2933",
        muted="#94a3b8"),
    "ネイビー・コーポレート": _light(
        accent="#1e3a8a", accent_hover="#e6ebf5", accent_press="#d4ddf0",
        bg="#eef1f5", panel="#ffffff", border="#c3ccd6",
        header_bg="#1e3a8a", header_fg="#ffffff", text="#1f2933",
        muted="#8a97a8", header_row_dark=True),
    "ティール・フラット": _light(
        accent="#0d9488", accent_hover="#e3f4f1", accent_press="#cfeae5",
        bg="#f4faf9", panel="#ffffff", border="#c5d8d4",
        header_bg="#e2f3ef", header_fg="#14302b", text="#14302b",
        muted="#7d9b95", pad="7px 16px", font_px=14),
    "ダーク・スレート": _dark(),
}


def apply_theme(app, name):
    """アプリ全体にテーマを適用する。未知の名前は「システム」扱い。"""
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    app.setStyleSheet(THEMES.get(name, ""))
    return name in THEMES
