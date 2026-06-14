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
import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from .analysis import (
    band_for_temp,
    composite_backlash_minmax,
    deviation_sec,
    pp,
    repeatability_summary,
    summarize,
)
from .pcorr import apply_compensation, compensation_table
from .bs_format import SECTION_TO_SERIES, data_to_doc, doc_to_data, load_bs, save_bs
from .ks_format import (
    data_to_doc as ks_data_to_doc,
    doc_to_data as ks_doc_to_data,
    load_ks,
    save_ks,
    tilt_accuracy,
)
from .masters import condition_params, find_entry, formula_minmax, load_masters
from .fanuc import FanucConfig, generate as generate_fanuc
from .firestore_sync import FirestoreSync, build_measurement_doc, overall_judgement
from .export import (
    MODE_KEY,
    build_save_path,
    judgement_texts,
    load_measurement,
    misc_rows,
    repeat_result_rows,
    sanitize_filename,
    save_csv,
    save_repeat_csv,
)
from .nd287 import ND287Device, deg_to_dms, scan_report
from .switchbot import (
    DEFAULT_PATTERNS,
    bot_configured,
    fetch_temperature,
    press_bot,
)
from .sequence import (
    CombinedSequence,
    IndexingSequence,
    RepeatabilitySequence,
    SERIES_LABELS,
    rotary_blocks,
    tilt_blocks,
)
from .settings import (
    PROFILE_LABELS,
    apply_active_profile,
    resolve_save_root,
    save_settings,
)

MODES = ("回転分割", "傾斜分割", "回転再現性", "傾斜再現性",
         "回転分割+再現", "傾斜分割+再現")

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
    "rep_start": "再現開始[°]",
    "rep_end": "再現終了[°]",
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

        bs_row = QtWidgets.QHBoxLayout()
        self.e_bs_root = QtWidgets.QLineEdit(str(settings.get("bs_save_root", "")))
        self.e_bs_root.setToolTip(
            "旧形式(.BS/.KS)の保存先（検査表システムのデータフォルダ）。空にすると書かない"
        )
        b_bs_browse = QtWidgets.QPushButton("参照...")
        b_bs_browse.clicked.connect(self.browse_bs_root)
        bs_row.addWidget(self.e_bs_root)
        bs_row.addWidget(b_bs_browse)

        # 各種条件CSVの場所（相対ならアプリフォルダ基準）
        self.e_conditions = self._make_file_row(
            settings.get("conditions_csv", r"マスタ/測定条件.csv"), "測定条件CSVを選択"
        )
        self.e_judgement = self._make_file_row(
            settings.get("judgement_csv", r"マスタ/合否判定.csv"), "合否判定CSVを選択"
        )
        self.e_user_rotary = self._make_file_row(
            settings.get("user_rotary_csv", r"マスタ/ユーザー回転条件.csv"),
            "ユーザー回転条件CSVを選択",
        )
        self.e_user_tilt = self._make_file_row(
            settings.get("user_tilt_csv", r"マスタ/ユーザー傾斜条件.csv"),
            "ユーザー傾斜条件CSVを選択",
        )

        form.addRow("ポート", self.e_port)
        form.addRow("ボーレート", self.e_baud)
        form.addRow("パリティ", self.e_parity)
        form.addRow("測定データ保存先", root_row)
        form.addRow(".BS/.KS保存先（旧形式）", bs_row)
        form.addRow("測定条件CSV", self.e_conditions.row)
        form.addRow("合否判定CSV", self.e_judgement.row)
        form.addRow("ユーザー回転条件CSV", self.e_user_rotary.row)
        form.addRow("ユーザー傾斜条件CSV", self.e_user_tilt.row)

        note = QtWidgets.QLabel(
            "相対パスはアプリフォルダ基準。温度別の合否規格（ホイール/ウォーム/総合）は"
            " settings.json の judgement_spec で編集"
        )
        note.setStyleSheet("color:#666;")
        note.setWordWrap(True)
        form.addRow(note)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _make_file_row(self, value, title):
        """CSVファイルパス用の「入力欄＋参照...」行を作る。

        戻り値の QLineEdit に .row（QHBoxLayout）を付けて返す。
        """
        edit = QtWidgets.QLineEdit(str(value or ""))
        btn = QtWidgets.QPushButton("参照...")
        btn.clicked.connect(lambda: self._browse_file(edit, title))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(edit)
        row.addWidget(btn)
        edit.row = row
        return edit

    def _browse_file(self, edit, title):
        # 既存・新規どちらのパスも選べるよう getSaveFileName を使う（上書き確認はしない）
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, title, edit.text(), "CSVファイル (*.csv);;すべて (*)",
            options=QtWidgets.QFileDialog.DontConfirmOverwrite,
        )
        if path:
            edit.setText(path)

    def browse_root(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "保存先フォルダを選択", self.e_root.text()
        )
        if path:
            self.e_root.setText(path)

    def browse_bs_root(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, ".BS保存先フォルダを選択", self.e_bs_root.text()
        )
        if path:
            self.e_bs_root.setText(path)

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
            bs_save_root=self.e_bs_root.text().strip(),
            conditions_csv=self.e_conditions.text().strip() or r"マスタ/測定条件.csv",
            judgement_csv=self.e_judgement.text().strip() or r"マスタ/合否判定.csv",
            user_rotary_csv=self.e_user_rotary.text().strip()
            or r"マスタ/ユーザー回転条件.csv",
            user_tilt_csv=self.e_user_tilt.text().strip()
            or r"マスタ/ユーザー傾斜条件.csv",
        )


class RawDataDialog(QtWidgets.QDialog):
    """生データ（数値）の一覧。Ctrl+Shift+E → 管理者パスワードで編集可能になる"""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.admin = False
        self.entries = []  # 行 → データ書き戻し先
        self.setWindowTitle("生データ")
        self.resize(680, 640)
        layout = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget()
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)
        bottom = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel("表示のみ")
        self.b_apply = QtWidgets.QPushButton("編集を適用")
        self.b_apply.setVisible(False)
        self.b_apply.clicked.connect(self.apply_edits)
        b_close = QtWidgets.QPushButton("閉じる")
        b_close.clicked.connect(self.accept)
        bottom.addWidget(self.status)
        bottom.addStretch(1)
        bottom.addWidget(self.b_apply)
        bottom.addWidget(b_close)
        layout.addLayout(bottom)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+E"), self,
                        activated=self.enter_admin)
        self.populate()

    def populate(self):
        self.entries = []
        if self.win.view_kind == "repeat":
            headers = ["ブロック", "方向", "回", "指令角度[°]", "測定値[°]", "偏差[\"]"]
            rows = []
            points = self.win.rep_points or []
            for (dirn, block), values in sorted((self.win.rep_data or {}).items(),
                                                key=lambda kv: (kv[0][1], kv[0][0])):
                angle = points[block] if block < len(points) else 0.0
                for rep, value in enumerate(values):
                    dev = float(deviation_sec([angle], [value])[0])
                    rows.append([f"ブロック{block + 1}", dirn.upper(), rep + 1,
                                 f"{angle:.4f}", f"{value:.6f}", f"{dev:.2f}"])
                    self.entries.append(("repeat", (dirn, block), rep))
            value_column = 4
        else:
            headers = ["系列", "指令角度[°]", "測定値[°]", "測定値(度分秒)", "偏差[\"]"]
            rows = []
            for key in SERIES_LABELS:
                targets, measured = (self.win.data or {}).get(key, ([], []))
                for index, (t, m) in enumerate(zip(targets, measured)):
                    dev = float(deviation_sec([t], [m])[0])
                    rows.append([SERIES_LABELS[key], f"{t:.4f}", f"{m:.6f}",
                                 deg_to_dms(m), f"{dev:.2f}"])
                    self.entries.append(("indexing", key, index))
            value_column = 2
        self.value_column = value_column
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, text in enumerate(row):
                item = QtWidgets.QTableWidgetItem(str(text))
                if not (self.admin and j == value_column):
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                self.table.setItem(i, j, item)
        self.table.resizeColumnsToContents()

    def enter_admin(self):
        password, ok = QtWidgets.QInputDialog.getText(
            self, "管理者モード", "パスワード:", QtWidgets.QLineEdit.Password
        )
        expected = str(self.win.settings.get("admin_password") or "")
        if not ok:
            return
        if not expected or password != expected:
            self.status.setText("パスワードが違います")
            return
        self.admin = True
        self.b_apply.setVisible(True)
        self.status.setText("管理者モード: 測定値[°]列を編集できます")
        self.populate()

    def apply_edits(self):
        if not self.admin:
            return
        errors = 0
        for i, entry in enumerate(self.entries):
            item = self.table.item(i, self.value_column)
            try:
                value = float(item.text())
            except (TypeError, ValueError):
                errors += 1
                continue
            if entry[0] == "indexing":
                _, key, index = entry
                self.win.data[key][1][index] = value
            else:
                _, data_key, rep = entry
                self.win.rep_data[data_key][rep] = value
        self.win.redraw()
        if not self.win.b_take.isEnabled():
            self.win.finish()
        self.win.update_counts()
        self.win.statusBar().showMessage("生データを編集しました（管理者モード）")
        self.status.setText(
            "適用しました" + (f"（{errors}件は数値でないため無視）" if errors else "")
        )
        self.populate()


