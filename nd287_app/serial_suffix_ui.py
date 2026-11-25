# -*- coding: utf-8 -*-
"""機番末尾の表（社内の表そのまま）。マスを押すと、その記号が機番の末尾に付く。
自動で決まった記号は最初から選ばれた状態で、押して選び直せる。複数は表の左から順に並べる。精度NGは -X の数字。"""
from PySide6 import QtCore, QtWidgets

from . import serial_suffix as S

ROWS = [("driver", False, "駆動装置[K]\nアンクランプ"), ("driver", True, "駆動装置[K]\nクランプ[G]"),
        ("normal", False, "正規モータ\nアンクランプ"), ("normal", True, "正規モータ\nクランプ[G]"),
        ("temp", False, "仮モータ[I]\nアンクランプ"), ("temp", True, "仮モータ[I]\nクランプ[G]")]
# 列: (種類, 多軸の軸, 見出し)
COLUMNS = [("semi", 1, "セミクロ"), ("tilt", 1, "傾斜単品"), ("rotary", 1, "回転単品"),
           ("multi", 1, "多軸M"), ("multi", 2, "多軸2"), ("multi", 3, "多軸3"), ("multi", 4, "多軸4"),
           ("fixed", 1, "対向固定"), ("move", 1, "対向移動"), ("repair_in", 1, "修理受入"), ("repair_out", 1, "修理後"),
           ("close", 1, "クローズ"), ("tilt_set", 1, "傾斜完組"), ("rotary_set", 1, "回転完組"),
           ("tilt_set_after", 1, "傾斜完組\n慣らし後"), ("rotary_set_after", 1, "回転完組\n慣らし後")]
ON_STYLE = "background:#f2c200;color:#000;font-weight:bold;"
NA_STYLE = "background:#ddd;color:#999;"


class SuffixPicker(QtWidgets.QWidget):
    """表＋精度NGの数字。changed(末尾の文字列) を出す。"""
    changed = QtCore.Signal(str)

    def __init__(self, parent=None, selection=None):
        super().__init__(parent)
        self.sel = selection or S.Selection()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tbl = QtWidgets.QTableWidget(len(ROWS), len(COLUMNS))
        self.tbl.setHorizontalHeaderLabels([c[2] for c in COLUMNS])
        self.tbl.setVerticalHeaderLabels([r[2] for r in ROWS])
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.tbl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.tbl.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.tbl.cellClicked.connect(self._clicked)
        lay.addWidget(self.tbl, 1)
        bottom = QtWidgets.QHBoxLayout()
        self.lbl = QtWidgets.QLabel("")
        bottom.addWidget(self.lbl, 1)
        bottom.addWidget(QtWidgets.QLabel("精度NG（測り直した回数）"))
        self.spin_ng = QtWidgets.QSpinBox()
        self.spin_ng.setRange(0, 99)
        self.spin_ng.setValue(self.sel.ng)
        self.spin_ng.valueChanged.connect(self._ng)
        bottom.addWidget(self.spin_ng)
        lay.addLayout(bottom)
        self._fill()
        self._refresh()

    def _fill(self):
        for r, (motor, clamp, _l) in enumerate(ROWS):
            for c, (kind, axis, _h) in enumerate(COLUMNS):
                try:
                    text = S.cell(motor, clamp, kind, axis)
                except ValueError:
                    text = ""
                item = QtWidgets.QTableWidgetItem(text if text else "（なし）")
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                if not text and kind != "semi":
                    item.setFlags(QtCore.Qt.NoItemFlags)           # 斜線（該当なし）は押せない
                self.tbl.setItem(r, c, item)

    def _refresh(self):
        for r, (motor, clamp, _l) in enumerate(ROWS):
            for c, (kind, axis, _h) in enumerate(COLUMNS):
                it = self.tbl.item(r, c)
                if it.flags() == QtCore.Qt.NoItemFlags:
                    it.setBackground(QtCore.Qt.lightGray)
                    continue
                on = self.sel.is_on(motor, clamp, kind, axis)
                it.setBackground(QtCore.Qt.yellow if on else QtCore.Qt.white)
                it.setForeground(QtCore.Qt.black)
        self.lbl.setText(f"末尾: {self.sel.suffix() or '（なし）'}")

    def _clicked(self, r, c):
        motor, clamp, _l = ROWS[r]
        kind, axis, _h = COLUMNS[c]
        try:
            self.sel.press(motor, clamp, kind, axis)
        except ValueError:
            return
        self._refresh()
        self.changed.emit(self.sel.suffix())

    def _ng(self, n):
        self.sel.ng = int(n)
        self._refresh()
        self.changed.emit(self.sel.suffix())

    def suffix(self):
        return self.sel.suffix()

    def set_auto(self, motor="normal", clamp=False, kinds=(), multi_axis=1, ng=0):
        """自動で決まった記号を、最初から選ばれた状態にする。"""
        self.sel = S.Selection(motor, clamp, kinds, multi_axis, ng)
        self.spin_ng.blockSignals(True)
        self.spin_ng.setValue(self.sel.ng)
        self.spin_ng.blockSignals(False)
        self._refresh()


class SuffixDialog(QtWidgets.QDialog):
    """機番末尾を選ぶ画面（保存のとき、機番の横から開く）。"""

    def __init__(self, parent=None, serial="", selection=None):
        super().__init__(parent)
        self.setWindowTitle("機番の末尾を選ぶ")
        lay = QtWidgets.QVBoxLayout(self)
        self.lbl_serial = QtWidgets.QLabel("")
        self.lbl_serial.setWordWrap(True)
        lay.addWidget(self.lbl_serial)
        self.picker = SuffixPicker(self, selection)
        self.picker.changed.connect(lambda *_: self._show())
        lay.addWidget(self.picker, 1)
        self.serial = str(serial)
        b = QtWidgets.QHBoxLayout()
        b.addStretch(1)
        ok = QtWidgets.QPushButton("この末尾にする")
        ok.clicked.connect(self.accept)
        ng = QtWidgets.QPushButton("やめる")
        ng.clicked.connect(self.reject)
        b.addWidget(ok)
        b.addWidget(ng)
        lay.addLayout(b)
        self.setMinimumSize(0, 0)
        self._show()

    def _show(self):
        self.lbl_serial.setText(f"保存する名前: {self.serial}{self.picker.suffix()}　（押したマスの記号が末尾に付きます。表の左から順に並びます）")

    def result_name(self):
        return f"{self.serial}{self.picker.suffix()}"


class SuffixPanel(QtWidgets.QGroupBox):
    """保存の画面の機番欄の横に、表そのものを常に見える形で置く部品。狭いときだけ、見出しのチェックで折りたためる。
    使い方: panel.picker.changed でファイル名の末尾を更新、panel.picker.set_auto(...) で自動の記号を選んでおく。"""

    def __init__(self, parent=None, selection=None, compact=True):
        super().__init__("機番の末尾（表を押して選ぶ）", parent)
        self.setCheckable(True)
        self.setChecked(True)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.picker = SuffixPicker(self, selection)
        if compact:
            f = self.picker.tbl.font()
            f.setPointSizeF(max(f.pointSizeF() * 0.85, 6.0))
            self.picker.tbl.setFont(f)
            self.picker.tbl.setMinimumHeight(190)
            self.picker.tbl.verticalHeader().setMinimumWidth(80)
        lay.addWidget(self.picker)
        self.toggled.connect(self.picker.setVisible)

    def suffix(self):
        return self.picker.suffix()
