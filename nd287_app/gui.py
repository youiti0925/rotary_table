# -*- coding: utf-8 -*-
"""測定画面（PySide6 + pyqtgraph）

操作の流れ:
    1. 型式・機番・日付・名前・測定温度を入力し、等分数を確認して「取込開始」
       （全部そろっていないと取込は始まらない）
    2. データ受け取り状態になる。テーブルを割り出して静止し、ND287のPRINTキー
       （またはX41トリガ）で値を送ると、順番どおりに箱へ入る。「手動取込」でも可
    3. 箱が全部埋まると、系列ごとの精度PP・単一誤差・隣接誤差・傾きと、
       バックラッシMIN/MAX・温度規格による合否・真の最大最小が自動表示される
    4. 「セーブ」で <保存先>/<型式の系列>/<機番>.csv に保存（例 RWE-200 → RWE/12345.csv)
    5. 「ロード」で過去の測定を読み戻してグラフ・結果を再表示
"""

from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from .analysis import deviation_sec, summarize
from .export import (
    build_save_path,
    judgement_texts,
    load_csv,
    misc_rows,
    save_csv,
)
from .nd287 import ND287Device, deg_to_dms
from .sequence import Sequence, SERIES_LABELS
from .settings import resolve_save_root, save_settings

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

BAUDRATES = ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"]

META_KEYS = {
    "wheel_pitch": "ホイール刻み[°]",
    "worm_pitch": "ウォーム刻み[°]",
    "worm_range": "ウォーム範囲[°]",
    "worm_start": "ウォーム開始[°]",
    "date": "日付",
    "operator": "名前",
    "temperature": "測定温度[°C]",
    "comment": "コメント",
    "blcorr": "バックラッシ補正[秒]",
}

SERIES_METRIC_HEADERS = ["系列", "精度PP", "単一誤差", "隣接誤差", "傾き"]