class ProgramDialog(QtWidgets.QDialog):
    """FANUC測定プログラム生成ダイアログ（設定→プレビュー→保存）"""

    def __init__(self, parent, settings, params):
        super().__init__(parent)
        self.settings = settings
        self.params = params  # 測定条件（rotary, wheel_pitch, blocks 等）
        self.setWindowTitle("FANUC測定プログラム作成")
        self.resize(720, 720)
        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self.e_axis = QtWidgets.QLineEdit(str(settings.get("fanuc_axis", "X")))
        self.e_axis.setMaximumWidth(60)
        self.e_pre = QtWidgets.QDoubleSpinBox()
        self.e_pre.setRange(0.0, 360.0)
        self.e_pre.setDecimals(3)
        self.e_pre.setValue(float(settings.get("fanuc_preswing", 10.0)))
        self.e_pre.setSuffix(" °")
        self.e_reset_sw = QtWidgets.QDoubleSpinBox()
        self.e_reset_sw.setRange(0.0, 360.0)
        self.e_reset_sw.setDecimals(3)
        self.e_reset_sw.setValue(float(settings.get(
            "fanuc_reset_swing", settings.get("fanuc_preswing", 10.0))))
        self.e_reset_sw.setSuffix(" °")
        self.e_dwell = QtWidgets.QDoubleSpinBox()
        self.e_dwell.setRange(0.0, 60.0)
        self.e_dwell.setDecimals(2)
        self.e_dwell.setValue(float(settings.get("fanuc_dwell_sec", 1.0)))
        self.e_dwell.setSuffix(" 秒")
        self.e_mcode = QtWidgets.QLineEdit(str(settings.get("fanuc_mcode", "M80")))
        self.e_mcode.setMaximumWidth(80)
        self.c_sub = QtWidgets.QCheckBox("再現をサブプロにする（外すと1本に展開）")
        self.c_sub.setChecked(bool(settings.get("fanuc_use_subprogram", True)))
        self.c_reset = QtWidgets.QCheckBox(
            "先頭にカウンターリセット（バックラッシュ消し→M00）を入れる")
        self.c_reset.setChecked(bool(settings.get("fanuc_counter_reset", True)))
        self.c_return = QtWidgets.QCheckBox("測定後に0°（基準）へ戻す")
        self.c_return.setChecked(bool(settings.get("fanuc_return_to_start", True)))
        self.c_div = QtWidgets.QCheckBox("分割を含める")
        self.c_div.setChecked(params.get("include_division", True))
        self.c_rep = QtWidgets.QCheckBox("再現を含める")
        self.c_rep.setChecked(params.get("include_repeat", True))
        self.e_main = QtWidgets.QSpinBox()
        self.e_main.setRange(1, 9999)
        self.e_main.setValue(int(settings.get("fanuc_main_number", 100)))
        self.e_sub = QtWidgets.QSpinBox()
        self.e_sub.setRange(1, 9999)
        self.e_sub.setValue(int(settings.get("fanuc_rep_sub_number", 9001)))

        form.addRow("割出軸", self.e_axis)
        form.addRow("前振り量（測定点のバックラッシュ消し）", self.e_pre)
        form.addRow("リセット振り量（カウンター0設定用）", self.e_reset_sw)
        form.addRow("ドゥエル（位置決め後の待ち）", self.e_dwell)
        form.addRow("完了信号Mコード", self.e_mcode)
        form.addRow("メインO番号", self.e_main)
        form.addRow("再現サブプロO番号", self.e_sub)
        form.addRow(self.c_reset)
        form.addRow(self.c_sub)
        form.addRow(self.c_return)
        form.addRow(self.c_div)
        form.addRow(self.c_rep)
        layout.addLayout(form)

        self.preview = QtWidgets.QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setStyleSheet("font-family: monospace; font-size: 12px;")
        layout.addWidget(self.preview, 1)

        buttons = QtWidgets.QHBoxLayout()
        b_refresh = QtWidgets.QPushButton("プレビュー更新")
        b_save = QtWidgets.QPushButton("保存(.NC)")
        b_close = QtWidgets.QPushButton("閉じる")
        b_refresh.clicked.connect(self.refresh)
        b_save.clicked.connect(self.save)
        b_close.clicked.connect(self.accept)
        buttons.addWidget(b_refresh)
        buttons.addStretch(1)
        buttons.addWidget(b_save)
        buttons.addWidget(b_close)
        layout.addLayout(buttons)

        for w in (self.e_axis, self.e_mcode):
            w.textChanged.connect(self.refresh)
        for w in (self.e_pre, self.e_reset_sw, self.e_dwell):
            w.valueChanged.connect(self.refresh)
        for w in (self.e_main, self.e_sub):
            w.valueChanged.connect(self.refresh)
        for w in (self.c_sub, self.c_reset, self.c_return, self.c_div, self.c_rep):
            w.toggled.connect(self.refresh)
        self.refresh()

    def _config(self):
        return FanucConfig(
            axis=self.e_axis.text().strip() or "X",
            preswing=self.e_pre.value(),
            dwell_sec=self.e_dwell.value(),
            mcode=self.e_mcode.text().strip() or "M80",
            use_subprogram=self.c_sub.isChecked(),
            main_number=self.e_main.value(),
            rep_sub_number=self.e_sub.value(),
            return_to_start=self.c_return.isChecked(),
            counter_reset=self.c_reset.isChecked(),
            reset_swing=self.e_reset_sw.value(),
        )

    def _generate(self):
        p = self.params
        return generate_fanuc(
            self._config(),
            rotary=p["rotary"], title=p.get("title", "MEASURE"),
            wheel_pitch=p["wheel_pitch"], wheel_start=p["wheel_start"],
            wheel_end=p["wheel_end"], worm_pitch=p["worm_pitch"],
            worm_range=p["worm_range"], worm_start=p["worm_start"],
            blocks=p["blocks"], repeats=p["repeats"],
            include_division=self.c_div.isChecked(),
            include_repeat=self.c_rep.isChecked(),
        )

    def refresh(self):
        try:
            self.preview.setPlainText(self._generate())
        except Exception as e:
            self.preview.setPlainText(f"生成エラー: {e}")

    def save(self):
        text = self._generate()
        default = f"{self.params.get('machine') or 'program'}.NC"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "測定プログラムを保存", default, "NCプログラム (*.NC *.txt)")
        if not path:
            return
        # FANUCはASCII。CRLFで保存
        with open(path, "w", encoding="ascii", errors="replace", newline="") as f:
            f.write(text)
        QtWidgets.QMessageBox.information(self, "保存", f"保存しました:\n{path}")


class ConditionRegistryDialog(QtWidgets.QDialog):
    """測定条件の登録/編集（回転・傾斜を別ファイルで管理）。

    傾斜は傾斜専用ファイルに保存するので、回転の条件と混ざらない。
    """

    def __init__(self, win, tilt: bool):
        super().__init__(win)
        self.win = win
        self.tilt = tilt
        from .masters import (ROTARY_USER_FIELDS, TILT_USER_FIELDS)
        self.fields = TILT_USER_FIELDS if tilt else ROTARY_USER_FIELDS
        self.setWindowTitle("傾斜の条件登録/編集" if tilt else "回転の条件登録/編集")
        self.resize(640, 460)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(
            ("傾斜分割の測定条件を型式ごとに登録します（回転とは別ファイル）。"
             if tilt else
             "回転分割の測定条件を型式ごとに登録します（提供CSVより優先されます）。")))
        self.table = QtWidgets.QTableWidget(0, len(self.fields))
        self.table.setHorizontalHeaderLabels(self.fields)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self.table.cellDoubleClicked.connect(self.load_selected_to_screen)
        layout.addWidget(self.table)

        buttons = QtWidgets.QHBoxLayout()
        b_reg = QtWidgets.QPushButton("現在の画面条件で登録/更新")
        b_del = QtWidgets.QPushButton("選択を削除")
        b_load = QtWidgets.QPushButton("選択を画面へ")
        b_close = QtWidgets.QPushButton("閉じる")
        b_reg.clicked.connect(self.register_current)
        b_del.clicked.connect(self.delete_selected)
        b_load.clicked.connect(self.load_selected_to_screen)
        b_close.clicked.connect(self.accept)
        buttons.addWidget(b_reg)
        buttons.addWidget(b_del)
        buttons.addWidget(b_load)
        buttons.addStretch(1)
        buttons.addWidget(b_close)
        layout.addLayout(buttons)
        self.reload()

    def _path(self):
        from .masters import _user_path
        key = "user_tilt_csv" if self.tilt else "user_rotary_csv"
        default = ("マスタ/ユーザー傾斜条件.csv" if self.tilt
                   else "マスタ/ユーザー回転条件.csv")
        return _user_path(self.win.settings, key, default)

    def reload(self):
        from .masters import load_user_conditions
        records = load_user_conditions(self._path(), self.fields)
        rows = sorted(records.values(), key=lambda r: r.get("型式", ""))
        self.table.setRowCount(len(rows))
        for i, rec in enumerate(rows):
            for j, col in enumerate(self.fields):
                self.table.setItem(i, j, QtWidgets.QTableWidgetItem(str(rec.get(col, ""))))
        self.table.resizeColumnsToContents()

    def _current_record(self):
        w = self.win
        model = w.e_model.text().strip()
        if not model:
            return None
        if self.tilt:
            return {
                "型式": model,
                "開始角度": f"{w.e_wstart.value():g}", "終了角度": f"{w.e_wend.value():g}",
                "刻み": f"{w.e_wheel.value():g}", "ウォーム刻み": f"{w.e_worm.value():g}",
                "ウォーム範囲": f"{w.e_range.value():g}", "ウォーム開始": f"{w.e_start.value():g}",
            }
        return {
            "型式": model, "ホイール刻み": f"{w.e_wheel.value():g}",
            "ウォーム刻み": f"{w.e_worm.value():g}", "ウォーム範囲": f"{w.e_range.value():g}",
            "ウォーム開始": f"{w.e_start.value():g}",
        }

    def register_current(self):
        from .masters import upsert_user_condition
        rec = self._current_record()
        if not rec:
            QtWidgets.QMessageBox.warning(self, "登録", "型式を入力してください")
            return
        upsert_user_condition(self._path(), self.fields, rec)
        self.win.reload_masters()
        self.reload()
        self.win.statusBar().showMessage(f"{rec['型式']} の条件を登録しました")

    def delete_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        model = self.table.item(row, 0).text()
        from .masters import delete_user_condition
        if delete_user_condition(self._path(), self.fields, model):
            self.win.reload_masters()
            self.reload()
            self.win.statusBar().showMessage(f"{model} の条件を削除しました")

    def load_selected_to_screen(self, *args):
        row = self.table.currentRow()
        if row < 0:
            return
        self.win.e_model.setText(self.table.item(row, 0).text())
        self.win.on_model_entered()


class PastDataDialog(QtWidgets.QDialog):
    """過去データの一覧（直近N件）。クリックでロードできる。"""

    COLUMNS = ["日付", "型式", "機番", "名前", "モード", "主要条件", "判定", "ファイル"]

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("過去データ")
        self.resize(900, 520)
        layout = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("直近"))
        self.e_count = QtWidgets.QSpinBox()
        self.e_count.setRange(1, 500)
        self.e_count.setValue(int(win.settings.get("recent_count") or 10))
        self.e_count.setSuffix(" 件")
        self.e_count.valueChanged.connect(self.reload)
        top.addWidget(self.e_count)
        b_refresh = QtWidgets.QPushButton("更新")
        b_refresh.clicked.connect(self.reload)
        top.addWidget(b_refresh)
        top.addStretch(1)
        layout.addLayout(top)

        self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self.table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self.load_selected)
        layout.addWidget(self.table, 1)

        buttons = QtWidgets.QHBoxLayout()
        b_load = QtWidgets.QPushButton("選択をロード")
        b_close = QtWidgets.QPushButton("閉じる")
        b_load.clicked.connect(self.load_selected)
        b_close.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(b_load)
        buttons.addWidget(b_close)
        layout.addLayout(buttons)
        self._paths = []
        self.reload()

    def reload(self):
        from .export import MODE_KEY, load_measurement
        from .masters import _read_text  # noqa: F401 (未使用だが将来用)
        root = resolve_save_root(self.win.settings)
        files = []
        try:
            for p in Path(root).rglob("*.csv"):
                if p.name.endswith("_再現.csv"):
                    continue
                files.append(p)
        except Exception:
            files = []
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        files = files[: self.e_count.value()]
        self._paths = files
        self.table.setRowCount(len(files))
        for i, p in enumerate(files):
            meta = {}
            try:
                meta, _kind, _payload = load_measurement(str(p))
            except Exception:
                pass
            cond = self._condition_summary(meta)
            cells = [
                meta.get("日付", ""), meta.get("型式", ""), meta.get("機番", p.stem),
                meta.get("名前", ""), meta.get(MODE_KEY, ""), cond,
                self._judgement(p), p.name,
            ]
            for j, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(str(text))
                if j == 6 and str(text).startswith("NG"):
                    item.setForeground(QtGui.QBrush(QtGui.QColor("red")))
                self.table.setItem(i, j, item)
        self.table.resizeColumnsToContents()

    @staticmethod
    def _condition_summary(meta):
        parts = []
        if meta.get("ホイール刻み[°]"):
            parts.append(f"H{meta['ホイール刻み[°]']}°")
        if meta.get("開始角度[°]"):
            parts.append(f"{meta.get('開始角度[°]')}〜{meta.get('終了角度[°]', '')}°")
        if meta.get("ウォーム刻み[°]"):
            parts.append(f"W{meta['ウォーム刻み[°]']}×{meta.get('ウォーム範囲[°]', '')}°")
        if meta.get("ブロック数"):
            parts.append(f"{meta['ブロック数']}箇所×{meta.get('回数', '')}回")
        return " ".join(parts)

    def _judgement(self, path):
        """保存CSVの結果サマリから判定（NGが1つでもあればNG）を拾う"""
        try:
            text = path.read_text(encoding="cp932", errors="ignore")
        except Exception:
            return ""
        if "NG（" in text or "NG(" in text:
            return "NG"
        if "OK（" in text or "OK(" in text:
            return "OK"
        return ""

    def load_selected(self, *args):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._paths):
            return
        self.win.load_path(str(self._paths[row]))
        self.accept()


