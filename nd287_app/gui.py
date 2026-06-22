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

# グラフは白背景・濃い軸にする。黒背景だと印刷で黒インクを大量に消費するため。
pg.setConfigOption("background", "w")
pg.setConfigOption("foreground", "#222222")
pg.setConfigOption("antialias", True)

from .analysis import (
    adjacent,
    band_for_temp,
    composite_backlash_minmax,
    deviation_sec,
    pp,
    repeatability_summary,
    single,
    slope,
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
from .masters import (
    condition_params,
    find_entry,
    formula_minmax,
    load_masters,
    missing_masters,
)
from .fanuc import FanucConfig, generate as generate_fanuc
from .firestore_sync import FirestoreSync, build_measurement_doc, overall_judgement
from . import help_text
from .export import (
    MODE_KEY,
    build_save_path,
    judgement_texts,
    load_measurement,
    repeat_result_rows,
    sanitize_filename,
    save_csv,
    save_repeat_csv,
    series_rows,
)
from . import excel_export, report
from .themes import (
    DEFAULT_FONT_PT,
    DEFAULT_THEME,
    THEME_NAMES,
    apply_font,
    apply_theme,
)
from .nd287 import ND287Device, deg_to_dms, scan_report
from .switchbot import (
    DEFAULT_PATTERNS,
    bot_configured,
    press_bot,
)
from .sequence import (
    CombinedSequence,
    IndexingSequence,
    RepeatabilitySequence,
    SERIES_KEYS,
    SERIES_LABELS,
    rotary_blocks,
    tilt_blocks,
)
from .settings import (
    PROFILE_LABELS,
    app_dir,
    apply_active_profile,
    resolve_save_root,
    save_settings,
)
from . import fanuc_alarms
from . import nc_param

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
# 精度結果は2つの表に分ける（横を狭く・縦を長くしてグラフを広げる）。
# 上＝精度PPと傾き、下＝単一誤差と隣接誤差。
PP_SLOPE_HEADERS = ["系列", "精度PP", "傾き"]
SINGLE_ADJ_HEADERS = ["系列", "単一誤差", "隣接誤差"]

# 統一規格[秒]（全型式共通）。単一誤差≦5、隣接誤差≦10。
SINGLE_SPEC = 5.0
ADJACENT_SPEC = 10.0
REPEAT_HEADERS = ["ブロック", "角度", "CW範囲", "CCW範囲"]


class SettingsDialog(QtWidgets.QDialog):
    """通信（ポート・ボーレート・パリティ）と保存先の設定"""

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle("設定")
        # 縦に長くなりすぎないよう、左右2列に分ける（左=通信/保存先、右=SwitchBot）
        form = QtWidgets.QFormLayout()

        self.e_port = QtWidgets.QLineEdit(str(settings.get("port", "auto")))
        self.e_port.setToolTip("auto = 自動検出。COM3 のように明示指定も可")
        self.e_baud = QtWidgets.QComboBox()
        self.e_baud.addItems(BAUDRATES)
        self.e_baud.setEditable(True)
        self.e_baud.setCurrentText(str(settings.get("baudrate", 9600)))
        self.e_parity = QtWidgets.QComboBox()
        self.e_parity.addItems(["E", "N", "O"])
        self.e_parity.setCurrentText(str(settings.get("parity", "E")))

        self.e_theme = QtWidgets.QComboBox()
        self.e_theme.addItems(THEME_NAMES)
        self.e_theme.setCurrentText(str(settings.get("ui_theme", DEFAULT_THEME)))
        self.e_theme.setToolTip("画面の見た目。OKですぐ反映される")
        self.e_font = QtWidgets.QSpinBox()
        self.e_font.setRange(7, 22)
        self.e_font.setSuffix(" pt")
        self.e_font.setValue(int(settings.get("ui_font_pt", DEFAULT_FONT_PT)))
        self.e_font.setToolTip("画面全体の文字サイズ。OKですぐ反映される")
        self.c_comp_bl = QtWidgets.QCheckBox("ホイール/ウォーム単品のバックラッシも表示")
        self.c_comp_bl.setChecked(bool(settings.get("show_component_backlash", False)))
        self.c_comp_bl.setToolTip("既定は総合バックラッシのみ。ONで単品も参考表示する")

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

        # SwitchBot Bot（BLE直結でNCの起動ボタンを物理押し）。クラウド/温度は使わない
        self.e_sb_mac = QtWidgets.QLineEdit(str(settings.get("switchbot_ble_mac", "")))
        self.e_sb_mac.setToolTip("SwitchBot Bot本体のBLE MACアドレス。"
                                 "「スキャン」で自動検出して選べる")
        self.b_sb_scan = QtWidgets.QPushButton("スキャン")
        self.b_sb_scan.setToolTip("近くのSwitchBotをBLEスキャンして自動で見つける（手入力不要）")
        self.b_sb_scan.clicked.connect(self.scan_switchbot)
        mac_row = QtWidgets.QHBoxLayout()
        mac_row.setContentsMargins(0, 0, 0, 0)
        mac_row.addWidget(self.e_sb_mac, 1)
        mac_row.addWidget(self.b_sb_scan)
        self.e_sb_pw = QtWidgets.QLineEdit(str(settings.get("switchbot_ble_password", "")))
        self.e_sb_pw.setEchoMode(QtWidgets.QLineEdit.Password)
        self.e_sb_pw.setToolTip("Botにパスワードを設定している場合のみ")
        self.cmb_sb_pattern = QtWidgets.QComboBox()
        patterns = list((settings.get("switchbot_patterns") or DEFAULT_PATTERNS).keys())
        self.cmb_sb_pattern.addItems(patterns)
        cur_pat = str(settings.get("switchbot_pattern_name") or "1回押し")
        if cur_pat in patterns:
            self.cmb_sb_pattern.setCurrentText(cur_pat)
        self.cmb_sb_pattern.setToolTip("自動測定で機械を起動するときの押し回数・間隔")
        self.sp_sb_retry = QtWidgets.QSpinBox()
        self.sp_sb_retry.setRange(0, 9)
        self.sp_sb_retry.setValue(int(settings.get("auto_max_retries", 2)))
        self.sp_sb_retry.setSuffix(" 回")
        self.sp_sb_retry.setToolTip("自動測定で傾きNGのとき自動で測り直す上限回数")
        self.sp_sb_prec = QtWidgets.QSpinBox()
        self.sp_sb_prec.setRange(0, 9)
        self.sp_sb_prec.setValue(int(settings.get("auto_max_precision_retries", 1)))
        self.sp_sb_prec.setSuffix(" 回")
        self.sp_sb_prec.setToolTip("自動測定で精度NG（単一>5/隣接>10）のとき測り直す上限回数")
        self.sp_sb_wait = QtWidgets.QDoubleSpinBox()
        self.sp_sb_wait.setRange(0.0, 60.0)
        self.sp_sb_wait.setDecimals(1)
        self.sp_sb_wait.setValue(float(settings.get("auto_wait_before_press", 1.0)))
        self.sp_sb_wait.setSuffix(" 秒")
        self.sp_sb_wait.setToolTip("取込開始からSwitchBot押下までの待ち時間")
        self.c_sb_dry = QtWidgets.QCheckBox("空打ち（実際には押さずに動作確認）")
        self.c_sb_dry.setChecked(bool(settings.get("switchbot_dry_run", False)))
        self.b_sb_help = QtWidgets.QPushButton("接続方法（ヘルプ）")
        self.b_sb_help.setToolTip("SwitchBotのつなぎ方・使い方を表示する")
        self.b_sb_help.clicked.connect(self.show_switchbot_help)

        sb_group = QtWidgets.QGroupBox("自動測定（SwitchBot Bot・BLE直結）")
        sb_form = QtWidgets.QFormLayout(sb_group)
        sb_form.addRow("Bot BLE MAC", mac_row)
        sb_form.addRow("BLEパスワード", self.e_sb_pw)
        sb_form.addRow("押し方（押し回数）", self.cmb_sb_pattern)
        sb_form.addRow("傾きNG 再測定上限", self.sp_sb_retry)
        sb_form.addRow("精度NG 再測定上限", self.sp_sb_prec)
        sb_form.addRow("押下までの待ち", self.sp_sb_wait)
        sb_form.addRow(self.c_sb_dry)
        sb_form.addRow(self.b_sb_help)

        form.addRow("ポート", self.e_port)
        form.addRow("ボーレート", self.e_baud)
        form.addRow("パリティ", self.e_parity)
        form.addRow("画面テーマ", self.e_theme)
        form.addRow("文字サイズ", self.e_font)
        form.addRow(self.c_comp_bl)
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

        # 左列=通信・表示・保存先、右列=SwitchBot。横に並べて縦の長さを抑える
        left = QtWidgets.QWidget()
        left.setLayout(form)
        right = QtWidgets.QVBoxLayout()
        right.addWidget(sb_group)
        right.addWidget(note)
        right.addStretch(1)
        right_w = QtWidgets.QWidget()
        right_w.setLayout(right)
        columns = QtWidgets.QHBoxLayout()
        columns.addWidget(left)
        columns.addWidget(right_w)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        outer = QtWidgets.QVBoxLayout(self)
        outer.addLayout(columns)
        outer.addWidget(buttons)

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

    SWITCHBOT_HELP = (
        "SwitchBot Bot をBLE直結（ハブ不要）で使う手順\n"
        "\n"
        "【準備】\n"
        "1. SwitchBotアプリでBotを登録し、モードを「押す（Press）」にする。\n"
        "   （長押し/切替モードだと正しく起動できません）\n"
        "2. Botを機械の「起動」ボタンの上に貼り付ける（アームが届く位置）。\n"
        "3. このPCのBluetoothをONにする。\n"
        "\n"
        "【接続（手入力は不要）】\n"
        "4. この設定欄の「スキャン」を押す。近くのSwitchBotが電波の強い順に\n"
        "   一覧表示される。自分のBotを選ぶと「Bot BLE MAC」が自動で入る。\n"
        "5. Bot本体にパスワードを設定している場合のみ「BLEパスワード」を入力。\n"
        "   設定していなければ空のままでOK。\n"
        "6. 「押し方」を選ぶ（機械が1回押しで起動なら「1回押し」）。\n"
        "\n"
        "【使い方】\n"
        "7. 型式・機番・温度を入れて本画面の「自動測定」を押すと、\n"
        "   取込開始→Botが起動ボタンを押す→測定→傾き/精度判定→NGなら\n"
        "   設定した回数まで自動で測り直す。\n"
        "8. 「空打ち」をONにすると、実際には押さず動作確認できる。\n"
        "\n"
        "【うまくいかないとき】\n"
        "・スキャンに出ない：PCのBluetooth ON、Botの電池、距離（数m以内）を確認。\n"
        "・「bleak 未導入」と出る：pip install bleak（exe版は同梱）。\n"
        "・押せるが起動しない：Botの位置・アーム長・モード（押す）を見直す。\n"
        "  SwitchBotアプリの押し込み量の調整も有効。\n"
        "・ハブは不要（PCのBluetoothから直接つなぐ方式）。クラウドAPIは使いません。"
    )

    def show_switchbot_help(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("SwitchBot 接続方法")
        dlg.resize(560, 520)
        layout = QtWidgets.QVBoxLayout(dlg)
        text = QtWidgets.QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self.SWITCHBOT_HELP)
        layout.addWidget(text)
        b = QtWidgets.QPushButton("閉じる")
        b.clicked.connect(dlg.accept)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(b)
        layout.addLayout(row)
        dlg.exec()

    def scan_switchbot(self):
        """BLEスキャンで近くのSwitchBotを自動検出し、選んでMACを入れる（手入力不要）。"""
        from .switchbot import scan_switchbot_ble
        self.b_sb_scan.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            ok, devices, message = scan_switchbot_ble()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.b_sb_scan.setEnabled(True)
        if not ok:
            QtWidgets.QMessageBox.warning(self, "BLEスキャン", message)
            return
        candidates = [d for d in devices if d["switchbot"]] or devices
        if not candidates:
            QtWidgets.QMessageBox.information(
                self, "BLEスキャン", "SwitchBotが見つかりませんでした")
            return
        items = [f'{d["name"] or "(名前なし)"} [{d["model"] or "?"}] '
                 f'RSSI{d["rssi"]}  {d["mac"]}' for d in candidates]
        choice, picked = QtWidgets.QInputDialog.getItem(
            self, "SwitchBotを選択",
            "見つかった機器（SwitchBot優先・電波強い順）:", items, 0, False)
        if picked and choice:
            self.e_sb_mac.setText(choice.split()[-1])

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
            ui_theme=self.e_theme.currentText(),
            ui_font_pt=self.e_font.value(),
            show_component_backlash=self.c_comp_bl.isChecked(),
            save_root=self.e_root.text().strip() or "測定データ",
            bs_save_root=self.e_bs_root.text().strip(),
            conditions_csv=self.e_conditions.text().strip() or r"マスタ/測定条件.csv",
            judgement_csv=self.e_judgement.text().strip() or r"マスタ/合否判定.csv",
            user_rotary_csv=self.e_user_rotary.text().strip()
            or r"マスタ/ユーザー回転条件.csv",
            user_tilt_csv=self.e_user_tilt.text().strip()
            or r"マスタ/ユーザー傾斜条件.csv",
            switchbot_use_ble=True,  # BLE直結のみ（クラウドは使わない）
            switchbot_ble_mac=self.e_sb_mac.text().strip(),
            switchbot_ble_password=self.e_sb_pw.text(),
            switchbot_pattern_name=self.cmb_sb_pattern.currentText(),
            switchbot_dry_run=self.c_sb_dry.isChecked(),
            auto_max_retries=self.sp_sb_retry.value(),
            auto_max_precision_retries=self.sp_sb_prec.value(),
            auto_wait_before_press=self.sp_sb_wait.value(),
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
        self.e_swing_dwell = QtWidgets.QDoubleSpinBox()
        self.e_swing_dwell.setRange(0.0, 10.0)
        self.e_swing_dwell.setDecimals(2)
        self.e_swing_dwell.setSingleStep(0.5)
        self.e_swing_dwell.setValue(float(settings.get("fanuc_swing_dwell_sec", 1.0)))
        self.e_swing_dwell.setSuffix(" 秒")
        self.e_swing_dwell.setToolTip(
            "バックラッシュ消しの振り後のドゥエル。測定とは無関係なので小さい方が"
            "プログラムが速い（0.5〜1.0目安）")
        self.e_dwell = QtWidgets.QDoubleSpinBox()
        self.e_dwell.setRange(0.0, 60.0)
        self.e_dwell.setDecimals(2)
        self.e_dwell.setSingleStep(0.5)
        self.e_dwell.setValue(float(settings.get("fanuc_dwell_sec", 1.0)))
        self.e_dwell.setSuffix(" 秒")
        self.e_dwell.setToolTip("測定点で静止・読取前のドゥエル。測定に効くので 1.0〜5.0 で調整")
        self.e_mcode = QtWidgets.QLineEdit(str(settings.get("fanuc_mcode", "M80")))
        self.e_mcode.setMaximumWidth(80)
        # クランプ分割: 測定点でクランプ→読取→アンクランプ
        self.c_clamp = QtWidgets.QCheckBox(
            "クランプ分割（測定点でクランプ→完了信号→アンクランプ）")
        self.c_clamp.setChecked(bool(settings.get("fanuc_clamp_enabled", False)))
        self.c_clamp.setToolTip(
            "ONにすると各測定点で軸をクランプしてから完了信号を出し、読取後に"
            "アンクランプして次へ動く。クランプ/アンクランプの信号と待ち時間は下で設定")
        self.e_clamp_m = QtWidgets.QLineEdit(str(settings.get("fanuc_clamp_mcode", "M10")))
        self.e_clamp_m.setMaximumWidth(80)
        self.e_clamp_m.setToolTip("クランプ信号のMコード（軸ごとに決まる。例 4軸 M10）")
        self.e_unclamp_m = QtWidgets.QLineEdit(
            str(settings.get("fanuc_unclamp_mcode", "M11")))
        self.e_unclamp_m.setMaximumWidth(80)
        self.e_unclamp_m.setToolTip("アンクランプ信号のMコード（例 4軸 M11）")
        self.e_clamp_dwell = QtWidgets.QDoubleSpinBox()
        self.e_clamp_dwell.setRange(0.0, 30.0)
        self.e_clamp_dwell.setDecimals(2)
        self.e_clamp_dwell.setSingleStep(0.5)
        self.e_clamp_dwell.setValue(float(settings.get("fanuc_clamp_dwell_sec", 1.0)))
        self.e_clamp_dwell.setSuffix(" 秒")
        self.e_clamp_dwell.setToolTip(
            "クランプ信号後のドゥエル。信号を出してもすぐ締まらないので締まり待ち")
        self.e_unclamp_dwell = QtWidgets.QDoubleSpinBox()
        self.e_unclamp_dwell.setRange(0.0, 30.0)
        self.e_unclamp_dwell.setDecimals(2)
        self.e_unclamp_dwell.setSingleStep(0.5)
        self.e_unclamp_dwell.setValue(float(settings.get("fanuc_unclamp_dwell_sec", 1.0)))
        self.e_unclamp_dwell.setSuffix(" 秒")
        self.e_unclamp_dwell.setToolTip("アンクランプ信号後のドゥエル（次の動き前の緩み待ち）")
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

        # 機械へ送信の設定ウィジェット（LAN＝共有フォルダ/FTP、またはメモリカード）
        self.cmb_send = QtWidgets.QComboBox()
        self.cmb_send.addItem("フォルダ／メモリカード（LAN共有・カードどちらも）", "folder")
        self.cmb_send.addItem("FTP（機械のIPへ）", "ftp")
        si = self.cmb_send.findData(str(settings.get("nc_send_method", "folder")))
        self.cmb_send.setCurrentIndex(si if si >= 0 else 0)
        self.e_send_folder = QtWidgets.QLineEdit(str(settings.get("nc_send_folder", "")))
        self.e_send_folder.setPlaceholderText(r"LAN共有 \\192.168.0.10\nc / カード E:\ など")
        self.e_ftp_host = QtWidgets.QLineEdit(str(settings.get("nc_ftp_host", "")))
        self.e_ftp_host.setPlaceholderText("機械のIP 例 192.168.0.10")
        self.e_ftp_port = QtWidgets.QSpinBox()
        self.e_ftp_port.setRange(1, 65535)
        self.e_ftp_port.setValue(int(settings.get("nc_ftp_port", 21) or 21))
        self.e_ftp_user = QtWidgets.QLineEdit(str(settings.get("nc_ftp_user", "")))
        self.e_ftp_pw = QtWidgets.QLineEdit(str(settings.get("nc_ftp_password", "")))
        self.e_ftp_pw.setEchoMode(QtWidgets.QLineEdit.Password)
        self.e_ftp_dir = QtWidgets.QLineEdit(str(settings.get("nc_ftp_dir", "")))
        self.e_ftp_dir.setPlaceholderText("空=ルート")
        self.c_ftp_passive = QtWidgets.QCheckBox("パッシブ")
        self.c_ftp_passive.setChecked(bool(settings.get("nc_ftp_passive", True)))

        form.addRow("割出軸", self.e_axis)
        form.addRow("前振り量（測定点のバックラッシュ消し）", self.e_pre)
        form.addRow("リセット振り量（カウンター0設定用）", self.e_reset_sw)
        form.addRow("振りドゥエル（バックラッシュ消し後・小さめ）", self.e_swing_dwell)
        form.addRow("測定ドゥエル（測定点で静止・読取前 1.0〜5.0）", self.e_dwell)
        form.addRow("完了信号Mコード", self.e_mcode)
        form.addRow(self.c_clamp)
        clamp_m_row = QtWidgets.QHBoxLayout()
        clamp_m_row.addWidget(QtWidgets.QLabel("クランプ"))
        clamp_m_row.addWidget(self.e_clamp_m)
        clamp_m_row.addSpacing(12)
        clamp_m_row.addWidget(QtWidgets.QLabel("アンクランプ"))
        clamp_m_row.addWidget(self.e_unclamp_m)
        clamp_m_row.addStretch(1)
        form.addRow("クランプ信号Mコード", clamp_m_row)
        clamp_d_row = QtWidgets.QHBoxLayout()
        clamp_d_row.addWidget(QtWidgets.QLabel("クランプ後"))
        clamp_d_row.addWidget(self.e_clamp_dwell)
        clamp_d_row.addSpacing(12)
        clamp_d_row.addWidget(QtWidgets.QLabel("アンクランプ後"))
        clamp_d_row.addWidget(self.e_unclamp_dwell)
        clamp_d_row.addStretch(1)
        form.addRow("クランプ信号後ドゥエル", clamp_d_row)
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

        layout.addWidget(self._build_send_group())

        buttons = QtWidgets.QHBoxLayout()
        b_refresh = QtWidgets.QPushButton("プレビュー更新")
        self.b_edit = QtWidgets.QPushButton("編集")
        self.b_edit.setCheckable(True)
        self.b_edit.setToolTip("プレビューを手で編集できるようにする"
                               "（編集中は設定変更で上書きしない。保存は編集後の内容）")
        b_save = QtWidgets.QPushButton("保存(.NC)")
        self.b_send = QtWidgets.QPushButton("機械へ送信")
        self.b_send.setToolTip("生成したプログラムをLAN経由で機械へ送る（カード不要）")
        b_sendhelp = QtWidgets.QPushButton("送信の使い方")
        b_close = QtWidgets.QPushButton("閉じる")
        b_refresh.clicked.connect(self.regenerate)
        self.b_edit.toggled.connect(self.toggle_edit)
        b_save.clicked.connect(self.save)
        self.b_send.clicked.connect(self.do_send)
        b_sendhelp.clicked.connect(self.show_send_help)
        b_close.clicked.connect(self.accept)
        buttons.addWidget(b_refresh)
        buttons.addWidget(self.b_edit)
        buttons.addStretch(1)
        buttons.addWidget(b_save)
        buttons.addWidget(self.b_send)
        buttons.addWidget(b_sendhelp)
        buttons.addWidget(b_close)
        layout.addLayout(buttons)

        for w in (self.e_axis, self.e_mcode, self.e_clamp_m, self.e_unclamp_m):
            w.textChanged.connect(self.refresh)
        for w in (self.e_pre, self.e_reset_sw, self.e_swing_dwell, self.e_dwell,
                  self.e_clamp_dwell, self.e_unclamp_dwell):
            w.valueChanged.connect(self.refresh)
        for w in (self.e_main, self.e_sub):
            w.valueChanged.connect(self.refresh)
        for w in (self.c_sub, self.c_reset, self.c_return, self.c_div, self.c_rep,
                  self.c_clamp):
            w.toggled.connect(self.refresh)
        self.refresh()

    def _config(self):
        return FanucConfig(
            axis=self.e_axis.text().strip() or "X",
            preswing=self.e_pre.value(),
            swing_dwell_sec=self.e_swing_dwell.value(),
            dwell_sec=self.e_dwell.value(),
            mcode=self.e_mcode.text().strip() or "M80",
            use_subprogram=self.c_sub.isChecked(),
            main_number=self.e_main.value(),
            rep_sub_number=self.e_sub.value(),
            return_to_start=self.c_return.isChecked(),
            counter_reset=self.c_reset.isChecked(),
            reset_swing=self.e_reset_sw.value(),
            clamp_enabled=self.c_clamp.isChecked(),
            clamp_mcode=self.e_clamp_m.text().strip() or "M10",
            unclamp_mcode=self.e_unclamp_m.text().strip() or "M11",
            clamp_dwell_sec=self.e_clamp_dwell.value(),
            unclamp_dwell_sec=self.e_unclamp_dwell.value(),
        )

    def _fanuc_settings(self):
        """画面のFANUC設定を settings のキーに対応づけて返す（記憶用）。"""
        return dict(
            fanuc_axis=self.e_axis.text().strip() or "X",
            fanuc_preswing=self.e_pre.value(),
            fanuc_reset_swing=self.e_reset_sw.value(),
            fanuc_swing_dwell_sec=self.e_swing_dwell.value(),
            fanuc_dwell_sec=self.e_dwell.value(),
            fanuc_mcode=self.e_mcode.text().strip() or "M80",
            fanuc_use_subprogram=self.c_sub.isChecked(),
            fanuc_main_number=self.e_main.value(),
            fanuc_rep_sub_number=self.e_sub.value(),
            fanuc_return_to_start=self.c_return.isChecked(),
            fanuc_counter_reset=self.c_reset.isChecked(),
            fanuc_clamp_enabled=self.c_clamp.isChecked(),
            fanuc_clamp_mcode=self.e_clamp_m.text().strip() or "M10",
            fanuc_unclamp_mcode=self.e_unclamp_m.text().strip() or "M11",
            fanuc_clamp_dwell_sec=self.e_clamp_dwell.value(),
            fanuc_unclamp_dwell_sec=self.e_unclamp_dwell.value(),
        )

    def _persist(self, extra=None):
        """FANUC設定（＋extra）を settings.json に記憶する。失敗は無視。"""
        self.settings.update(self._fanuc_settings())
        if extra:
            self.settings.update(extra)
        try:
            save_settings(self.settings)
        except Exception:
            pass

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
        # 手編集中は設定変更でプレビューを上書きしない
        if self.b_edit.isChecked():
            return
        try:
            self.preview.setPlainText(self._generate())
        except Exception as e:
            self.preview.setPlainText(f"生成エラー: {e}")

    def regenerate(self):
        """設定から作り直す（手編集は破棄）。「プレビュー更新」用。"""
        self.b_edit.setChecked(False)
        self.preview.setReadOnly(True)
        try:
            self.preview.setPlainText(self._generate())
        except Exception as e:
            self.preview.setPlainText(f"生成エラー: {e}")

    def toggle_edit(self, on):
        self.preview.setReadOnly(not on)
        self.b_edit.setText("編集中…" if on else "編集")
        if on:
            self.preview.setFocus()

    def save(self):
        # 画面のプレビュー（手編集していればその内容）をそのまま保存する
        text = self.preview.toPlainText()
        default = f"{self.params.get('machine') or 'program'}.NC"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "測定プログラムを保存", default, "NCプログラム (*.NC *.txt)")
        if not path:
            return
        # FANUCはASCII。CRLFで保存
        with open(path, "w", encoding="ascii", errors="replace", newline="") as f:
            f.write(text)
        self._persist()  # 次回も同じ設定で作れるよう記憶
        QtWidgets.QMessageBox.information(self, "保存", f"保存しました:\n{path}")

    def _build_send_group(self):
        """「機械へ送信」の送信先設定（方式で共有フォルダ/FTPを切替）。"""
        group = QtWidgets.QGroupBox("機械へ送信（カード不要・LAN）")
        v = QtWidgets.QVBoxLayout(group)
        m_row = QtWidgets.QHBoxLayout()
        m_row.addWidget(QtWidgets.QLabel("方式"))
        m_row.addWidget(self.cmb_send)
        m_row.addStretch(1)
        v.addLayout(m_row)

        self.send_stack = QtWidgets.QStackedWidget()
        # ページ0: 共有フォルダ
        fpage = QtWidgets.QWidget()
        fl = QtWidgets.QHBoxLayout(fpage)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(QtWidgets.QLabel("フォルダ"))
        fl.addWidget(self.e_send_folder, 1)
        b_browse = QtWidgets.QPushButton("参照...")
        b_browse.clicked.connect(self._browse_send_folder)
        fl.addWidget(b_browse)
        # ページ1: FTP
        ppage = QtWidgets.QWidget()
        pf = QtWidgets.QFormLayout(ppage)
        pf.setContentsMargins(0, 0, 0, 0)
        host_row = QtWidgets.QHBoxLayout()
        host_row.addWidget(self.e_ftp_host, 1)
        host_row.addWidget(QtWidgets.QLabel("ポート"))
        host_row.addWidget(self.e_ftp_port)
        host_row.addWidget(self.c_ftp_passive)
        pf.addRow("ホスト", host_row)
        cred_row = QtWidgets.QHBoxLayout()
        cred_row.addWidget(QtWidgets.QLabel("ユーザ"))
        cred_row.addWidget(self.e_ftp_user, 1)
        cred_row.addWidget(QtWidgets.QLabel("パスワード"))
        cred_row.addWidget(self.e_ftp_pw, 1)
        pf.addRow("認証", cred_row)
        pf.addRow("送信先フォルダ", self.e_ftp_dir)
        self.send_stack.addWidget(fpage)
        self.send_stack.addWidget(ppage)
        v.addWidget(self.send_stack)

        def _sync():
            self.send_stack.setCurrentIndex(
                1 if self.cmb_send.currentData() == "ftp" else 0)
        self.cmb_send.currentIndexChanged.connect(_sync)
        _sync()
        return group

    def _browse_send_folder(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "送信先（機械が見えるフォルダ）を選択", self.e_send_folder.text())
        if path:
            self.e_send_folder.setText(path)

    def _send_settings(self):
        return dict(
            nc_send_method=self.cmb_send.currentData() or "folder",
            nc_send_folder=self.e_send_folder.text().strip(),
            nc_ftp_host=self.e_ftp_host.text().strip(),
            nc_ftp_port=self.e_ftp_port.value(),
            nc_ftp_user=self.e_ftp_user.text().strip(),
            nc_ftp_password=self.e_ftp_pw.text(),
            nc_ftp_dir=self.e_ftp_dir.text().strip(),
            nc_ftp_passive=self.c_ftp_passive.isChecked(),
        )

    def do_send(self):
        text = self.preview.toPlainText()
        if not text.strip():
            QtWidgets.QMessageBox.warning(self, "送信", "送るプログラムがありません")
            return
        # FANUC設定＋送信先設定を記憶（次回も同じ設定で送れるように）
        self._persist(self._send_settings())
        from . import ncsend
        machine = self.params.get("machine") or ""
        self.b_send.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            dest = ncsend.send(text, self.settings, machine=machine,
                               main_number=self.e_main.value())
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.b_send.setEnabled(True)
            QtWidgets.QMessageBox.warning(
                self, "送信に失敗しました",
                f"{e}\n\n「送信の使い方」を確認してください。")
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        self.b_send.setEnabled(True)
        QtWidgets.QMessageBox.information(
            self, "送信しました",
            f"機械へ送りました:\n{dest}\n\n"
            "機械側でこのプログラムを選んで運転してください"
            "（安全のため起動は機械側で）。")

    SEND_HELP = (
        "測定プログラムを機械へ渡す（LAN または メモリカード）\n"
        "\n"
        "渡し方は2通り。どちらも「機械に取り込んで→メモリから運転」する形で、\n"
        "Data Serverは不要です（内蔵イーサネット または カードでOK）。\n"
        "\n"
        "────────────────────────\n"
        "方式1: フォルダ／メモリカード（おすすめ・簡単）\n"
        "────────────────────────\n"
        "● LAN（内蔵イーサネット）で渡す場合:\n"
        "  ・機械から見える共有フォルダを用意（このPCの共有 or 機械側の共有）。\n"
        "  ・「方式＝フォルダ／メモリカード」を選び、そのパスを入れる。\n"
        "    例 \\\\192.168.0.10\\nc または \\\\<このPCのIP>\\NC\n"
        "● メモリカード（CF/USB）で渡す場合:\n"
        "  ・カードをこのPCに挿し、そのドライブ/フォルダのパスを入れる。例 E:\\\n"
        "  ・「機械へ送信」で <機番>.NC をカードに書き出す → カードを機械へ挿す。\n"
        "→ いずれも機械側でそのファイルを選び、取り込んで運転する。\n"
        "\n"
        "────────────────────────\n"
        "方式2: FTP（機械がFTPサーバのとき）\n"
        "────────────────────────\n"
        "1. 「方式＝FTP」を選ぶ。\n"
        "2. ホストに機械のIPアドレス、必要ならユーザ／パスワード、\n"
        "   送信先フォルダを入れる。繋がらないときは「パッシブ」を切り替える。\n"
        "3. 「機械へ送信」を押す → FTPで <機番>.NC を送る。\n"
        "4. 機械側でそのファイルを選んで運転する。\n"
        "\n"
        "【注意】\n"
        "・このアプリは「渡すだけ」です。安全のため、運転開始（サイクル\n"
        "  スタート）は機械側で人が行ってください（自動起動はしません）。\n"
        "・送信先や接続情報は次回も使えるよう保存されます。\n"
        "\n"
        "【うまくいかないとき】\n"
        "・「フォルダが見つかりません」→ そのパス（共有/カードのドライブ）を\n"
        "  このPCのエクスプローラで開けるか確認。\n"
        "・FTPで失敗 → IP・ユーザ／パス・送信先フォルダ・パッシブ設定を確認。\n"
        "・ファイル名は <機番>.NC（機番が無ければ Oxxxx.NC）。"
    )

    def show_send_help(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("機械へ送信の使い方")
        dlg.resize(580, 560)
        layout = QtWidgets.QVBoxLayout(dlg)
        text = QtWidgets.QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self.SEND_HELP)
        layout.addWidget(text)
        b = QtWidgets.QPushButton("閉じる")
        b.clicked.connect(dlg.accept)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(b)
        layout.addLayout(row)
        dlg.exec()


