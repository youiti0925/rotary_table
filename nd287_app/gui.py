# -*- coding: utf-8 -*-
"""測定画面（PySide6 + pyqtgraph）

測定モード（画面左上で切替）:
    回転分割   … ホイール0°→360°（閉じ点込み）CW/CCW ＋ ウォームCW/CCW
    傾斜分割   … 開始角度〜終了角度（例 -30°〜+110°）を刻みで CW/CCW ＋ ウォーム
    回転再現性 … 各ブロック（例 0,90,180,270）で CW×N回 → CCW×N回。
                 各ブロックの最大−最小を出し、全ブロックの最大が再現性の結果
    傾斜再現性 … 同上（ブロックは開始〜終了角度の範囲を刻みで設定）

操作の流れ:
    1. 型式・機番・日付・名前・測定温度を入力し、条件を確認して「取込開始」
       （全部そろっていないと取込は始まらない）
    2. データ受け取り状態になる。テーブルを割り出して静止し、ND287のPRINTキー
       （またはX41トリガ）で値を送ると、順番どおりに箱へ入る。「手動取込」でも可
    3. 箱が全部埋まると結果が自動表示される
    4. 「セーブ」で <保存先>/<型式の系列>/<機番>.csv に保存（例 RWE-200 → RWE/12345.csv)
    5. 「ロード」で過去の測定（どのモードでも）を読み戻して再表示
"""

from pathlib import Path
import threading

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from .analysis import deviation_sec, repeatability_summary, summarize
from .export import (
    MODE_KEY,
    build_save_path,
    judgement_texts,
    load_measurement,
    misc_rows,
    repeat_result_rows,
    save_csv,
    save_repeat_csv,
)
from .nd287 import ND287Device, deg_to_dms, scan_report
from .sequence import (
    IndexingSequence,
    RepeatabilitySequence,
    SERIES_LABELS,
    rotary_blocks,
    tilt_blocks,
)
from .settings import resolve_save_root, save_settings

MODES = ("回転分割", "傾斜分割", "回転再現性", "傾斜再現性")

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
    "blocks": "ブロック数",
    "worm_pitch": "ウォーム刻み[°]",
    "worm_range": "ウォーム範囲[°]",
    "worm_start": "ウォーム開始[°]",
    "wheel_start": "開始角度[°]",
    "wheel_end": "終了角度[°]",
    "repeats": "回数",
    "date": "日付",
    "operator": "名前",
    "temperature": "測定温度[°C]",
    "comment": "コメント",
    "blcorr": "バックラッシ補正[秒]",
}