class MainWindow(QtWidgets.QMainWindow):
    # 接続スレッド完了通知（成功か, ステータス文）。スレッドからGUIへ安全に渡す
    _conn_done = QtCore.Signal(bool, str)
    # 通信診断スレッド完了通知（レポート文字列）
    _diag_done = QtCore.Signal(str)
    # SwitchBot温度取得完了通知（成功か, メッセージ, 温度）
    _temp_done = QtCore.Signal(bool, str, float)
    # SwitchBot Bot押下の経過通知
    _bot_msg = QtCore.Signal(str)
    # Webモニタからのコマンド（HTTPスレッド→GUIスレッド）
    _remote_cmd = QtCore.Signal(str)
    # Webアプリ（Firestore）送信の完了通知
    _sync_done = QtCore.Signal(str)
    # Webアプリ連動の指令（Firestoreポーリングスレッド→GUIスレッド）
    _command_signal = QtCore.Signal(object)

    def __init__(self, device, wheel_pitch, worm_pitch, worm_range, worm_start, settings):
        super().__init__()
        self.setWindowTitle("ND287 分割測定")
        self.resize(1200, 800)
        self.dev = device
        self.settings = settings
        self._connecting = False  # 接続スレッド実行中はシリアルに触らない
        self.auto_mode = False    # 自動測定（SwitchBot起動＋NG自動再測定）中か
        self.auto_retries = 0
        self.web = None           # Webモニタ（起動は__init__末尾で）
        self._web_lock = threading.Lock()
        self._web_state = {}
        self._web_png = b""
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

        self.e_rstart = QtWidgets.QDoubleSpinBox()
        self.e_rstart.setRange(-360.0, 720.0)
        self.e_rstart.setValue(0.0)
        self.e_rstart.setSuffix(" °")
        self.l_rstart = add_field("再現開始", self.e_rstart)
        self.e_rend = QtWidgets.QDoubleSpinBox()
        self.e_rend.setRange(-360.0, 720.0)
        self.e_rend.setValue(270.0)
        self.e_rend.setSuffix(" °")
        self.l_rend = add_field("再現終了", self.e_rend)

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
        self.b_auto = QtWidgets.QPushButton("自動測定")
        self.b_auto.setStyleSheet("font-size:16px; padding:4px 18px;")
        self.b_auto.setToolTip(
            "取込開始→SwitchBotで機械を起動→完了後に傾き判定→NGなら自動で再測定"
        )
        self.b_cancel = QtWidgets.QPushButton("中止")
        self.b_cancel.setEnabled(False)
        self.b_take = QtWidgets.QPushButton("手動取込")
        self.b_take.setEnabled(False)
        self.b_undo = QtWidgets.QPushButton("1点戻る")
        self.b_undo.setEnabled(False)
        b_start.clicked.connect(self.start)
        self.b_auto.clicked.connect(self.auto_start)
        self.b_cancel.clicked.connect(self.cancel)
        self.b_take.clicked.connect(self.take_manual)
        self.b_undo.clicked.connect(self.undo)
        row_ops.addStretch(1)
        row_ops.addWidget(b_start)
        row_ops.addWidget(self.b_auto)
        row_ops.addWidget(self.b_cancel)
        row_ops.addWidget(self.b_take)
        row_ops.addWidget(self.b_undo)

        # --- 評価範囲（傾斜分割のみ。客先要求の部分抜き出し評価）---
        row_ranges = QtWidgets.QHBoxLayout()
        self.c_r1 = QtWidgets.QCheckBox("評価範囲1")
        self.e_r1s = QtWidgets.QDoubleSpinBox()
        self.e_r1e = QtWidgets.QDoubleSpinBox()
        self.c_r2 = QtWidgets.QCheckBox("評価範囲2")
        self.e_r2s = QtWidgets.QDoubleSpinBox()
        self.e_r2e = QtWidgets.QDoubleSpinBox()
        for spin in (self.e_r1s, self.e_r1e, self.e_r2s, self.e_r2e):
            spin.setRange(-360.0, 720.0)
            spin.setSuffix(" °")
        self.e_r1s.setValue(0.0)
        self.e_r1e.setValue(90.0)
        self.e_r2s.setValue(-90.0)
        self.e_r2e.setValue(0.0)
        self.range_widgets = []
        for w in (self.c_r1, QtWidgets.QLabel("開始"), self.e_r1s,
                  QtWidgets.QLabel("終了"), self.e_r1e,
                  self.c_r2, QtWidgets.QLabel("開始"), self.e_r2s,
                  QtWidgets.QLabel("終了"), self.e_r2e):
            row_ranges.addWidget(w)
            self.range_widgets.append(w)
        row_ranges.addStretch(1)
        for check in (self.c_r1, self.c_r2):
            check.toggled.connect(self.refresh_results)
        for spin in (self.e_r1s, self.e_r1e, self.e_r2s, self.e_r2e):
            spin.valueChanged.connect(self.refresh_results)

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
        self.b_temp = QtWidgets.QPushButton("取得")
        self.b_temp.setMaximumWidth(44)
        self.b_temp.setToolTip("SwitchBot温湿度計から測定温度を取得"
                               "（settings.jsonのswitchbot_token等を設定）")
        self.b_temp.clicked.connect(self.fetch_temp_from_switchbot)
        self.b_save = QtWidgets.QPushButton("セーブ")
        self.b_save.setEnabled(False)
        self.b_print = QtWidgets.QPushButton("印刷")
        self.b_print.setEnabled(False)
        self.b_raw = QtWidgets.QPushButton("生データ")
        self.b_raw.clicked.connect(self.show_raw_data)
        self.b_past = QtWidgets.QPushButton("過去データ")
        self.b_past.clicked.connect(self.show_past_data)
        self.b_cond = QtWidgets.QPushButton("条件編集")
        self.b_cond.clicked.connect(self.show_condition_editor)
        self.b_program = QtWidgets.QPushButton("プログラム作成")
        self.b_program.setToolTip("現在の測定条件からFANUC測定プログラム(Gコード)を作成")
        self.b_program.clicked.connect(self.show_program_dialog)
        b_load = QtWidgets.QPushButton("ロード")
        b_settings = QtWidgets.QPushButton("設定")
        self.b_save.clicked.connect(self.save)
        self.b_print.clicked.connect(self.print_report)
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
        row2.addWidget(self.b_temp)
        row2.addStretch(1)
        row2.addWidget(self.b_save)
        row2.addWidget(self.b_print)
        row2.addWidget(self.b_raw)
        row2.addWidget(self.b_past)
        row2.addWidget(self.b_cond)
        row2.addWidget(self.b_program)
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
        # 主点評価: 測定の等分数より粗い格子で精度を評価し直す（例 72等分測定→12等分評価）
        self.e_evald = QtWidgets.QSpinBox()
        self.e_evald.setRange(0, 720)
        self.e_evald.setSpecialValueText("なし")
        self.e_evald.setSuffix(" 等分")
        self.e_evald.valueChanged.connect(self.refresh_results)
        self.l_evald = QtWidgets.QLabel("主点評価")
        row3.addWidget(QtWidgets.QLabel("コメント"))
        row3.addWidget(self.e_comment, 1)
        row3.addWidget(self.l_evald)
        row3.addWidget(self.e_evald)
        row3.addWidget(self.l_blcorr)
        row3.addWidget(self.e_blcorr)
        row3.addWidget(self.b_corr)

        # --- ガイドと受信値・データ数 ---
        self.guide = QtWidgets.QLabel("―")
        self.guide.setStyleSheet("font-size:22px; font-family:monospace; padding:4px;")
        self.live = QtWidgets.QLabel("")
        self.live.setStyleSheet("font-size:15px; color:#666; padding:2px;")
        self.counts = QtWidgets.QLabel("")
        self.counts.setStyleSheet("font-size:15px; color:#444; padding:2px;")
        live_row = QtWidgets.QHBoxLayout()
        live_row.addWidget(self.live)
        live_row.addStretch(1)
        live_row.addWidget(self.counts)

        # --- グラフ（分割: 左ホイール/右ウォーム 7:3。再現性: 左のみ） ---
        self.plot_wheel = pg.PlotWidget(title="ホイール")
        self.plot_worm = pg.PlotWidget(title="ウォーム")
        for plot in (self.plot_wheel, self.plot_worm):
            plot.addLegend(offset=(10, 10))
            plot.setLabel("bottom", "指令角度", units="°")
            plot.setLabel("left", "偏差", units='"')
            plot.showGrid(x=True, y=True, alpha=0.3)
            # pyqtgraphの自動SI接頭辞を無効化。有効のままだと値が大きいとき
            # 単位が「k"」（キロ秒角）等に化けて読み違いのもとになる
            for axis_name in ("left", "bottom"):
                plot.getAxis(axis_name).enableAutoSIPrefix(False)
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
        v.addLayout(row_ranges)
        v.addLayout(row2)
        v.addLayout(row3)
        v.addLayout(row_ops)
        v.addWidget(self.guide)
        v.addLayout(live_row)
        v.addLayout(plots, 1)
        v.addLayout(tables)
        self.setCentralWidget(container)

        # 接続先プロファイル切替（X32直結 / X31変換器でポート・ボーレートを別管理）
        self.profile_combo = QtWidgets.QComboBox()
        for key, label in PROFILE_LABELS.items():
            self.profile_combo.addItem(label, key)
        index = self.profile_combo.findData(self.settings.get("active_profile", "X32"))
        if index >= 0:
            self.profile_combo.setCurrentIndex(index)
        self.profile_combo.currentIndexChanged.connect(self.on_profile_changed)
        self.statusBar().addPermanentWidget(QtWidgets.QLabel("接続先"))
        self.statusBar().addPermanentWidget(self.profile_combo)
        self.b_diag = QtWidgets.QPushButton("通信診断")
        self.b_diag.clicked.connect(self.run_diagnostics)
        self.statusBar().addPermanentWidget(self.b_diag)
        self.b_conn = QtWidgets.QPushButton("再接続")
        self.b_conn.clicked.connect(self.connect_device)
        self.statusBar().addPermanentWidget(self.b_conn)
        self._conn_done.connect(self.on_connect_done)
        self._diag_done.connect(self.on_diagnostics_done)
        self._temp_done.connect(self.on_temp_done)
        self._bot_msg.connect(self.on_bot_msg)
        self._remote_cmd.connect(self.on_remote_command)
        self._sync_done.connect(lambda m: self.statusBar().showMessage(m))
        self._command_signal.connect(self.on_command)
        # ウィンドウ表示後に接続（ポート探索はバックグラウンドで行うので画面は固まらない）
        QtCore.QTimer.singleShot(100, self.connect_device)

        # ND287側から送られてくるデータの受信ループ
        self.rx_timer = QtCore.QTimer(self)
        self.rx_timer.timeout.connect(self.poll_serial)
        self.rx_timer.start(200)

        # 型式マスタ（測定条件・合否判定）。型式入力で自動適用される
        self.master_cond = None
        self.master_judge = None
        try:
            self.masters = load_masters(self.settings)
        except Exception as e:
            self.masters = None
            QtCore.QTimer.singleShot(
                0, lambda: self.statusBar().showMessage(f"型式マスタの読み込みに失敗: {e}")
            )
        self.e_model.editingFinished.connect(self.on_model_entered)

        self.on_mode_changed(self.mode_combo.currentText())

        # Webモニタ（離れたPCのブラウザから閲覧・再測定指示）
        if self.settings.get("web_enabled"):
            try:
                from .webmonitor import WebMonitor
                port = int(self.settings.get("web_port") or 8765)
                self.web = WebMonitor(
                    self.web_status, self.web_png,
                    lambda cmd: self._remote_cmd.emit(cmd),
                    port=port, token=self.settings.get("web_token", ""),
                )
                self.web.start()
                self.statusBar().showMessage(
                    f"Webモニタ起動: http://<このPCのIP>:{port}/ "
                    f"{'（token必須）' if self.settings.get('web_token') else ''}"
                )
            except Exception as e:
                self.web = None
                self.statusBar().showMessage(f"Webモニタ起動失敗: {e}")
        self.update_web_snapshot()

        # Webアプリ連動（時間取り→測定→時間取り終了）の指令監視
        self.active_work_id = None     # 現在処理中の作業ID（相関ID）
        self.cmd_bus = None
        self._handled_commands = set()
        if (self.settings.get("webapp_commands_enabled")
                and str(self.settings.get("webapp_station") or "").strip()):
            self.cmd_bus = FirestoreSync(
                api_key=str(self.settings.get("webapp_api_key") or ""),
                project_id=str(self.settings.get("webapp_project_id") or ""),
                app_data_id=str(self.settings.get("webapp_data_id") or ""),
                collection=str(self.settings.get("webapp_command_collection")
                               or "rotaryCommands"),
            )
            self.cmd_timer = QtCore.QTimer(self)
            self.cmd_timer.timeout.connect(self.poll_commands)
            interval = int(float(self.settings.get("webapp_command_poll_sec") or 3.0) * 1000)
            self.cmd_timer.start(max(interval, 1000))
            self.statusBar().showMessage(
                f"Webアプリ連動 監視中（ステーション {self.settings.get('webapp_station')}）")

    # ----- Webアプリ連動（指令の監視と実行） -----

    def poll_commands(self):
        """Firestoreの指令を監視し、自ステーション宛の未処理分を実行する"""
        if self.cmd_bus is None or self._connecting:
            return
        station = str(self.settings.get("webapp_station") or "").strip()

        def work():
            try:
                docs = self.cmd_bus.list_documents()
            except Exception:
                return
            pending = []
            for doc_id, fields in docs:
                if (str(fields.get("station") or "") == station
                        and str(fields.get("status") or "") == "pending"
                        and doc_id not in self._handled_commands):
                    pending.append((doc_id, fields))
            for doc_id, fields in pending:
                self._handled_commands.add(doc_id)
                self._command_signal.emit({"id": doc_id, **fields})

        threading.Thread(target=work, daemon=True).start()

    def on_command(self, command):
        """指令をGUIスレッドで実行する"""
        ctype = command.get("type")
        work_id = command.get("workId") or command.get("work_id")
        if ctype == "prepare":
            self.handle_prepare(command, work_id)
        elif ctype == "start_capture":
            self.handle_start_capture(command, work_id)
        # 処理済みにする＋必要なイベントを返す
        self._finish_command(command, work_id, ctype)

    def handle_prepare(self, command, work_id):
        """準備: 前面化＋型式・機番・モードをセットしてマスタ自動適用、取込待ち"""
        self.active_work_id = work_id
        mode = command.get("mode")
        if mode in MODES:
            self.mode_combo.setCurrentText(mode)
        if command.get("model"):
            self.e_model.setText(str(command["model"]))
        if command.get("machine"):
            self.e_machine.setText(str(command["machine"]))
        self.on_model_entered()  # マスタ自動適用
        self.raise_()
        self.activateWindow()
        self.statusBar().showMessage(
            f"Webアプリ連動: 準備（{command.get('machine', '')} / {command.get('model', '')}）")

    def handle_start_capture(self, command, work_id):
        """測定開始: 自動測定を起動（SwitchBot ON ならNCスタートも）"""
        self.active_work_id = work_id
        missing = self.missing_required_fields()
        if missing:
            self.push_event(work_id, "error",
                            message="必須項目不足: " + "、".join(missing))
            self.statusBar().showMessage(
                "Webアプリ連動: 測定開始できず（" + "、".join(missing) + "）")
            return
        self.auto_start()
        self.push_event(work_id, "capturing")
        self.statusBar().showMessage("Webアプリ連動: 測定開始（自動測定）")

    def _finish_command(self, command, work_id, ctype):
        bus = self.cmd_bus
        if bus is None:
            return
        doc_id = command.get("id")
        event_type = "ready" if ctype == "prepare" else None

        def work():
            try:
                bus.update_fields(doc_id, {"status": "done"})
            except Exception:
                pass
            if event_type:
                self._write_event(work_id, event_type, {})

        threading.Thread(target=work, daemon=True).start()

    def push_event(self, work_id, event_type, **extra):
        """アプリ→Webのイベントをバックグラウンドで送る"""
        if self.cmd_bus is None or not work_id:
            return
        threading.Thread(
            target=self._write_event, args=(work_id, event_type, extra), daemon=True
        ).start()

    def _write_event(self, work_id, event_type, extra):
        import time as _t
        bus = self.cmd_bus
        if bus is None:
            return
        station = str(self.settings.get("webapp_station") or "")
        doc = dict(workId=work_id, station=station, type=event_type,
                   createdAtEpoch=int(_t.time() * 1000), **(extra or {}))
        doc_id = f"{work_id}_{event_type}_{int(_t.time() * 1000)}"
        try:
            bus.push_document(doc_id, doc,
                              collection=str(self.settings.get("webapp_event_collection")
                                             or "rotaryEvents"))
        except Exception:
            pass

    def notify_measurement_done(self):
        """測定完了をWebへ通知（時間取り終了のトリガ）。active_work_idがある時だけ"""
        if self.cmd_bus is None or not self.active_work_id:
            return
        judgement = overall_judgement(self.collect_result_rows())
        self.push_event(self.active_work_id, "done", judgement=judgement,
                        machine=self.e_machine.text().strip())
        self.active_work_id = None

    # ----- 型式マスタ -----

    def reload_masters(self):
        """マスタCSV（提供＋ユーザー登録）を読み直す"""
        try:
            self.masters = load_masters(self.settings)
        except Exception as e:
            self.statusBar().showMessage(f"マスタ再読込に失敗: {e}")

    def show_condition_editor(self):
        """現在のモードに応じた条件登録/編集ダイアログを開く（回転/傾斜を分離）"""
        ConditionRegistryDialog(self, tilt=self.is_tilt()).exec()

    def show_past_data(self):
        PastDataDialog(self).exec()

    def refresh_master_refs(self):
        """型式に対応するマスタ参照だけ更新する（入力欄は書き換えない。ロード用）。

        傾斜系は回転マスタを参照しない（判定・測定順の誤用防止）。
        """
        text = self.e_model.text().strip()
        if not self.masters:
            return
        if self.is_tilt():
            self.master_cond = None
            self.master_judge = None
            return
        self.master_cond = find_entry(self.masters["conditions"], text)
        self.master_judge = find_entry(self.masters["judgement"], text)

    def on_model_entered(self):
        """型式が入力されたら測定条件・合否判定を自動適用する（モード別に厳密に分離）。

        傾斜系モードは傾斜専用マスタのみを参照し、回転分割用の条件・温度規格は
        一切使わない（誤用防止）。回転系・合体は ユーザー回転条件 → 提供測定条件 の順。
        """
        from .masters import user_condition_params
        text = self.e_model.text().strip()
        if not self.masters or not text:
            return
        key = text.upper()

        if self.is_tilt():
            # ★傾斜：回転の測定条件・規格は絶対に使わない
            self.master_cond = None
            self.master_judge = None
            rec = self.masters.get("user_tilt", {}).get(key)
            if rec:
                p = user_condition_params(rec, tilt=True)
                self.e_wstart.setValue(p["wheel_start"])
                self.e_wend.setValue(p["wheel_end"])
                self.e_wheel.setValue(p["wheel_pitch"])
                self.e_worm.setValue(p["worm_pitch"])
                self.e_range.setValue(p["worm_range"])
                self.e_start.setValue(p["worm_start"])
                self.statusBar().showMessage(
                    f"{text} 傾斜条件を適用: {p['wheel_start']:g}〜{p['wheel_end']:g}°"
                    f"・刻み{p['wheel_pitch']:g}°")
            else:
                self.statusBar().showMessage(
                    f"型式 {text} は傾斜条件マスタに未登録（手入力。「条件編集」で登録できます）")
            return

        # 回転系・合体：ユーザー回転条件を優先、無ければ提供の測定条件CSV
        user = self.masters.get("user_rotary", {}).get(key)
        if user:
            p = user_condition_params(user, tilt=False)
            self.e_wheel.setValue(p["wheel_pitch"])
            self.e_worm.setValue(p["worm_pitch"])
            self.e_range.setValue(p["worm_range"])
            self.e_start.setValue(p["worm_start"])
            self.master_cond = None  # ユーザー登録は測定順なし＝既定順
            self.master_judge = find_entry(self.masters["judgement"], text)
            self.statusBar().showMessage(f"{text} ユーザー回転条件を適用")
            return

        cond = find_entry(self.masters["conditions"], text)
        judge = find_entry(self.masters["judgement"], text)
        self.master_cond = cond
        self.master_judge = judge
        if cond is None and judge is None:
            self.statusBar().showMessage(f"型式 {text} はマスタに見つかりません（手入力で測定可）")
            return
        message = []
        if cond:
            params = condition_params(cond, judge)
            if "wheel_pitch" in params:
                self.e_wheel.setValue(params["wheel_pitch"])
            if "worm_pitch" in params:
                self.e_worm.setValue(params["worm_pitch"])
            if "worm_range" in params:
                self.e_range.setValue(params["worm_range"])
            self.e_start.setValue(0.0)
            message.append(
                f"測定条件: ホイール{self.e_wheel.value():g}°刻み"
                f"・ウォーム{self.e_worm.value():g}°×{self.e_range.value():g}°"
                f"・測定順{'→'.join(params['order'])}"
            )
        if judge:
            temp = self.parse_temp()
            mm = formula_minmax(judge, temp if temp is not None else 20.0)
            if mm:
                message.append(f"規格 {mm[0]:.1f}〜{mm[1]:.1f}\"")
        self.statusBar().showMessage(f"{text} マスタ適用: " + "　".join(message))

    # ----- モード -----

    def current_mode(self):
        return self.mode_combo.currentText()

    def is_tilt(self):
        return self.current_mode().startswith("傾斜")

    def is_repeat(self):
        return self.current_mode() in ("回転再現性", "傾斜再現性")

    def is_combined(self):
        return "+再現" in self.current_mode()

    def on_mode_changed(self, mode):
        is_tilt, is_repeat, is_combined = self.is_tilt(), self.is_repeat(), self.is_combined()
        show_division = not is_repeat   # 分割系（単独 or 合体）で分割入力を出す
        show_repeat_params = is_repeat or is_combined
        for w in (self.l_wstart, self.e_wstart, self.l_wend, self.e_wend):
            w.setVisible(is_tilt)
        for w in (
            self.l_worm, self.e_worm, self.l_range, self.e_range, self.l_start, self.e_start,
            self.l_wheel, self.e_wheel,
        ):
            w.setVisible(show_division)
        for w in (self.l_blocks, self.e_blocks, self.l_repeats, self.e_repeats,
                  self.l_rstart, self.e_rstart, self.l_rend, self.e_rend):
            w.setVisible(show_repeat_params)
        for w in (self.l_blcorr, self.e_blcorr, self.b_corr,
                  self.l_evald, self.e_evald):
            w.setVisible(show_division)
        for w in self.range_widgets:
            w.setVisible(is_tilt and show_division)
        self.l_wheel.setText("刻み" if is_tilt else "ホイール刻み")
        self.plot_worm.setVisible(show_division)
        self.plot_wheel.setTitle(
            "再現性（ブロックごとのばらつき）" if is_repeat else "ホイール")
        # モードを変えたら取込中の測定はキャンセル
        self.view_kind = "repeat" if is_repeat else ("combined" if is_combined else "indexing")
        self.discard_measurement()

    def discard_measurement(self):
        """取込中の測定を破棄して初期状態に戻す"""
        self.seq = None
        if self.view_kind == "repeat":
            self.rep_points = None
            self.rep_data = None
        elif self.view_kind == "combined":
            self.data = None
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
        self.b_print.setEnabled(False)
        self.b_corr.setEnabled(False)
        self.guide.setText("―")
        self.live.setText("")
        self.counts.setText("")
        self.update_web_snapshot(with_png=True)

    def cancel(self):
        """取込中の測定を中止する（取込済みデータは破棄。自動測定も停止）"""
        if self.seq is None:
            return
        self.auto_mode = False
        taken = self.seq.idx
        self.discard_measurement()
        self.statusBar().showMessage(f"取込を中止しました（{taken}点破棄）")

    # ----- Webモニタ -----

    def web_status(self):
        """HTTPスレッドから呼ばれる: スナップショットを返す（プレーンデータのみ）"""
        with self._web_lock:
            return dict(self._web_state)

    def web_png(self):
        with self._web_lock:
            return self._web_png

    def collect_result_rows(self):
        """結果の (項目, 値) 一覧。Webモニタ・Webアプリ送信で共用"""
        if not (self.has_view_data() and not self.b_take.isEnabled()):
            return []
        try:
            if self.view_kind == "repeat":
                rsum = repeatability_summary(self.rep_points, self.rep_data)
                return repeat_result_rows(rsum)
            # 系列＋バックラッシ・判定・総合・主点・傾き判定・任意誤差
            from .export import series_rows
            summary, _ = summarize(self.data, self.applied_blcorr)
            results = series_rows(summary)
            results += misc_rows(summary, self.current_judgements(summary))
            results += self.composite_backlash_rows()
            results += self.main_grid_rows()
            results += self.slope_judgement_rows(summary)
            if self.is_tilt():
                results += self.tilt_accuracy_rows()
            if self.is_combined() and self.rep_data:
                results.append(("― 再現性 ―", ""))
                results += repeat_result_rows(
                    repeatability_summary(self.rep_points, self.rep_data))
            return results
        except Exception:
            return []

    def update_web_snapshot(self, with_png=False):
        """GUIスレッドで現在の状態をスナップショット化してWebモニタへ公開する"""
        if self.web is None:
            return
        if self.seq is not None and not self.seq.done():
            state = f"測定中 {self.seq.idx}/{len(self.seq)}"
            if self.auto_mode:
                state += f"（自動測定 再測定{self.auto_retries}回目）" if self.auto_retries else "（自動測定）"
        elif self.has_view_data():
            state = "測定完了"
        else:
            state = "待機中"
        results = self.collect_result_rows()
        snapshot = dict(
            state=state,
            timestamp=QtCore.QDateTime.currentDateTime().toString("HH:mm:ss"),
            meta=dict(
                model=self.e_model.text(), machine=self.e_machine.text(),
                operator=self.e_operator.text(), temperature=self.e_temp.text(),
                mode=self.current_mode(),
            ),
            counts=self.counts.text(),
            results=[[k, v] for k, v in results],
        )
        with self._web_lock:
            self._web_state = snapshot
        if with_png:
            image = self.plot_wheel.grab().toImage()
            buffer = QtCore.QBuffer()
            buffer.open(QtCore.QIODevice.WriteOnly)
            image.save(buffer, "PNG")
            with self._web_lock:
                self._web_png = bytes(buffer.data())

    def on_remote_command(self, command):
        """Webモニタからの指示（GUIスレッドで実行）"""
        if command == "remeasure":
            self.statusBar().showMessage("Webモニタから再測定指示を受信")
            if self.seq is not None and not self.seq.done():
                self.cancel()
            self.auto_start()
            self.update_web_snapshot(with_png=True)

    # ----- 自動測定（SwitchBotで機械起動 + 傾きNG自動再測定） -----

    def auto_start(self):
        """自動測定: 取込開始→SwitchBotで機械起動→完了後に傾き判定→NGなら再測定"""
        self.start()
        if self.seq is None or not self.b_take.isEnabled():
            return  # 必須項目の検証で開始できなかった
        self.auto_mode = True
        self.auto_retries = 0
        if not bot_configured(self.settings):
            self.statusBar().showMessage(
                "自動測定（SwitchBot未設定のため物理押下はスキップ。"
                "機械は手動で起動してください）"
            )
        self.trigger_bot()

    def trigger_bot(self):
        """SwitchBot Botを押しパターンに従ってバックグラウンドで起動する"""
        settings = dict(self.settings)
        patterns = settings.get("switchbot_patterns") or DEFAULT_PATTERNS
        name = settings.get("switchbot_pattern_name") or "1回押し"
        pattern = patterns.get(name) or [0.0]
        wait_before = float(settings.get("auto_wait_before_press") or 0.0)
        configured = bot_configured(settings)

        def work():
            if wait_before > 0:
                time.sleep(wait_before)
            total = len(pattern)
            for i, wait_after in enumerate(pattern, start=1):
                if not configured:
                    self._bot_msg.emit(f"SwitchBot未設定: 押下スキップ {i}/{total}")
                else:
                    ok, message = press_bot(settings)
                    self._bot_msg.emit(
                        f"SwitchBot {i}/{total}: {'OK' if ok else 'NG'} {message}")
                if wait_after > 0:
                    time.sleep(wait_after)

        threading.Thread(target=work, daemon=True).start()

    def on_bot_msg(self, message):
        self.statusBar().showMessage(message)

    def auto_judge_ok(self):
        """自動測定の合否: マスタの傾きH/W規格と突き合わせる（無ければOK扱い）"""
        if self.view_kind != "indexing" or not self.master_judge:
            return True
        summary, _ = summarize(self.data, self.applied_blcorr)
        for key, limit in (
            ("wheel_cw", self.master_judge.get("slope_h")),
            ("wheel_ccw", self.master_judge.get("slope_h")),
            ("worm_cw", self.master_judge.get("slope_w")),
            ("worm_ccw", self.master_judge.get("slope_w")),
        ):
            if limit and key in summary and abs(summary[key]["slope"]) > limit:
                return False
        return True

    def auto_after_complete(self):
        """測定完了時の自動測定の続き: 傾きOKなら終了、NGなら自動再測定"""
        if not self.auto_mode:
            return
        if self.auto_judge_ok():
            self.auto_mode = False
            self.statusBar().showMessage("自動測定完了: 傾きOK")
            return
        max_retries = int(self.settings.get("auto_max_retries") or 0)
        if self.auto_retries >= max_retries:
            self.auto_mode = False
            self.statusBar().showMessage(
                f"自動測定終了: 傾きNGのまま再測定上限（{max_retries}回）に到達"
            )
            return
        self.auto_retries += 1
        self.statusBar().showMessage(
            f"傾きNG → 自動再測定 {self.auto_retries}/{max_retries}"
        )
        self.start()
        if self.seq is None:
            self.auto_mode = False
            return
        self.auto_mode = True  # start()はauto_modeに触らないが明示
        self.trigger_bot()

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

    def on_profile_changed(self):
        """接続先プロファイル（X32/X31）の切替 → 設定を読み替えて再接続"""
        key = self.profile_combo.currentData()
        if not key or key == self.settings.get("active_profile"):
            return
        self.settings["active_profile"] = key
        apply_active_profile(self.settings)
        try:
            save_settings(self.settings)
        except Exception as e:
            self.statusBar().showMessage(f"設定の保存に失敗: {e}")
        if not self.dev.dummy:
            self.dev.close()
            self.dev = ND287Device(
                self.settings["port"], self.settings["baudrate"], self.settings["parity"]
            )
            self.connect_device()
        else:
            self.statusBar().showMessage(
                f"接続先を {PROFILE_LABELS[key]} に切替（ダミーモード中）"
            )

    def open_settings(self):
        dlg = SettingsDialog(self, self.settings)
        dlg.setWindowTitle(
            f"設定（接続先: {PROFILE_LABELS[self.settings['active_profile']]}）"
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        values = dlg.values()
        self.settings.update(values)
        # 接続設定は現在アクティブなプロファイルにも保存する
        self.settings["profiles"][self.settings["active_profile"]].update(
            {k: values[k] for k in ("port", "baudrate", "parity")}
        )
        try:
            save_settings(self.settings)
        except Exception as e:
            self.statusBar().showMessage(f"設定の保存に失敗: {e}")
            return
        # 条件CSVの場所が変わった可能性があるので読み直す
        self.reload_masters()
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

    def repeat_blocks(self):
        """現在の設定での再現ブロック角度リスト。

        再現開始・再現終了・ブロック数で「両端を含む等間隔」を作る。
        例: 開始0・終了270・4箇所 → 0,90,180,270
        """
        return tilt_blocks(self.e_rstart.value(), self.e_rend.value(),
                           self.e_blocks.value())

    def build_division_sequence(self):
        wheel_start = self.e_wstart.value() if self.is_tilt() else 0.0
        wheel_end = self.e_wend.value() if self.is_tilt() else 360.0
        # マスタの測定順（HR/WR/WL/HL）があれば従う
        order = None
        if self.master_cond and not self.is_tilt():
            sections = self.master_cond.get("order") or []
            order = [SECTION_TO_SERIES[s] for s in sections if s in SECTION_TO_SERIES]
        return IndexingSequence(
            self.e_wheel.value(),
            self.e_worm.value(),
            self.e_range.value(),
            self.e_start.value(),
            wheel_start,
            wheel_end,
            order=order or None,
        )

    def build_sequence(self):
        if self.is_repeat():
            return RepeatabilitySequence(self.repeat_blocks(), self.e_repeats.value())
        if self.is_combined():
            # 分割→再現（再現はCW/CCW1サイクルずつ交互＝FANUC合体プログラムと同順）
            division = self.build_division_sequence()
            repeat = RepeatabilitySequence(self.repeat_blocks(), self.e_repeats.value(),
                                           interleave=True)
            return CombinedSequence(division, repeat)
        return self.build_division_sequence()

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
        elif self.is_combined():
            self.data = self.seq.data
            self.rep_points = self.seq.rep_points
            self.rep_data = self.seq.rep_data
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
        self.b_print.setEnabled(False)
        self.update_counts()
        self.show_guide()
        self.update_web_snapshot(with_png=True)

    def show_guide(self):
        text = self.seq.guide_text()
        if text is not None:
            self.guide.setText(text)

    def update_counts(self):
        """系列ごとの「現在のデータ数/必要数」を表示する（例 ホイール CW 5/37）"""
        if self.seq is not None:
            parts = [f"{label} {cur}/{req}" for label, cur, req in self.seq.counts()]
        elif self.view_kind == "indexing" and self.data:
            parts = [
                f"{SERIES_LABELS[key]} {len(t)}/{len(t)}"
                for key, (t, _) in self.data.items()
                if t
            ]
        elif self.view_kind == "repeat" and self.rep_data:
            totals = {"cw": 0, "ccw": 0}
            for (dirn, _), vals in self.rep_data.items():
                totals[dirn] += len(vals)
            parts = [f"CW {totals['cw']}/{totals['cw']}", f"CCW {totals['ccw']}/{totals['ccw']}"]
        else:
            self.counts.setText("")
            return
        self.counts.setText("データ数:  " + "　".join(parts))

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
        except Exception as e:
            # 取込中の受信エラーは見えるようにする（USB抜け・ポート消失など）
            self.statusBar().showMessage(f"受信エラー: {e} → ケーブル/ポートを確認して再接続")
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
        self.update_counts()
        self.redraw()
        if self.seq.done():
            self.guide.setText("測定完了 → セーブで保存")
            self.finish()
            self.update_web_snapshot(with_png=True)
            self.notify_measurement_done()
            self.auto_after_complete()
        else:
            self.show_guide()
            self.update_web_snapshot(with_png=(self.seq.idx % 5 == 0))

    def undo(self):
        if self.seq and self.seq.undo():
            self.table_series.setRowCount(0)
            self.table_misc.setRowCount(0)
            self.b_save.setEnabled(False)
            self.b_print.setEnabled(False)
            self.b_corr.setEnabled(False)
            self.b_take.setEnabled(True)
            self.b_undo.setEnabled(self.seq.idx > 0)
            self.update_counts()
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

    def refresh_results(self):
        """評価範囲の変更で結果表を再計算する（測定完了後のみ）"""
        if (self.view_kind in ("indexing", "combined") and self.data
                and not self.b_take.isEnabled()):
            if any(t for t, _ in self.data.values()):
                self.finish_indexing()

    def current_judgements(self, summary):
        """温度別合否判定文。型式マスタの温度式を優先し、無ければ規格帯設定を使う

        傾斜分割はバックラッシの合否判定をしない（旧アプリと同じ）。
        """
        if self.is_tilt():
            return {}
        temp = self.parse_temp()
        mm = formula_minmax(self.master_judge, temp)
        if mm is None:
            return judgement_texts(summary, temp, self.settings.get("judgement_spec"))
        spec_min, spec_max = mm
        texts = {}
        for key in ("wheel_backlash", "worm_backlash"):
            if key in summary:
                ok = spec_min <= summary[key]["min"] and summary[key]["max"] <= spec_max
                texts[key] = (
                    f'{"OK" if ok else "NG"}'
                    f'（規格 {spec_min:.1f}〜{spec_max:.1f}" @ {temp:g}°C）'
                )
        return texts

    def slope_judgement_rows(self, summary):
        """型式マスタの傾きH/W規格との突き合わせ（|傾き| ≦ 規格）"""
        rows = []
        if not self.master_judge:
            return rows
        for key, label, limit in (
            ("wheel_cw", "ホイールCW", self.master_judge.get("slope_h")),
            ("wheel_ccw", "ホイールCCW", self.master_judge.get("slope_h")),
            ("worm_cw", "ウォームCW", self.master_judge.get("slope_w")),
            ("worm_ccw", "ウォームCCW", self.master_judge.get("slope_w")),
        ):
            if limit and key in summary:
                slope = summary[key]["slope"]
                ok = abs(slope) <= limit
                rows.append(
                    (f"{label} 傾き 判定",
                     f'{"OK" if ok else "NG"}（|{slope:+.2f}"| ≦ {limit:g}"）')
                )
        return rows

    def finish(self):
        self.b_take.setEnabled(False)
        self.b_cancel.setEnabled(False)
        self.b_save.setEnabled(True)
        self.b_print.setEnabled(True)
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

        # 右表: バックラッシMIN/MAX・温度規格による合否・傾き判定・真の最大最小
        rows = misc_rows(summary, self.current_judgements(summary))
        rows.extend(self.composite_backlash_rows())
        rows.extend(self.main_grid_rows())
        rows.extend(self.slope_judgement_rows(summary))
        if self.is_tilt():
            rows.extend(self.tilt_accuracy_rows())
        if self.is_combined() and self.rep_data:
            rows.append(("― 再現性 ―", ""))
            rows.extend(repeat_result_rows(
                repeatability_summary(self.rep_points, self.rep_data)))
        self.fill_misc_table(rows)

    def main_grid_rows(self):
        """主点評価: 測定より粗い任意の等分数で精度を評価し直す（例 72等分→12等分）"""
        divisions = self.e_evald.value()
        if divisions <= 0:
            return []
        targets = sorted(self.data.get("wheel_cw", ([], []))[0])
        intervals = len(targets) - 1
        if intervals <= 0:
            return []
        if intervals % divisions != 0 or divisions > intervals:
            return [("主点評価", f"{divisions}等分は測定{intervals}等分と割り切れません")]
        step = intervals // divisions
        rows = []
        for key, label in (("wheel_cw", "ホイールCW"), ("wheel_ccw", "ホイールCCW")):
            t, m = self.data.get(key, ([], []))
            pairs = sorted(zip(t, m))
            devs = deviation_sec([p[0] for p in pairs], [p[1] for p in pairs])
            rows.append((f"主点精度 {label}（{divisions}等分）", f'{pp(devs[::step]):.2f}"'))
        return rows

    def composite_backlash_rows(self):
        """総合バックラッシ（ウォームを0°位置でホイールに合わせた機械全体の値）"""
        comp = composite_backlash_minmax(self.data)
        if comp is None:
            return []
        comp_min = comp[0] + self.applied_blcorr
        comp_max = comp[1] + self.applied_blcorr
        rows = [
            ("総合バックラッシ MIN（0°合わせ）", f'{comp_min:.2f}"'),
            ("総合バックラッシ MAX（0°合わせ）", f'{comp_max:.2f}"'),
        ]
        if not self.is_tilt():
            temp = self.parse_temp()
            mm = formula_minmax(self.master_judge, temp)
            if mm:
                ok = mm[0] <= comp_min and comp_max <= mm[1]
                rows.append((
                    "総合バックラッシ 判定",
                    f'{"OK" if ok else "NG"}'
                    f'（規格 {mm[0]:.1f}〜{mm[1]:.1f}" @ {temp:g}°C）',
                ))
        return rows

    def tilt_accuracy_rows(self):
        """傾斜分割の任意誤差評価（精度 = ホイール精度 + ウォーム精度）"""
        specs = [("全範囲", None)]
        if self.c_r1.isChecked():
            specs.append((f"範囲1 {self.e_r1s.value():g}〜{self.e_r1e.value():g}°",
                          (self.e_r1s.value(), self.e_r1e.value())))
        if self.c_r2.isChecked():
            specs.append((f"範囲2 {self.e_r2s.value():g}〜{self.e_r2e.value():g}°",
                          (self.e_r2s.value(), self.e_r2e.value())))
        rows = []
        for label, range_ in specs:
            acc = tilt_accuracy(self.data, range_=range_)
            for dirn, jp in (("cw", "正"), ("ccw", "逆")):
                entry = acc.get(dirn, {})
                if "total" in entry:
                    rows.append((
                        f"任意誤差 {jp}（{label}）",
                        f'H {entry["h"]:.1f}" + W {entry["w"]:.1f}" = {entry["total"]:.1f}"',
                    ))
        return rows

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

    def has_repeat_data(self):
        return bool(self.rep_data)

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
        if self.is_repeat() or self.is_combined():
            meta[META_KEYS["blocks"]] = self.e_blocks.value()
            meta[META_KEYS["repeats"]] = self.e_repeats.value()
            meta[META_KEYS["rep_start"]] = self.e_rstart.value()
            meta[META_KEYS["rep_end"]] = self.e_rend.value()
        if not self.is_repeat():
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
                # 合体測定は再現性も別CSV（機番_再現.csv）に保存
                if self.view_kind == "combined" and self.has_repeat_data():
                    rep_path = path.with_name(f"{path.stem}_再現{path.suffix}")
                    rsum = repeatability_summary(self.rep_points, self.rep_data)
                    save_repeat_csv(rep_path, self.rep_points, self.rep_data, rsum, meta)
            self.statusBar().showMessage(f"保存しました: {path}")
        except Exception as e:
            self.statusBar().showMessage(f"保存失敗: {e}")
            return
        if self.view_kind in ("indexing", "combined"):
            if self.is_tilt():
                self.save_ks_file(machine_no)
            else:
                self.save_bs_file(machine_no, summary)
        self.sync_to_webapp(machine_no, saved_files=[str(path)])

    def sync_to_webapp(self, machine_no, saved_files=None):
        """product-inspection（Firestore）へ測定結果を送信する（セーブ時）"""
        if not self.settings.get("webapp_sync_enabled"):
            return
        sync = FirestoreSync(
            api_key=str(self.settings.get("webapp_api_key") or ""),
            project_id=str(self.settings.get("webapp_project_id") or ""),
            app_data_id=str(self.settings.get("webapp_data_id") or ""),
            collection=str(self.settings.get("webapp_collection") or "rotaryMeasurements"),
        )
        if not sync.configured():
            self.statusBar().showMessage("Web連携が未設定です（settings.jsonのwebapp_*）")
            return
        plot_png = None
        if self.settings.get("webapp_send_png"):
            image = self.plot_wheel.grab().toImage()
            buffer = QtCore.QBuffer()
            buffer.open(QtCore.QIODevice.WriteOnly)
            image.save(buffer, "PNG")
            plot_png = bytes(buffer.data())
        document = build_measurement_doc(
            model=self.e_model.text().strip(),
            machine=machine_no,
            operator=self.e_operator.text().strip(),
            date=self.e_date.date().toString("yyyy-MM-dd"),
            temperature=self.e_temp.text().strip(),
            mode=self.current_mode(),
            results=self.collect_result_rows(),
            comment=self.e_comment.text().strip(),
            plot_png=plot_png,
            saved_files=saved_files,
        )
        doc_id = (f"{sanitize_filename(machine_no)}_"
                  f"{QtCore.QDateTime.currentDateTime().toString('yyyyMMdd-HHmmss')}")

        def work():
            ok, message = sync.push_document(doc_id, document)
            self._sync_done.emit(message)

        threading.Thread(target=work, daemon=True).start()

    def save_bs_file(self, machine_no, summary):
        """旧形式(.BS)を併せて保存する（検査表システム互換、分割測定のみ）"""
        bs_root = str(self.settings.get("bs_save_root") or "").strip()
        if not bs_root:
            return
        try:
            mm = formula_minmax(self.master_judge, self.parse_temp())
            if mm is None:
                band = band_for_temp(
                    (self.settings.get("judgement_spec") or {}).get("wheel_backlash"),
                    self.parse_temp() or 0.0,
                )
                mm = (band["min"], band["max"]) if band else (0.0, 0.0)
            doc = data_to_doc(
                self.data,
                summary,
                model=self.e_model.text().strip(),
                date=self.e_date.date().toString("yyyy/MM/dd"),
                operator=self.e_operator.text().strip(),
                temperature=self.e_temp.text().strip(),
                spec_min=mm[0],
                spec_max=mm[1],
                # 精度1の主点グリッドはマスタの1/N（例 RWE-200は30°主点=3おき）
                wheel_n=self.master_cond["n_h"] if self.master_cond else 1,
                worm_n=self.master_cond["n_w"] if self.master_cond else 1,
            )
            bs_path = Path(bs_root) / f"{sanitize_filename(machine_no)}.BS"
            bs_path.parent.mkdir(parents=True, exist_ok=True)
            save_bs(bs_path, doc)
            self.statusBar().showMessage(
                f"{self.statusBar().currentMessage()} ／ .BSも保存: {bs_path}"
            )
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "セーブ", f".BS（旧形式）の保存に失敗しました（CSVは保存済み）:\n{e}"
            )

    def load(self):
        root = resolve_save_root(self.settings)
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "測定データを開く", str(root),
            "測定データ (*.csv *.bs *.BS *.ks *.KS);;CSV (*.csv);;"
            "旧形式 回転 (*.bs *.BS);;旧形式 傾斜 (*.ks *.KS)",
        )
        if not path:
            return
        self.load_path(path)

    def load_path(self, path):
        """拡張子で判別して測定データを読み込む（過去データ閲覧などから呼ぶ）"""
        if path.lower().endswith(".bs"):
            self.load_bs_file(path)
            return
        if path.lower().endswith(".ks"):
            self.load_ks_file(path)
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
            (self.e_rstart, META_KEYS["rep_start"]),
            (self.e_rend, META_KEYS["rep_end"]),
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
        self.refresh_master_refs()
        self.update_counts()
        self.redraw()
        self.finish()
        self.guide.setText(f"ロード: {Path(path).name}")
        self.statusBar().showMessage(f"ロードしました: {path}")
        self.update_web_snapshot(with_png=True)

    def load_bs_file(self, path):
        """旧形式(.BS)を読み戻す。機番はファイル名から取る"""
        try:
            doc = load_bs(path)
            data = doc_to_data(doc)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "ロード", f".BSの読み込みに失敗しました:\n{e}")
            return
        if not any(t for t, _ in data.values()):
            QtWidgets.QMessageBox.warning(self, "ロード", "測定データが入っていないファイルです")
            return
        self.mode_combo.setCurrentText("回転分割")
        self.seq = None
        self.data = data
        self.e_model.setText(doc["model"])
        self.e_machine.setText(Path(path).stem)
        self.e_operator.setText(doc["operator"])
        self.e_temp.setText(doc["temperature"])
        date = QtCore.QDate.fromString(doc["date"], "yyyy/MM/dd")
        if date.isValid():
            self.e_date.setDate(date)
        wheel_targets = data["wheel_cw"][0]
        if len(wheel_targets) >= 2:
            self.e_wheel.setValue(abs(wheel_targets[1] - wheel_targets[0]))
        worm_targets = data["worm_cw"][0]
        if len(worm_targets) >= 2:
            self.e_worm.setValue(abs(worm_targets[1] - worm_targets[0]))
            self.e_range.setValue(worm_targets[-1] - worm_targets[0])
            self.e_start.setValue(worm_targets[0])
        self.applied_blcorr = 0.0
        self.e_blcorr.setValue(0.0)
        self.b_undo.setEnabled(False)
        self.live.setText("")
        self.refresh_master_refs()
        self.update_counts()
        self.redraw()
        self.finish()
        self.guide.setText(f"ロード(.BS): {Path(path).name}")
        self.statusBar().showMessage(f"ロードしました: {path}")
        self.update_web_snapshot(with_png=True)

    def save_ks_file(self, machine_no):
        """旧形式（傾斜分割 .KS）を併せて保存する（検査表システム互換）"""
        bs_root = str(self.settings.get("bs_save_root") or "").strip()
        if not bs_root:
            return
        try:
            range1 = ((self.e_r1s.value(), self.e_r1e.value())
                      if self.c_r1.isChecked() else None)
            range2 = ((self.e_r2s.value(), self.e_r2e.value())
                      if self.c_r2.isChecked() else None)
            order = [1, 2, 3, 4]
            if self.master_cond:
                # 測定順（HR,WR,WL,HLの順番号）をマスタから
                sections = self.master_cond.get("order") or []
                positions = {s: i + 1 for i, s in enumerate(sections)}
                order = [positions.get(s, 0) for s in ("HR", "WR", "WL", "HL")]
            doc = ks_data_to_doc(
                self.data,
                model=self.e_model.text().strip(),
                date=self.e_date.date().toString("yyyy/MM/dd"),
                operator=self.e_operator.text().strip(),
                range1=range1,
                range2=range2,
                b_corr=self.applied_blcorr,
                order=order,
            )
            ks_path = Path(bs_root) / f"{sanitize_filename(machine_no)}.KS"
            ks_path.parent.mkdir(parents=True, exist_ok=True)
            save_ks(ks_path, doc)
            self.statusBar().showMessage(
                f"{self.statusBar().currentMessage()} ／ .KSも保存: {ks_path}"
            )
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "セーブ", f".KS（旧形式）の保存に失敗しました（CSVは保存済み）:\n{e}"
            )

    def load_ks_file(self, path):
        """旧形式（傾斜分割 .KS）を読み戻す。機番はファイル名から取る"""
        try:
            doc = load_ks(path)
            data = ks_doc_to_data(doc)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "ロード", f".KSの読み込みに失敗しました:\n{e}")
            return
        if not any(t for t, _ in data.values()):
            QtWidgets.QMessageBox.warning(self, "ロード", "測定データが入っていないファイルです")
            return
        self.mode_combo.setCurrentText("傾斜分割")
        self.seq = None
        self.data = data
        self.e_model.setText(doc["model"])
        self.e_machine.setText(Path(path).stem)
        self.e_operator.setText(doc["operator"])
        date = QtCore.QDate.fromString(doc["date"], "yyyy/MM/dd")
        if date.isValid():
            self.e_date.setDate(date)
        wheel_targets = data["wheel_cw"][0]
        if len(wheel_targets) >= 2:
            self.e_wstart.setValue(wheel_targets[0])
            self.e_wend.setValue(wheel_targets[-1])
            self.e_wheel.setValue(abs(wheel_targets[1] - wheel_targets[0]))
        worm_targets = data["worm_cw"][0]
        if len(worm_targets) >= 2:
            self.e_worm.setValue(abs(worm_targets[1] - worm_targets[0]))
            self.e_range.setValue(worm_targets[-1] - worm_targets[0])
            self.e_start.setValue(worm_targets[0])
        # 評価範囲1/2をヘッダから復元（0.0001°単位）
        if doc.get("range1_start") is not None and doc.get("range1_end"):
            self.e_r1s.setValue(doc["range1_start"] * 1e-4)
            self.e_r1e.setValue(doc["range1_end"] * 1e-4)
            self.c_r1.setChecked(True)
        if doc.get("range2_start") is not None and doc.get("range2_end"):
            self.e_r2s.setValue(doc["range2_start"] * 1e-4)
            self.e_r2e.setValue(doc["range2_end"] * 1e-4)
            self.c_r2.setChecked(True)
        else:
            self.c_r2.setChecked(False)
        self.applied_blcorr = 0.0
        self.e_blcorr.setValue(0.0)
        self.b_undo.setEnabled(False)
        self.live.setText("")
        self.refresh_master_refs()
        self.update_counts()
        self.redraw()
        self.finish()
        self.guide.setText(f"ロード(.KS): {Path(path).name}")
        self.statusBar().showMessage(f"ロードしました: {path}")
        self.update_web_snapshot(with_png=True)

    # ----- 生データ・温度取得 -----

    def show_raw_data(self):
        """生データ（数値）の一覧を表示する（Ctrl+Shift+Eで管理者編集）"""
        if not self.has_view_data():
            self.statusBar().showMessage("表示する生データがありません")
            return
        RawDataDialog(self).exec()

    def show_program_dialog(self):
        """現在の測定条件からFANUC測定プログラムを作成するダイアログを開く"""
        if self.is_repeat():
            rotary = not self.is_tilt()
            wheel_start, wheel_end = (
                (self.e_wstart.value(), self.e_wend.value()) if self.is_tilt()
                else (0.0, 360.0))
            params = dict(
                rotary=rotary, include_division=False, include_repeat=True,
                wheel_pitch=self.e_wheel.value() if self.e_wheel.value() else 10.0,
                wheel_start=wheel_start, wheel_end=wheel_end,
                worm_pitch=self.e_worm.value(), worm_range=self.e_range.value(),
                worm_start=self.e_start.value(),
                blocks=self.repeat_blocks(), repeats=self.e_repeats.value(),
            )
        else:
            rotary = not self.is_tilt()
            wheel_start, wheel_end = (
                (self.e_wstart.value(), self.e_wend.value()) if self.is_tilt()
                else (0.0, 360.0))
            params = dict(
                rotary=rotary,
                include_division=True,
                include_repeat=self.is_combined(),
                wheel_pitch=self.e_wheel.value(),
                wheel_start=wheel_start, wheel_end=wheel_end,
                worm_pitch=self.e_worm.value(), worm_range=self.e_range.value(),
                worm_start=self.e_start.value(),
                blocks=self.repeat_blocks() if self.is_combined() else [],
                repeats=self.e_repeats.value(),
            )
        model = self.e_model.text().strip() or "MEASURE"
        params["title"] = f"{model} {self.current_mode()}"
        params["machine"] = self.e_machine.text().strip()
        ProgramDialog(self, self.settings, params).exec()

    def fetch_temp_from_switchbot(self):
        """SwitchBot温湿度計から測定温度を取得して入力欄に入れる"""
        token = str(self.settings.get("switchbot_token") or "")
        secret = str(self.settings.get("switchbot_secret") or "")
        device = str(self.settings.get("switchbot_device") or "")
        if not (token and secret and device):
            QtWidgets.QMessageBox.information(
                self, "温度取得",
                "SwitchBotが未設定です。settings.json に\n"
                "switchbot_token / switchbot_secret / switchbot_device\n"
                "を設定してください（SwitchBotアプリの開発者向けオプションで取得）",
            )
            return
        self.b_temp.setEnabled(False)
        self.statusBar().showMessage("SwitchBotから温度を取得中...")

        def work():
            try:
                temperature = fetch_temperature(token, secret, device)
                self._temp_done.emit(True, f"温度を取得: {temperature:g}°C", temperature)
            except Exception as e:
                self._temp_done.emit(False, f"温度取得に失敗: {e}", 0.0)

        threading.Thread(target=work, daemon=True).start()

    def on_temp_done(self, ok, message, temperature):
        self.b_temp.setEnabled(True)
        self.statusBar().showMessage(message)
        if ok:
            self.e_temp.setText(f"{temperature:g}")

    # ----- 印刷 -----

    def print_report(self):
        """A4一枚（＋分割モードはP補正ページ）の検査記録を印刷する"""
        if not self.has_view_data():
            return
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageOrientation(QtGui.QPageLayout.Landscape)
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("検査記録の印刷")
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        document = self.build_report_document()
        document.print_(printer)
        self.statusBar().showMessage("印刷しました")

    def _plot_image(self, plot):
        return plot.grab().toImage()

    def _render_series_plot(self, series, title):
        """印刷用に系列をオフスクリーン描画して画像にする"""
        plot = pg.PlotWidget(title=title)
        plot.resize(880, 360)
        plot.addLegend(offset=(10, 10))
        plot.setLabel("bottom", "指令角度", units="°")
        plot.setLabel("left", "偏差", units='"')
        plot.showGrid(x=True, y=True, alpha=0.3)
        for axis in ("left", "bottom"):
            plot.getAxis(axis).enableAutoSIPrefix(False)
        colors = {"wheel_cw": "#1f77b4", "wheel_ccw": "#d62728"}
        for key, (t, dev) in series.items():
            plot.plot(t, dev, pen=pg.mkPen(colors.get(key, "#2ca02c"), width=2),
                      symbol="o", symbolSize=4,
                      name=SERIES_LABELS.get(key, key))
        image = plot.grab().toImage()
        plot.deleteLater()
        return image

    def _meta_html(self):
        items = [
            ("型式", self.e_model.text()), ("機番", self.e_machine.text()),
            ("測定日", self.e_date.date().toString("yyyy/MM/dd")),
            ("測定者", self.e_operator.text()),
            ("測定温度", f"{self.e_temp.text()} °C"),
            ("モード", self.current_mode()),
        ]
        if self.is_tilt():
            items.append(("測定範囲",
                          f"{self.e_wstart.value():g}〜{self.e_wend.value():g}°"))
        items += [
            ("刻み", f"{self.e_wheel.value():g}°"),
            ("ウォーム", f"{self.e_worm.value():g}°×{self.e_range.value():g}°"),
        ]
        if self.applied_blcorr:
            items.append(("バックラッシ手動補正", f"{self.applied_blcorr:+.2f}\""))
        cells = "".join(
            f"<td style='border:1px solid #999; padding:1px 6px;'>"
            f"<b>{k}</b>: {v}</td>" for k, v in items
        )
        comment = self.e_comment.text().strip()
        comment_html = (f"<p style='margin:2px;'>コメント: {comment}</p>"
                        if comment else "")
        return (f"<table style='font-size:7pt;' cellspacing='0'><tr>{cells}</tr></table>"
                + comment_html)

    def _results_html(self, summary):
        head = "".join(f"<th style='border:1px solid #999; padding:1px 5px;'>{h}</th>"
                       for h in SERIES_METRIC_HEADERS)
        body = ""
        for key in SERIES_LABELS:
            if key not in summary:
                continue
            s = summary[key]
            cells = [SERIES_LABELS[key], f'{s["pp"]:.2f}"', f'{s["single"]:.2f}"',
                     f'{s["adjacent"]:.2f}"', f'{s["slope"]:+.2f}"']
            body += "<tr>" + "".join(
                f"<td style='border:1px solid #999; padding:1px 5px;'>{c}</td>"
                for c in cells) + "</tr>"
        series_table = (f"<table style='font-size:7pt;' cellspacing='0'>"
                        f"<tr>{head}</tr>{body}</table>")

        rows = misc_rows(summary, self.current_judgements(summary))
        rows.extend(self.composite_backlash_rows())
        rows.extend(self.main_grid_rows())
        rows.extend(self.slope_judgement_rows(summary))
        if self.is_tilt():
            rows.extend(self.tilt_accuracy_rows())
        if self.is_combined() and self.rep_data:
            rows.append(("― 再現性 ―", ""))
            rows.extend(repeat_result_rows(
                repeatability_summary(self.rep_points, self.rep_data)))
        misc = "".join(
            "<tr>"
            f"<td style='border:1px solid #999; padding:1px 5px;'>{item}</td>"
            f"<td style='border:1px solid #999; padding:1px 5px;"
            f"{' color:red;' if str(value).startswith('NG') else ''}'>{value}</td></tr>"
            for item, value in rows
        )
        misc_table = (f"<table style='font-size:7pt;' cellspacing='0'>{misc}</table>")
        return (f"<table width='100%'><tr><td valign='top'>{series_table}</td>"
                f"<td valign='top'>{misc_table}</td></tr></table>")

    def _pcorr_html(self, document):
        """P補正表＋補正後グラフのページ（分割モードのみ）"""
        interval = float(self.settings.get("p_interval") or 100000) * 1e-4
        unit = float(self.settings.get("p_unit") or 0.001)
        table = compensation_table(self.data, interval, unit)
        if not table:
            return ""
        corrected = apply_compensation(self.data, table)
        image = self._render_series_plot(corrected, "ピッチエラー補正後（シミュレーション）")
        document.addResource(QtGui.QTextDocument.ImageResource,
                             QtCore.QUrl("pcorr.png"), image)
        head = "".join(
            f"<th style='border:1px solid #999; padding:1px 5px;'>{h}</th>"
            for h in ("No", "角度[°]", "CW偏差[\"]", "CCW偏差[\"]",
                      "平均[\"]", f"補正値[{unit:g}°]")
        )
        body = ""
        for row in table:
            ccw = "―" if row["ccw"] is None else f'{row["ccw"]:.2f}'
            body += "<tr>" + "".join(
                f"<td style='border:1px solid #999; padding:1px 5px;"
                f" text-align:right;'>{c}</td>"
                for c in (row["no"], f'{row["angle"]:g}', f'{row["cw"]:.2f}',
                          ccw, f'{row["mean"]:.2f}', row["units"])
            ) + "</tr>"
        return (
            "<div style='page-break-before:always;'></div>"
            "<h3 style='margin:2px;'>ピッチエラー補正表"
            f"（間隔 {interval:g}°・単位 {unit:g}°）</h3>"
            "<p style='font-size:7pt; margin:2px;'>補正値はCW/CCW平均偏差を"
            "打ち消す向き。客先フォーマットは見本に合わせて要確認。</p>"
            f"<table style='font-size:7pt;' cellspacing='0'>"
            f"<tr>{head}</tr>{body}</table>"
            "<p><img src='pcorr.png' width='900'></p>"
        )

    def build_report_document(self):
        document = QtGui.QTextDocument()
        wheel_img = self._plot_image(self.plot_wheel)
        document.addResource(QtGui.QTextDocument.ImageResource,
                             QtCore.QUrl("wheel.png"), wheel_img)
        has_worm = (self.view_kind in ("indexing", "combined")
                    and bool(self.data.get("worm_cw", ([], []))[0]))
        if has_worm:
            worm_img = self._plot_image(self.plot_worm)
            document.addResource(QtGui.QTextDocument.ImageResource,
                                 QtCore.QUrl("worm.png"), worm_img)
            # ホイール:ウォーム = 7:3 で横並び（A4横）
            graphs = ("<table width='100%' cellspacing='0'><tr>"
                      "<td><img src='wheel.png' width='670'></td>"
                      "<td><img src='worm.png' width='287'></td>"
                      "</tr></table>")
        else:
            graphs = "<p style='margin:2px;'><img src='wheel.png' width='930'></p>"
        if self.view_kind == "repeat":
            rsum = repeatability_summary(self.rep_points, self.rep_data)
            rows = [(f"ブロック{i + 1} ({b['angle']:g}°)",
                     f'CW {b["cw"]:.2f}" / CCW {b["ccw"]:.2f}"')
                    for i, b in enumerate(rsum["blocks"])
                    if b["cw"] is not None and b["ccw"] is not None]
            rows += [("再現性 CW（全ブロック最大）", f'{rsum["cw"]:.2f}"'),
                     ("再現性 CCW（全ブロック最大）", f'{rsum["ccw"]:.2f}"'),
                     ("再現性 総合", f'{rsum["overall"]:.2f}"')]
            results = "<table style='font-size:7pt;' cellspacing='0'>" + "".join(
                f"<tr><td style='border:1px solid #999; padding:1px 5px;'>{k}</td>"
                f"<td style='border:1px solid #999; padding:1px 5px;'>{v}</td></tr>"
                for k, v in rows) + "</table>"
            pcorr = ""
        else:
            summary, _ = summarize(self.data, self.applied_blcorr)
            results = self._results_html(summary)
            pcorr = self._pcorr_html(document)
        html = (
            "<h2 style='margin:2px;'>分割測定 検査記録</h2>"
            + self._meta_html() + graphs + results + pcorr
        )
        document.setHtml(html)
        return document

    def closeEvent(self, event):
        self.rx_timer.stop()
        if getattr(self, "cmd_timer", None) is not None:
            self.cmd_timer.stop()
        if getattr(self, "web", None) is not None:
            self.web.stop()
        self.dev.close()
        super().closeEvent(event)


def run(device, wheel_pitch, worm_pitch, worm_range, worm_start, settings):
    import sys

    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow(device, wheel_pitch, worm_pitch, worm_range, worm_start, settings)
    win.show()
    sys.exit(app.exec())