class ParamDialog(QtWidgets.QDialog):
    """製品ごとのパラメータ変更（差分）の確認表・差分ファイルを作る。

    紙の「パラメータ表」をCSV化（型式,番号,軸,変更値,メモ）しておけば、型式を
    選ぶと該当製品の変更だけを確認表と差分ファイルにして、カード/LAN共有へ出力する。
    機械への入力（PWE=1）は安全のため人が機械側で行う。
    """

    WARN = ("⚠ 機械側で PWE=1（パラメータ書込許可）にして、該当番号だけ入力してください。"
            "一部は電源再投入が必要です。差分ファイルの厳密な書式は機種で異なるため、"
            "実機バックアップ1個で必ず確認してから投入してください（未確認のまま投入しない）。")

    def __init__(self, parent, settings, *, model="", machine=""):
        super().__init__(parent)
        self.settings = settings
        self.machine = machine
        self.setWindowTitle("パラメータ変更（差分）の出力")
        self.resize(720, 520)
        v = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self.e_model = QtWidgets.QLineEdit(model)
        self.e_model.setPlaceholderText("型式（変更表CSVの『型式』と照合）")
        form.addRow("型式", self.e_model)

        self.e_csv = QtWidgets.QLineEdit(str(settings.get("param_change_csv", "")))
        self.e_csv.setPlaceholderText("紙のパラメータ表をCSV化したファイル")
        form.addRow("変更表CSV", self._with_browse(self.e_csv, self._browse_csv))

        self.e_master = QtWidgets.QLineEdit(str(settings.get("param_master_backup", "")))
        self.e_master.setPlaceholderText("任意: マスタ/バックアップ（旧値表示用）")
        form.addRow("マスタ(任意)", self._with_browse(self.e_master, self._browse_master))

        self.e_out = QtWidgets.QLineEdit(str(settings.get("param_out_folder", "")
                                             or settings.get("nc_send_folder", "")))
        self.e_out.setPlaceholderText(r"出力先（カード E:\ や LAN共有 \\192.168.0.10\nc）")
        form.addRow("出力先", self._with_browse(self.e_out, self._browse_out))
        v.addLayout(form)

        hint = QtWidgets.QLabel(
            "変更表CSVの列: 型式, 番号, 軸, 変更値, メモ（紙のパラメータ表を一度だけCSV化）。"
            "型式を選ぶと該当製品の変更だけを出力します。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#374151;")
        v.addWidget(hint)

        warn = QtWidgets.QLabel(self.WARN)
        warn.setWordWrap(True)
        warn.setStyleSheet("color:#b45309; background:#fffbeb; padding:6px;"
                           "border:1px solid #f59e0b;")
        v.addWidget(warn)

        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["番号", "軸", "旧値", "新値", "メモ"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        v.addWidget(self.table, 1)

        self.lbl = QtWidgets.QLabel("")
        v.addWidget(self.lbl)

        row = QtWidgets.QHBoxLayout()
        b_reload = QtWidgets.QPushButton("読み込み/更新")
        b_reload.clicked.connect(self.reload)
        b_check = QtWidgets.QPushButton("確認表を保存")
        b_check.clicked.connect(self.save_checklist)
        b_file = QtWidgets.QPushButton("差分ファイルを保存")
        b_file.clicked.connect(self.save_param_file)
        b_both = QtWidgets.QPushButton("出力先へ両方出す")
        b_both.setObjectName("primary")
        b_both.clicked.connect(self.export_both)
        b_close = QtWidgets.QPushButton("閉じる")
        b_close.clicked.connect(self.accept)
        for b in (b_reload, b_check, b_file, b_both):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(b_close)
        v.addLayout(row)

        self._changes = []
        self.reload()

    def _with_browse(self, edit, slot):
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit, 1)
        b = QtWidgets.QPushButton("参照...")
        b.clicked.connect(slot)
        h.addWidget(b)
        return w

    def _browse_csv(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "変更表CSV", self.e_csv.text(), "CSV (*.csv);;すべて (*.*)")
        if p:
            self.e_csv.setText(p)
            self.reload()

    def _browse_master(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "マスタ/バックアップ", self.e_master.text(), "すべて (*.*)")
        if p:
            self.e_master.setText(p)
            self.reload()

    def _browse_out(self):
        p = QtWidgets.QFileDialog.getExistingDirectory(self, "出力先フォルダ/カード",
                                                       self.e_out.text())
        if p:
            self.e_out.setText(p)

    def _master_table(self):
        path = self.e_master.text().strip()
        if not path:
            return {}
        try:
            with open(path, "r", encoding="cp932", errors="replace") as f:
                return nc_param.parse_param_backup(f.read())
        except Exception:
            return {}

    def reload(self):
        """CSVから現在の型式の変更を読み、表に表示する。"""
        self._changes = []
        model = self.e_model.text().strip()
        csv_path = self.e_csv.text().strip()
        if not model or not csv_path:
            self.table.setRowCount(0)
            self.lbl.setText("型式と変更表CSVを指定してください。")
            return
        try:
            allc = nc_param.load_changes(csv_path)
        except Exception as e:
            self.table.setRowCount(0)
            self.lbl.setText(f"変更表CSVを読めません: {e}")
            return
        self._changes = nc_param.changes_for_model(allc, model)
        rows = nc_param.checklist_rows(self._changes, self._master_table())
        self.table.setRowCount(len(rows))
        for i, (num, axis, old, new, note) in enumerate(rows):
            for j, val in enumerate((num, axis, old, new, note)):
                it = QtWidgets.QTableWidgetItem(str(val))
                if j == 3:
                    it.setForeground(QtGui.QBrush(QtGui.QColor("#dc2626")))
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()
        if self._changes:
            self.lbl.setText(f"型式『{model}』の変更点数: {len(self._changes)} 件")
        else:
            self.lbl.setText(f"型式『{model}』に一致する変更がCSVにありません。")

    def _basename(self):
        from .ncsend import _safe_component
        name = _safe_component(self.e_model.text().strip() or self.machine or "param")
        return name or "param"

    def save_checklist(self):
        if not self._ensure_changes():
            return
        text = nc_param.format_checklist(
            self.e_model.text().strip(), self._changes, self._master_table(),
            date=QtCore.QDate.currentDate().toString("yyyy/MM/dd"))
        p, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "確認表を保存", f"{self._basename()}_パラメータ確認表.txt",
            "テキスト (*.txt)")
        if not p:
            return
        nc_param.save_checklist(p, text)
        QtWidgets.QMessageBox.information(self, "保存", f"確認表を保存しました:\n{p}")

    def save_param_file(self):
        if not self._ensure_changes():
            return
        p, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "差分パラメータファイルを保存", f"{self._basename()}.prm",
            "パラメータ (*.prm *.txt);;すべて (*.*)")
        if not p:
            return
        nc_param.save_param_file(p, self._changes)
        QtWidgets.QMessageBox.information(
            self, "保存",
            f"差分パラメータファイルを保存しました:\n{p}\n\n"
            "※機械へ投入する前に、実機バックアップで書式を確認してください。")

    def export_both(self):
        """出力先（カード/LAN共有）へ 確認表＋差分ファイル を書き出す。"""
        if not self._ensure_changes():
            return
        out = self.e_out.text().strip()
        if not out:
            QtWidgets.QMessageBox.warning(self, "出力", "出力先フォルダ/カードを指定してください")
            return
        from pathlib import Path
        d = Path(out)
        if not d.is_dir():
            QtWidgets.QMessageBox.warning(
                self, "出力", f"出力先が見つかりません（カード/共有が見えていない可能性）:\n{d}")
            return
        base = self._basename()
        try:
            chk = d / f"{base}_パラメータ確認表.txt"
            prm = d / f"{base}.prm"
            nc_param.save_checklist(chk, nc_param.format_checklist(
                self.e_model.text().strip(), self._changes, self._master_table(),
                date=QtCore.QDate.currentDate().toString("yyyy/MM/dd")))
            nc_param.save_param_file(prm, self._changes)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "出力に失敗", str(e))
            return
        self._persist()
        QtWidgets.QMessageBox.information(
            self, "出力しました",
            f"出力先に置きました:\n・{chk.name}\n・{prm.name}\n\n"
            "機械側で PWE=1 にして、確認表を見ながら入力してください"
            "（差分ファイルは書式確認後に使用）。")

    def _ensure_changes(self):
        if not self._changes:
            QtWidgets.QMessageBox.warning(
                self, "パラメータ", "出力する変更がありません（型式・CSVを確認）。")
            return False
        return True

    def _persist(self):
        try:
            from .settings import save_settings
            self.settings.update(dict(
                param_change_csv=self.e_csv.text().strip(),
                param_master_backup=self.e_master.text().strip(),
                param_out_folder=self.e_out.text().strip(),
            ))
            save_settings(self.settings)
        except Exception:
            pass


