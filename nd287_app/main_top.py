# -*- coding: utf-8 -*-
"""測定の画面（最初の画面）の上の2段の部品。

2026-11-25 利用者: 「上のあたりは後から無理やり足した感じで見づらい」→ 案A に決定
（見本: /mnt/project-files/設計/メイン画面_上側の見本/）。

- 1段目: ボタンを仕事の順のかたまり（測る／結果を残す／データを見る／機械）に分け、小さい見出しを付ける。
- 2段目: いまどこ（StepStrip）・次にやること・ボタン1つ・つながっている相手（Chip）。

ここは見た目の部品だけ。押したときの動きは gui.MainWindow が今までどおり持つ。
色は themes.py の QSS（#topBar / #flowBar）で決める。
"""

from PySide6 import QtCore, QtGui, QtWidgets

GOOD = "#16a34a"      # 済んだ・つながっている
BAD = "#dc2626"       # 直すことがある
IDLE = "#94a3b8"      # まだ


def group(caption, widgets):
    """見出し（小さい字）の下にボタンを横に並べた、ひとかたまり。"""
    box = QtWidgets.QWidget()
    v = QtWidgets.QVBoxLayout(box)
    v.setContentsMargins(6, 0, 6, 0)
    v.setSpacing(1)
    cap = QtWidgets.QLabel(caption or " ")
    cap.setObjectName("topCap")
    v.addWidget(cap)
    row = QtWidgets.QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    for w in widgets:
        row.addWidget(w, 0, QtCore.Qt.AlignVCenter)
    v.addLayout(row)
    return box


def separator():
    line = QtWidgets.QFrame()
    line.setObjectName("topSep")
    line.setFixedWidth(1)
    return line


def set_now(button, on):
    """「今やる所」のボタンに印を付ける（色は QSS の [now="true"]）。"""
    on = bool(on)
    button.setProperty("now", on)
    st = button.style()
    st.unpolish(button)
    st.polish(button)
    button.update()


def _dot_icon(color, filled, size):
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    c = QtGui.QColor(color)
    p.setPen(QtGui.QPen(c, max(1.5, size / 7)))
    p.setBrush(c if filled else QtCore.Qt.NoBrush)
    m = size * 0.2
    p.drawEllipse(QtCore.QRectF(m, m, size - 2 * m, size - 2 * m))
    p.end()
    return QtGui.QIcon(pm)


class Chip(QtWidgets.QPushButton):
    """「機械 つながっています」のような、相手とつながっているかの小さい表示（押せる）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chip")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._state = None

    def set_state(self, text, ok, bad=False):
        state = (text, bool(ok), bool(bad))
        if state == self._state:
            return
        self._state = state
        size = max(10, self.fontMetrics().height() - 2)
        self.setIconSize(QtCore.QSize(size, size))
        self.setIcon(_dot_icon(GOOD if ok else (BAD if bad else IDLE), ok or bad, size))
        self.setText(text)


class StepStrip(QtWidgets.QWidget):
    """① ─ ② ─ ③ … の「いまどこ」。済んだ所は緑の✓、今の所は濃い色、先は灰色。"""

    def __init__(self, names, parent=None):
        super().__init__(parent)
        self._names = [self._plain(n) for n in names]
        self._stage = 0
        self._compact = False               # True: 名前は今の所だけ（幅が足りないとき）
        self._accent = "#1e3a8a"            # 今の所の色（themes.py の QSS が qproperty-accent で入れる）
        self._on_accent = "#ffffff"
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

    @staticmethod
    def _plain(name):
        # "① 機械の準備" → "機械の準備"（番号は丸の中に描く）
        parts = str(name).split(" ", 1)
        return parts[1] if len(parts) == 2 and len(parts[0]) == 1 else str(name)

    def stage(self):
        return self._stage

    def set_stage(self, stage):
        stage = int(stage)
        if stage != self._stage:
            self._stage = stage
            self.updateGeometry()
            self.update()

    def _get_accent(self):
        return self._accent

    def _set_accent(self, v):
        self._accent = str(v)
        self.update()

    def _get_on_accent(self):
        return self._on_accent

    def _set_on_accent(self, v):
        self._on_accent = str(v)
        self.update()

    accent = QtCore.Property(str, _get_accent, _set_accent)
    onAccent = QtCore.Property(str, _get_on_accent, _set_on_accent)

    def set_compact(self, on):
        on = bool(on)
        if on != self._compact:
            self._compact = on
            self.updateGeometry()
            self.update()

    def _shown(self, i):
        return (not self._compact) or i == self._stage

    def full_width(self):
        keep, self._compact = self._compact, False
        try:
            return self.sizeHint().width()
        finally:
            self._compact = keep

    def _metrics(self):
        fm = self.fontMetrics()
        d = fm.height() + 4                 # 丸の直径
        gap = max(4, fm.height() // 3)      # 丸と名前の間
        bar = max(10, fm.height())          # つなぎの線
        return fm, d, gap, bar

    def sizeHint(self):
        fm, d, gap, bar = self._metrics()
        bold = QtGui.QFont(self.font())
        bold.setBold(True)
        bfm = QtGui.QFontMetrics(bold)
        w = sum(d + ((gap + bfm.horizontalAdvance(n)) if self._shown(i) else 0)
                for i, n in enumerate(self._names))
        w += (len(self._names) - 1) * (bar + 2 * gap)
        return QtCore.QSize(w + 4, d + 4)

    minimumSizeHint = sizeHint

    def paintEvent(self, _event):
        fm, d, gap, bar = self._metrics()
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        accent = QtGui.QColor(self._accent)
        on_accent = QtGui.QColor(self._on_accent)
        good, idle = QtGui.QColor(GOOD), QtGui.QColor(IDLE)
        bold = QtGui.QFont(self.font())
        bold.setBold(True)
        bfm = QtGui.QFontMetrics(bold)
        x = 2.0
        cy = self.height() / 2
        for i, name in enumerate(self._names):
            done, cur = i < self._stage, i == self._stage
            color = good if done else (accent if cur else idle)
            circle = QtCore.QRectF(x, cy - d / 2, d, d)
            p.setPen(QtGui.QPen(color, 1.5))
            p.setBrush(color if (done or cur) else QtCore.Qt.NoBrush)
            p.drawEllipse(circle)
            p.setFont(bold)
            p.setPen(QtGui.QColor("#ffffff") if done else (on_accent if cur else idle))
            p.drawText(circle, QtCore.Qt.AlignCenter, "✓" if done else str(i + 1))
            x += d
            if self._shown(i):
                x += gap
                p.setFont(bold if cur else self.font())
                p.setPen(color)
                w = bfm.horizontalAdvance(name)
                p.drawText(QtCore.QRectF(x, 0, w, self.height()),
                           QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, name)
                x += w
            if i < len(self._names) - 1:
                p.setPen(QtGui.QPen(good if done else idle, 2))
                p.drawLine(QtCore.QPointF(x + gap, cy), QtCore.QPointF(x + gap + bar, cy))
                x += bar + 2 * gap
        p.end()