SERIES_METRIC_HEADERS = ["系列", "精度PP", "単一誤差", "隣接誤差", "傾き"]
REPEAT_HEADERS = ["ブロック", "角度", "CW範囲", "CCW範囲"]


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
    # 接続スレッド完了通知（成功か, ステータス文）。スレッドからGUIへ安全に渡す
    _conn_done = QtCore.Signal(bool, str)
    # 通信診断スレッド完了通知（レポート文字列）
    _diag_done = QtCore.Signal(str)

    def __init__(self, device, wheel_pitch, worm_pitch, worm_range, worm_start, settings):
        super().__init__()
        self.setWindowTitle("ND287 分割測定")
        self.resize(1200, 800)
        self.dev = device
        self.settings = settings
        self._connecting = False  # 接続スレッド実行中はシリアルに触らない
        self.seq = None        # 取込中のシーケンス（ロード表示時は None）
        self.data = None       # 分割測定の表示対象データ
        self.rep_points = None  # 再現性測定のブロック角度
        self.rep_data = None    # 再現性測定の表示対象データ
        self.view_kind = "indexing"  # 現在表示中のデータ種別

        # --- 1段目: モード・測定条件と取込操作 ---
        row1 = QtWidgets.QHBoxLayout()
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(MODES)
        self.mode_combo.currentTextChanged.connect(self.on_mode_changed)
        row1.addWidget(QtWidgets.QLabel("モード"))
        row1.addWidget(self.mode_combo)

        def add_field(label_text, widget):
            label = QtWidgets.QLabel(label_text)
            row1.addWidget(label)
            row1.addWidget(widget)
            return label

        self.e_wstart = QtWidgets.QDoubleSpinBox()
        self.e_wstart.setRange(-360.0, 360.0)
        self.e_wstart.setValue(-30.0)
        self.e_wstart.setSuffix(" °")
        self.l_wstart = add_field("開始角度", self.e_wstart)
        self.e_wend = QtWidgets.QDoubleSpinBox()
        self.e_wend.setRange(-360.0, 720.0)
        self.e_wend.setValue(110.0)
        self.e_wend.setSuffix(" °")
        self.l_wend = add_field("終了角度", self.e_wend)

        self.e_wheel = QtWidgets.QDoubleSpinBox()
        self.e_wheel.setRange(0.001, 180.0)
        self.e_wheel.setValue(wheel_pitch)
        self.e_wheel.setSuffix(" °/pt")
        self.l_wheel = add_field("ホイール刻み", self.e_wheel)

        self.e_blocks = QtWidgets.QSpinBox()
        self.e_blocks.setRange(2, 360)
        self.e_blocks.setValue(4)
        self.e_blocks.setSuffix(" 箇所")
        self.l_blocks = add_field("ブロック数", self.e_blocks)

        self.e_repeats = QtWidgets.QSpinBox()
        self.e_repeats.setRange(2, 99)
        self.e_repeats.setValue(7)
        self.e_repeats.setSuffix(" 回")
        self.l_repeats = add_field("回数", self.e_repeats)

        self.e_worm = QtWidgets.QDoubleSpinBox()
        self.e_worm.setDecimals(4)
        self.e_worm.setRange(0.0001, 90.0)
        self.e_worm.setValue(worm_pitch)
        self.e_worm.setSuffix(" °/pt")
        self.l_worm = add_field("ウォーム刻み", self.e_worm)
        self.e_range = QtWidgets.QDoubleSpinBox()
        self.e_range.setDecimals(4)
        self.e_range.setRange(0.001, 360.0)
        self.e_range.setValue(worm_range)
        self.e_range.setSuffix(" °")
        self.l_range = add_field("ウォーム範囲", self.e_range)
        self.e_start = QtWidgets.QDoubleSpinBox()
        self.e_start.setDecimals(4)
        self.e_start.setRange(0.0, 360.0)
        self.e_start.setValue(worm_start)
        self.e_start.setSuffix(" °")
        self.l_start = add_field("ウォーム開始", self.e_start)

        row1.addStretch(1)

        # --- 操作ボタン段（条件欄と分けて、欄が増えてもボタンが隠れないようにする） ---
        row_ops = QtWidgets.QHBoxLayout()
        b_start = QtWidgets.QPushButton("取込開始")
        b_start.setStyleSheet("font-size:16px; padding:4px 18px;")
        self.b_cancel = QtWidgets.QPushButton("中止")
        self.b_cancel.setEnabled(False)
        self.b_take = QtWidgets.QPushButton("手動取込")
        self.b_take.setEnabled(False)
        self.b_undo = QtWidgets.QPushButton("1点戻る")
        self.b_undo.setEnabled(False)
        b_start.clicked.connect(self.start)
        self.b_cancel.clicked.connect(self.cancel)
        self.b_take.clicked.connect(self.take_manual)
        self.b_undo.clicked.connect(self.undo)
        row_ops.addStretch(1)
        row_ops.addWidget(b_start)
        row_ops.addWidget(self.b_cancel)
        row_ops.addWidget(self.b_take)
        row_ops.addWidget(self.b_undo)

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
        self.l_blcorr = QtWidgets.QLabel("バックラッシ補正")
        row3.addWidget(QtWidgets.QLabel("コメント"))
        row3.addWidget(self.e_comment, 1)
        row3.addWidget(self.l_blcorr)
        row3.addWidget(self.e_blcorr)
        row3.addWidget(self.b_corr)

        # --- ガイドと受信値 ---
        self.guide = QtWidgets.QLabel("―")
        self.guide.setStyleSheet("font-size:22px; font-family:monospace; padding:4px;")
        self.live = QtWidgets.QLabel("")
        self.live.setStyleSheet("font-size:15px; color:#666; padding:2px;")

        # --- グラフ（分割: 左ホイール/右ウォーム 7:3。再現性: 左のみ） ---
        self.plot_wheel = pg.PlotWidget(title="ホイール")
        self.plot_worm = pg.PlotWidget(title="ウォーム")
        for plot in (self.plot_wheel, self.plot_worm):
            plot.addLegend(offset=(10, 10))
            plot.setLabel("bottom", "指令角度", units="°")
            plot.setLabel("left", "偏差", units='"')
            plot.showGrid(x=True, y=True, alpha=0.3)
        self.curves = {}
        plots = QtWidgets.QHBoxLayout()
        plots.addWidget(self.plot_wheel, 7)
        plots.addWidget(self.plot_worm, 3)

        # --- 結果表（左: 系列/ブロックごとの指標 / 右: バックラッシ・判定など） ---
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
        v.addLayout(row_ops)
        v.addWidget(self.guide)
        v.addWidget(self.live)
        v.addLayout(plots, 1)
        v.addLayout(tables)
        self.setCentralWidget(container)

        self.b_diag = QtWidgets.QPushButton("通信診断")
        self.b_diag.clicked.connect(self.run_diagnostics)
        self.statusBar().addPermanentWidget(self.b_diag)
        self.b_conn = QtWidgets.QPushButton("再接続")
        self.b_conn.clicked.connect(self.connect_device)
        self.statusBar().addPermanentWidget(self.b_conn)
        self._conn_done.connect(self.on_connect_done)
        self._diag_done.connect(self.on_diagnostics_done)
        # ウィンドウ表示後に接続（ポート探索はバックグラウンドで行うので画面は固まらない）
        QtCore.QTimer.singleShot(100, self.connect_device)

        # ND287側から送られてくるデータの受信ループ
        self.rx_timer = QtCore.QTimer(self)
        self.rx_timer.timeout.connect(self.poll_serial)
        self.rx_timer.start(200)

        self.on_mode_changed(self.mode_combo.currentText())

    # ----- モード -----

    def current_mode(self):
        return self.mode_combo.currentText()

    def is_tilt(self):
        return self.current_mode().startswith("傾斜")

    def is_repeat(self):
        return "再現性" in self.current_mode()

    def on_mode_changed(self, mode):
        is_tilt, is_repeat = self.is_tilt(), self.is_repeat()
        for w in (self.l_wstart, self.e_wstart, self.l_wend, self.e_wend):
            w.setVisible(is_tilt)
        for w in (
            self.l_worm, self.e_worm, self.l_range, self.e_range, self.l_start, self.e_start,
            self.l_wheel, self.e_wheel,
        ):
            w.setVisible(not is_repeat)
        for w in (self.l_blocks, self.e_blocks, self.l_repeats, self.e_repeats):
            w.setVisible(is_repeat)
        for w in (self.l_blcorr, self.e_blcorr, self.b_corr):
            w.setVisible(not is_repeat)
        self.l_wheel.setText("刻み" if is_tilt else "ホイール刻み")
        self.plot_worm.setVisible(not is_repeat)
        self.plot_wheel.setTitle("再現性（ブロックごとのばらつき）" if is_repeat else "ホイール")
        # モードを変えたら取込中の測定はキャンセル
        self.view_kind = "repeat" if is_repeat else "indexing"
        self.discard_measurement()

    def discard_measurement(self):
        """取込中の測定を破棄して初期状態に戻す"""
        self.seq = None
        if self.view_kind == "repeat":
            self.rep_points = None
            self.rep_data = None
        else:
            self.data = None
        self.rebuild_curves()
        self.table_series.setRowCount(0)
        self.table_misc.setRowCount(0)
        self.b_take.setEnabled(False)
        self.b_cancel.setEnabled(False)
        self.b_undo.setEnabled(False)
        self.b_save.setEnabled(False)
        self.b_corr.setEnabled(False)
        self.guide.setText("―")
        self.live.setText("")

    def cancel(self):
        """取込中の測定を中止する（取込済みデータは破棄）"""
        if self.seq is None:
            return
        taken = self.seq.idx
        self.discard_measurement()
        self.statusBar().showMessage(f"取込を中止しました（{taken}点破棄）")

    def rebuild_curves(self):
        for plot in (self.plot_wheel, self.plot_worm):
            plot.clear()
            if plot.plotItem.legend is not None:
                plot.plotItem.legend.clear()
        if self.is_repeat():
            self.curves = {
                "rep_cw": self.plot_wheel.plot(
                    [], [], pen=None, symbol="o", symbolSize=7,
                    symbolBrush="#1f77b4", name="CW",
                ),
                "rep_ccw": self.plot_wheel.plot(
                    [], [], pen=None, symbol="t", symbolSize=7,
                    symbolBrush="#d62728", name="CCW",
                ),
            }
        else:
            self.curves = {
                key: (self.plot_wheel if key.startswith("wheel") else self.plot_worm).plot(
                    name=SERIES_LABELS[key], **style
                )
                for key, style in CURVE_STYLES.items()
            }

    # ----- 接続 -----

    def connect_device(self):
        """接続（ポート自動探索）をバックグラウンドで実行する。

        探索は1ポートあたり最大1秒前後かかり、Bluetooth仮想COMポート等は
        開くだけで長時間固まることがあるため、GUIスレッドでは実行しない。
        """
        if self.dev.dummy:
            self.dev.open()
            self.statusBar().showMessage("ダミーモード（実機なし）")
            return
        if self._connecting:
            return
        self._connecting = True
        self.b_conn.setEnabled(False)
        self.statusBar().showMessage("ND287を検索中...（画面はそのまま操作できます）")
        dev = self.dev

        def work():
            try:
                dev.close()
                dev.open()
                self._conn_done.emit(True, f"接続: {dev.port}")
            except Exception as e:
                self._conn_done.emit(False, f"接続失敗: {e}")

        threading.Thread(target=work, daemon=True).start()

    def on_connect_done(self, ok, message):
        self._connecting = False
        self.b_conn.setEnabled(True)
        self.statusBar().showMessage(message)

    def run_diagnostics(self):
        """全ポート×複数ボーレートでCTRL Bを試す通信診断（バックグラウンド実行）"""
        if self.dev.dummy:
            QtWidgets.QMessageBox.information(
                self, "通信診断", "ダミーモードで起動中のため診断対象がありません"
            )
            return
        if self._connecting:
            self.statusBar().showMessage("接続処理中です。終わってから診断してください")
            return
        self._connecting = True  # 診断中はシリアルを独占する
        self.b_diag.setEnabled(False)
        self.b_conn.setEnabled(False)
        self.statusBar().showMessage("通信診断中...（全ポート×複数ボーレートを試します）")
        dev = self.dev
        baud = self.settings.get("baudrate")
        parity = self.settings.get("parity")

        def work():
            try:
                dev.close()  # 自分で開いているポートも診断対象にするため一旦閉じる
                report = scan_report(baud, parity)
            except Exception as e:
                report = f"診断に失敗しました: {e}"
            self._diag_done.emit(report)

        threading.Thread(target=work, daemon=True).start()

    def on_diagnostics_done(self, report):
        self._connecting = False
        self.b_diag.setEnabled(True)
        self.b_conn.setEnabled(True)
        self.statusBar().showMessage("通信診断が完了しました（接続するには再接続を押す）")
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("通信診断結果")
        dlg.resize(720, 480)
        v = QtWidgets.QVBoxLayout(dlg)
        text = QtWidgets.QPlainTextEdit(report)
        text.setReadOnly(True)
        text.setStyleSheet("font-family: monospace; font-size: 13px;")
        b_copy = QtWidgets.QPushButton("コピー")
        b_copy.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(report)
        )
        b_close = QtWidgets.QPushButton("閉じる")
        b_close.clicked.connect(dlg.accept)
        h = QtWidgets.QHBoxLayout()
        h.addStretch(1)
        h.addWidget(b_copy)
        h.addWidget(b_close)
        v.addWidget(text)
        v.addLayout(h)
        dlg.exec()

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

    def build_sequence(self):
        if self.is_repeat():
            n = self.e_blocks.value()
            if self.is_tilt():
                points = tilt_blocks(self.e_wstart.value(), self.e_wend.value(), n)
            else:
                points = rotary_blocks(n)  # 一周をn等分（例 4 → 0,90,180,270）
            return RepeatabilitySequence(points, self.e_repeats.value())
        wheel_start = self.e_wstart.value() if self.is_tilt() else 0.0
        wheel_end = self.e_wend.value() if self.is_tilt() else 360.0
        return IndexingSequence(
            self.e_wheel.value(),
            self.e_worm.value(),
            self.e_range.value(),
            self.e_start.value(),
            wheel_start,
            wheel_end,
        )

    def start(self):
        missing = self.missing_required_fields()
        if missing:
            QtWidgets.QMessageBox.warning(
                self,
                "取込開始",
                "次の項目を入力してから取込を開始してください:\n  " + "、".join(missing),
            )
            return
        if self.is_tilt() and self.e_wend.value() <= self.e_wstart.value():
            QtWidgets.QMessageBox.warning(
                self, "取込開始", "終了角度は開始角度より大きくしてください"
            )
            return
        self.seq = self.build_sequence()
        if self.is_repeat():
            self.rep_points = self.seq.points
            self.rep_data = self.seq.data
        else:
            self.data = self.seq.data
        self.dev.flush_input()  # 取込開始前に届いていた古いデータは捨てる
        self.rebuild_curves()
        self.table_series.setRowCount(0)
        self.table_misc.setRowCount(0)
        self.applied_blcorr = 0.0
        self.e_blcorr.setValue(0.0)
        self.b_corr.setEnabled(False)
        self.b_take.setEnabled(True)
        self.b_cancel.setEnabled(True)
        self.b_undo.setEnabled(False)
        self.b_save.setEnabled(False)
        self.show_guide()

    def show_guide(self):
        text = self.seq.guide_text()
        if text is not None:
            self.guide.setText(text)

    def poll_serial(self):
        """ND287側から送信された値を受け取り、順番どおりに箱へ入れる"""
        if self._connecting:
            return
        if self.seq is None or self.seq.done():
            # 取込中でなくても受信値はモニタ表示する（記録はしない）。
            # 配線確認: ND287のPRINTキーを押してここに値が出れば受信経路はOK
            try:
                angles = self.dev.poll_received()
            except Exception:
                return
            if angles:
                self.live.setText(f"受信（取込外・記録なし）: {deg_to_dms(angles[-1])}")
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
        if self._connecting:
            self.statusBar().showMessage("接続処理中です。少し待ってください")
            return
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
        if self.view_kind == "repeat":
            xs = {"cw": [], "ccw": []}
            ys = {"cw": [], "ccw": []}
            for (dirn, i), vals in (self.rep_data or {}).items():
                angle = self.rep_points[i]
                for v in vals:
                    xs[dirn].append(angle)
                    ys[dirn].append((v - angle) * 3600.0)
            self.curves["rep_cw"].setData(xs["cw"], ys["cw"])
            self.curves["rep_ccw"].setData(xs["ccw"], ys["ccw"])
            return
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
        self.b_cancel.setEnabled(False)
        self.b_save.setEnabled(True)
        if self.view_kind == "repeat":
            self.finish_repeat()
        else:
            self.finish_indexing()

    def finish_indexing(self):
        self.b_corr.setEnabled(True)
        summary, _ = summarize(self.data, self.applied_blcorr)

        # 左表: 系列ごとの 精度PP・単一誤差・隣接誤差・傾き
        self.table_series.setColumnCount(len(SERIES_METRIC_HEADERS))
        self.table_series.setHorizontalHeaderLabels(SERIES_METRIC_HEADERS)
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
        self.fill_misc_table(rows)

    def finish_repeat(self):
        rsum = repeatability_summary(self.rep_points, self.rep_data)
        self.table_series.setColumnCount(len(REPEAT_HEADERS))
        self.table_series.setHorizontalHeaderLabels(REPEAT_HEADERS)
        self.table_series.setRowCount(len(rsum["blocks"]))
        for i, b in enumerate(rsum["blocks"]):
            cells = [
                f"ブロック{i + 1}",
                f'{b["angle"]:g}°',
                f'{b["cw"]:.2f}"' if b["cw"] is not None else "―",
                f'{b["ccw"]:.2f}"' if b["ccw"] is not None else "―",
            ]
            for j, text in enumerate(cells):
                self.table_series.setItem(i, j, QtWidgets.QTableWidgetItem(text))
        rows = []
        for key, label in (
            ("cw", "再現性 CW（全ブロック最大）"),
            ("ccw", "再現性 CCW（全ブロック最大）"),
            ("overall", "再現性 総合"),
        ):
            if rsum.get(key) is not None:
                rows.append((label, f'{rsum[key]:.2f}"'))
        self.fill_misc_table(rows)

    def fill_misc_table(self, rows):
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
        self.table_series.resizeColumnsToContents()
        self.table_misc.resizeColumnToContents(0)

    # ----- セーブ・ロード -----

    def has_view_data(self):
        if self.view_kind == "repeat":
            return bool(self.rep_data)
        return bool(self.data) and any(t for t, _ in self.data.values())

    def build_meta(self):
        meta = {
            MODE_KEY: self.current_mode(),
            "型式": self.e_model.text().strip(),
            "機番": self.e_machine.text().strip(),
            META_KEYS["date"]: self.e_date.date().toString("yyyy-MM-dd"),
            META_KEYS["operator"]: self.e_operator.text().strip(),
            META_KEYS["temperature"]: self.e_temp.text().strip(),
        }
        if self.is_tilt():
            meta[META_KEYS["wheel_start"]] = self.e_wstart.value()
            meta[META_KEYS["wheel_end"]] = self.e_wend.value()
        if self.is_repeat():
            meta[META_KEYS["blocks"]] = self.e_blocks.value()
            meta[META_KEYS["repeats"]] = self.e_repeats.value()
        else:
            meta[META_KEYS["wheel_pitch"]] = self.e_wheel.value()
            meta[META_KEYS["worm_pitch"]] = self.e_worm.value()
            meta[META_KEYS["worm_range"]] = self.e_range.value()
            meta[META_KEYS["worm_start"]] = self.e_start.value()
            meta[META_KEYS["blcorr"]] = self.applied_blcorr
        meta[META_KEYS["comment"]] = self.e_comment.text().strip()
        return meta

    def save(self):
        if not self.has_view_data():
            return
        machine_no = self.e_machine.text().strip()
        if not machine_no:
            QtWidgets.QMessageBox.warning(self, "セーブ", "機番を入力してください")
            return
        if not self.is_repeat() and self.e_blcorr.value() != self.applied_blcorr:
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
        meta = self.build_meta()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if self.view_kind == "repeat":
                rsum = repeatability_summary(self.rep_points, self.rep_data)
                save_repeat_csv(path, self.rep_points, self.rep_data, rsum, meta)
            else:
                summary, _ = summarize(self.data, self.applied_blcorr)
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
            meta, kind, payload = load_measurement(path)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "ロード", f"読み込みに失敗しました:\n{e}")
            return

        # モードを合わせる（on_mode_changed が状態をリセットする）
        mode = meta.get(MODE_KEY)
        if mode not in MODES:
            mode = "回転再現性" if kind == "repeat" else "回転分割"
        self.mode_combo.setCurrentText(mode)

        if kind == "repeat":
            points, data = payload
            if not data:
                QtWidgets.QMessageBox.warning(self, "ロード", "測定データが入っていないファイルです")
                return
            self.rep_points, self.rep_data = points, data
        else:
            if not any(t for t, _ in payload.values()):
                QtWidgets.QMessageBox.warning(self, "ロード", "測定データが入っていないファイルです")
                return
            self.data = payload

        self.seq = None  # 取込中状態は解除
        self.e_model.setText(meta.get("型式", ""))
        self.e_machine.setText(meta.get("機番", ""))
        self.e_operator.setText(meta.get(META_KEYS["operator"], ""))
        self.e_temp.setText(meta.get(META_KEYS["temperature"], ""))
        self.e_comment.setText(meta.get(META_KEYS["comment"], ""))
        date = QtCore.QDate.fromString(meta.get(META_KEYS["date"], ""), "yyyy-MM-dd")
        if date.isValid():
            self.e_date.setDate(date)
        for spin, key in [
            (self.e_wheel, META_KEYS["wheel_pitch"]),
            (self.e_blocks, META_KEYS["blocks"]),
            (self.e_worm, META_KEYS["worm_pitch"]),
            (self.e_range, META_KEYS["worm_range"]),
            (self.e_start, META_KEYS["worm_start"]),
            (self.e_wstart, META_KEYS["wheel_start"]),
            (self.e_wend, META_KEYS["wheel_end"]),
            (self.e_repeats, META_KEYS["repeats"]),
        ]:
            if key in meta:
                try:
                    value = float(meta[key])
                    spin.setValue(int(value) if isinstance(spin, QtWidgets.QSpinBox) else value)
                except ValueError:
                    pass
        try:
            self.applied_blcorr = float(meta.get(META_KEYS["blcorr"], 0.0))
        except ValueError:
            self.applied_blcorr = 0.0
        self.e_blcorr.setValue(self.applied_blcorr)
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