class ConditionRegistryDialog(QtWidgets.QDialog):
    """測定条件の登録/編集（回転分割・傾斜分割・回転再現・傾斜再現を上の「対象」で切替）。

    回転と傾斜、分割と再現をそれぞれ別ファイルで管理するので条件が混ざらない。
    """

    def __init__(self, win, target="回転分割"):
        super().__init__(win)
        self.win = win
        from .masters import (ROTARY_USER_FIELDS, TILT_USER_FIELDS, REPEAT_USER_FIELDS)
        self.TARGETS = {
            "回転分割": dict(key="user_rotary_csv", default="マスタ/ユーザー回転条件.csv",
                             fields=ROTARY_USER_FIELDS, tilt=False, repeat=False,
                             note="回転分割の測定条件を型式ごとに登録（提供CSVより優先）。"),
            "傾斜分割": dict(key="user_tilt_csv", default="マスタ/ユーザー傾斜条件.csv",
                             fields=TILT_USER_FIELDS, tilt=True, repeat=False,
                             note="傾斜分割の測定条件を型式ごとに登録（回転とは別ファイル）。"),
            "回転再現": dict(key="user_rotary_repeat_csv",
                             default="マスタ/ユーザー回転再現条件.csv",
                             fields=REPEAT_USER_FIELDS, tilt=False, repeat=True,
                             note="回転再現性のブロック数・回数・再現範囲を型式ごとに登録。"),
            "傾斜再現": dict(key="user_tilt_repeat_csv",
                             default="マスタ/ユーザー傾斜再現条件.csv",
                             fields=REPEAT_USER_FIELDS, tilt=True, repeat=True,
                             note="傾斜再現性のブロック数・回数・再現範囲を型式ごとに登録。"),
        }
        self.setWindowTitle("測定条件の登録/編集")
        self.resize(660, 480)
        layout = QtWidgets.QVBoxLayout(self)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("対象"))
        self.cmb_target = QtWidgets.QComboBox()
        self.cmb_target.addItems(list(self.TARGETS.keys()))
        if target in self.TARGETS:
            self.cmb_target.setCurrentText(target)
        self.cmb_target.currentTextChanged.connect(self.on_target_changed)
        top.addWidget(self.cmb_target)
        top.addStretch(1)
        layout.addLayout(top)

        self.note = QtWidgets.QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#666;")
        layout.addWidget(self.note)

        self.table = QtWidgets.QTableWidget(0, 0)
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
        self.on_target_changed()

    @property
    def spec(self):
        return self.TARGETS[self.cmb_target.currentText()]

    @property
    def fields(self):
        return self.spec["fields"]

    def on_target_changed(self, *args):
        self.note.setText(self.spec["note"])
        self.table.setColumnCount(len(self.fields))
        self.table.setHorizontalHeaderLabels(self.fields)
        self.reload()

    def _path(self):
        from .masters import _user_path
        return _user_path(self.win.settings, self.spec["key"], self.spec["default"])

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
        spec = self.spec
        if spec["repeat"]:
            return {"型式": model, "ブロック数": f"{w.e_blocks.value():g}",
                    "回数": f"{w.e_repeats.value():g}",
                    "再現開始": f"{w.e_rstart.value():g}",
                    "再現終了": f"{w.e_rend.value():g}"}
        if spec["tilt"]:
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
        self.win.statusBar().showMessage(
            f"{rec['型式']} の{self.cmb_target.currentText()}条件を登録しました")

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
        rec = {self.fields[j]: (self.table.item(row, j).text()
                                if self.table.item(row, j) else "")
               for j in range(len(self.fields))}
        w = self.win
        w.e_model.setText(rec.get("型式", ""))
        if self.spec["repeat"]:
            def num(field, default):
                try:
                    return float(rec.get(field, "") or default)
                except ValueError:
                    return default
            w.e_blocks.setValue(int(num("ブロック数", w.e_blocks.value())))
            w.e_repeats.setValue(int(num("回数", w.e_repeats.value())))
            w.e_rstart.setValue(num("再現開始", w.e_rstart.value()))
            w.e_rend.setValue(num("再現終了", w.e_rend.value()))
            w.statusBar().showMessage(f"{rec.get('型式', '')} の再現条件を画面へ反映しました")
        else:
            w.on_model_entered()


class PastDataDialog(QtWidgets.QDialog):
    """過去データ検索。.BS/.KS（検査表）フォルダを型式・機番で探し、保存済みの結果を表示。

    型式の頭文字フォルダ（例 RWE）から探す。値は旧アプリが保存した結果をそのまま使う。
    検索はバックグラウンドで行い、画面が固まらないようにする。
    """

    _search_done = QtCore.Signal(int, list)

    RESULT_COLUMNS = [
        ("ホイールCW 精度", "ホイール CW 精度PP"),
        ("ホイールCCW 精度", "ホイール CCW 精度PP"),
        ("ウォームCW 精度", "ウォーム CW 精度PP"),
        ("ウォームCCW 精度", "ウォーム CCW 精度PP"),
        ("総合BL MIN", "総合BL MIN"),
        ("総合BL MAX", "総合BL MAX"),
        ("総合BL 差", "総合BL 差"),
    ]
    COLUMNS = (["日付", "型式", "機番", "名前", "モード"]
               + [c[0] for c in RESULT_COLUMNS] + ["ファイル"])

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("過去データ検索（.BS/.KS）")
        self.resize(1180, 560)
        layout = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("型式"))
        self.e_model = QtWidgets.QLineEdit(self.win.e_model.text().strip())
        self.e_model.setPlaceholderText("例: RWE / RWE-200")
        self.e_model.setMaximumWidth(160)
        self.e_model.returnPressed.connect(self.reload)
        top.addWidget(self.e_model)
        top.addWidget(QtWidgets.QLabel("機番"))
        self.e_machine = QtWidgets.QLineEdit(self.win.e_machine.text().strip())
        self.e_machine.setPlaceholderText("例: 261042")
        self.e_machine.setMaximumWidth(140)
        self.e_machine.returnPressed.connect(self.reload)
        top.addWidget(self.e_machine)
        top.addWidget(QtWidgets.QLabel("上限"))
        self.e_count = QtWidgets.QSpinBox()
        self.e_count.setRange(1, 2000)
        self.e_count.setValue(max(int(win.settings.get("recent_count") or 10), 50))
        self.e_count.setSuffix(" 件")
        top.addWidget(self.e_count)
        b_search = QtWidgets.QPushButton("検索")
        b_search.clicked.connect(self.reload)
        top.addWidget(b_search)
        top.addStretch(1)
        layout.addLayout(top)

        self.lbl_root = QtWidgets.QLabel("")
        self.lbl_root.setStyleSheet("color:#666;")
        layout.addWidget(self.lbl_root)

        self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self.table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self.load_selected)
        layout.addWidget(self.table, 1)

        buttons = QtWidgets.QHBoxLayout()
        b_csv = QtWidgets.QPushButton("CSV出力")
        b_csv.setToolTip("検索結果の一覧表をCSV(Excelで開ける)で保存する")
        b_load = QtWidgets.QPushButton("選択をロード")
        b_close = QtWidgets.QPushButton("閉じる")
        b_csv.clicked.connect(self.export_csv)
        b_load.clicked.connect(self.load_selected)
        b_close.clicked.connect(self.accept)
        buttons.addWidget(b_csv)
        buttons.addStretch(1)
        buttons.addWidget(b_load)
        buttons.addWidget(b_close)
        layout.addLayout(buttons)
        self._records = []
        self._search_gen = 0
        self._search_done.connect(self._on_search_done)
        self.reload()

    def _bs_root(self):
        return str(self.win.settings.get("bs_save_root") or "").strip()

    def reload(self, *args):
        root = self._bs_root()
        if not root:
            self.lbl_root.setText("⚠ 「.BS/.KS保存先」が未設定です（設定画面で指定してください）")
            self.table.setRowCount(0)
            self._records = []
            self.win.statusBar().showMessage(
                "検索できません：設定の「.BS/.KS保存先」が空です")
            return
        if not Path(root).exists():
            self.lbl_root.setText(f"⚠ 保存先が見つかりません: {root}")
            self.table.setRowCount(0)
            self._records = []
            self.win.statusBar().showMessage(
                f"検索できません：保存先フォルダが存在しません（{root}）")
            return
        self.lbl_root.setText(f"検索中… {root}")
        self._search_gen += 1
        gen = self._search_gen
        model = self.e_model.text()
        machine = self.e_machine.text()
        limit = self.e_count.value()

        def work():
            try:
                recs = report.search_inspection(
                    root, model=model, machine=machine, limit=limit)
            except Exception:
                recs = []
            self._search_done.emit(gen, recs)

        threading.Thread(target=work, daemon=True).start()

    def _on_search_done(self, gen, records):
        if gen != self._search_gen:
            return  # 新しい検索が始まっているので古い結果は捨てる
        self._records = records
        self.lbl_root.setText(f"検索先: {self._bs_root()}")
        self.table.setRowCount(len(records))
        for i, rec in enumerate(records):
            cells = ([rec.get("日付", ""), rec.get("型式", ""), rec.get("機番", ""),
                      rec.get("名前", ""), rec.get("モード", "")]
                     + self._result_cells(rec.get("metrics", {}))
                     + [rec.get("ファイル", "")])
            for j, text in enumerate(cells):
                self.table.setItem(i, j, QtWidgets.QTableWidgetItem(str(text)))
        self.table.resizeColumnsToContents()
        self.win.statusBar().showMessage(f"過去データ検索: {len(records)}件")

    @classmethod
    def _result_cells(cls, metrics):
        cells = []
        for _label, key in cls.RESULT_COLUMNS:
            v = metrics.get(key)
            cells.append("" if v is None else f'{v:g}"')
        return cells

    def export_csv(self):
        if self.table.rowCount() == 0:
            self.win.statusBar().showMessage("出力するデータがありません")
            return
        from datetime import datetime
        default = str(resolve_save_root(self.win.settings)
                      / f"過去データ検索_{datetime.now():%Y%m%d_%H%M}.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "CSVに保存", default, "CSVファイル (*.csv)")
        if not path:
            return
        rows = [[(self.table.item(i, j).text() if self.table.item(i, j) else "")
                 for j in range(self.table.columnCount())]
                for i in range(self.table.rowCount())]
        try:
            report.write_table_csv(path, list(self.COLUMNS), rows)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "CSV出力", f"失敗しました:\n{e}")
            return
        self.win.statusBar().showMessage(f"CSV出力: {path}")

    def load_selected(self, *args):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._records):
            return
        self.win.load_path(self._records[row]["パス"])
        self.accept()


