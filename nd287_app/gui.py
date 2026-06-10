# -*- coding: utf-8 -*-
"""測定画面（PySide6 + pyqtgraph）

操作の流れ:
    測定開始 → 画面上部のガイドに従いテーブルを割り出して静止 → 「取込」で1点取得
    → ホイールCW一周 → ホイールCCW一周 → ウォームCW → ウォームCCW → 結果表
"""

import numpy as np
from PySide6 import QtCore, QtWidgets
import pyqtgraph as pg

from .analysis import deviation_sec, summarize
from .export import result_rows, save_csv
from .nd287 import deg_to_dms
from .sequence import Sequence, SERIES_LABELS

CURVE_STYLES = {
    "wheel_cw": dict(pen=pg.mkPen("#1f77b4", width=2), symbol="o", symbolSize=5),
    "wheel_ccw": dict(pen=pg.mkPen("#d62728", width=2), symbol="o", symbolSize=5),
    "worm_cw": dict(
        pen=pg.mkPen("#2ca02c", width=2, style=QtCore.Qt.DashLine), symbol="t", symbolSize=6
    ),
    "worm_ccw": dict(
        pen=pg.mkPen("#ff7f0e", width=2, style=QtCore.Qt.DashLine), symbol="t", symbolSize=6
    ),
}


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, device, wheel_pitch, worm_pitch, worm_range, worm_start=0.0):
        super().__init__()
        self.setWindowTitle("ND287 分割測定")
        self.resize(1100, 700)
        self.dev = device
        self.seq = None

        # --- 上段: 測定条件と操作ボタン ---
        top = QtWidgets.QHBoxLayout()
        self.e_wheel = QtWidgets.QDoubleSpinBox()
        self.e_wheel.setRange(0.001, 180.0)
        self.e_wheel.setValue(wheel_pitch)
        self.e_wheel.setSuffix(" °/pt")
        self.e_worm = QtWidgets.QDoubleSpinBox()
        self.e_worm.setDecimals(4)
        self.e_worm.setRange(0.0001, 90.0)
        self.e_worm.setValue(worm_pitch)
        self.e_worm.setSuffix(" °/pt")
        self.e_range = QtWidgets.QDoubleSpinBox()
        self.e_range.setDecimals(4)
        self.e_range.setRange(0.001, 360.0)
        self.e_range.setValue(worm_range)
        self.e_range.setSuffix(" °")
        self.e_start = QtWidgets.QDoubleSpinBox()
        self.e_start.setDecimals(4)
        self.e_start.setRange(0.0, 360.0)
        self.e_start.setValue(worm_start)
        self.e_start.setSuffix(" °")

        b_start = QtWidgets.QPushButton("測定開始")
        self.b_take = QtWidgets.QPushButton("取込")
        self.b_take.setEnabled(False)
        self.b_take.setStyleSheet("font-size:18px; padding:6px 24px;")
        self.b_undo = QtWidgets.QPushButton("1点戻る")
        self.b_undo.setEnabled(False)
        self.b_save = QtWidgets.QPushButton("CSV保存")
        self.b_save.setEnabled(False)
        b_start.clicked.connect(self.start)
        self.b_take.clicked.connect(self.take)
        self.b_undo.clicked.connect(self.undo)
        self.b_save.clicked.connect(self.save)

        for widget, label in [
            (self.e_wheel, "ホイール刻み"),
            (self.e_worm, "ウォーム刻み"),
            (self.e_range, "ウォーム範囲"),
            (self.e_start, "ウォーム開始"),
        ]:
            top.addWidget(QtWidgets.QLabel(label))
            top.addWidget(widget)
        top.addStretch(1)
        top.addWidget(b_start)
        top.addWidget(self.b_take)
        top.addWidget(self.b_undo)
        top.addWidget(self.b_save)

        # --- 中段: ガイドと受信値 ---
        self.guide = QtWidgets.QLabel("―")
        self.guide.setStyleSheet("font-size:22px; font-family:monospace; padding:4px;")
        self.live = QtWidgets.QLabel("")
        self.live.setStyleSheet("font-size:15px; color:#666; padding:2px;")

        # --- グラフ ---
        self.plot = pg.PlotWidget()
        self.plot.addLegend(offset=(10, 10))
        self.plot.setLabel("bottom", "指令角度", units="°")
        self.plot.setLabel("left", "偏差", units='"')
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.curves = {
            key: self.plot.plot(name=SERIES_LABELS[key], **style)
            for key, style in CURVE_STYLES.items()
        }

        # --- 結果表 ---
        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["項目", "値"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMaximumHeight(230)

        container = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(container)
        v.addLayout(top)
        v.addWidget(self.guide)
        v.addWidget(self.live)
        v.addWidget(self.plot, 1)
        v.addWidget(self.table)
        self.setCentralWidget(container)

        try:
            self.dev.open()
            self.statusBar().showMessage(
                "ダミーモード" if self.dev.dummy else f"接続: {self.dev.port}"
            )
        except Exception as e:
            self.statusBar().showMessage(f"接続失敗: {e}")

    # ----- 操作 -----

    def start(self):
        self.seq = Sequence(
            self.e_wheel.value(),
            self.e_worm.value(),
            self.e_range.value(),
            self.e_start.value(),
        )
        for curve in self.curves.values():
            curve.setData([], [])
        self.table.setRowCount(0)
        self.b_take.setEnabled(True)
        self.b_undo.setEnabled(False)
        self.b_save.setEnabled(False)
        self.show_guide()

    def show_guide(self):
        cur = self.seq.current()
        if cur is None:
            self.guide.setText("測定完了")
            self.b_take.setEnabled(False)
            return
        key, target, _ = cur
        self.guide.setText(
            f"[{SERIES_LABELS[key]}]  {deg_to_dms(target)} へ割り出して静止 → 取込"
            f"   ({self.seq.idx + 1}/{len(self.seq)})"
        )

    def take(self):
        cur = self.seq.current()
        if cur is None:
            return
        key, target, direction = cur
        self.dev.prepare_point(target, direction)
        try:
            angle = self.dev.read_angle()
        except Exception as e:
            self.statusBar().showMessage(f"読取エラー: {e}")
            return
        if angle is None:
            self.statusBar().showMessage("応答パース不可（本体出力設定を確認）")
            return
        self.live.setText(f"受信: {deg_to_dms(angle)}")
        self.seq.record(angle)
        self.b_undo.setEnabled(True)
        self.redraw()
        if self.seq.done():
            self.finish()
        else:
            self.show_guide()

    def undo(self):
        if self.seq and self.seq.undo():
            self.table.setRowCount(0)
            self.b_save.setEnabled(False)
            self.b_take.setEnabled(True)
            self.b_undo.setEnabled(self.seq.idx > 0)
            self.redraw()
            self.show_guide()

    def redraw(self):
        for key, curve in self.curves.items():
            targets, measured = self.seq.data[key]
            if targets:
                curve.setData(np.asarray(targets), deviation_sec(targets, measured))
            else:
                curve.setData([], [])

    def finish(self):
        self.guide.setText("測定完了")
        self.b_take.setEnabled(False)
        self.b_save.setEnabled(True)
        summary, _ = summarize(self.seq.data)
        rows = result_rows(summary)
        self.table.setRowCount(len(rows))
        for i, (item, value) in enumerate(rows):
            self.table.setItem(i, 0, QtWidgets.QTableWidgetItem(item))
            self.table.setItem(i, 1, QtWidgets.QTableWidgetItem(value))

    def save(self):
        if not self.seq or not self.seq.done():
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "測定結果を保存", "分割測定結果.csv", "CSV (*.csv)"
        )
        if not path:
            return
        summary, _ = summarize(self.seq.data)
        try:
            save_csv(path, self.seq.data, summary)
            self.statusBar().showMessage(f"保存しました: {path}")
        except Exception as e:
            self.statusBar().showMessage(f"保存失敗: {e}")

    def closeEvent(self, event):
        self.dev.close()
        super().closeEvent(event)


def run(device, wheel_pitch, worm_pitch, worm_range, worm_start=0.0):
    import sys

    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow(device, wheel_pitch, worm_pitch, worm_range, worm_start)
    win.show()
    sys.exit(app.exec())