class SettingsDialog(QtWidgets.QDialog):
    """通信（ポート・ボーレート・パリティ）と保存先の設定"""

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle("設定")
        form = QtWidgets.QFormLayout(self)

        self.e_port = QtWidgets.QLineEdit(str(settings.get("port", "auto")))
        self.e_port.setToolTip("auto = 自動検出。COM3 のように明示指定も可")
        self.e_baud = QtWidgets.QComboBox()
        self.e_baud.addItems(BAUDRATES)
        self.e_baud.setEditable(True)
        self.e_baud.setCurrentText(str(settings.get("baudrate", 9600)))
        self.e_parity = QtWidgets.QComboBox()
        self.e_parity.addItems(["E", "N", "O"])
        self.e_parity.setCurrentText(str(settings.get("parity", "E")))

        root_row = QtWidgets.QHBoxLayout()
        self.e_root = QtWidgets.QLineEdit(str(settings.get("save_root", "測定データ")))
        b_browse = QtWidgets.QPushButton("参照...")
        b_browse.clicked.connect(self.browse_root)
        root_row.addWidget(self.e_root)
        root_row.addWidget(b_browse)

        form.addRow("ポート", self.e_port)
        form.addRow("ボーレート", self.e_baud)
        form.addRow("パリティ", self.e_parity)
        form.addRow("保存先フォルダ", root_row)

        note = QtWidgets.QLabel(
            "温度別の合否規格（ホイール/ウォーム/総合）は settings.json の judgement_spec で編集"
        )
        note.setStyleSheet("color:#666;")
        form.addRow(note)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def browse_root(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "保存先フォルダを選択", self.e_root.text()
        )
        if path:
            self.e_root.setText(path)

    def values(self):
        try:
            baud = int(self.e_baud.currentText())
        except ValueError:
            baud = 9600
        return dict(
            port=self.e_port.text().strip() or "auto",
            baudrate=baud,
            parity=self.e_parity.currentText(),
            save_root=self.e_root.text().strip() or "測定データ",
        )


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, device, wheel_pitch, worm_pitch, worm_range, worm_start, settings):
        super().__init__()
        self.setWindowTitle("ND287 分割測定")
        self.resize(1100, 740)
        self.dev = device
        self.settings = settings
        self.seq = None    # 取込中のシーケンス（ロード表示時は None）
        self.data = None   # グラフ・結果・セーブの対象データ

        # --- 1段目: 測定条件と取込操作 ---
        row1 = QtWidgets.QHBoxLayout()
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

        b_start = QtWidgets.QPushButton("取込開始")
        b_start.setStyleSheet("font-size:16px; padding:4px 18px;")
        self.b_take = QtWidgets.QPushButton("手動取込")
        self.b_take.setEnabled(False)
        self.b_undo = QtWidgets.QPushButton("1点戻る")
        self.b_undo.setEnabled(False)
        b_start.clicked.connect(self.start)
        self.b_take.clicked.connect(self.take_manual)
        self.b_undo.clicked.connect(self.undo)

        for widget, label in [
            (self.e_wheel, "ホイール刻み"),
            (self.e_worm, "ウォーム刻み"),
            (self.e_range, "ウォーム範囲"),
            (self.e_start, "ウォーム開始"),
        ]:
            row1.addWidget(QtWidgets.QLabel(label))
            row1.addWidget(widget)
        row1.addStretch(1)
        row1.addWidget(b_start)
        row1.addWidget(self.b_take)
        row1.addWidget(self.b_undo)

        # --- 2段目: 測定情報（取込開始の必須項目）とファイル操作 ---
        row2 = QtWidgets.QHBoxLayout()
        self.e_model = QtWidgets.QLineEdit()
        self.e_model.setPlaceholderText("例: RWE-200")
        self.e_model.setMaximumWidth(130)
        self.e_machine = QtWidgets.QLineEdit()
        self.e_machine.setPlaceholderText("例: 12345")
        self.e_machine.setMaximumWidth(110)
        self.e_date = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        self.e_date.setDisplayFormat("yyyy-MM-dd")
        self.e_date.setCalendarPopup(True)
        self.e_operator = QtWidgets.QLineEdit()
        self.e_operator.setPlaceholderText("測定者")
        self.e_operator.setMaximumWidth(110)
        self.e_temp = QtWidgets.QLineEdit()
        self.e_temp.setPlaceholderText("例: 23.5")
        self.e_temp.setMaximumWidth(70)
        self.e_temp.setValidator(QtGui.QDoubleValidator(-20.0, 60.0, 2))
        self.b_save = QtWidgets.QPushButton("セーブ")
        self.b_save.setEnabled(False)
        b_load = QtWidgets.QPushButton("ロード")
        b_settings = QtWidgets.QPushButton("設定")
        self.b_save.clicked.connect(self.save)
        b_load.clicked.connect(self.load)
        b_settings.clicked.connect(self.open_settings)
        for widget, label in [
            (self.e_model, "型式"),
            (self.e_machine, "機番"),
            (self.e_date, "日付"),
            (self.e_operator, "名前"),
            (self.e_temp, "測定温度[°C]"),
        ]:
            row2.addWidget(QtWidgets.QLabel(label))
            row2.addWidget(widget)
        row2.addStretch(1)
        row2.addWidget(self.b_save)
        row2.addWidget(b_load)
        row2.addWidget(b_settings)

        # --- 3段目: コメントとバックラッシ手動補正 ---
        row3 = QtWidgets.QHBoxLayout()
        self.e_comment = QtWidgets.QLineEdit()
        self.e_comment.setPlaceholderText("コメント（任意。セーブ時に保存される）")
        self.e_blcorr = QtWidgets.QDoubleSpinBox()
        self.e_blcorr.setRange(-999.0, 999.0)
        self.e_blcorr.setDecimals(2)
        self.e_blcorr.setSuffix(' "')
        self.e_blcorr.setToolTip(
            "実際のメカ的な隙間が測定結果と差がある場合の補正値。\n"
            "測定結果に対して何秒多いか（+）少ないか（−）を入力して補正適用"
        )
        self.b_corr = QtWidgets.QPushButton("補正適用")
        self.b_corr.setEnabled(False)
        self.b_corr.clicked.connect(self.apply_correction)
        self.applied_blcorr = 0.0  # 補正適用ボタンで確定した補正値
        row3.addWidget(QtWidgets.QLabel("コメント"))
        row3.addWidget(self.e_comment, 1)
        row3.addWidget(QtWidgets.QLabel("バックラッシ補正"))
        row3.addWidget(self.e_blcorr)
        row3.addWidget(self.b_corr)

        # --- ガイドと受信値 ---
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

        # --- 結果表（左: 系列ごとの指標 / 右: バックラッシ・判定・真の最大最小） ---
        self.table_series = QtWidgets.QTableWidget(0, len(SERIES_METRIC_HEADERS))
        self.table_series.setHorizontalHeaderLabels(SERIES_METRIC_HEADERS)
        self.table_series.horizontalHeader().setStretchLastSection(True)
        self.table_series.verticalHeader().setVisible(False)
        self.table_series.setMaximumHeight(190)
        self.table_misc = QtWidgets.QTableWidget(0, 2)
        self.table_misc.setHorizontalHeaderLabels(["項目", "値"])
        self.table_misc.horizontalHeader().setStretchLastSection(True)
        self.table_misc.verticalHeader().setVisible(False)
        self.table_misc.setMaximumHeight(190)
        tables = QtWidgets.QHBoxLayout()
        tables.addWidget(self.table_series, 5)
        tables.addWidget(self.table_misc, 4)

        container = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(container)
        v.addLayout(row1)
        v.addLayout(row2)
        v.addLayout(row3)
        v.addWidget(self.guide)
        v.addWidget(self.live)
        v.addWidget(self.plot, 1)
        v.addLayout(tables)
        self.setCentralWidget(container)

        self.b_conn = QtWidgets.QPushButton("再接続")
        self.b_conn.clicked.connect(self.connect_device)
        self.statusBar().addPermanentWidget(self.b_conn)
        # ウィンドウ表示後に接続（自動検出は数秒かかるため、先に画面を出す）
        QtCore.QTimer.singleShot(100, self.connect_device)

        # ND287側から送られてくるデータの受信ループ
        self.rx_timer = QtCore.QTimer(self)
        self.rx_timer.timeout.connect(self.poll_serial)
        self.rx_timer.start(200)

    # ----- 接続 -----

    def connect_device(self):
        if self.dev.dummy:
            self.dev.open()
            self.statusBar().showMessage("ダミーモード（実機なし）")
            return
        self.statusBar().showMessage("ND287を検索中...（数秒かかります）")
        self.b_conn.setEnabled(False)
        QtWidgets.QApplication.processEvents()
        try:
            self.dev.close()
            self.dev.open()
            self.statusBar().showMessage(f"接続: {self.dev.port}")
        except Exception as e:
            self.statusBar().showMessage(f"接続失敗: {e}")
        finally:
            self.b_conn.setEnabled(True)

    def open_settings(self):
        dlg = SettingsDialog(self, self.settings)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        self.settings.update(dlg.values())
        try:
            save_settings(self.settings)
        except Exception as e:
            self.statusBar().showMessage(f"設定の保存に失敗: {e}")
            return
        if not self.dev.dummy:
            self.dev.close()
            self.dev = ND287Device(
                self.settings["port"], self.settings["baudrate"], self.settings["parity"]
            )
            self.connect_device()

    # ----- 取込 -----

    def parse_temp(self):
        """測定温度欄の値[°C]。未入力・不正なら None"""
        try:
            return float(self.e_temp.text().strip())
        except ValueError:
            return None

    def missing_required_fields(self):
        """取込開始に必要な未入力項目のリスト"""
        missing = []
        if not self.e_model.text().strip():
            missing.append("型式")
        if not self.e_machine.text().strip():
            missing.append("機番")
        if not self.e_operator.text().strip():
            missing.append("名前")
        if self.parse_temp() is None:
            missing.append("測定温度")
        return missing

    def start(self):
        missing = self.missing_required_fields()
        if missing:
            QtWidgets.QMessageBox.warning(
                self,
                "取込開始",
                "次の項目を入力してから取込を開始してください:\n  " + "、".join(missing),
            )
            return
        self.seq = Sequence(
            self.e_wheel.value(),
            self.e_worm.value(),
            self.e_range.value(),
            self.e_start.value(),
        )
        self.data = self.seq.data
        self.dev.flush_input()  # 取込開始前に届いていた古いデータは捨てる
        for curve in self.curves.values():
            curve.setData([], [])
        self.table_series.setRowCount(0)
        self.table_misc.setRowCount(0)
        self.applied_blcorr = 0.0
        self.e_blcorr.setValue(0.0)
        self.b_corr.setEnabled(False)
        self.b_take.setEnabled(True)
        self.b_undo.setEnabled(False)
        self.b_save.setEnabled(False)
        self.show_guide()

    def show_guide(self):
        cur = self.seq.current()
        if cur is None:
            return
        key, target, _ = cur
        self.guide.setText(
            f"[{SERIES_LABELS[key]}]  {deg_to_dms(target)} へ割り出して静止"
            f" → ND287のPRINT（または手動取込）   ({self.seq.idx + 1}/{len(self.seq)})"
        )

    def poll_serial(self):
        """ND287側から送信された値を受け取り、順番どおりに箱へ入れる"""
        if self.seq is None or self.seq.done():
            return
        try:
            angles = self.dev.poll_received()
        except Exception:
            return
        for angle in angles:
            if self.seq.done():
                break
            self.accept_point(angle)

    def take_manual(self):
        """PC側からCTRL Bで現在値を要求して1点取り込む"""
        cur = self.seq.current() if self.seq else None
        if cur is None:
            return
        _, target, direction = cur
        self.dev.prepare_point(target, direction)
        try:
            angle = self.dev.read_angle()
        except Exception as e:
            self.statusBar().showMessage(f"読取エラー: {e}")
            return
        if angle is None:
            self.statusBar().showMessage("応答パース不可（本体出力設定を確認）")
            return
        self.accept_point(angle)

    def accept_point(self, angle):
        self.live.setText(f"受信: {deg_to_dms(angle)}")
        self.seq.record(angle)
        self.b_undo.setEnabled(True)
        self.redraw()
        if self.seq.done():
            self.guide.setText("測定完了 → セーブで保存")
            self.finish()
        else:
            self.show_guide()

    def undo(self):
        if self.seq and self.seq.undo():
            self.table_series.setRowCount(0)
            self.table_misc.setRowCount(0)
            self.b_save.setEnabled(False)
            self.b_corr.setEnabled(False)
            self.b_take.setEnabled(True)
            self.b_undo.setEnabled(self.seq.idx > 0)
            self.redraw()
            self.show_guide()

    def apply_correction(self):
        """バックラッシ手動補正を確定し、結果と合否判定を再計算する"""
        self.applied_blcorr = self.e_blcorr.value()
        self.finish()
        if self.applied_blcorr:
            self.statusBar().showMessage(
                f"バックラッシ補正 {self.applied_blcorr:+.2f}\" を適用しました"
            )
        else:
            self.statusBar().showMessage("バックラッシ補正を解除しました（補正0）")

    # ----- 表示 -----

    def redraw(self):
        for key, curve in self.curves.items():
            targets, measured = self.data[key]
            if targets:
                curve.setData(np.asarray(targets), deviation_sec(targets, measured))
            else:
                curve.setData([], [])

    def current_judgements(self, summary):
        """ホイール/ウォーム/総合の温度別合否判定文（測定温度・規格による）"""
        return judgement_texts(
            summary, self.parse_temp(), self.settings.get("judgement_spec")
        )

    def finish(self):
        self.b_take.setEnabled(False)
        self.b_save.setEnabled(True)
        self.b_corr.setEnabled(True)
        summary, _ = summarize(self.data, self.applied_blcorr)

        # 左表: 系列ごとの 精度PP・単一誤差・隣接誤差・傾き
        series = [k for k in SERIES_LABELS if k in summary]
        self.table_series.setRowCount(len(series))
        for i, key in enumerate(series):
            s = summary[key]
            cells = [
                SERIES_LABELS[key],
                f'{s["pp"]:.2f}"',
                f'{s["single"]:.2f}"',
                f'{s["adjacent"]:.2f}"',
                f'{s["slope"]:+.2f}"',
            ]
            for j, text in enumerate(cells):
                self.table_series.setItem(i, j, QtWidgets.QTableWidgetItem(text))

        # 右表: バックラッシMIN/MAX・温度規格による合否・真の最大最小
        rows = misc_rows(summary, self.current_judgements(summary))
        self.table_misc.setRowCount(len(rows))
        for i, (item, value) in enumerate(rows):
            self.table_misc.setItem(i, 0, QtWidgets.QTableWidgetItem(item))
            cell = QtWidgets.QTableWidgetItem(value)
            if "判定" in item:
                if value.startswith("NG"):
                    cell.setForeground(QtGui.QBrush(QtGui.QColor("red")))
                elif value.startswith("OK"):
                    cell.setForeground(QtGui.QBrush(QtGui.QColor("green")))
            self.table_misc.setItem(i, 1, cell)

    # ----- セーブ・ロード -----

    def save(self):
        if not self.data or not any(t for t, _ in self.data.values()):
            return
        machine_no = self.e_machine.text().strip()
        if not machine_no:
            QtWidgets.QMessageBox.warning(self, "セーブ", "機番を入力してください")
            return
        if self.e_blcorr.value() != self.applied_blcorr:
            answer = QtWidgets.QMessageBox.question(
                self,
                "セーブ",
                f"バックラッシ補正 {self.e_blcorr.value():+.2f}\" が未適用です。"
                "適用してからセーブしますか？",
            )
            if answer == QtWidgets.QMessageBox.Yes:
                self.apply_correction()
        root = resolve_save_root(self.settings)
        path = build_save_path(root, self.e_model.text(), machine_no)
        if path.exists():
            answer = QtWidgets.QMessageBox.question(
                self, "セーブ", f"{path.name} は既にあります。上書きしますか？"
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return
        meta = {
            "型式": self.e_model.text().strip(),
            "機番": machine_no,
            META_KEYS["date"]: self.e_date.date().toString("yyyy-MM-dd"),
            META_KEYS["operator"]: self.e_operator.text().strip(),
            META_KEYS["temperature"]: self.e_temp.text().strip(),
            META_KEYS["wheel_pitch"]: self.e_wheel.value(),
            META_KEYS["worm_pitch"]: self.e_worm.value(),
            META_KEYS["worm_range"]: self.e_range.value(),
            META_KEYS["worm_start"]: self.e_start.value(),
            META_KEYS["comment"]: self.e_comment.text().strip(),
            META_KEYS["blcorr"]: self.applied_blcorr,
        }
        summary, _ = summarize(self.data, self.applied_blcorr)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            save_csv(path, self.data, summary, meta, self.current_judgements(summary))
            self.statusBar().showMessage(f"保存しました: {path}")
        except Exception as e:
            self.statusBar().showMessage(f"保存失敗: {e}")

    def load(self):
        root = resolve_save_root(self.settings)
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "測定データを開く", str(root), "CSV (*.csv)"
        )
        if not path:
            return
        try:
            meta, data = load_csv(path)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "ロード", f"読み込みに失敗しました:\n{e}")
            return
        if not any(t for t, _ in data.values()):
            QtWidgets.QMessageBox.warning(self, "ロード", "測定データが入っていないファイルです")
            return
        self.seq = None  # 取込中状態は解除
        self.data = data
        self.e_model.setText(meta.get("型式", ""))
        self.e_machine.setText(meta.get("機番", ""))
        self.e_operator.setText(meta.get(META_KEYS["operator"], ""))
        self.e_temp.setText(meta.get(META_KEYS["temperature"], ""))
        self.e_comment.setText(meta.get(META_KEYS["comment"], ""))
        try:
            self.applied_blcorr = float(meta.get(META_KEYS["blcorr"], 0.0))
        except ValueError:
            self.applied_blcorr = 0.0
        self.e_blcorr.setValue(self.applied_blcorr)
        date = QtCore.QDate.fromString(meta.get(META_KEYS["date"], ""), "yyyy-MM-dd")
        if date.isValid():
            self.e_date.setDate(date)
        for attr, key in [
            (self.e_wheel, META_KEYS["wheel_pitch"]),
            (self.e_worm, META_KEYS["worm_pitch"]),
            (self.e_range, META_KEYS["worm_range"]),
            (self.e_start, META_KEYS["worm_start"]),
        ]:
            if key in meta:
                try:
                    attr.setValue(float(meta[key]))
                except ValueError:
                    pass
        self.b_undo.setEnabled(False)
        self.live.setText("")
        self.redraw()
        self.finish()
        self.guide.setText(f"ロード: {Path(path).name}")
        self.statusBar().showMessage(f"ロードしました: {path}")

    def closeEvent(self, event):
        self.rx_timer.stop()
        self.dev.close()
        super().closeEvent(event)


def run(device, wheel_pitch, worm_pitch, worm_range, worm_start, settings):
    import sys

    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow(device, wheel_pitch, worm_pitch, worm_range, worm_start, settings)
    win.show()
    sys.exit(app.exec())