class AnalysisDialog(QtWidgets.QDialog):
    """分析画面。

    「横断比較」タブ … 保存先の過去データを型式/件数で絞り、各精度PP・
        バックラッシ等を一覧表＋棒グラフで比較する。
    「1件詳細」タブ … 現在表示中の測定を大きいグラフ＋全指標で見る。
    どちらも CSV / Excel(.xlsx・グラフ入り) で出力でき、印刷もできる。
    """

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("分析")
        self.resize(1040, 720)
        self.records = []
        self.headers = []
        self.rows = []
        self.metric_cols = []
        self.single_result_rows = []
        self.single_series = None
        self._models_loaded = False

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._build_compare_tab(), "横断比較")
        tabs.addTab(self._build_single_tab(), "1件詳細")
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(tabs)

        self.reload_compare()
        self.load_single()

    # ----- 共通 -----

    def _default_name(self, kind, ext):
        from datetime import datetime
        root = resolve_save_root(self.win.settings)
        return str(Path(root) / f"{kind}_{datetime.now():%Y%m%d_%H%M}.{ext}")

    @staticmethod
    def _fill_table(table, headers, rows):
        table.clear()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, text in enumerate(row):
                item = QtWidgets.QTableWidgetItem(str(text))
                if headers[j] == "判定" and str(text).startswith("NG"):
                    item.setForeground(QtGui.QBrush(QtGui.QColor("red")))
                table.setItem(i, j, item)
        table.resizeColumnsToContents()

    @staticmethod
    def _fill_kv_table(table, rows):
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["項目", "値"])
        table.setRowCount(len(rows))
        for i, (item, value) in enumerate(rows):
            table.setItem(i, 0, QtWidgets.QTableWidgetItem(str(item)))
            cell = QtWidgets.QTableWidgetItem(str(value))
            if str(value).startswith("NG"):
                cell.setForeground(QtGui.QBrush(QtGui.QColor("red")))
            table.setItem(i, 1, cell)
        table.resizeColumnsToContents()

    # ----- 横断比較タブ -----

    def _build_compare_tab(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("型式"))
        self.e_cmp_model = QtWidgets.QLineEdit(self.win.e_model.text().strip())
        self.e_cmp_model.setPlaceholderText("例: RWE / RWE-200")
        self.e_cmp_model.setMaximumWidth(150)
        self.e_cmp_model.returnPressed.connect(self.reload_compare)
        top.addWidget(self.e_cmp_model)
        top.addWidget(QtWidgets.QLabel("機番"))
        self.e_cmp_machine = QtWidgets.QLineEdit()
        self.e_cmp_machine.setPlaceholderText("例: 261042")
        self.e_cmp_machine.setMaximumWidth(130)
        self.e_cmp_machine.returnPressed.connect(self.reload_compare)
        top.addWidget(self.e_cmp_machine)
        top.addWidget(QtWidgets.QLabel("種別"))
        self.cmb_cmp_mode = QtWidgets.QComboBox()
        self.cmb_cmp_mode.addItem("すべて", "")
        for m in MODES:
            self.cmb_cmp_mode.addItem(m, m)
        self.cmb_cmp_mode.currentIndexChanged.connect(self.reload_compare)
        top.addWidget(self.cmb_cmp_mode)
        top.addWidget(QtWidgets.QLabel("上限"))
        self.sp_count = QtWidgets.QSpinBox()
        self.sp_count.setRange(1, 2000)
        self.sp_count.setValue(max(int(self.win.settings.get("recent_count") or 10), 50))
        self.sp_count.setSuffix(" 件")
        top.addWidget(self.sp_count)
        b_search = QtWidgets.QPushButton("検索")
        b_search.clicked.connect(self.reload_compare)
        top.addWidget(b_search)
        top.addWidget(QtWidgets.QLabel("グラフ指標"))
        self.cmb_metric = QtWidgets.QComboBox()
        self.cmb_metric.currentIndexChanged.connect(self.update_compare_plot)
        top.addWidget(self.cmb_metric, 1)
        b_refresh = QtWidgets.QPushButton("更新")
        b_refresh.clicked.connect(self.reload_compare)
        top.addWidget(b_refresh)
        v.addLayout(top)

        # 2段目: グラフの横軸・集計・種類の切り替え
        opt = QtWidgets.QHBoxLayout()
        opt.addWidget(QtWidgets.QLabel("横軸"))
        self.cmb_xaxis = QtWidgets.QComboBox()
        for label in report.XAXIS_FIELDS:  # 機番 / 日付 / 型式
            self.cmb_xaxis.addItem(label, label)
        self.cmb_xaxis.currentIndexChanged.connect(self.update_compare_plot)
        opt.addWidget(self.cmb_xaxis)
        opt.addWidget(QtWidgets.QLabel("集計"))
        self.cmb_agg = QtWidgets.QComboBox()
        for label, key in (("個別", "none"), ("平均", "mean"),
                           ("最大", "max"), ("最小", "min")):
            self.cmb_agg.addItem(label, key)
        self.cmb_agg.currentIndexChanged.connect(self.update_compare_plot)
        opt.addWidget(self.cmb_agg)
        opt.addWidget(QtWidgets.QLabel("グラフ"))
        self.cmb_gtype = QtWidgets.QComboBox()
        for label, key in (("棒", "bar"), ("折れ線", "line")):
            self.cmb_gtype.addItem(label, key)
        self.cmb_gtype.currentIndexChanged.connect(self.update_compare_plot)
        opt.addWidget(self.cmb_gtype)
        opt.addStretch(1)
        v.addLayout(opt)

        self.cmp_table = QtWidgets.QTableWidget(0, 0)
        self.cmp_table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.cmp_table.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        v.addWidget(self.cmp_table, 3)

        self.cmp_plot = pg.PlotWidget()
        self.cmp_plot.showGrid(x=True, y=True, alpha=0.3)
        self.cmp_plot.setLabel("left", "値", units='"')
        self.cmp_plot.getAxis("left").enableAutoSIPrefix(False)
        v.addWidget(self.cmp_plot, 2)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        for label, slot in (("CSV出力", self.export_compare_csv),
                            ("Excel出力", self.export_compare_xlsx),
                            ("印刷", self.print_compare)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            btns.addWidget(b)
        v.addLayout(btns)
        return w

    def reload_compare(self, *args):
        root = str(self.win.settings.get("bs_save_root") or "").strip()
        if not root:
            self.records = []
            self.win.statusBar().showMessage(
                "分析できません：設定の「.BS/.KS保存先」が空です")
        elif not Path(root).exists():
            self.records = []
            self.win.statusBar().showMessage(
                f"分析できません：保存先フォルダが存在しません（{root}）")
        else:
            self.records = report.search_inspection(
                root, model=self.e_cmp_model.text(),
                machine=self.e_cmp_machine.text(), limit=self.sp_count.value())
        # 種別（回転分割/傾斜分割/再現…）で絞る＝表がごちゃつかない
        sel_mode = self.cmb_cmp_mode.currentData()
        if sel_mode:
            self.records = [r for r in self.records if r.get("モード") == sel_mode]
        self.metric_cols = report.available_metrics(self.records)
        self.headers, self.rows = report.build_comparison_table(
            self.records, self.metric_cols)
        self._fill_table(self.cmp_table, self.headers, self.rows)
        cur = self.cmb_metric.currentText()
        self.cmb_metric.blockSignals(True)
        self.cmb_metric.clear()
        self.cmb_metric.addItems(self.metric_cols)
        idx = self.cmb_metric.findText(cur)
        if idx >= 0:
            self.cmb_metric.setCurrentIndex(idx)
        self.cmb_metric.blockSignals(False)
        self.update_compare_plot()

    def _compare_options(self):
        """グラフの選択状態 (指標, 横軸キー, 集計キー, グラフ種別) を返す"""
        return (
            self.cmb_metric.currentText(),
            self.cmb_xaxis.currentData() or "機番",
            self.cmb_agg.currentData() or "none",
            self.cmb_gtype.currentData() or "bar",
        )

    def _compare_series(self):
        """選択中の指標・横軸・集計で (指標, 横軸, グラフ種別, ラベル列, 値列)"""
        metric, x_field, agg, gtype = self._compare_options()
        labels, values = ([], [])
        if metric:
            labels, values = report.aggregate_series(
                self.records, metric, x_field, agg)
        return metric, x_field, gtype, labels, values

    def _draw_compare(self, plot, metric, x_field, gtype, labels, values):
        x = list(range(len(values)))
        if gtype == "line":
            plot.plot(x, values, pen=pg.mkPen("#1f77b4", width=2),
                      symbol="o", symbolSize=6)
        else:
            plot.addItem(pg.BarGraphItem(x=x, height=values, width=0.6,
                                         brush="#1f77b4"))
        plot.getAxis("bottom").setTicks([list(zip(x, labels))])
        plot.setLabel("bottom", x_field)
        plot.setTitle(metric)

    def update_compare_plot(self, *args):
        self.cmp_plot.clear()
        metric, x_field, gtype, labels, values = self._compare_series()
        if not values:
            return
        self._draw_compare(self.cmp_plot, metric, x_field, gtype, labels, values)

    def export_compare_csv(self):
        if not self.rows:
            self.win.statusBar().showMessage("出力するデータがありません")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "CSVに保存", self._default_name("横断比較", "csv"),
            "CSVファイル (*.csv)")
        if not path:
            return
        try:
            report.write_table_csv(path, self.headers, self.rows)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "CSV出力", f"失敗しました:\n{e}")
            return
        self.win.statusBar().showMessage(f"CSV出力: {path}")

    def export_compare_xlsx(self):
        if not self.rows:
            self.win.statusBar().showMessage("出力するデータがありません")
            return
        if not excel_export.HAVE_OPENPYXL:
            QtWidgets.QMessageBox.warning(
                self, "Excel出力", "openpyxl が見つかりません（pip install openpyxl）")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Excelに保存", self._default_name("横断比較", "xlsx"),
            "Excelブック (*.xlsx)")
        if not path:
            return
        # 1枚目=全データ表、2枚目=選択中の横軸/集計/種類でのグラフ用データ＋ネイティブグラフ
        sheets = [dict(name="横断比較", headers=self.headers, rows=self.rows)]
        metric, x_field, gtype, labels, values = self._compare_series()
        if labels:
            grows = [[lab, f"{val:.2f}"] for lab, val in zip(labels, values)]
            sheets.append(dict(
                name="グラフ", headers=[x_field, metric], rows=grows,
                chart=dict(type=gtype, title=metric, cat_col=0, val_cols=[1],
                           x_title=x_field, y_title="秒")))
        try:
            excel_export.write_xlsx(path, sheets)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Excel出力", f"失敗しました:\n{e}")
            return
        self.win.statusBar().showMessage(f"Excel出力: {path}")

    def _compare_plot_image(self):
        metric, x_field, gtype, labels, values = self._compare_series()
        if not values:
            return None
        plot = pg.PlotWidget(title=metric)
        plot.resize(1000, 360)
        plot.showGrid(x=True, y=True, alpha=0.3)
        plot.getAxis("left").enableAutoSIPrefix(False)
        self._draw_compare(plot, metric, x_field, gtype, labels, values)
        image = plot.grab().toImage()
        plot.deleteLater()
        return image

    def _compare_html(self, with_image):
        head = "".join(
            f"<th style='border:1px solid #999;padding:1px 4px;'>{h}</th>"
            for h in self.headers)
        body = ""
        for row in self.rows:
            body += "<tr>" + "".join(
                f"<td style='border:1px solid #999;padding:1px 4px;'>{c}</td>"
                for c in row) + "</tr>"
        img = "<p><img src='cmp.png' width='1000'></p>" if with_image else ""
        title = (self.e_cmp_model.text().strip() or "すべて") + (
            f" / 機番 {self.e_cmp_machine.text().strip()}"
            if self.e_cmp_machine.text().strip() else "")
        return (f"<h3>過去データ横断比較（{title}）</h3>{img}"
                f"<table style='font-size:7pt;' cellspacing='0'>"
                f"<tr>{head}</tr>{body}</table>")

    def print_compare(self):
        if not self.rows:
            self.win.statusBar().showMessage("印刷するデータがありません")
            return
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageOrientation(QtGui.QPageLayout.Landscape)
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("横断比較の印刷")
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        document = QtGui.QTextDocument()
        image = self._compare_plot_image()
        if image is not None:
            document.addResource(QtGui.QTextDocument.ImageResource,
                                 QtCore.QUrl("cmp.png"), image)
        document.setHtml(self._compare_html(image is not None))
        document.print_(printer)
        self.win.statusBar().showMessage("印刷しました")

    # ----- 1件詳細タブ -----

    def _build_single_tab(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        self.single_meta = QtWidgets.QLabel("")
        self.single_meta.setStyleSheet("font-weight:bold;")
        self.single_meta.setWordWrap(True)
        v.addWidget(self.single_meta)
        self.single_plot = pg.PlotWidget()
        self.single_plot.addLegend(offset=(10, 10))
        self.single_plot.setLabel("bottom", "指令角度", units="°")
        self.single_plot.setLabel("left", "偏差", units='"')
        self.single_plot.showGrid(x=True, y=True, alpha=0.3)
        for axis in ("left", "bottom"):
            self.single_plot.getAxis(axis).enableAutoSIPrefix(False)
        v.addWidget(self.single_plot, 3)
        self.single_table = QtWidgets.QTableWidget(0, 2)
        self.single_table.setHorizontalHeaderLabels(["項目", "値"])
        self.single_table.horizontalHeader().setStretchLastSection(True)
        self.single_table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        v.addWidget(self.single_table, 2)
        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        for label, slot in (("CSV出力", self.export_single_csv),
                            ("Excel出力", self.export_single_xlsx),
                            ("印刷", self.print_single)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            btns.addWidget(b)
        v.addLayout(btns)
        return w

    def load_single(self):
        win = self.win
        self.single_plot.clear()
        self.single_result_rows = []
        self.single_series = None
        if not win.has_view_data():
            self.single_meta.setText(
                "表示中の測定データがありません（取込またはロード後に開いてください）")
            self.single_table.setRowCount(0)
            return
        meta = [
            ("型式", win.e_model.text()), ("機番", win.e_machine.text()),
            ("日付", win.e_date.date().toString("yyyy/MM/dd")),
            ("測定者", win.e_operator.text()),
            ("測定温度", f"{win.e_temp.text()} °C"),
            ("モード", win.current_mode()),
        ]
        self.single_meta.setText("　".join(f"{k}: {v}" for k, v in meta))
        self.single_result_rows = win.current_result_rows()
        self._fill_kv_table(self.single_table, self.single_result_rows)
        series = win.current_series_devs()
        self.single_series = series
        if series:
            for key, style in CURVE_STYLES.items():
                ser = series.get(key)
                if ser and ser[0]:
                    self.single_plot.plot(
                        ser[0], ser[1], name=SERIES_LABELS.get(key, key), **style)
            self.single_plot.setTitle("偏差")
        else:
            self.single_plot.setTitle("再現性（数値は下表を参照）")

    def export_single_csv(self):
        if not self.single_result_rows:
            self.win.statusBar().showMessage("出力するデータがありません")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "CSVに保存", self._default_name("1件詳細", "csv"),
            "CSVファイル (*.csv)")
        if not path:
            return
        try:
            report.write_table_csv(path, ["項目", "値"], self.single_result_rows)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "CSV出力", f"失敗しました:\n{e}")
            return
        self.win.statusBar().showMessage(f"CSV出力: {path}")

    def export_single_xlsx(self):
        if not self.single_result_rows:
            self.win.statusBar().showMessage("出力するデータがありません")
            return
        if not excel_export.HAVE_OPENPYXL:
            QtWidgets.QMessageBox.warning(
                self, "Excel出力", "openpyxl が見つかりません（pip install openpyxl）")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Excelに保存", self._default_name("1件詳細", "xlsx"),
            "Excelブック (*.xlsx)")
        if not path:
            return
        sheets = [dict(name="指標", headers=["項目", "値"],
                       rows=self.single_result_rows)]
        if self.single_series:
            h, r, val_cols = report.deviation_table(self.single_series)
            if r:
                sheets.append(dict(
                    name="偏差", headers=h, rows=r,
                    chart=dict(type="line", title="偏差", cat_col=0,
                               val_cols=val_cols, x_title="指令角度[°]",
                               y_title="秒")))
        try:
            excel_export.write_xlsx(path, sheets)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Excel出力", f"失敗しました:\n{e}")
            return
        self.win.statusBar().showMessage(f"Excel出力: {path}")

    def print_single(self):
        if not self.win.has_view_data():
            self.win.statusBar().showMessage("印刷するデータがありません")
            return
        self.win.print_report()


class PitchCorrectionDialog(QtWidgets.QDialog):
    """ピッチエラー補正（提出用）。補正表＋補正前後グラフを表示し、CSV保存/印刷する。

    補正間隔・補正単位はその場で変更でき、「既定にする」で settings.json にも保存できる。
    """

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("ピッチエラー補正（提出用）")
        self.resize(920, 720)
        self._rows = []
        layout = QtWidgets.QVBoxLayout(self)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("補正間隔"))
        self.sp_interval = QtWidgets.QDoubleSpinBox()
        self.sp_interval.setRange(0.001, 360.0)
        self.sp_interval.setDecimals(4)
        self.sp_interval.setSuffix(" °")
        self.sp_interval.setValue(float(win.settings.get("p_interval") or 100000) * 1e-4)
        top.addWidget(self.sp_interval)
        top.addWidget(QtWidgets.QLabel("補正単位"))
        self.sp_unit = QtWidgets.QDoubleSpinBox()
        self.sp_unit.setRange(0.0001, 1.0)
        self.sp_unit.setDecimals(4)
        self.sp_unit.setSuffix(" °")
        self.sp_unit.setValue(float(win.settings.get("p_unit") or 0.001))
        top.addWidget(self.sp_unit)
        self.c_default = QtWidgets.QCheckBox("この間隔/単位を既定にする")
        top.addWidget(self.c_default)
        top.addStretch(1)
        layout.addLayout(top)
        self.sp_interval.valueChanged.connect(self.recompute)
        self.sp_unit.valueChanged.connect(self.recompute)

        self.plot = pg.PlotWidget(title="ホイール偏差（補正前＝破線／補正後＝実線）")
        self.plot.addLegend(offset=(10, 10))
        self.plot.setLabel("bottom", "指令角度", units="°")
        self.plot.setLabel("left", "偏差", units='"')
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        for axis in ("left", "bottom"):
            self.plot.getAxis(axis).enableAutoSIPrefix(False)
        layout.addWidget(self.plot, 2)

        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        layout.addWidget(self.table, 3)

        btns = QtWidgets.QHBoxLayout()
        b_csv = QtWidgets.QPushButton("CSV保存")
        b_print = QtWidgets.QPushButton("印刷")
        b_close = QtWidgets.QPushButton("閉じる")
        b_csv.clicked.connect(self.export_csv)
        b_print.clicked.connect(self.print_table)
        b_close.clicked.connect(self._close)
        btns.addStretch(1)
        btns.addWidget(b_csv)
        btns.addWidget(b_print)
        btns.addWidget(b_close)
        layout.addLayout(btns)

        self.recompute()

    def _unit_header(self):
        return f'補正値[{self.sp_unit.value():g}°]'

    def recompute(self, *args):
        interval = self.sp_interval.value()
        unit = self.sp_unit.value()
        self._rows = compensation_table(self.win.data or {}, interval, unit)
        self._fill_table()
        self._draw()

    def _fill_table(self):
        headers = ["No", "角度[°]", "CW偏差[\"]", "CCW偏差[\"]", "平均[\"]", self._unit_header()]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            ccw = "—" if row["ccw"] is None else f'{row["ccw"]:.2f}'
            cells = [row["no"], f'{row["angle"]:g}', f'{row["cw"]:.2f}', ccw,
                     f'{row["mean"]:.2f}', row["units"]]
            for j, text in enumerate(cells):
                self.table.setItem(i, j, QtWidgets.QTableWidgetItem(str(text)))
        self.table.resizeColumnsToContents()

    def _draw(self):
        self.plot.clear()
        data = self.win.data or {}
        for key, color in (("wheel_cw", "#1f77b4"), ("wheel_ccw", "#d62728")):
            t, m = data.get(key, ([], []))
            if t:
                self.plot.plot(
                    np.asarray(t, dtype=float), deviation_sec(t, m),
                    pen=pg.mkPen(color, width=1, style=QtCore.Qt.DashLine),
                    name=f"{SERIES_LABELS[key]}（前）")
        for key, color in (("wheel_cw", "#1f77b4"), ("wheel_ccw", "#d62728")):
            corrected = apply_compensation(data, self._rows)
            if key in corrected:
                t, d = corrected[key]
                self.plot.plot(t, d, pen=pg.mkPen(color, width=2),
                               symbol="o", symbolSize=4,
                               name=f"{SERIES_LABELS[key]}（後）")

    def _export_rows(self):
        rows = []
        for row in self._rows:
            ccw = "" if row["ccw"] is None else f'{row["ccw"]:.2f}'
            rows.append([row["no"], f'{row["angle"]:g}', f'{row["cw"]:.2f}', ccw,
                         f'{row["mean"]:.2f}', row["units"]])
        return rows

    def export_csv(self):
        if not self._rows:
            self.win.statusBar().showMessage("補正表がありません（分割データが必要）")
            return
        from datetime import datetime
        machine = self.win.e_machine.text().strip() or "pcorr"
        default = str(resolve_save_root(self.win.settings)
                      / f"{machine}_ピッチエラー補正_{datetime.now():%Y%m%d_%H%M}.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "CSVに保存", default, "CSVファイル (*.csv)")
        if not path:
            return
        headers = ["No", "角度[°]", "CW偏差[\"]", "CCW偏差[\"]", "平均[\"]", self._unit_header()]
        meta = [
            ["ピッチエラー補正表"],
            ["型式", self.win.e_model.text().strip()],
            ["機番", machine],
            ["補正間隔[°]", f"{self.sp_interval.value():g}"],
            ["補正単位[°]", f"{self.sp_unit.value():g}"],
            [],
            headers,
        ]
        try:
            report.write_table_csv(path, None, meta + self._export_rows())
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "CSV保存", f"失敗しました:\n{e}")
            return
        self.win.statusBar().showMessage(f"CSV保存: {path}")

    def _html(self, with_image):
        head = "".join(
            f"<th style='border:1px solid #999;padding:1px 6px;'>{h}</th>"
            for h in ["No", "角度[°]", "CW偏差[\"]", "CCW偏差[\"]", "平均[\"]", self._unit_header()])
        body = ""
        for row in self._rows:
            ccw = "—" if row["ccw"] is None else f'{row["ccw"]:.2f}'
            cells = [row["no"], f'{row["angle"]:g}', f'{row["cw"]:.2f}', ccw,
                     f'{row["mean"]:.2f}', row["units"]]
            body += "<tr>" + "".join(
                f"<td style='border:1px solid #999;padding:1px 6px;text-align:right;'>{c}</td>"
                for c in cells) + "</tr>"
        img = "<p><img src='pc.png' width='950'></p>" if with_image else ""
        meta = (f"型式 {self.win.e_model.text().strip()}／機番 "
                f"{self.win.e_machine.text().strip()}／補正間隔 {self.sp_interval.value():g}°"
                f"／補正単位 {self.sp_unit.value():g}°")
        return (f"<h3>ピッチエラー補正表（提出用）</h3><p>{meta}</p>{img}"
                f"<table style='font-size:8pt;' cellspacing='0'><tr>{head}</tr>{body}</table>")

    def print_table(self):
        if not self._rows:
            self.win.statusBar().showMessage("補正表がありません（分割データが必要）")
            return
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageOrientation(QtGui.QPageLayout.Landscape)
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("ピッチエラー補正の印刷")
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        document = QtGui.QTextDocument()
        corrected = apply_compensation(self.win.data or {}, self._rows)
        image = self.win._render_series_plot(
            corrected, "ピッチエラー補正後（シミュレーション）")
        document.addResource(QtGui.QTextDocument.ImageResource,
                             QtCore.QUrl("pc.png"), image)
        document.setHtml(self._html(True))
        document.print_(printer)
        self.win.statusBar().showMessage("印刷しました")

    def _close(self):
        if self.c_default.isChecked():
            self.win.settings["p_interval"] = int(round(self.sp_interval.value() * 1e4))
            self.win.settings["p_unit"] = self.sp_unit.value()
            try:
                save_settings(self.win.settings)
                self.win.statusBar().showMessage("補正間隔/単位を既定として保存しました")
            except Exception:
                pass
        self.accept()


class GraphZoomDialog(QtWidgets.QDialog):
    """グラフを画面いっぱいに拡大表示する（現在の表示データを大きく描く）。"""

    def __init__(self, win):
        super().__init__(win)
        self.setWindowTitle("グラフ拡大")
        layout = QtWidgets.QVBoxLayout(self)

        def style_plot(plot, title):
            plot.setTitle(title)
            plot.addLegend(offset=(10, 10))
            plot.showGrid(x=True, y=True, alpha=0.3)
            plot.setLabel("bottom", "指令角度", units="°")
            plot.setLabel("left", "偏差", units='"')
            for axis in ("left", "bottom"):
                plot.getAxis(axis).enableAutoSIPrefix(False)

        if win.view_kind == "repeat":
            plot = pg.PlotWidget()
            style_plot(plot, "再現性（ブロックごとのばらつき）")
            xs = {"cw": [], "ccw": []}
            ys = {"cw": [], "ccw": []}
            for (dirn, i), vals in (win.rep_data or {}).items():
                angle = win.rep_points[i]
                for v in vals:
                    xs[dirn].append(angle)
                    ys[dirn].append((v - angle) * 3600.0)
            plot.plot(xs["cw"], ys["cw"], pen=None, symbol="o",
                      symbolBrush="#1f77b4", symbolSize=8, name="CW")
            plot.plot(xs["ccw"], ys["ccw"], pen=None, symbol="o",
                      symbolBrush="#d62728", symbolSize=8, name="CCW")
            layout.addWidget(plot, 1)
        else:
            devs = win.display_series_devs() or {}
            wheel = pg.PlotWidget()
            worm = pg.PlotWidget()
            style_plot(wheel, "ホイール")
            style_plot(worm, "ウォーム")
            for key, st in CURVE_STYLES.items():
                ser = devs.get(key)
                if ser and ser[0]:
                    target = wheel if key.startswith("wheel") else worm
                    target.plot(ser[0], ser[1], name=SERIES_LABELS.get(key, key), **st)
            row = QtWidgets.QHBoxLayout()
            row.addWidget(wheel, 7)
            if not win.is_tilt() or any(devs.get(k) and devs[k][0]
                                        for k in ("worm_cw", "worm_ccw")):
                row.addWidget(worm, 3)
            layout.addLayout(row, 1)

        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)
        b_close = QtWidgets.QPushButton("閉じる")
        b_close.clicked.connect(self.accept)
        bottom.addWidget(b_close)
        layout.addLayout(bottom)


class HelpDialog(QtWidgets.QDialog):
    """アプリ全体＋新機能の詳細ヘルプ（左に見出し一覧・右に本文）。"""

    def __init__(self, parent=None, start_title=None):
        super().__init__(parent)
        self.setWindowTitle("詳細ヘルプ")
        self.resize(860, 620)
        layout = QtWidgets.QVBoxLayout(self)

        split = QtWidgets.QHBoxLayout()
        self.list = QtWidgets.QListWidget()
        self.list.setMaximumWidth(240)
        self.body = QtWidgets.QTextBrowser()
        self.body.setOpenExternalLinks(False)
        for title, _ in help_text.HELP_SECTIONS:
            self.list.addItem(title)
        self.list.currentRowChanged.connect(self._show_row)
        split.addWidget(self.list)
        split.addWidget(self.body, 1)
        layout.addLayout(split, 1)

        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)
        b_close = QtWidgets.QPushButton("閉じる")
        b_close.clicked.connect(self.accept)
        bottom.addWidget(b_close)
        layout.addLayout(bottom)

        start = 0
        if start_title:
            for i, (title, _) in enumerate(help_text.HELP_SECTIONS):
                if start_title in title:
                    start = i
                    break
        self.list.setCurrentRow(start)

    def _show_row(self, row):
        if 0 <= row < len(help_text.HELP_SECTIONS):
            self.body.setHtml(help_text.HELP_SECTIONS[row][1])


class AlarmHelpDialog(QtWidgets.QDialog):
    """FANUCアラームを番号/キーワードで検索し、意味と対処の目安を表示する。"""

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle("FANUCアラーム検索")
        self.resize(820, 600)
        csv_path = settings.get("fanuc_alarm_csv") if settings else None
        if csv_path and not Path(csv_path).is_absolute():
            csv_path = str(app_dir() / csv_path)
        self.alarms = fanuc_alarms.load_alarms(csv_path)

        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(fanuc_alarms.DISCLAIMER)
        note.setWordWrap(True)
        note.setStyleSheet("color:#b45309;")  # 注意喚起のアンバー
        layout.addWidget(note)

        search_row = QtWidgets.QHBoxLayout()
        search_row.addWidget(QtWidgets.QLabel("検索"))
        self.e_query = QtWidgets.QLineEdit()
        self.e_query.setPlaceholderText("例: 510 / OT / オーバートラベル / 100 / 電池 / 通信")
        self.e_query.textChanged.connect(self._refilter)
        search_row.addWidget(self.e_query, 1)
        self.lbl_count = QtWidgets.QLabel("")
        search_row.addWidget(self.lbl_count)
        layout.addLayout(search_row)

        split = QtWidgets.QHBoxLayout()
        self.list = QtWidgets.QListWidget()
        self.list.setMaximumWidth(280)
        self.list.currentRowChanged.connect(self._show_row)
        self.body = QtWidgets.QTextBrowser()
        split.addWidget(self.list)
        split.addWidget(self.body, 1)
        layout.addLayout(split, 1)

        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)
        b_close = QtWidgets.QPushButton("閉じる")
        b_close.clicked.connect(self.accept)
        bottom.addWidget(b_close)
        layout.addLayout(bottom)

        self._results = []
        self._refilter()

    def _refilter(self):
        self._results = fanuc_alarms.search_alarms(self.alarms, self.e_query.text())
        self.list.blockSignals(True)
        self.list.clear()
        for a in self._results:
            self.list.addItem(f'{a.get("code", "")}　{a.get("title", "")}')
        self.list.blockSignals(False)
        self.lbl_count.setText(f"{len(self._results)}件")
        if self._results:
            self.list.setCurrentRow(0)
        else:
            self.body.setHtml("<p>該当するアラームが見つかりません。"
                              "別の番号やキーワードで試すか、機械メーカーの資料を確認してください。</p>")

    def _show_row(self, row):
        if not (0 <= row < len(self._results)):
            return
        a = self._results[row]
        self.body.setHtml(
            f'<h3>{a.get("code", "")}　{a.get("title", "")}</h3>'
            f'<p style="color:#666;">分類: {a.get("group", "")}</p>'
            f'<p><b>原因</b><br>{a.get("cause", "")}</p>'
            f'<p><b>対処</b><br>{a.get("remedy", "")}</p>'
        )


class MainWindow(QtWidgets.QMainWindow):
    # 接続スレッド完了通知（成功か, ステータス文）。スレッドからGUIへ安全に渡す
    _conn_done = QtCore.Signal(bool, str)
    # 通信診断スレッド完了通知（レポート文字列）
    _diag_done = QtCore.Signal(str)
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
        self.auto_tilt_retries = 0      # 傾きNGでの再測定回数
        self.auto_prec_retries = 0      # 精度NGでの再測定回数
        self.web = None           # Webモニタ（起動は__init__末尾で）
        self._web_lock = threading.Lock()
        self._web_state = {}
        self._web_png = b""
        self.seq = None        # 取込中のシーケンス（ロード表示時は None）
        self.data = None       # 分割測定の表示対象データ
        self.rep_points = None  # 再現性測定のブロック角度
        self.rep_data = None    # 再現性測定の表示対象データ
        self.view_kind = "indexing"  # 現在表示中のデータ種別

        # ラベル＋入力をひと組の小箱にして、モードに応じて箱ごと出し入れする
        # （横一列に詰め込まず、左サイドの「測定条件」グループに縦並びにする）
        def field_box(label_text, *widgets, label_width=104):
            box = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(5)
            lbl = QtWidgets.QLabel(label_text)
            lbl.setMinimumWidth(label_width)
            h.addWidget(lbl)
            for w in widgets:
                h.addWidget(w)
            h.addStretch(1)
            return box, lbl

        # ===== 測定情報グループ（取込開始の必須項目）=====
        self.e_model = QtWidgets.QLineEdit()
        self.e_model.setPlaceholderText("例: RWE-200")
        self.e_machine = QtWidgets.QLineEdit()
        self.e_machine.setPlaceholderText("例: 12345")
        self.e_date = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        self.e_date.setDisplayFormat("yyyy-MM-dd")
        self.e_date.setCalendarPopup(True)
        self.e_operator = QtWidgets.QLineEdit()
        self.e_operator.setPlaceholderText("測定者")
        self.e_temp = QtWidgets.QLineEdit()
        self.e_temp.setPlaceholderText("23.5")
        self.e_temp.setValidator(QtGui.QDoubleValidator(-20.0, 60.0, 2))
        # 入力欄は短く（枠が無駄に伸びないように上限を付ける）
        self.e_model.setMaximumWidth(120)
        self.e_machine.setMaximumWidth(120)
        self.e_date.setMaximumWidth(120)
        self.e_operator.setMaximumWidth(96)
        self.e_temp.setMaximumWidth(58)

        self.info_group = info_group = QtWidgets.QGroupBox("測定情報")
        info_grid = QtWidgets.QGridLayout(info_group)
        info_grid.setContentsMargins(6, 4, 6, 4)
        info_grid.setHorizontalSpacing(6)
        info_grid.setVerticalSpacing(4)
        info_grid.addWidget(QtWidgets.QLabel("型式"), 0, 0)
        info_grid.addWidget(self.e_model, 0, 1)
        info_grid.addWidget(QtWidgets.QLabel("機番"), 1, 0)
        info_grid.addWidget(self.e_machine, 1, 1)
        info_grid.addWidget(QtWidgets.QLabel("日付"), 2, 0)
        info_grid.addWidget(self.e_date, 2, 1)
        # 名前の下に測定温度（測定情報を縦に積んで横幅を詰める）
        info_grid.addWidget(QtWidgets.QLabel("名前"), 3, 0)
        info_grid.addWidget(self.e_operator, 3, 1)
        info_grid.addWidget(QtWidgets.QLabel("温度℃"), 4, 0)
        info_grid.addWidget(self.e_temp, 4, 1)
        info_grid.setColumnStretch(2, 1)  # 右に余白を作って左へ寄せる

        # ===== 測定条件グループ =====
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(MODES)
        self.mode_combo.currentTextChanged.connect(self.on_mode_changed)

        # ホイールの開始/終了角度（傾斜分割用）。ラベルと配置は box_ranges 側で行う
        self.e_wstart = QtWidgets.QDoubleSpinBox()
        self.e_wstart.setRange(-360.0, 360.0)
        self.e_wstart.setValue(-30.0)
        self.e_wstart.setSuffix(" °")
        self.e_wend = QtWidgets.QDoubleSpinBox()
        self.e_wend.setRange(-360.0, 720.0)
        self.e_wend.setValue(110.0)
        self.e_wend.setSuffix(" °")

        self.e_wheel = QtWidgets.QDoubleSpinBox()
        self.e_wheel.setRange(0.001, 180.0)
        self.e_wheel.setValue(wheel_pitch)
        self.e_wheel.setSuffix(" °/pt")
        self.box_wheel, self.l_wheel = field_box("ホイール刻み", self.e_wheel)

        self.e_worm = QtWidgets.QDoubleSpinBox()
        self.e_worm.setDecimals(4)
        self.e_worm.setRange(0.0001, 90.0)
        self.e_worm.setValue(worm_pitch)
        self.e_worm.setSuffix(" °/pt")
        self.box_worm, self.l_worm = field_box("ウォーム刻み", self.e_worm)
        self.e_range = QtWidgets.QDoubleSpinBox()
        self.e_range.setDecimals(4)
        self.e_range.setRange(0.001, 360.0)
        self.e_range.setValue(worm_range)
        self.e_range.setSuffix(" °")
        self.box_range, self.l_range = field_box("ウォーム範囲", self.e_range)
        self.e_start = QtWidgets.QDoubleSpinBox()
        self.e_start.setDecimals(4)
        self.e_start.setRange(0.0, 360.0)
        self.e_start.setValue(worm_start)
        self.e_start.setSuffix(" °")
        self.box_start, self.l_start = field_box("ウォーム開始", self.e_start)

        self.e_blocks = QtWidgets.QSpinBox()
        self.e_blocks.setRange(2, 360)
        self.e_blocks.setValue(4)
        self.e_blocks.setSuffix(" 箇所")
        self.box_blocks, self.l_blocks = field_box("ブロック数", self.e_blocks)
        self.e_repeats = QtWidgets.QSpinBox()
        self.e_repeats.setRange(2, 99)
        self.e_repeats.setValue(7)
        self.e_repeats.setSuffix(" 回")
        self.box_repeats, self.l_repeats = field_box("回数", self.e_repeats)
        self.e_rstart = QtWidgets.QDoubleSpinBox()
        self.e_rstart.setRange(-360.0, 720.0)
        self.e_rstart.setValue(0.0)
        self.e_rstart.setSuffix(" °")
        self.box_rstart, self.l_rstart = field_box("再現開始", self.e_rstart)
        self.e_rend = QtWidgets.QDoubleSpinBox()
        self.e_rend.setRange(-360.0, 720.0)
        self.e_rend.setValue(270.0)
        self.e_rend.setSuffix(" °")
        self.box_rend, self.l_rend = field_box("再現終了", self.e_rend)

        # 主点評価・バックラッシ手動補正（分割系のみ）
        self.e_evald = QtWidgets.QSpinBox()
        self.e_evald.setRange(0, 720)
        self.e_evald.setSpecialValueText("なし")
        self.e_evald.setSuffix(" 等分")
        self.e_evald.setToolTip(
            "主点（1/N）の等分数。型式を選ぶとマスタの分割数（1/N）から自動で入る。"
            "値を変えると、グラフの主点マーカーと主点精度が即更新される")
        self.e_evald.valueChanged.connect(self.refresh_results)
        self.box_evald, self.l_evald = field_box("主点評価", self.e_evald)
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
        self.box_blcorr, self.l_blcorr = field_box(
            "バックラッシ補正", self.e_blcorr, self.b_corr)

        # 評価範囲1/2（傾斜分割のみ。客先要求の部分抜き出し評価）
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
        for spin in (self.e_r1s, self.e_r1e, self.e_r2s, self.e_r2e):
            spin.setMaximumWidth(74)
        # 狭いサイドに収まるよう、チェック行→開始/終了行 で2段に折り返す
        self.box_ranges = QtWidgets.QWidget()
        ranges_h = QtWidgets.QHBoxLayout(self.box_ranges)
        ranges_h.setContentsMargins(0, 0, 0, 0)
        ranges_h.setSpacing(18)

        def range_block(check, e_start, e_end):
            box = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            v.addWidget(check)
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(12, 0, 0, 0)
            row.setSpacing(4)
            row.addWidget(QtWidgets.QLabel("開始"))
            row.addWidget(e_start)
            row.addWidget(QtWidgets.QLabel("終了"))
            row.addWidget(e_end)
            v.addLayout(row)
            return box

        # 評価範囲1・2は横に並べる（縦積みで右に空白を作らない）
        ranges_h.addWidget(range_block(self.c_r1, self.e_r1s, self.e_r1e),
                           0, QtCore.Qt.AlignTop)
        ranges_h.addWidget(range_block(self.c_r2, self.e_r2s, self.e_r2e),
                           0, QtCore.Qt.AlignTop)
        # ホイールの開始/終了角度も同じ横帯に並べる（ホイール群から移して縦を詰める）
        self.l_wstart2 = QtWidgets.QLabel("開始角度")
        self.l_wend2 = QtWidgets.QLabel("終了角度")
        wr_head = QtWidgets.QLabel("ホイール範囲")
        _wf = wr_head.font(); _wf.setBold(True); wr_head.setFont(_wf)
        wr_head.setStyleSheet("background:#e3e9f2; padding:2px 6px;")
        wrange = QtWidgets.QWidget()
        wrv = QtWidgets.QVBoxLayout(wrange)
        wrv.setContentsMargins(0, 0, 0, 0)
        wrv.setSpacing(2)
        wrv.addWidget(wr_head)
        wrow = QtWidgets.QHBoxLayout()
        wrow.setContentsMargins(12, 0, 0, 0)
        wrow.setSpacing(4)
        wrow.addWidget(self.l_wstart2)
        wrow.addWidget(self.e_wstart)
        wrow.addWidget(self.l_wend2)
        wrow.addWidget(self.e_wend)
        wrv.addLayout(wrow)
        ranges_h.addWidget(wrange, 0, QtCore.Qt.AlignTop)
        ranges_h.addStretch(1)
        for check in (self.c_r1, self.c_r2):
            check.toggled.connect(self.refresh_results)
        for spin in (self.e_r1s, self.e_r1e, self.e_r2s, self.e_r2e):
            spin.valueChanged.connect(self.refresh_results)

        self.e_comment = QtWidgets.QLineEdit()
        self.e_comment.setPlaceholderText("コメント（任意。セーブ時に保存される）")

        # 入力欄は幅をそろえてサイドをコンパクトに保つ
        for sp in (self.e_wstart, self.e_wend, self.e_wheel, self.e_worm,
                   self.e_range, self.e_start, self.e_rstart, self.e_rend,
                   self.e_blocks, self.e_repeats, self.e_evald, self.e_blcorr):
            sp.setMaximumWidth(118)

        self.cond_group = cond_group = QtWidgets.QGroupBox("測定条件")
        cond_v = QtWidgets.QVBoxLayout(cond_group)
        cond_v.setSpacing(3)
        cond_v.setContentsMargins(6, 4, 6, 4)
        mode_row = QtWidgets.QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        lbl_mode = QtWidgets.QLabel("モード")
        lbl_mode.setMinimumWidth(104)
        mode_row.addWidget(lbl_mode)
        mode_row.addWidget(self.mode_combo, 1)
        cond_v.addLayout(mode_row)
        # ホイール／ウォーム／再現 を見出しで分けて並べる（混在させない）
        def cond_header(text):
            h = QtWidgets.QLabel(text)
            hf = h.font()
            hf.setBold(True)
            h.setFont(hf)
            h.setStyleSheet("background:#e3e9f2; padding:2px 6px;")
            return h

        # ホイール群（傾斜は 開始/終了角度 も出す）
        self.wheel_group = QtWidgets.QWidget()
        wg = QtWidgets.QGridLayout(self.wheel_group)
        wg.setContentsMargins(0, 0, 0, 0)
        wg.setHorizontalSpacing(8)
        wg.setVerticalSpacing(3)
        # 開始/終了角度は評価範囲の横帯へ移したので、ここは刻みだけ
        wg.addWidget(cond_header("ホイール"), 0, 0, 1, 4)
        wg.addWidget(QtWidgets.QLabel("刻み"), 1, 0)
        wg.addWidget(self.e_wheel, 1, 1)
        wg.setColumnStretch(4, 1)

        # ウォーム群
        self.worm_group = QtWidgets.QWidget()
        wmg = QtWidgets.QGridLayout(self.worm_group)
        wmg.setContentsMargins(0, 0, 0, 0)
        wmg.setHorizontalSpacing(8)
        wmg.setVerticalSpacing(3)
        wmg.addWidget(cond_header("ウォーム"), 0, 0, 1, 4)
        wmg.addWidget(QtWidgets.QLabel("刻み"), 1, 0)
        wmg.addWidget(self.e_worm, 1, 1)
        wmg.addWidget(QtWidgets.QLabel("範囲"), 1, 2)
        wmg.addWidget(self.e_range, 1, 3)
        wmg.addWidget(QtWidgets.QLabel("開始"), 2, 0)
        wmg.addWidget(self.e_start, 2, 1)
        wmg.setColumnStretch(4, 1)

        # 再現群（再現性・合体のみ）
        self.repeat_group = QtWidgets.QWidget()
        rg = QtWidgets.QGridLayout(self.repeat_group)
        rg.setContentsMargins(0, 0, 0, 0)
        rg.setHorizontalSpacing(8)
        rg.setVerticalSpacing(3)
        rg.addWidget(cond_header("再現"), 0, 0, 1, 4)
        rg.addWidget(QtWidgets.QLabel("ブロック数"), 1, 0)
        rg.addWidget(self.e_blocks, 1, 1)
        rg.addWidget(QtWidgets.QLabel("回数"), 1, 2)
        rg.addWidget(self.e_repeats, 1, 3)
        rg.addWidget(QtWidgets.QLabel("再現開始"), 2, 0)
        rg.addWidget(self.e_rstart, 2, 1)
        rg.addWidget(QtWidgets.QLabel("再現終了"), 2, 2)
        rg.addWidget(self.e_rend, 2, 3)
        rg.setColumnStretch(4, 1)

        # ホイール/ウォーム/再現を横並びにして縦の高さを詰める（全モードで全部見える）
        groups_row = QtWidgets.QHBoxLayout()
        groups_row.setContentsMargins(0, 0, 0, 0)
        groups_row.setSpacing(14)
        groups_row.addWidget(self.wheel_group, 0, QtCore.Qt.AlignTop)
        groups_row.addWidget(self.worm_group, 0, QtCore.Qt.AlignTop)
        groups_row.addWidget(self.repeat_group, 0, QtCore.Qt.AlignTop)
        groups_row.addStretch(1)
        cond_v.addLayout(groups_row)

        # 主点評価・バックラッシ補正（分割系のみ）
        self.eval_group = QtWidgets.QWidget()
        eg = QtWidgets.QGridLayout(self.eval_group)
        eg.setContentsMargins(0, 0, 0, 0)
        eg.setHorizontalSpacing(8)
        eg.setVerticalSpacing(3)
        eg.addWidget(QtWidgets.QLabel("主点評価"), 0, 0)
        eg.addWidget(self.e_evald, 0, 1)
        eg.addWidget(QtWidgets.QLabel("バックラッシ補正"), 1, 0)
        eg.addWidget(self.e_blcorr, 1, 1)
        eg.addWidget(self.b_corr, 1, 2)
        eg.setColumnStretch(3, 1)
        # 主点評価・バックラッシ補正は、ホイール/ウォーム/再現の群と同じ横帯に
        # 並べて横スペースを使う（短い行が単独で残って右に大きな空白ができるのを防ぐ）。
        groups_row.insertWidget(groups_row.count() - 1, self.eval_group,
                                0, QtCore.Qt.AlignTop)

        cond_v.addWidget(self.box_ranges)  # 評価範囲（傾斜のみ）は全幅
        comment_row = QtWidgets.QHBoxLayout()
        comment_row.setContentsMargins(0, 0, 0, 0)
        lbl_comment = QtWidgets.QLabel("コメント")
        lbl_comment.setMinimumWidth(104)
        comment_row.addWidget(lbl_comment)
        comment_row.addWidget(self.e_comment, 1)
        cond_v.addLayout(comment_row)

        # ===== 操作グループ（取込のメイン操作）=====
        self.b_start = QtWidgets.QPushButton("取込開始")
        self.b_start.setObjectName("primary")
        self.b_auto = QtWidgets.QPushButton("自動測定")
        self.b_auto.setObjectName("primary")
        self.b_auto.setToolTip(
            "取込開始→SwitchBotで機械を起動→完了後に傾き判定→NGなら自動で再測定"
        )
        self.b_cancel = QtWidgets.QPushButton("中止")
        self.b_cancel.setEnabled(False)
        self.b_take = QtWidgets.QPushButton("手動取込")
        self.b_take.setEnabled(False)
        self.b_undo = QtWidgets.QPushButton("1点戻る")
        self.b_undo.setEnabled(False)
        self.b_partial = QtWidgets.QPushButton("部分再測定")
        self.b_partial.setToolTip("選んだ系列（例 ホイールCWだけ）を測り直す。他のデータはそのまま")
        self.b_start.clicked.connect(self.start)
        self.b_auto.clicked.connect(self.auto_start)
        self.b_cancel.clicked.connect(self.cancel)
        self.b_take.clicked.connect(self.take_manual)
        self.b_undo.clicked.connect(self.undo)
        self.b_partial.clicked.connect(self.show_partial_remeasure)
        # 取込開始・自動測定は上部ツールバー（セーブの横）へ。
        # ここには取込中の操作（中止・手動取込・1点戻る）だけ置く。
        ops_group = QtWidgets.QGroupBox("取込中の操作")
        ops_v = QtWidgets.QVBoxLayout(ops_group)
        ops_v.setContentsMargins(6, 4, 6, 4)
        sub_row = QtWidgets.QHBoxLayout()
        for b in (self.b_cancel, self.b_take, self.b_undo):
            sub_row.addWidget(b, 1)
        ops_v.addLayout(sub_row)

        # ===== 上部ツールバー（ファイル/データ操作）=====
        self.b_save = QtWidgets.QPushButton("セーブ")
        self.b_save.setEnabled(False)
        self.b_print = QtWidgets.QPushButton("印刷")
        self.b_print.setEnabled(False)
        self.b_raw = QtWidgets.QPushButton("生データ")
        self.b_raw.clicked.connect(self.show_raw_data)
        self.b_past = QtWidgets.QPushButton("過去データ")
        self.b_past.clicked.connect(self.show_past_data)
        self.b_analyze = QtWidgets.QPushButton("分析")
        self.b_analyze.setToolTip("過去データの横断比較・1件詳細をグラフ/表で見てCSV・Excel・印刷")
        self.b_analyze.clicked.connect(self.show_analysis)
        self.b_cond = QtWidgets.QPushButton("条件編集")
        self.b_cond.clicked.connect(self.show_condition_editor)
        self.b_program = QtWidgets.QPushButton("プログラム作成")
        self.b_program.setToolTip("現在の測定条件からFANUC測定プログラム(Gコード)を作成")
        self.b_program.clicked.connect(self.show_program_dialog)
        self.b_param = QtWidgets.QPushButton("パラメータ")
        self.b_param.setToolTip("製品ごとのパラメータ変更（差分）の確認表・差分ファイルを"
                                "作ってカード/LANへ出力（機械への入力は人が実施）")
        self.b_param.clicked.connect(self.show_param_dialog)
        self.b_pcorr = QtWidgets.QPushButton("ピッチエラー補正")
        self.b_pcorr.setToolTip("提出用のピッチエラー補正表＋補正後グラフを表示・CSV保存・印刷")
        self.b_pcorr.clicked.connect(self.show_pitch_correction)
        self.b_alarm = QtWidgets.QPushButton("アラーム")
        self.b_alarm.setToolTip("FANUCのアラーム番号・メッセージから意味と対処の目安を調べる")
        self.b_alarm.clicked.connect(self.show_alarm_help)
        b_load = QtWidgets.QPushButton("ロード")
        b_settings = QtWidgets.QPushButton("設定")
        b_help = QtWidgets.QPushButton("ヘルプ")
        b_help.setToolTip("アプリ全体と新機能（クランプ分割・機械へ送信など）の詳細ヘルプ")
        self.b_save.clicked.connect(self.save)
        self.b_print.clicked.connect(self.print_report)
        b_load.clicked.connect(self.load)
        b_settings.clicked.connect(self.open_settings)
        b_help.clicked.connect(self.show_help)
        toolbar = QtWidgets.QToolBar("操作")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(QtCore.Qt.TopToolBarArea, toolbar)
        # 取込開始・自動測定を左上（セーブの横）に置く
        toolbar.addWidget(self.b_start)
        toolbar.addWidget(self.b_auto)
        toolbar.addWidget(self.b_partial)
        toolbar.addSeparator()
        for b in (self.b_save, self.b_print, b_load):
            toolbar.addWidget(b)
        toolbar.addSeparator()
        for b in (self.b_raw, self.b_past, self.b_analyze):
            toolbar.addWidget(b)
        toolbar.addSeparator()
        for b in (self.b_cond, self.b_program, self.b_param, self.b_pcorr, self.b_alarm):
            toolbar.addWidget(b)
        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                             QtWidgets.QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        toolbar.addWidget(b_settings)
        toolbar.addWidget(b_help)

        # ===== ガイドと受信値・データ数 =====
        self.guide = QtWidgets.QLabel("―")
        self.guide.setObjectName("guide")
        self.guide.setAlignment(QtCore.Qt.AlignCenter)
        self.guide.setStyleSheet("padding:6px;")
        self.live = QtWidgets.QLabel("")
        self.live.setStyleSheet("color:#64748b; padding:2px;")
        self.counts = QtWidgets.QLabel("")
        # 受信点数はグラフのすぐ上に大きく出す（例 ホイール CW 3/36）
        self.counts.setStyleSheet(
            "color:#1d4ed8; padding:2px 6px; font-weight:bold; font-size:13pt;")
        live_row = QtWidgets.QHBoxLayout()
        live_row.addWidget(self.live)
        live_row.addStretch(1)
        live_row.addWidget(self.counts)

        # ===== グラフ（分割: 左ホイール/右ウォーム 7:3。再現性: 左のみ）=====
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
        self.main_markers = {}  # 主点（1/N）グリッドの強調マーカー
        plots_widget = QtWidgets.QWidget()
        plots = QtWidgets.QHBoxLayout(plots_widget)
        plots.setContentsMargins(0, 0, 0, 0)
        plots.addWidget(self.plot_wheel, 7)
        plots.addWidget(self.plot_worm, 3)

        # ===== 結果表 =====
        # 精度結果は2つに分ける（横を狭く・縦を長く＝グラフを広げる）:
        #   上＝系列/精度PP/傾き、下＝系列/単一誤差/隣接誤差。さらに下にバックラッシ表。
        def make_metric_table(headers):
            t = QtWidgets.QTableWidget(0, len(headers))
            t.setHorizontalHeaderLabels(headers)
            hh = t.horizontalHeader()
            # 全列を中身ぶんの幅にする（数値セルが無駄に大きくならない）。
            # 表全体の幅は right_col 側を中身に合わせて決めるので余白も出ない。
            for c in range(len(headers)):
                hh.setSectionResizeMode(c, QtWidgets.QHeaderView.ResizeToContents)
            hh.setStretchLastSection(False)
            t.verticalHeader().setVisible(False)
            t.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            t.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            t.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            t.setFocusPolicy(QtCore.Qt.NoFocus)
            return t

        self.table_series = make_metric_table(PP_SLOPE_HEADERS)     # 上: 精度PP・傾き
        self.table_series2 = make_metric_table(SINGLE_ADJ_HEADERS)  # 下: 単一・隣接
        self.table_misc = QtWidgets.QTableWidget(0, 2)
        self.table_misc.setHorizontalHeaderLabels(["項目", "値"])
        _mh = self.table_misc.horizontalHeader()
        _mh.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        _mh.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        _mh.setVisible(False)  # 行を「項目＋値」の全幅1行で出すので列見出しは隠す
        self.table_misc.verticalHeader().setVisible(False)
        self.table_misc.setWordWrap(True)  # 規格つきの長い値は折返して切らさない
        self.table_misc.setTextElideMode(QtCore.Qt.ElideNone)
        self.table_misc.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.table_misc.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        # 補正前（生の偏差）／補正後（ホイールのピッチエラー補正を当てた偏差）の切替
        self.show_corrected = False
        self.b_before = QtWidgets.QPushButton("補正前（生データ）")
        self.b_after = QtWidgets.QPushButton("補正後（傾き補正）")
        self.b_after.setToolTip("始点と終点の偏差を一致させた（傾き成分を除いた）"
                                "偏差・精度を表示。ピッチエラー補正は含まない")
        for b in (self.b_before, self.b_after):
            b.setCheckable(True)
        self.b_before.setChecked(True)
        corr_group = QtWidgets.QButtonGroup(self)
        corr_group.setExclusive(True)
        corr_group.addButton(self.b_before)
        corr_group.addButton(self.b_after)
        self.b_before.clicked.connect(lambda: self.set_corrected(False))
        self.b_after.clicked.connect(lambda: self.set_corrected(True))
        self.corr_bar = QtWidgets.QWidget()
        corr_row = QtWidgets.QHBoxLayout(self.corr_bar)
        corr_row.setContentsMargins(0, 0, 0, 0)
        corr_row.addWidget(QtWidgets.QLabel("表示"))
        corr_row.addWidget(self.b_before)
        corr_row.addWidget(self.b_after)
        corr_row.addStretch(1)
        self.b_zoom = QtWidgets.QPushButton("グラフ拡大")
        self.b_zoom.setToolTip("グラフを画面いっぱいに拡大表示する")
        self.b_zoom.clicked.connect(self.show_graph_zoom)
        # グラフ拡大は corr_bar に入れず、下のバーへ（全モードで常に表示）

        # ===== 全体レイアウト（左＝条件＋グラフ / 右＝精度結果の縦長列）=====
        # 左カラム（広い）: 上に[測定情報＋測定条件（横に広げる）]、その下に操作バー、
        #   さらに下にグラフ（左下で縦に大きく取る。右に表が無いぶん縦長・やや横狭）。
        # 右カラム（縦長）: 精度結果(系列)を上、バックラッシ(項目/値)を下に積む。
        #   窓の高さをいっぱい使うので、縦に長い精度表もグラフに被らず全部出る。
        info_group.setMaximumWidth(210)  # 温度を名前の下にして横幅を詰めた

        # 左上: 測定情報＋測定条件。測定条件は中身ぶんの幅だけ取り（無駄に伸ばさない）、
        # 余った横は右側のあき（クリーンな余白）にする。短い行の横に大きな空白が
        # できないよう、主点評価/バックラッシ補正は群の横帯にまとめてある。
        top_left = QtWidgets.QHBoxLayout()
        top_left.setSpacing(8)
        top_left.addWidget(info_group, 0, QtCore.Qt.AlignTop)
        top_left.addWidget(cond_group, 0, QtCore.Qt.AlignTop)
        top_left.addStretch(1)

        # グラフのすぐ上に「取込中の操作・補正前/後・グラフ拡大・データ数」を常時表示。
        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(2, 0, 2, 0)
        bar.setSpacing(10)
        bar.addWidget(ops_group)
        bar.addWidget(self.corr_bar)        # 補正前/後（分割系のみ表示）
        bar.addWidget(self.b_zoom)          # グラフ拡大（全モードで常に表示）
        bar.addWidget(self.live)
        bar.addStretch(1)
        bar.addWidget(self.counts)
        plots_widget.setMinimumHeight(240)

        # 左カラム: ガイド → 測定情報/条件 → 操作バー → グラフ（残りを縦いっぱい）
        left_col = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left_col)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(4)
        lv.addWidget(self.guide, 0)
        lv.addLayout(top_left, 0)
        lv.addLayout(bar, 0)
        lv.addWidget(plots_widget, 1)       # グラフは左下で縦に大きく

        # 右カラム（縦長）: 精度PP・傾き → 単一・隣接 → バックラッシ を上下に積み、
        # 上へ寄せる。各表は中身ぶんの高さ（_fit_table_height）で全行見える。
        for _t in (self.table_series, self.table_series2, self.table_misc):
            _t.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                             QtWidgets.QSizePolicy.Fixed)
        self.right_col = right_col = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right_col)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)
        rv.addWidget(self.table_series, 0)   # 精度PP・傾き
        rv.addWidget(self.table_series2, 0)  # 単一・隣接
        rv.addWidget(self.table_misc, 0)     # バックラッシ
        rv.addStretch(1)                     # 表は上に寄せ、余白は下へ

        # 左（条件＋グラフ）と右（精度結果の縦長列）を横に並べる。右カラムの幅は
        # フォントに合わせて固定し（apply_ui_fonts で設定）、実機のフォントが大きく
        # ても数値やバックラッシ値が切れない。余りはすべて左（グラフ）が使う。
        page = QtWidgets.QWidget()
        ph = QtWidgets.QHBoxLayout(page)
        ph.setContentsMargins(6, 4, 6, 6)
        ph.setSpacing(8)
        ph.addWidget(left_col, 1)           # グラフ側が残り幅をすべて使う
        ph.addWidget(right_col, 0)          # 右は精度結果の縦長列（フォント連動の固定幅）
        self.setCentralWidget(page)
        self.apply_ui_fonts()
        # 表示後に各表の高さを中身（行数）に合わせる
        QtCore.QTimer.singleShot(0, self._shrink_result_tables)

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
        # 起動後にマスタの欠落を確認して、見つからなければ画面が出てから警告する
        QtCore.QTimer.singleShot(300, self.warn_missing_masters)
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

    def reload_masters(self, warn=False):
        """マスタCSV（提供＋ユーザー登録）を読み直す。warn=Trueで欠落を警告表示。"""
        try:
            self.masters = load_masters(self.settings)
        except Exception as e:
            self.statusBar().showMessage(f"マスタ再読込に失敗: {e}")
            return
        if warn:
            self.warn_missing_masters()

    def warn_missing_masters(self):
        """型式マスタ（測定条件/合否判定）が指定の場所に無ければ、はっきり警告する。"""
        missing = missing_masters(self.settings)
        if not missing:
            return
        lines = "\n".join(f"・{label}：{path}" for label, path in missing)
        QtWidgets.QMessageBox.warning(
            self, "型式マスタが見つかりません",
            "次の型式マスタが指定の場所にありません。\n"
            "このままだと型式の自動適用・合否判定ができません。\n"
            "「設定」でCSVの場所（または設置）を確認してください。\n\n" + lines,
        )

    def show_condition_editor(self):
        """条件登録/編集ダイアログを開く（現在のモードに合った対象を初期選択）"""
        if self.is_repeat() or self.is_combined():
            target = "傾斜再現" if self.is_tilt() else "回転再現"
        else:
            target = "傾斜分割" if self.is_tilt() else "回転分割"
        ConditionRegistryDialog(self, target=target).exec()

    def show_past_data(self):
        PastDataDialog(self).exec()

    def show_analysis(self):
        AnalysisDialog(self).exec()

    def apply_ui_fonts(self):
        """基準フォントサイズに合わせて、強調表示（ガイド・主要ボタン等）を拡大する。

        QSSではなくアプリのフォント(pt)を基準にするので、設定の文字サイズに追従する。
        """
        app = QtWidgets.QApplication.instance()
        base = app.font().pointSize() if app else DEFAULT_FONT_PT
        if base <= 0:
            base = DEFAULT_FONT_PT
        guide_font = QtGui.QFont("monospace")
        guide_font.setPointSize(base + 2)
        guide_font.setBold(True)
        self.guide.setFont(guide_font)
        for button in (self.b_start, self.b_auto):
            bf = button.font()
            bf.setPointSize(base + 3)
            bf.setBold(True)
            button.setFont(bf)
        for label in (self.live, self.counts):
            lf = label.font()
            lf.setPointSize(max(base - 1, 8))
            label.setFont(lf)
        # 測定情報・測定条件・精度結果は少し大きめにして読みやすく（+1）。
        # 大きくしすぎると上段が高くなりグラフを圧迫するので控えめにする。
        for w in (self.info_group, self.cond_group, self.table_series,
                  self.table_series2, self.table_misc):
            wf = w.font()
            wf.setPointSize(base + 1)
            w.setFont(wf)
        # フォント変更後に右カラム幅を実寸へ合わせ直す
        QtCore.QTimer.singleShot(0, self._fit_right_col_width)

    def current_result_rows(self):
        """表示中データの (項目, 値) 行（印刷・分析で使う。画面と同じ内容）。

        分割系は系列指標＋総合バックラッシ（画面と同じく総合のみ。真の最大最小・
        総合判定は出さない）＋主点・傾き判定＋（複合なら）再現性。
        再現性単独は各ブロックの範囲。
        """
        if not self.has_view_data():
            return []
        if self.view_kind == "repeat":
            rsum = repeatability_summary(self.rep_points, self.rep_data)
            return list(repeat_result_rows(rsum))
        summary, _ = summarize(self.data, self.applied_blcorr)
        rows = list(series_rows(summary))
        rows.extend(self.compact_misc_rows(summary))
        rows.extend(self.main_grid_rows())
        rows.extend(self.slope_judgement_rows(summary))
        if self.is_tilt():
            rows.extend(self.tilt_accuracy_rows())
        if self.is_combined() and self.rep_data:
            rows.append(("― 再現性 ―", ""))
            rows.extend(repeat_result_rows(
                repeatability_summary(self.rep_points, self.rep_data)))
        return rows

    def current_series_devs(self):
        """表示中(分割)データの {系列: (指令角度list, 偏差list)}。

        再現性モードやデータ無しのときは None。
        """
        if not self.has_view_data() or self.view_kind == "repeat":
            return None
        _, devs = summarize(self.data, self.applied_blcorr)
        return {k: (t.tolist(), d.tolist()) for k, (t, d) in devs.items()}

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
        """型式入力で測定条件を自動適用する。

        分割条件（分割系モードのみ）と再現条件（再現/合体モードのみ）を、
        回転・傾斜で別マスタから別々に適用する（条件の誤用を防ぐ）。
        """
        text = self.e_model.text().strip()
        if not self.masters or not text:
            return
        key = text.upper()
        if not self.is_repeat():
            self._apply_division_conditions(key, text)
        if self.is_repeat() or self.is_combined():
            self._apply_repeat_conditions(key)

    def _apply_repeat_conditions(self, key):
        """型式ごとの再現性条件（回転/傾斜で別ファイル）を画面へ適用する。"""
        master = self.masters.get(
            "user_tilt_repeat" if self.is_tilt() else "user_rotary_repeat", {})
        rec = master.get(key)
        if not rec:
            self.statusBar().showMessage(
                f"型式 {key} の再現条件は未登録（「条件編集」で登録できます）")
            return

        def num(field, default):
            try:
                return float(rec.get(field, "") or default)
            except ValueError:
                return default
        self.e_blocks.setValue(int(num("ブロック数", self.e_blocks.value())))
        self.e_repeats.setValue(int(num("回数", self.e_repeats.value())))
        self.e_rstart.setValue(num("再現開始", self.e_rstart.value()))
        self.e_rend.setValue(num("再現終了", self.e_rend.value()))
        self.statusBar().showMessage(
            f"{key} 再現条件を適用: {self.e_blocks.value()}箇所×{self.e_repeats.value()}回")

    def _apply_division_conditions(self, key, text):
        """分割の測定条件・合否判定を適用する（傾斜は傾斜専用マスタのみ参照）。"""
        from .masters import user_condition_params

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
        # 主点評価は手入力させず、マスタの1/N（分割数1）から自動で入れる
        if cond and int(cond.get("div1") or 0) > 0:
            self.e_evald.setValue(int(cond["div1"]))
            message.append(f"主点 1/N={int(cond['div1'])}等分")
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
        show_repeat = is_repeat or is_combined
        # 群ごとにまとめて表示／非表示（ホイールとウォームが混ざらない）
        self.wheel_group.setVisible(show_division)
        self.worm_group.setVisible(show_division)
        self.eval_group.setVisible(show_division)
        self.repeat_group.setVisible(show_repeat)
        # ホイールの開始/終了角度は傾斜分割のときだけ（回転は0〜360固定）
        for w in (self.l_wstart2, self.e_wstart, self.l_wend2, self.e_wend):
            w.setVisible(is_tilt)
        self.box_ranges.setVisible(is_tilt and show_division)
        # 単一誤差・隣接誤差の表は分割系のみ（再現性単独では出さない）
        self.table_series2.setVisible(show_division)
        self.corr_bar.setVisible(show_division)  # 補正前/後は分割系のみ
        self.plot_worm.setVisible(show_division)
        self.plot_wheel.setTitle(
            "再現性（ブロックごとのばらつき）" if is_repeat else "ホイール")
        # モードを変えたら取込中の測定はキャンセル
        self.view_kind = "repeat" if is_repeat else ("combined" if is_combined else "indexing")
        self.discard_measurement()
        # 群の表示/非表示で右カラムの必要幅が変わるので合わせ直す
        QtCore.QTimer.singleShot(0, self._fit_right_col_width)

    def discard_measurement(self):
        """取込中の測定を破棄して初期状態に戻す"""
        self.seq = None
        # 表示は補正前に戻す（再描画は下流で行うのでフラグのみ）
        self.show_corrected = False
        self.b_before.setChecked(True)
        self.b_after.setChecked(False)
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
        self.table_series.clearSpans()
        self.table_series.setRowCount(0)
        self.table_series2.clearSpans()
        self.table_series2.setRowCount(0)
        self.table_misc.setRowCount(0)
        self._shrink_result_tables()
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
            # 系列＋総合バックラッシ・主点・傾き判定・任意誤差（画面と同じ）
            summary, _ = summarize(self.data, self.applied_blcorr)
            results = series_rows(summary)
            results += self.compact_misc_rows(summary)
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

    def show_partial_remeasure(self):
        """部分再測定: 系列を選んで測り直す（他の系列はそのまま残す）。"""
        if self.view_kind not in ("indexing", "combined") or not (
                self.data and any(t for t, _ in self.data.values())):
            self.statusBar().showMessage(
                "部分再測定は、回転/傾斜分割の測定データがあるときに使えます")
            return
        if self.seq is not None and not self.seq.done():
            self.statusBar().showMessage("取込中は部分再測定できません（先に完了か中止を）")
            return
        present = [k for k in SERIES_KEYS if self.data.get(k) and self.data[k][0]]
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("部分再測定")
        layout = QtWidgets.QVBoxLayout(dlg)
        layout.addWidget(QtWidgets.QLabel(
            "測り直す系列を選んでください（チェックした系列だけ測り直し、\n"
            "他のデータはそのまま残ります）:"))
        checks = {}
        for key in present:
            cb = QtWidgets.QCheckBox(SERIES_LABELS[key])
            checks[key] = cb
            layout.addWidget(cb)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("再測定開始")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        selected = [k for k in present if checks[k].isChecked()]
        if not selected:
            self.statusBar().showMessage("系列が選ばれていません")
            return
        self.start_partial(selected)

    def start_partial(self, selected):
        """選択系列のみ測り直す。非選択系列は現在のデータを引き継ぐ。"""
        missing = self.missing_required_fields()
        if missing:
            QtWidgets.QMessageBox.warning(
                self, "部分再測定",
                "次の項目を入力してください:\n  " + "、".join(missing))
            return
        old = {k: (list(t), list(m)) for k, (t, m) in self.data.items()}
        seq = IndexingSequence(
            wheel_pitch=self.e_wheel.value(),
            worm_pitch=self.e_worm.value(),
            worm_range=self.e_range.value(),
            worm_start=self.e_start.value(),
            wheel_start=self.e_wstart.value() if self.is_tilt() else 0.0,
            wheel_end=self.e_wend.value() if self.is_tilt() else 360.0,
            order=selected,
        )
        # 非選択系列は元データを引き継ぐ（選択系列は空＝これから測り直す）
        for key in SERIES_KEYS:
            if key not in selected and key in old:
                seq.data[key] = (list(old[key][0]), list(old[key][1]))
        self.seq = seq
        self.data = seq.data
        self.dev.flush_input()
        self.rebuild_curves()
        self.table_series.clearSpans()
        self.table_series.setRowCount(0)
        self.table_series2.clearSpans()
        self.table_series2.setRowCount(0)
        self.table_misc.setRowCount(0)
        self._shrink_result_tables()
        self.applied_blcorr = 0.0
        self.e_blcorr.setValue(0.0)
        self.b_corr.setEnabled(False)
        self.b_take.setEnabled(True)
        self.b_cancel.setEnabled(True)
        self.b_undo.setEnabled(False)
        self.b_save.setEnabled(False)
        self.b_print.setEnabled(False)
        self.redraw()  # 残す系列をすぐ表示
        self.update_counts()
        self.show_guide()
        self.update_web_snapshot(with_png=True)
        self.statusBar().showMessage(
            "部分再測定: " + "・".join(SERIES_LABELS[k] for k in selected)
            + " を測り直します（他はそのまま）")

    def auto_start(self):
        """自動測定: 取込開始→SwitchBotで機械起動→完了後に傾き/精度判定→NGなら再測定"""
        self.start()
        if self.seq is None or not self.b_take.isEnabled():
            return  # 必須項目の検証で開始できなかった
        self.auto_mode = True
        self.auto_retries = 0
        self.auto_tilt_retries = 0
        self.auto_prec_retries = 0
        if self.settings.get("switchbot_dry_run"):
            self.statusBar().showMessage("自動測定（空打ち: 実際には押しません）")
        elif not bot_configured(self.settings):
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
        dry_run = bool(settings.get("switchbot_dry_run"))
        configured = bot_configured(settings)

        def work():
            if wait_before > 0:
                time.sleep(wait_before)
            total = len(pattern)
            for i, wait_after in enumerate(pattern, start=1):
                if dry_run:
                    self._bot_msg.emit(f"空打ち（リハーサル）: 押下スキップ {i}/{total}")
                elif not configured:
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

    def auto_tilt_ok(self):
        """傾き判定: マスタの傾きH/W規格と突き合わせる（無ければOK扱い）"""
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

    def auto_precision_ok(self):
        """精度判定: 単一誤差≦5/隣接誤差≦10（統一規格）を全系列で満たすか"""
        if self.view_kind != "indexing":
            return True
        summary, _ = summarize(self.data, self.applied_blcorr)
        for key in ("wheel_cw", "wheel_ccw", "worm_cw", "worm_ccw"):
            s = summary.get(key)
            if s and (s["single"] > SINGLE_SPEC or s["adjacent"] > ADJACENT_SPEC):
                return False
        return True

    def auto_after_complete(self):
        """測定完了時の自動測定の続き: 傾き/精度OKなら終了、NGなら種類別に自動再測定"""
        if not self.auto_mode:
            return
        tilt_ok = self.auto_tilt_ok()
        prec_ok = self.auto_precision_ok()
        if tilt_ok and prec_ok:
            self.auto_mode = False
            self.statusBar().showMessage("自動測定完了: 傾き・精度OK")
            return
        max_tilt = int(self.settings.get("auto_max_retries") or 0)
        max_prec = int(self.settings.get("auto_max_precision_retries") or 0)
        if not tilt_ok and self.auto_tilt_retries < max_tilt:
            self.auto_tilt_retries += 1
            reason = f"傾きNG → 自動再測定（傾き {self.auto_tilt_retries}/{max_tilt}）"
        elif not prec_ok and self.auto_prec_retries < max_prec:
            self.auto_prec_retries += 1
            reason = f"精度NG → 自動再測定（精度 {self.auto_prec_retries}/{max_prec}）"
        else:
            self.auto_mode = False
            ng = "傾き" if not tilt_ok else "精度"
            self.statusBar().showMessage(
                f"自動測定終了: {ng}NGのまま再測定上限に到達")
            return
        self.auto_retries += 1
        self.statusBar().showMessage(reason)
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
            self.main_markers = {}
        else:
            self.curves = {
                key: (self.plot_wheel if key.startswith("wheel") else self.plot_worm).plot(
                    name=SERIES_LABELS[key], **style
                )
                for key, style in CURVE_STYLES.items()
            }
            # 主点（1/N）の点を全カーブの上に大きいオレンジ点で重ねる
            self.main_markers = {
                key: (self.plot_wheel if key.startswith("wheel")
                      else self.plot_worm).plot(
                    [], [], pen=None, symbol="o", symbolSize=11,
                    symbolBrush=(255, 165, 0), symbolPen="k",
                )
                for key in CURVE_STYLES
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

    def show_help(self):
        """アプリ全体＋新機能の詳細ヘルプを開く。"""
        HelpDialog(self).exec()

    def show_alarm_help(self):
        """FANUCアラームの番号/メッセージから意味・対処を調べる。"""
        AlarmHelpDialog(self, self.settings).exec()

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
        # 条件CSVの場所が変わった可能性があるので読み直す（欠落は警告）
        self.reload_masters(warn=True)
        # テーマ・文字サイズを即時反映
        app = QtWidgets.QApplication.instance()
        apply_font(app, self.settings.get("ui_font_pt"))
        apply_theme(app, self.settings.get("ui_theme"))
        self.apply_ui_fonts()
        self.refresh_results()  # バックラッシ表示切替などを即反映
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
        self.table_series.clearSpans()
        self.table_series.setRowCount(0)
        self.table_series2.clearSpans()
        self.table_series2.setRowCount(0)
        self.table_misc.setRowCount(0)
        self._shrink_result_tables()
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
            for t in (self.table_series, self.table_series2, self.table_misc):
                t.clearSpans()
                t.setRowCount(0)
            self._shrink_result_tables()
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

    def set_corrected(self, corrected):
        """補正前(生)／補正後(P補正)の表示を切り替えて、グラフと精度表を更新する。"""
        self.show_corrected = bool(corrected)
        self.b_before.setChecked(not self.show_corrected)
        self.b_after.setChecked(self.show_corrected)
        if self.view_kind in ("indexing", "combined") and self.has_view_data():
            self.redraw()
            self.finish_indexing()

    def display_series_devs(self):
        """現在の表示モードでの {系列: (指令角度list, 偏差list)}。

        補正後は傾き補正（始点と終点の偏差を一致させ、傾き成分を除く）を
        各系列にかけた偏差。補正前は生の偏差。
        ※ピッチエラー補正(P補正)はここには入れない（明示操作のときだけ）。
        """
        devs = {}
        for key in SERIES_KEYS:
            targets, measured = self.data.get(key, ([], []))
            if not targets:
                continue
            dev = list(deviation_sec(targets, measured))
            if self.show_corrected:
                dev = self._detrend(dev)
            devs[key] = (list(targets), dev)
        return devs

    @staticmethod
    def _detrend(dev):
        """始点と終点の偏差を一致させる（始終点を結ぶ直線＝傾き成分を除く）。"""
        n = len(dev)
        if n < 2:
            return list(dev)
        d = np.asarray(dev, dtype=float)
        return list(d - (d[-1] - d[0]) * np.arange(n) / (n - 1))

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
        devs = self.display_series_devs()
        for key, curve in self.curves.items():
            if key in devs:
                t, d = devs[key]
                curve.setData(np.asarray(t, dtype=float), np.asarray(d, dtype=float))
            else:
                curve.setData([], [])
        self._update_main_markers(devs)

    def _update_main_markers(self, devs):
        """主点（1/N）の点だけを大きいマーカーで重ねる。主点評価Nに追従。"""
        n = self.e_evald.value()
        for key, marker in getattr(self, "main_markers", {}).items():
            ser = devs.get(key)
            if not ser or not ser[0] or n <= 0:
                marker.setData([], [])
                continue
            t = np.asarray(ser[0], dtype=float)
            d = np.asarray(ser[1], dtype=float)
            intervals = len(t) - 1
            if intervals <= 0 or n > intervals or intervals % n != 0:
                marker.setData([], [])
                continue
            step = intervals // n
            marker.setData(t[::step], d[::step])

    def refresh_results(self):
        """評価範囲・主点評価の変更で結果表とグラフを再計算する（測定完了後のみ）"""
        if (self.view_kind in ("indexing", "combined") and self.data
                and not self.b_take.isEnabled()):
            if any(t for t, _ in self.data.values()):
                self.finish_indexing()
                self.redraw()  # 主点マーカーなどグラフも追従

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
                slope_val = summary[key]["slope"]  # 関数 slope() を隠さないよう別名
                ok = abs(slope_val) <= limit
                rows.append(
                    (f"{label} 傾き 判定",
                     f'{"OK" if ok else "NG"}（|{slope_val:+.2f}"| ≦ {limit:g}"）')
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

    def _spec_limit(self, grp, kind):
        """規格の上限[秒]。単一/隣接は統一規格、傾きは合否判定.csv。無ければ None。

        精度PPの規格は型式.csv（規格値）を取り込んだら入れる（今は未設定）。
        """
        if kind == "single":
            return SINGLE_SPEC
        if kind == "adjacent":
            return ADJACENT_SPEC
        if kind == "slope":
            return (self.master_judge or {}).get(
                "slope_h" if grp == "wheel" else "slope_w")
        return None

    def _spec_text(self, grp, kind):
        limit = self._spec_limit(grp, kind)
        return f'≦{limit:g}"' if limit else "—"

    def _render_metric_table(self, table, headers, rows):
        """精度の表を描く共通処理。rows = (cells, is_spec, span, col_limits) の並び。

        span=None  …通常行（全列に値）。col_limits[j] を超える値は赤字。
        span="head"…見出し行（ラベルを全列に結合。空セルの枠を出さない）。
        span="one" …単一値の行（系列名＋値。値を右端まで結合して空セルを出さない）。
        """
        ncol = len(headers)
        table.clearSpans()
        table.setColumnCount(ncol)
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(rows))
        for i, (cells, is_spec, span, limits) in enumerate(rows):
            if span == "head":
                item = QtWidgets.QTableWidgetItem(cells[0])
                item.setTextAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
                font = item.font(); font.setBold(True); item.setFont(font)
                item.setBackground(QtGui.QColor("#eaeef5"))
                table.setItem(i, 0, item)
                table.setSpan(i, 0, 1, ncol)
                continue
            if span == "one":
                # 単一値の行（主点精度・任意誤差）: 系列名＝0列、値＝精度PP列。
                # 値は傾き列まで結合して空セルの枠を出さない（数値列は広げない）。
                lab = QtWidgets.QTableWidgetItem(cells[0])
                lab.setTextAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
                lab.setToolTip(cells[0])
                table.setItem(i, 0, lab)
                val = QtWidgets.QTableWidgetItem(cells[1])
                val.setTextAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter)
                table.setItem(i, 1, val)
                table.setSpan(i, 1, 1, ncol - 1)
                continue
            for j, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                item.setTextAlignment(
                    (QtCore.Qt.AlignLeft if j == 0 else QtCore.Qt.AlignHCenter)
                    | QtCore.Qt.AlignVCenter)
                if is_spec:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setBackground(QtGui.QColor("#eaeef5"))
                elif limits[j] is not None:
                    try:
                        if abs(float(text.replace('"', ''))) > limits[j]:
                            item.setForeground(QtGui.QBrush(QtGui.QColor("#dc2626")))
                    except ValueError:
                        pass
                table.setItem(i, j, item)
        self._fit_table_height(table)

    def finish_indexing(self):
        self.b_corr.setEnabled(True)
        summary, _ = summarize(self.data, self.applied_blcorr)
        devs = self.display_series_devs()
        judge = self.master_judge or {}

        # 各系列の4指標(精度PP・単一・隣接・傾き)とその規格をまとめて集める
        groups = []  # (glabel, slope_limit, specs[4], series[(label, pp, single, adj, slope)])
        for grp, glabel in (("wheel", "ホイール"), ("worm", "ウォーム")):
            keys = [k for k in (f"{grp}_cw", f"{grp}_ccw") if k in devs]
            if not keys:
                continue
            slope_limit = judge.get(f"slope_{'h' if grp == 'wheel' else 'w'}")
            specs = (self._spec_text(grp, "pp"), self._spec_text(grp, "single"),
                     self._spec_text(grp, "adjacent"), self._spec_text(grp, "slope"))
            series = []
            for key in keys:
                d = np.asarray(devs[key][1], dtype=float)
                series.append((SERIES_LABELS[key], f'{pp(d):.1f}"', f'{single(d):.1f}"',
                               f'{adjacent(d):.1f}"', f'{slope(d):+.1f}"'))
            groups.append((glabel, slope_limit, specs, series))

        # 指標の並び: 0=精度PP, 1=単一, 2=隣接, 3=傾き。規格の上限（傾きはグループ毎）
        fixed_limit = {0: None, 1: SINGLE_SPEC, 2: ADJACENT_SPEC, 3: None}

        def build(idx_a, idx_b, extra=None):
            rows = []
            for glabel, slope_limit, specs, series in groups:
                rows.append(([f"規格（{glabel}）", specs[idx_a], specs[idx_b]],
                             True, None, [None, None, None]))
                lim_a = slope_limit if idx_a == 3 else fixed_limit[idx_a]
                lim_b = slope_limit if idx_b == 3 else fixed_limit[idx_b]
                for s in series:
                    rows.append(([s[0], s[1 + idx_a], s[1 + idx_b]],
                                 False, None, [None, lim_a, lim_b]))
            for head, items in (extra or []):
                if not items:
                    continue
                rows.append(([head, "", ""], True, "head", [None, None, None]))
                for label, value in items:
                    rows.append(([label, value, ""], False, "one", [None, None, None]))
            return rows

        # 上の表＝精度PP＋傾き。単一値の主点精度・任意誤差もこちら（精度なので）
        extra = [("― 主点精度（1/N）―", self.main_grid_rows())]
        if self.is_tilt():
            extra.append(("― 任意誤差（精度=H+W）―", self.tilt_accuracy_rows()))
        self._render_metric_table(self.table_series, PP_SLOPE_HEADERS, build(0, 3, extra))
        # 下の表＝単一誤差＋隣接誤差
        self._render_metric_table(self.table_series2, SINGLE_ADJ_HEADERS, build(1, 2))

        # バックラッシ表（主点精度・任意誤差は上の精度表へ）
        rows = self.compact_misc_rows(summary)
        if self.is_combined() and self.rep_data:
            rows.append(("― 再現性 ―", ""))
            rows.extend(repeat_result_rows(
                repeatability_summary(self.rep_points, self.rep_data)))
        self.fill_misc_table(rows)

    def compact_misc_rows(self, summary):
        """総合バックラッシ（MIN/MAX/平均/差）だけを出す。

        真の最大最小・総合判定は出さない（不要）。ホイール／ウォーム単品は
        設定 show_component_backlash が True のときだけ追加で出す。
        """
        rows = []
        if "backlash_correction" in summary:
            rows.append(("バックラッシ手動補正",
                         f'{summary["backlash_correction"]:+.2f}"'))
        temp = self.parse_temp()
        mm = None if self.is_tilt() else formula_minmax(self.master_judge, temp)
        comp = composite_backlash_minmax(self.data)
        if comp is not None:
            cmin = comp[0] + self.applied_blcorr
            cmax = comp[1] + self.applied_blcorr
            cavg = (cmin + cmax) / 2.0
            cdiff = cmax - cmin
            if mm:
                smin, smax = mm
                savg = (smin + smax) / 2.0
                rows.append(("総合バックラッシ MIN",
                             f'{cmin:.1f}"　規格 ≧{smin:.1f}"'
                             f'　{"OK" if cmin >= smin else "NG"}'))
                rows.append(("総合バックラッシ MAX",
                             f'{cmax:.1f}"　規格 ≦{smax:.1f}"'
                             f'　{"OK" if cmax <= smax else "NG"}'))
                rows.append(("総合バックラッシ 平均",
                             f'{cavg:.1f}"　規格 ≦{savg:.1f}"'
                             f'　{"OK" if cavg <= savg else "NG"}'))
            else:
                rows.append(("総合バックラッシ MIN", f'{cmin:.1f}"'))
                rows.append(("総合バックラッシ MAX", f'{cmax:.1f}"'))
                rows.append(("総合バックラッシ 平均", f'{cavg:.1f}"'))
            rows.append(("総合バックラッシ 差", f'{cdiff:.1f}"'))
        # 任意: ホイール/ウォーム単品バックラッシ（設定で表示ONのときだけ）
        if self.settings.get("show_component_backlash"):
            for grp, label in (("wheel", "ホイール"), ("worm", "ウォーム")):
                key = f"{grp}_backlash"
                if key in summary:
                    rows.append((f"（参考）{label} バックラッシ",
                                 f'{summary[key]["min"]:.1f}〜{summary[key]["max"]:.1f}"'))
        return rows

    def main_grid_rows(self):
        """主点評価（1/N）: いま選んでいる等分（主点評価欄＝マスタ1/Nから自動）で
        主点精度を出す。等分を変えれば都度この値が変わる。
        """
        divisions = self.e_evald.value()
        if divisions <= 0:
            return []
        targets = sorted(self.data.get("wheel_cw", ([], []))[0])
        intervals = len(targets) - 1
        if intervals <= 0:
            return []
        if intervals % divisions != 0 or divisions > intervals:
            return [(f"主点評価（{divisions}等分）",
                     f"測定{intervals}等分と割り切れません")]
        step = intervals // divisions
        rows = []
        for key, label in (("wheel_cw", "ホイールCW"), ("wheel_ccw", "ホイールCCW")):
            t, m = self.data.get(key, ([], []))
            pairs = sorted(zip(t, m))
            devs = deviation_sec([p[0] for p in pairs], [p[1] for p in pairs])
            rows.append((f"主点精度 {label}",
                         f'{pp(devs[::step]):.1f}"'))
        return rows

    def tilt_accuracy_rows(self):
        """傾斜分割の任意誤差評価（精度 = ホイール精度 + ウォーム精度）"""
        specs = [("全範囲", None)]
        if self.c_r1.isChecked():
            specs.append(("範囲1", (self.e_r1s.value(), self.e_r1e.value())))
        if self.c_r2.isChecked():
            specs.append(("範囲2", (self.e_r2s.value(), self.e_r2e.value())))
        rows = []
        for label, range_ in specs:
            acc = tilt_accuracy(self.data, range_=range_)
            for dirn, jp in (("cw", "正"), ("ccw", "逆")):
                entry = acc.get(dirn, {})
                if "total" in entry:
                    rows.append((f"任意誤差 {jp}（{label}）", f'{entry["total"]:.1f}"'))
        return rows

    def finish_repeat(self):
        rsum = repeatability_summary(self.rep_points, self.rep_data)
        self.table_series.clearSpans()  # 分割表示の結合が残らないように
        self.table_series.setColumnCount(len(REPEAT_HEADERS))
        self.table_series.setHorizontalHeaderLabels(REPEAT_HEADERS)
        # 全列を中身ぶんの幅に（分割→再現で列数が変わるので毎回設定）。右カラム幅は
        # _fit_right_col_width が中身合計に合わせる
        _hh = self.table_series.horizontalHeader()
        for _c in range(len(REPEAT_HEADERS)):
            _hh.setSectionResizeMode(_c, QtWidgets.QHeaderView.ResizeToContents)
        self.table_series.setRowCount(len(rsum["blocks"]))
        for i, b in enumerate(rsum["blocks"]):
            cells = [
                f"ブロック{i + 1}",
                f'{b["angle"]:g}°',
                f'{b["cw"]:.1f}"' if b["cw"] is not None else "―",
                f'{b["ccw"]:.1f}"' if b["ccw"] is not None else "―",
            ]
            for j, text in enumerate(cells):
                self.table_series.setItem(i, j, QtWidgets.QTableWidgetItem(text))
        self._fit_table_height(self.table_series)
        rows = []
        for key, label in (
            ("cw", "再現性 CW（全ブロック最大）"),
            ("ccw", "再現性 CCW（全ブロック最大）"),
            ("overall", "再現性 総合"),
        ):
            if rsum.get(key) is not None:
                rows.append((label, f'{rsum[key]:.1f}"'))
        self.fill_misc_table(rows)

    def _fit_table_height(self, table):
        """全行が見える高さに固定する（空でもヘッダ分だけ＝場所を食わない）。"""
        table.resizeRowsToContents()
        height = table.horizontalHeader().sizeHint().height() + 2 * table.frameWidth() + 4
        for r in range(table.rowCount()):
            rh = table.rowHeight(r)
            height += rh if rh > 0 else table.sizeHintForRow(r)
        table.setFixedHeight(height)

    def _shrink_result_tables(self):
        """結果表を内容（行数）に合わせた高さにする。空ならヘッダ分だけ。"""
        self._fit_table_height(self.table_series)
        self._fit_table_height(self.table_series2)
        self._fit_table_height(self.table_misc)
        self._fit_right_col_width()

    def _fit_right_col_width(self):
        """右カラム幅を精度表（系列＋数値2列）の中身ぶんに合わせる。

        全列 ResizeToContents なので、各列は中身ぶんの幅。その合計に右カラムを
        合わせると、数値セルが無駄に大きくならず、右端に余白も出ない。バックラッシ
        表は全幅1行（必要なら折返し）なので幅決定には使わない。グラフが残り幅を使う。
        """
        rc = getattr(self, "right_col", None)
        if rc is None:
            return
        need = 0
        for t in (self.table_series, self.table_series2):
            if not t.isVisible():
                continue
            t.resizeColumnsToContents()  # 全列を中身ぶんに確定させてから合計
            s = 2 * t.frameWidth() + 10
            for c in range(t.columnCount()):
                s += t.columnWidth(c)
            need = max(need, s)
        if need > 0:
            rc.setFixedWidth(max(220, min(need, 480)))

    def fill_misc_table(self, rows):
        # バックラッシ表は「項目＋値」を1行まるごと（全列結合）で表示する。
        # こうすると項目名と値が列幅を取り合わず、狭い右カラムでも切れない。
        self.table_misc.clearSpans()
        self.table_misc.setRowCount(len(rows))
        ncol = self.table_misc.columnCount()
        for i, (item, value) in enumerate(rows):
            value = value or ""
            text = f"{item}　{value}" if value else item
            cell = QtWidgets.QTableWidgetItem(text)
            cell.setTextAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            cell.setToolTip(text)
            if "NG" in value:
                cell.setForeground(QtGui.QBrush(QtGui.QColor("#dc2626")))
            elif "OK" in value:
                cell.setForeground(QtGui.QBrush(QtGui.QColor("#16a34a")))
            self.table_misc.setItem(i, 0, cell)
            self.table_misc.setSpan(i, 0, 1, ncol)
        self._fit_right_col_width()  # 結果が入った後の実寸で右カラム幅を合わせる
        self._fit_table_height(self.table_misc)

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
                self, "セーブ",
                f"元データ {path.name} が既にあります。上書き保存しますか？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                self.statusBar().showMessage("セーブを中止しました（上書きせず）")
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
        # 再現性は旧形式 .RS（回転）/ .RSK（傾斜）でも保存する
        if self.view_kind == "repeat" or (
                self.view_kind == "combined" and self.has_repeat_data()):
            self.save_rs_file(machine_no)
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

    def save_rs_file(self, machine_no):
        """再現性の旧形式を保存する（回転再現性=.RS / 傾斜再現性=.RSK）。"""
        bs_root = str(self.settings.get("bs_save_root") or "").strip()
        if not bs_root or not self.rep_points or not self.rep_data:
            return
        ext = ".RSK" if self.is_tilt() else ".RS"
        try:
            from .rs_format import data_to_rs_doc, save_rs
            close = ""
            if self.masters:
                entry = find_entry(self.masters.get("judgement", {}),
                                   self.e_model.text().strip())
                if entry:
                    close = entry.get("close", "") or ""
            doc = data_to_rs_doc(
                self.rep_points, self.rep_data,
                model=self.e_model.text().strip(),
                close=close,
                date=self.e_date.date().toString("yyyy/MM/dd"),
                operator=self.e_operator.text().strip(),
            )
            rs_path = Path(bs_root) / f"{sanitize_filename(machine_no)}{ext}"
            rs_path.parent.mkdir(parents=True, exist_ok=True)
            save_rs(rs_path, doc)
            self.statusBar().showMessage(
                f"{self.statusBar().currentMessage()} ／ {ext}も保存: {rs_path}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "セーブ",
                f"{ext}（旧形式）の保存に失敗しました（CSVは保存済み）:\n{e}")

    def _reset_eval_state(self):
        """ロード時に前回の評価条件（主点評価・評価範囲・補正・コメント）をリセットする。"""
        self.e_evald.setValue(0)
        self.c_r1.setChecked(False)
        self.c_r2.setChecked(False)
        self.applied_blcorr = 0.0
        self.e_blcorr.setValue(0.0)
        self.e_comment.clear()

    def _apply_loaded_main_grid(self):
        """ロードした型式のマスタ1/N（分割数1）を主点評価に入れる（回転系のみ）。"""
        if not self.is_tilt() and self.master_cond:
            self.e_evald.setValue(int(self.master_cond.get("div1") or 0))

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
        self._reset_eval_state()  # 前回の評価条件を残さない
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
        self._apply_loaded_main_grid()
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
        self._reset_eval_state()  # 前回の評価条件を残さない
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
        self.b_undo.setEnabled(False)
        self.live.setText("")
        self.refresh_master_refs()
        self._apply_loaded_main_grid()  # 主点評価を読み込んだ型式の1/Nに
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
        self._reset_eval_state()  # 前回の評価条件を残さない（評価範囲は下でファイルから復元）
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

    def show_graph_zoom(self):
        """グラフを画面いっぱいに拡大表示する"""
        if not self.has_view_data():
            self.statusBar().showMessage("拡大するグラフがありません")
            return
        dlg = GraphZoomDialog(self)
        dlg.setWindowState(QtCore.Qt.WindowMaximized)
        dlg.exec()

    def show_pitch_correction(self):
        """ピッチエラー補正（提出用）ダイアログを開く（分割のホイールデータが必要）"""
        if self.view_kind not in ("indexing", "combined") or not (
                self.data and self.data.get("wheel_cw", ([], []))[0]):
            self.statusBar().showMessage(
                "ピッチエラー補正には回転/傾斜分割のホイール測定データが必要です")
            return
        PitchCorrectionDialog(self).exec()

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

    def show_param_dialog(self):
        """製品ごとのパラメータ変更（差分）の確認表・差分ファイルを作るダイアログ。"""
        dlg = ParamDialog(self, self.settings,
                          model=self.e_model.text().strip(),
                          machine=self.e_machine.text().strip())
        dlg.exec()

    # ----- 印刷 -----

    def print_report(self):
        """A4一枚の検査記録を印刷する。ピッチエラー補正ページは明示選択時のみ。"""
        if not self.has_view_data():
            return
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        # ピッチエラー補正は自動では入れない。対象があるときだけ確認して付ける
        self.include_pcorr = False
        if self.view_kind in ("indexing", "combined") and not self.is_tilt():
            interval = float(self.settings.get("p_interval") or 100000) * 1e-4
            unit = float(self.settings.get("p_unit") or 0.001)
            if compensation_table(self.data, interval, unit):
                answer = QtWidgets.QMessageBox.question(
                    self, "印刷",
                    "ピッチエラー補正（提出用）のページも付けますか？",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No,
                )
                self.include_pcorr = answer == QtWidgets.QMessageBox.Yes

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

        rows = self.compact_misc_rows(summary)
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
        """P補正表＋補正後グラフのページ（印刷時に明示選択したときだけ出す）"""
        if not getattr(self, "include_pcorr", False):
            return ""
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
            f"<h2 style='margin:2px;'>{self.current_mode()} 検査記録</h2>"
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
    apply_font(app, settings.get("ui_font_pt", DEFAULT_FONT_PT))
    apply_theme(app, settings.get("ui_theme", DEFAULT_THEME))
    win = MainWindow(device, wheel_pitch, worm_pitch, worm_range, worm_start, settings)
    win.showMaximized()  # 画面いっぱいで開く（グラフに十分な高さを確保）
    sys.exit(app.exec())
