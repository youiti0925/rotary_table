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
import re
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
    composite_backlash_at_zero,
    adjacent_peak,
    deviation_sec,
    pp,
    rep_unwrap,
    repeatability_summary,
    single,
    slope,
    summarize,
)
from . import iso230
from .pcorr import apply_compensation, compensation_table
from .bs_format import (SECTION_TO_SERIES, data_to_doc, doc_to_data, load_bs,
                        save_bs, unpack_dms)
from .ks_format import (
    data_to_doc as ks_data_to_doc,
    doc_to_data as ks_doc_to_data,
    load_ks,
    save_ks,
    tilt_accuracy,
)
from .masters import (
    condition_params,
    load_conditions,
    find_entry,
    formula_minmax,
    load_masters,
    missing_masters,
)
from . import fanuc
from .fanuc import FanucConfig, generate as generate_fanuc
from .firestore_sync import FirestoreSync, build_measurement_doc, overall_judgement
from . import controller_import
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
from . import prm_format
from . import fanuc_param
from . import fanuc_pcorr
from . import param_build
from . import controllers
from . import seiban_flow
from . import batch_build
from . import param_origin
from . import param_view
from . import xls_param

ISO230_MODE = "位置決め精度(ISO230)"
MODES = ("回転分割", "傾斜分割", "回転再現性", "傾斜再現性",
         "回転分割+再現", "傾斜分割+再現", ISO230_MODE)

# FANUCのコメントに書ける文字は英数字だけなので、モード名のローマ字表記を持つ
MODE_TAGS = {
    "回転分割": "KAITEN BUNKATSU",
    "傾斜分割": "KEISHA BUNKATSU",
    "回転再現性": "KAITEN SAIGEN",
    "傾斜再現性": "KEISHA SAIGEN",
    "回転分割+再現": "KAITEN BUNKATSU-SAIGEN",
    "傾斜分割+再現": "KEISHA BUNKATSU-SAIGEN",
    ISO230_MODE: "ISO230",
}

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

def _legend_label():
    """グラフの上に出す色つき凡例ラベル（グラフ内に置かずカーブに被らせない）。"""
    lab = QtWidgets.QLabel()
    lab.setTextFormat(QtCore.Qt.RichText)
    lab.setAlignment(QtCore.Qt.AlignCenter)
    lab.setStyleSheet("padding:0 6px;")
    return lab


def _plot_box(legend, plot):
    """[凡例ラベル(上) ＋ グラフ] を縦に積んだ小箱。"""
    box = QtWidgets.QWidget()
    bv = QtWidgets.QVBoxLayout(box)
    bv.setContentsMargins(0, 0, 0, 0)
    bv.setSpacing(0)
    bv.addWidget(legend, 0)
    bv.addWidget(plot, 1)
    return box


def _legend_html(pairs):
    """[(名前, 色)] → 色つき●つきの凡例HTML。"""
    return "　".join(
        f'<span style="color:{c};">●</span> {name}' for name, c in pairs)


def mark_adjacent_peak(plot, t, d, spec=None, with_label=True):
    """系列(t,d)の「隣接誤差が最大の点」にひし形マーク＋値ラベルを付ける。

    規格(spec)超えなら赤、規格内なら紫。グラフ上で「隣接が外れている場所はここ」
    を示す。戻り値: 追加したプロット要素のリスト（消すときに使う）。
    """
    import numpy as _np
    if spec is None:
        spec = ADJACENT_SPEC
    t = _np.asarray(t, dtype=float)
    d = _np.asarray(d, dtype=float)
    if len(d) < 3:
        return []
    peak = adjacent_peak(d)
    if peak is None:
        return []
    idx, val = peak
    x, y = float(t[idx]), float(d[idx])
    over = spec is not None and val > spec
    color = "#dc2626" if over else "#7c3aed"
    items = [plot.plot([x], [y], pen=None, symbol="d", symbolSize=15,
                       symbolBrush=color, symbolPen=pg.mkPen("k", width=1))]
    if with_label:
        txt = pg.TextItem(f'隣接 {val:.1f}"', color=color, anchor=(0.5, 1.4))
        txt.setPos(x, y)
        plot.addItem(txt)
        items.append(txt)
    return items


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
        self.e_font.setRange(5, 22)
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
        fit_to_screen(self, 680, 640)
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


def wrap_long_labels(widget, min_chars=30):
    """長い文章のラベルを折り返すようにする。

    折り返さないラベルは「1行に収まる幅」を最小幅として要求するので、
    文字サイズを上げるとダイアログごと横に広がって画面をはみ出す。
    見出しや項目名（短いもの）はそのままにしたいので、長いものだけ。
    """
    n = 0
    for lab in widget.findChildren(QtWidgets.QLabel):
        if not lab.wordWrap() and len(lab.text()) >= min_chars:
            lab.setWordWrap(True)
            n += 1
    return n


def wrap_scrollable(dialog, keep_bottom=0):
    """ダイアログの中身をスクロールに入れ、画面より小さくできるようにする。

    固定の中身をそのまま入れていると、文字サイズを上げたときに
    「ダイアログの最小サイズ」が画面を超え、下のボタンが押せなくなる。
    中身をスクロールに入れれば、画面が小さくても必ず収まる。

    keep_bottom … 下から数えていくつのレイアウト項目を（ボタン列として）
                  スクロールの外に残すか。0なら全部スクロールへ入れる。
    """
    old = dialog.layout()
    if old is None or old.property("scroll_wrapped"):
        return None
    items = [old.takeAt(0) for _ in range(old.count())]
    keep = items[len(items) - keep_bottom:] if keep_bottom else []
    inner_items = items[:len(items) - keep_bottom] if keep_bottom else items

    inner = QtWidgets.QWidget()
    iv = QtWidgets.QVBoxLayout(inner)
    iv.setContentsMargins(0, 0, 0, 0)
    for it in inner_items:
        if it.widget() is not None:
            iv.addWidget(it.widget())
        elif it.layout() is not None:
            iv.addLayout(it.layout())
        elif it.spacerItem() is not None:
            iv.addItem(it.spacerItem())

    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    scroll.setWidget(inner)
    # ここが肝。明示的に小さい最小を与えないと、中身の最小が
    # そのままダイアログの最小になり、画面より小さくできない。
    scroll.setMinimumSize(320, 200)
    old.addWidget(scroll, 1)
    for it in keep:
        if it.widget() is not None:
            old.addWidget(it.widget())
        elif it.layout() is not None:
            old.addLayout(it.layout())
    old.setProperty("scroll_wrapped", True)
    return scroll


def param_out_ext(settings):
    """機械へ渡すパラメータファイルの拡張子（既定 .DAT＝実機が出力する形）。"""
    return str((settings or {}).get("param_out_ext") or ".DAT")


def param_eob(settings):
    """パラメータ/プログラムのブロック区切り（既定は実機と同じ LF CR CR）。"""
    return (settings or {}).get("nc_eob") or fanuc.DEFAULT_EOB


def zero_params_for(settings, checkbox=None):
    """出荷ファイルで0にする番号（グリッドシフト・バックラッシ補正）。OFFなら None。

    機械個体の実測値なので前の機械の値を持ち込まない。
    checkbox を渡すと、その画面のチェックも見る。
    """
    settings = settings or {}
    if not bool(settings.get("zero_individual", True)):
        return None
    if checkbox is not None and not checkbox.isChecked():
        return None
    return tuple(settings.get("zero_individual_params",
                              param_build.ZERO_INDIVIDUAL_PARAMS))


def fit_to_screen(dialog, want_w, want_h, margin=60):
    """ダイアログを「その画面に収まる大きさ」で開く。

    固定の大きさで resize すると、小さいノートPCや拡大表示のときに
    画面からはみ出して下のボタンが押せなくなる。実際の作業画面の
    大きさに合わせて縮める（大きい画面ではそのままの大きさ）。
    """
    screen = dialog.screen() or QtWidgets.QApplication.primaryScreen()
    avail = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1280, 800)
    w = max(360, min(int(want_w), avail.width() - margin))
    h = max(280, min(int(want_h), avail.height() - margin))
    dialog.setMaximumSize(avail.width(), avail.height())
    dialog.resize(w, h)
    return w, h


class ProgramDialog(QtWidgets.QDialog):
    """FANUC測定プログラム生成ダイアログ（設定→プレビュー→保存）"""

    def __init__(self, parent, settings, params):
        super().__init__(parent)
        self.settings = settings
        self.params = params  # 測定条件（rotary, wheel_pitch, blocks 等）
        self.setWindowTitle("FANUC測定プログラム作成")
        root = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        # 幅が狭いときは項目名を入力欄の上へ折り返す（横並びで左が細くなるため）
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.ExpandingFieldsGrow)
        # 機械ごとに変わるので選べるようにする（打ち込みも可）
        self.e_axis = QtWidgets.QComboBox()
        self.e_axis.setEditable(True)
        self.e_axis.addItems(["Z", "A", "B", "C", "Y", "U", "V", "W"])
        self.e_axis.setCurrentText(str(settings.get("fanuc_axis", "Z")))
        self.e_axis.setMaximumWidth(80)
        self.e_axis.setToolTip(
            "割出軸のアドレス。機械ごとに違うので、実機のプログラムに合わせる。\n"
            "X はドゥエル G04 X… と同じ文字なので避ける")
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
            "クランプ分割（測定点でロックして読む）")
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
        self.c_sub = QtWidgets.QCheckBox("再現をサブプロにする")
        self.c_sub.setToolTip("外すと M98 を使わず1本に展開する")
        self.c_sub.setChecked(bool(settings.get("fanuc_use_subprogram", True)))
        self.c_reset = QtWidgets.QCheckBox("先頭にカウンターリセットを入れる")
        self.c_reset.setToolTip("バックラッシュ消し→M00で止まるので、そこでカウンターを0にする")
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
        self.e_sub.setValue(int(settings.get("fanuc_rep_sub_number", 1000)))
        self.e_sub.setToolTip(
            "再現サブプロのO番号。O8000〜O9999は保護領域（パラメータ3202 NE8/NE9）で、\n"
            "書込禁止だと転送そのものが弾かれるので 1000番台にしてある")

        # ブロックの区切り（EOB）。既定は実機が出力したファイルと同じ形式。
        self.cmb_eob = QtWidgets.QComboBox()
        for label, value in fanuc.EOB_STYLES:
            self.cmb_eob.addItem(label, value)
        ei = self.cmb_eob.findData(settings.get("nc_eob") or fanuc.DEFAULT_EOB)
        self.cmb_eob.setCurrentIndex(ei if ei >= 0 else 0)
        self.cmb_eob.setToolTip(
            "ファイルに書く改行の形式。画面の \";\" は文字ではなく改行そのものなので、\n"
            "ファイルには書き込まない（書くと制御装置が読めない）。\n"
            "既定は実機が出力したファイルと同じ LF CR CR。読めないときだけ変える")

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
        self.e_pre.setToolTip("測定点でバックラッシュを消すための行き過ぎ量")
        form.addRow("前振り量", self.e_pre)
        self.e_reset_sw.setToolTip("先頭のカウンター0設定で振る量（測定の前振りとは別）")
        form.addRow("リセット振り量", self.e_reset_sw)
        form.addRow("振りドゥエル", self.e_swing_dwell)
        form.addRow("測定ドゥエル", self.e_dwell)
        form.addRow("完了信号Mコード", self.e_mcode)
        form.addRow(self.c_clamp)
        clamp_m_row = QtWidgets.QHBoxLayout()
        clamp_m_row.addWidget(QtWidgets.QLabel("クランプ"))
        clamp_m_row.addWidget(self.e_clamp_m)
        clamp_m_row.addSpacing(12)
        clamp_m_row.addWidget(QtWidgets.QLabel("アンクランプ"))
        clamp_m_row.addWidget(self.e_unclamp_m)
        clamp_m_row.addStretch(1)
        form.addRow("クランプ信号", clamp_m_row)
        clamp_d_row = QtWidgets.QHBoxLayout()
        clamp_d_row.addWidget(QtWidgets.QLabel("クランプ後"))
        clamp_d_row.addWidget(self.e_clamp_dwell)
        clamp_d_row.addSpacing(12)
        clamp_d_row.addWidget(QtWidgets.QLabel("アンクランプ後"))
        clamp_d_row.addWidget(self.e_unclamp_dwell)
        clamp_d_row.addStretch(1)
        form.addRow("クランプ後ドゥエル", clamp_d_row)
        form.addRow("メインO番号", self.e_main)
        form.addRow("再現サブプロO番号", self.e_sub)
        form.addRow("改行(EOB)の形式", self.cmb_eob)
        form.addRow(self.c_reset)
        form.addRow(self.c_sub)
        form.addRow(self.c_return)
        form.addRow(self.c_div)
        form.addRow(self.c_rep)

        # --- 左：設定（縦に長いのでスクロール）／右：プレビュー の横並び ---
        # 縦一列だと画面の下にはみ出してボタンが押せなかったため（実機で指摘あり）
        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addLayout(form)
        lv.addWidget(self._build_send_group())
        lv.addStretch(1)
        self.left_scroll = QtWidgets.QScrollArea()
        self.left_scroll.setWidgetResizable(True)
        self.left_scroll.setWidget(left)
        self.left_scroll.setMinimumWidth(330)
        self.left_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        right = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.lbl_warn = QtWidgets.QLabel()
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setStyleSheet(
            "color:#b91c1c; background:#fef2f2; border:1px solid #fecaca;"
            "border-radius:6px; padding:4px 7px;")
        self.lbl_warn.hide()
        rv.addWidget(self.lbl_warn)
        self.preview = QtWidgets.QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setStyleSheet("font-family: monospace; font-size: 12px;")
        self.preview.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        rv.addWidget(self.preview, 1)

        self.split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.split.addWidget(self.left_scroll)
        self.split.addWidget(right)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([380, 660])
        root.addWidget(self.split, 1)

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
        root.addLayout(buttons)

        self._fit_to_screen()

        self.e_axis.currentTextChanged.connect(self.refresh)
        for w in (self.e_mcode, self.e_clamp_m, self.e_unclamp_m):
            w.textChanged.connect(self.refresh)
        for w in (self.e_pre, self.e_reset_sw, self.e_swing_dwell, self.e_dwell,
                  self.e_clamp_dwell, self.e_unclamp_dwell):
            w.valueChanged.connect(self.refresh)
        for w in (self.e_main, self.e_sub):
            w.valueChanged.connect(self.refresh)
        for w in (self.c_sub, self.c_reset, self.c_return, self.c_div, self.c_rep,
                  self.c_clamp):
            w.toggled.connect(self.refresh)
        self.cmb_eob.currentIndexChanged.connect(self._check)
        self.preview.textChanged.connect(self._check)
        self.refresh()

    def _fit_to_screen(self):
        """画面からはみ出さない大きさで開く（下のボタンが押せなくなるのを防ぐ）。"""
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1280, 800)
        w = max(760, min(1150, avail.width() - 80))
        h = max(420, min(760, avail.height() - 80))
        self.setMaximumHeight(avail.height())
        self.resize(w, h)
        self.split.setSizes([int(w * 0.37), w - int(w * 0.37)])

    def _check(self):
        """機械が読めない書き方が残っていないか点検して、赤帯で知らせる。"""
        problems = fanuc.validate(self.preview.toPlainText(), self._config())
        if problems:
            self.lbl_warn.setText("読取エラーになりそうな点:\n・" + "\n・".join(problems[:6]))
            self.lbl_warn.show()
        else:
            self.lbl_warn.hide()
        return problems

    def _eob(self):
        return self.cmb_eob.currentData() or fanuc.DEFAULT_EOB

    def _config(self):
        return FanucConfig(
            axis=self.e_axis.currentText().strip() or "Z",
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
            fanuc_axis=self.e_axis.currentText().strip() or "Z",
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
        # 機械が読める形（ISOコードの文字だけ・EOBは改行）にしてから書く。
        # 以前は errors="replace" で日本語が "?" になり、";" もそのまま書いていた
        # ため、制御装置が読み込めなかった。
        with open(path, "wb") as f:
            f.write(fanuc.nc_bytes(text, self._eob()))
        self._persist({"nc_eob": self._eob()})  # 次回も同じ設定で作れるよう記憶
        problems = self._check()
        note = ("\n\n※点検で気になる点があります:\n・" + "\n・".join(problems[:4])
                if problems else "")
        QtWidgets.QMessageBox.information(self, "保存", f"保存しました:\n{path}{note}")

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
            nc_eob=self._eob(),
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


def _log_param_creation(settings, **f):
    """パラメータ作成のたびに作成ログ(追記式)へ1行残す。失敗は無視（作成は成功扱い）。"""
    path = settings.get("param_log_csv", "")
    if not path:
        return
    p = Path(path)
    if not p.is_absolute():
        from .settings import app_dir
        p = app_dir() / p
    try:
        nc_param.append_log(p, {
            "日時": QtCore.QDateTime.currentDateTime().toString("yyyy/MM/dd HH:mm"),
            "型式": f.get("model", ""), "種別": f.get("kind", ""),
            "モード": f.get("mode", ""), "モーター": f.get("motor", ""),
            "制御": f.get("controller", ""), "軸": f.get("axis", ""),
            "Seiban": f.get("seiban", ""), "使用BASIC": f.get("basic", ""),
            "出力ファイル": f.get("out", ""), "反映": f.get("applied", ""),
            "件数": f.get("total", "")})
    except Exception:
        pass


class ParamLogDialog(QtWidgets.QDialog):
    """作成ログ（追記式の履歴）の閲覧。読み取り専用の一覧表示。"""

    def __init__(self, parent, log_path):
        super().__init__(parent)
        self.setWindowTitle("パラメータ作成ログ")
        fit_to_screen(self, 900, 520)
        v = QtWidgets.QVBoxLayout(self)
        rows = nc_param.read_log(log_path)
        if not rows:
            v.addWidget(QtWidgets.QLabel(f"ログがありません:\n{log_path}"))
        else:
            header, body = rows[0], rows[1:]
            body = list(reversed(body))      # 新しい順
            t = QtWidgets.QTableWidget(len(body), len(header))
            t.setHorizontalHeaderLabels(header)
            t.verticalHeader().setVisible(False)
            t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            for i, r in enumerate(body):
                for j in range(len(header)):
                    t.setItem(i, j, QtWidgets.QTableWidgetItem(r[j] if j < len(r) else ""))
            t.resizeColumnsToContents()
            t.horizontalHeader().setStretchLastSection(True)
            v.addWidget(t, 1)
            v.addWidget(QtWidgets.QLabel(f"{len(body)} 件　{log_path}"))
        b = QtWidgets.QPushButton("閉じる"); b.clicked.connect(self.accept)
        row = QtWidgets.QHBoxLayout(); row.addStretch(1); row.addWidget(b)
        v.addLayout(row)


class ParamPreviewDialog(QtWidgets.QDialog):
    """作成前プレビュー: BASIC の旧値 → これから書き込む新値 を一覧で確認する。

    入力ミス防止のため、書き込む前に「番号・旧値→新値」を見せて OK/中止を選ぶ。
    旧値と新値が違う行を強調する。
    """

    def __init__(self, parent, rows, subtitle=""):
        super().__init__(parent)
        self.setWindowTitle("作成前プレビュー（旧値→新値）")
        fit_to_screen(self, 560, 460)
        v = QtWidgets.QVBoxLayout(self)
        # rows は (番号, 旧, 新) か (番号, 軸, 旧, 新)。軸つき=2軸テーブルの両軸表示
        has_axis = bool(rows) and len(rows[0]) == 4
        norm = [(r[0], r[1], r[2], r[3]) if has_axis else (r[0], "", r[1], r[2])
                for r in rows]
        changed = sum(1 for (_n, _a, o, nw) in norm if o != nw)
        head = QtWidgets.QLabel(
            (subtitle + "\n" if subtitle else "")
            + f"全 {len(norm)} 件中 {changed} 件が BASIC と異なります。"
              "内容を確認して『作成』を押してください。")
        head.setWordWrap(True)
        v.addWidget(head)
        cols = ["番号", "軸", "旧値(BASIC)", "新値"] if has_axis else ["番号", "旧値(BASIC)", "新値"]
        t = QtWidgets.QTableWidget(len(norm), len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        t.horizontalHeader().setStretchLastSection(True)
        for i, (num, axis, old, new) in enumerate(norm):
            cells = (num, axis, old, new) if has_axis else (num, old, new)
            newcol = len(cells) - 1
            for j, text in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(text)
                if old != new and j == newcol:
                    it.setForeground(QtGui.QBrush(QtGui.QColor("#dc2626")))
                t.setItem(i, j, it)
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)
        v.addWidget(t, 1)
        bb = QtWidgets.QDialogButtonBox()
        ok = bb.addButton("作成", QtWidgets.QDialogButtonBox.AcceptRole)
        ok.setObjectName("primary")
        bb.addButton("中止", QtWidgets.QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


class ControllerEditDialog(QtWidgets.QDialog):
    """制御装置(号機)を手入力で登録/編集する。BASICが無い・新規購入の号機用。

    号機・CNC機種・電圧・各軸の容量を入れる。容量がある軸＝実装軸として扱う。
    アンプ型式は任意（空でOK。必要ならCSVで記入）。OKで Controller を返す。
    """

    CAPS = ["", "10A", "20A", "40A", "80A", "160A"]
    VOLT = ["", "AC200V", "AV400V"]
    YN = ["", "○", "×"]

    def __init__(self, parent, controller=None):
        super().__init__(parent)
        self.setWindowTitle("制御装置の登録／編集（手入力）")
        fit_to_screen(self, 420, 460)
        self.result_controller = None
        self.orig_unit = controller.unit if controller else ""
        v = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.e_unit = QtWidgets.QLineEdit(controller.unit if controller else "")
        self.e_unit.setPlaceholderText("号機（例 30）")
        form.addRow("号機", self.e_unit)
        self.e_cnc = QtWidgets.QLineEdit(controller.cnc if controller else "")
        self.e_cnc.setPlaceholderText("CNC機種（例 0i-MF）任意")
        form.addRow("CNCユニット", self.e_cnc)
        self.e_ver = QtWidgets.QLineEdit(controller.ver if controller else "")
        form.addRow("Ver（任意）", self.e_ver)
        self.cmb_volt = QtWidgets.QComboBox(); self.cmb_volt.addItems(self.VOLT)
        if controller:
            i = self.cmb_volt.findText(controller.voltage)
            self.cmb_volt.setCurrentIndex(i if i >= 0 else 0)
        self.cmb_volt.setToolTip("AC200V か AV400V。400V(HV)機なら AV400V")
        form.addRow("制御電圧", self.cmb_volt)
        self.e_servo = QtWidgets.QLineEdit(controller.servo if controller else "")
        form.addRow("SERVO版（任意）", self.e_servo)

        # 各軸の容量（容量を入れた軸＝その号機にある軸）
        self.cmb_caps = {}
        for a in controllers.AXES:
            cmb = QtWidgets.QComboBox(); cmb.addItems(self.CAPS)
            if controller and controller.caps.get(a):
                j = cmb.findText(controller.caps[a]); cmb.setCurrentIndex(j if j >= 0 else 0)
            self.cmb_caps[a] = cmb
            form.addRow(f"{a}軸 容量", cmb)
        v.addLayout(form)
        v.addWidget(QtWidgets.QLabel(
            "※ 容量を入れた軸だけ『その号機にある軸』として扱います。\n"
            "　 アンプ型式は空でOK（必要ならマスタCSVで記入）。"))

        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept); bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _accept(self):
        unit = self.e_unit.text().strip()
        if not unit:
            QtWidgets.QMessageBox.warning(self, "入力", "号機を入れてください")
            return
        caps = {a: self.cmb_caps[a].currentText() for a in controllers.AXES
                if self.cmb_caps[a].currentText()}
        if not caps:
            QtWidgets.QMessageBox.warning(self, "入力", "少なくとも1軸の容量を選んでください")
            return
        self.result_controller = controllers.Controller(
            unit, cnc=self.e_cnc.text().strip(), ver=self.e_ver.text().strip(),
            voltage=self.cmb_volt.currentText(), servo=self.e_servo.text().strip(),
            caps=caps)
        self.accept()


class BasicScanDialog(QtWidgets.QDialog):
    """BASICフォルダを読み、各号機の軸数・容量を自動抽出して制御装置マスタへ登録する。

    軸名はパラメータ1020、容量はパラメータ2165(アンプ最大電流AMR)から読む。電圧
    (200/400V)はBASICからは確実に出ないので空のまま（後でマスタで記入）。未登録の
    号機だけ追記し、既存行は変更しない（容量が違う号機は警告表示のみ）。
    """

    def __init__(self, parent, basic_dir, master_path):
        super().__init__(parent)
        self.setWindowTitle("BASICから制御装置を取り込む")
        fit_to_screen(self, 720, 520)
        self.master_path = master_path
        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(QtWidgets.QLabel(
            "BASICから各号機の軸数・容量を自動抽出しました（これは正確）。\n"
            "BASICに無い『電圧』だけ各行で選んでください（既定200V。400V(HV)機は400Vに）。\n"
            "未登録(新規)の号機にチェックを入れて「マスタへ登録」を押します。"))
        self._scanned = controllers.scan_basic_folder(basic_dir)
        existing = controllers.load_controllers(master_path)
        self._by_unit = {c.unit: c for c, _ in self._scanned}
        diff = controllers.diff_scanned_vs_master(self._scanned, existing)

        self.tbl = QtWidgets.QTableWidget(len(diff), 6)
        self.tbl.setHorizontalHeaderLabels(
            ["登録", "号機", "軸:容量(BASIC)", "電圧", "状態", "BASIC"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self._checks = {}
        self._volts = {}
        STATUS = {"new": "新規（未登録）", "same": "登録済み（一致）",
                  "diff": "登録済み（容量が違う！要確認）"}
        for r, d in enumerate(diff):
            chk = QtWidgets.QCheckBox()
            chk.setEnabled(d["status"] == "new")
            chk.setChecked(d["status"] == "new")
            self._checks[d["unit"]] = chk
            w = QtWidgets.QWidget(); hl = QtWidgets.QHBoxLayout(w)
            hl.setContentsMargins(0, 0, 0, 0); hl.addWidget(chk); hl.setAlignment(QtCore.Qt.AlignCenter)
            self.tbl.setCellWidget(r, 0, w)
            caps = d["caps"]
            if d["status"] == "diff":
                caps += f"  （マスタ: {d['master_caps']}）"
            self.tbl.setItem(r, 1, self._ro(d["unit"]))
            self.tbl.setItem(r, 2, self._ro(caps, d["status"] == "diff"))
            # 電圧コンボ（BASICから出ないのでここで選ぶ。新規行のみ操作可、既定200V）
            cmb = QtWidgets.QComboBox(); cmb.addItems(["AC200V", "AV400V"])
            cmb.setEnabled(d["status"] == "new")
            self._volts[d["unit"]] = cmb
            self.tbl.setCellWidget(r, 3, cmb)
            self.tbl.setItem(r, 4, self._ro(STATUS.get(d["status"], d["status"]),
                                            d["status"] == "diff"))
            self.tbl.setItem(r, 5, self._ro(d["file"]))
        self.tbl.resizeColumnsToContents()
        self.tbl.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.tbl, 1)

        n_new = sum(1 for d in diff if d["status"] == "new")
        self.lbl = QtWidgets.QLabel(
            f"スキャン {len(diff)} 号機　新規 {n_new}　"
            f"差異 {sum(1 for d in diff if d['status']=='diff')}")
        v.addWidget(self.lbl)

        bb = QtWidgets.QDialogButtonBox()
        ok = bb.addButton("マスタへ登録", QtWidgets.QDialogButtonBox.AcceptRole)
        ok.setObjectName("primary")
        bb.addButton("閉じる", QtWidgets.QDialogButtonBox.RejectRole)
        ok.clicked.connect(self._register)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._registered = False

    @staticmethod
    def _ro(text, warn=False):
        it = QtWidgets.QTableWidgetItem(text)
        it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEditable)
        if warn:
            it.setForeground(QtGui.QBrush(QtGui.QColor("#dc2626")))
        return it

    def _register(self):
        add = []
        for u, c in self._checks.items():
            if c.isChecked() and c.isEnabled() and u in self._by_unit:
                ctl = self._by_unit[u]
                ctl.voltage = self._volts[u].currentText()   # BASICに無い電圧をここで付与
                add.append(ctl)
        if not add:
            QtWidgets.QMessageBox.information(self, "登録", "登録する号機（新規）が選ばれていません。")
            return
        # 既存マスタを .bak へバックアップしてから追記
        try:
            p = Path(self.master_path)
            if p.exists():
                bak = p.with_suffix(p.suffix + ".bak")
                bak.write_bytes(p.read_bytes())
            n = controllers.append_units_to_master(self.master_path, add)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "登録に失敗", str(e))
            return
        QtWidgets.QMessageBox.information(
            self, "登録しました",
            f"{n} 号機をマスタへ登録しました（号機 {', '.join(c.unit for c in add)}）。\n"
            "軸数・容量はBASICから、電圧は選択値で登録しました。"
            "CNC機種名は空欄です（必要なら『編集』で記入）。")
        self._registered = True
        self.accept()


class ControllerMasterDialog(QtWidgets.QDialog):
    """制御装置マスタ（号機の一覧）の管理。手入力で追加/編集/削除、BASICから取り込み。

    マスタ＝最初に画像で渡された『制御装置一覧表』のデジタル版。ここに無い号機は
    かんたん作成の候補に出ないので、無い号機はここで登録する。
    """

    COLS = ["号機", "CNC", "電圧", "容量", "軸数"]

    def __init__(self, parent, master_path, basic_dir="", settings=None):
        super().__init__(parent)
        self.setWindowTitle("制御装置マスタ（登録・編集）")
        fit_to_screen(self, 640, 520)
        self.master_path = master_path
        self.basic_dir = basic_dir
        self.settings = settings if settings is not None else {}
        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(QtWidgets.QLabel(
            "登録済みの制御装置(号機)一覧です。無い号機は「手入力で追加」または\n"
            "「BASICから取り込む」で登録してください（かんたん作成の候補になります）。"))
        self.tbl = QtWidgets.QTableWidget(0, len(self.COLS))
        self.tbl.setHorizontalHeaderLabels(self.COLS)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.doubleClicked.connect(self.edit)
        v.addWidget(self.tbl, 1)
        self.lbl = QtWidgets.QLabel("")
        v.addWidget(self.lbl)

        row = QtWidgets.QHBoxLayout()
        for label, slot, tip in (
            ("手入力で追加…", self.add, "号機・電圧・各軸容量を入れて登録"),
            ("編集…", self.edit, "選択した号機を編集"),
            ("削除", self.delete, "選択した号機を削除"),
            ("BASICから取り込む…", self.import_basic, "BASICフォルダから軸数・容量を自動登録"),
            ("Webから取り込む…", self.import_web,
             "product-inspection（Webアプリ/Firestore）の制御装置データを取り込む"),
        ):
            b = QtWidgets.QPushButton(label); b.setToolTip(tip); b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        b_close = QtWidgets.QPushButton("閉じる"); b_close.clicked.connect(self.accept)
        row.addWidget(b_close)
        v.addLayout(row)
        self.reload()

    def reload(self):
        self._ctls = controllers.load_controllers(self.master_path)
        self.tbl.setRowCount(len(self._ctls))
        for r, c in enumerate(self._ctls):
            vals = [c.unit, c.cnc, c.voltage, c.caps_text(), str(len(c.axes()))]
            for col, t in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(t)
                it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEditable)
                self.tbl.setItem(r, col, it)
        self.tbl.resizeColumnsToContents()
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.lbl.setText(f"{len(self._ctls)} 号機　{self.master_path}")

    def _selected(self):
        r = self.tbl.currentRow()
        return self._ctls[r] if 0 <= r < len(self._ctls) else None

    def _backup(self):
        p = Path(self.master_path)
        if p.exists():
            try:
                p.with_suffix(p.suffix + ".bak").write_bytes(p.read_bytes())
            except Exception:
                pass

    def add(self):
        dlg = ControllerEditDialog(self)
        if dlg.exec() and dlg.result_controller:
            ctl = dlg.result_controller
            if any(c.unit == ctl.unit for c in self._ctls):
                if QtWidgets.QMessageBox.question(
                        self, "確認", f"号機 {ctl.unit} は既にあります。上書きしますか？"
                ) != QtWidgets.QMessageBox.Yes:
                    return
            self._backup()
            controllers.upsert_controller(self.master_path, ctl)
            self.reload()

    def edit(self):
        sel = self._selected()
        if not sel:
            QtWidgets.QMessageBox.warning(self, "編集", "号機を選んでください")
            return
        dlg = ControllerEditDialog(self, controller=sel)
        if dlg.exec() and dlg.result_controller:
            ctl = dlg.result_controller
            self._backup()
            # 号機番号を変えた場合は旧番号を削除してから登録
            if dlg.orig_unit and dlg.orig_unit != ctl.unit:
                controllers.delete_controller(self.master_path, dlg.orig_unit)
            controllers.upsert_controller(self.master_path, ctl)
            self.reload()

    def delete(self):
        sel = self._selected()
        if not sel:
            QtWidgets.QMessageBox.warning(self, "削除", "号機を選んでください")
            return
        if QtWidgets.QMessageBox.question(
                self, "削除の確認", f"号機 {sel.unit} を削除しますか？"
        ) != QtWidgets.QMessageBox.Yes:
            return
        self._backup()
        controllers.delete_controller(self.master_path, sel.unit)
        self.reload()

    def import_basic(self):
        if not self.basic_dir or not Path(self.basic_dir).is_dir():
            QtWidgets.QMessageBox.warning(
                self, "BASIC取り込み", "BASICの場所が未設定です（詳細設定で指定）。")
            return
        BasicScanDialog(self, self.basic_dir, self.master_path).exec()
        self.reload()

    def import_web(self):
        WebControllerImportDialog(self, self.settings, self.master_path,
                                  backup=self._backup).exec()
        self.reload()


class WebControllerImportDialog(QtWidgets.QDialog):
    """product-inspection（Firestore）の制御装置データを制御装置マスタへ取り込む。

    データベースの公開データ配下からコレクションを選び（どれに制御装置情報が入って
    いるか画面で選べる）、ドキュメントを読み、フィールド名を賢く突き合わせて号機・
    容量・アンプ・電圧・CNC に写す。写せなかったフィールド名も出すので、実データを
    見ながら別名を足せる（＝コードを見なくても構造が分かる）。通信は Firestore REST。
    """

    def __init__(self, parent, settings, master_path, backup=None):
        super().__init__(parent)
        self.settings = settings or {}
        self.master_path = master_path
        self._backup = backup
        self._ctls = []
        self.setWindowTitle("Webから制御装置を取り込む（product-inspection）")
        fit_to_screen(self, 760, 620)
        v = QtWidgets.QVBoxLayout(self)

        self.sync = FirestoreSync(
            api_key=str(self.settings.get("webapp_api_key") or ""),
            project_id=str(self.settings.get("webapp_project_id") or ""),
            app_data_id=str(self.settings.get("webapp_data_id") or ""),
        )
        if not self.sync.configured():
            v.addWidget(QtWidgets.QLabel(
                "Web連携が未設定です。設定（webapp_api_key / webapp_project_id /\n"
                "webapp_data_id）を入れてから使ってください。"))
            b = QtWidgets.QPushButton("閉じる"); b.clicked.connect(self.reject)
            v.addWidget(b)
            return

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("コレクション"))
        self.cmb = QtWidgets.QComboBox(); self.cmb.setMinimumWidth(240)
        self.cmb.setEditable(True)   # 一覧に出なくても手入力で指定できる
        top.addWidget(self.cmb, 1)
        b_list = QtWidgets.QPushButton("一覧を取得")
        b_list.clicked.connect(self.fetch_collections)
        b_load = QtWidgets.QPushButton("読み込み")
        b_load.clicked.connect(self.load_docs)
        top.addWidget(b_list); top.addWidget(b_load)
        v.addLayout(top)

        self.tbl = QtWidgets.QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(["号機", "CNC", "電圧", "容量", "軸数"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.tbl, 2)

        self.note = QtWidgets.QLabel(""); self.note.setWordWrap(True)
        v.addWidget(self.note)
        self.raw = QtWidgets.QPlainTextEdit(); self.raw.setReadOnly(True)
        self.raw.setMaximumHeight(120)
        self.raw.setStyleSheet("font-family: monospace; font-size:8pt;")
        v.addWidget(self.raw)

        row = QtWidgets.QHBoxLayout()
        self.b_import = QtWidgets.QPushButton("制御装置マスタへ取り込み")
        self.b_import.setEnabled(False)
        self.b_import.clicked.connect(self.do_import)
        b_close = QtWidgets.QPushButton("閉じる"); b_close.clicked.connect(self.accept)
        row.addStretch(1); row.addWidget(self.b_import); row.addWidget(b_close)
        v.addLayout(row)

        # 前回のコレクション名を復元（制御装置用に別キーで覚える）
        last = str(self.settings.get("webapp_controllers_collection") or "")
        if last:
            self.cmb.setEditText(last)
        QtCore.QTimer.singleShot(0, self.fetch_collections)

    def _busy(self, on):
        if on:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        else:
            QtWidgets.QApplication.restoreOverrideCursor()

    def fetch_collections(self):
        self._busy(True)
        try:
            ids = self.sync.list_collection_ids()
        except Exception as e:
            self._busy(False)
            self.note.setText(f"コレクション一覧の取得に失敗: {e}")
            return
        self._busy(False)
        cur = self.cmb.currentText()
        self.cmb.clear()
        self.cmb.addItems(ids)
        if cur:
            self.cmb.setEditText(cur)
        # 制御装置っぽい名前があれば選んでおく
        for i, name in enumerate(ids):
            if any(k in name.lower() for k in ("control", "seigyo", "machine", "unit",
                                               "制御", "号機")):
                self.cmb.setCurrentIndex(i); break
        self.note.setText(f"コレクション {len(ids)} 件。制御装置が入っているものを選んで「読み込み」。")

    def load_docs(self):
        col = self.cmb.currentText().strip()
        if not col:
            self.note.setText("コレクション名を選んでください")
            return
        self._busy(True)
        try:
            docs = self.sync.list_documents(col)
        except Exception as e:
            self._busy(False)
            self.note.setText(f"読み込みに失敗: {e}")
            return
        self._busy(False)
        self._ctls, unmapped = controller_import.map_controllers(docs)
        self.tbl.setRowCount(len(self._ctls))
        for r, c in enumerate(self._ctls):
            vals = [c.unit, c.cnc, c.voltage, c.caps_text(), str(len(c.axes()))]
            for col_i, t in enumerate(vals):
                self.tbl.setItem(r, col_i, QtWidgets.QTableWidgetItem(t))
        self.tbl.resizeColumnsToContents()
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.b_import.setEnabled(bool(self._ctls))
        msg = f"{len(docs)} 件読み込み → {len(self._ctls)} 号機に変換。"
        if unmapped:
            msg += "　未対応フィールド（マスタに写せなかった項目）: " + "、".join(unmapped[:20])
        self.note.setText(msg)
        # 先頭ドキュメントの生フィールドを見せる（構造確認用）
        if docs:
            import json as _json
            self.raw.setPlainText("先頭ドキュメントの中身:\n" +
                                  _json.dumps(docs[0][1], ensure_ascii=False, indent=1))
        # 選んだコレクション名を覚える
        self.settings["webapp_controllers_collection"] = col
        try:
            save_settings(self.settings)
        except Exception:
            pass

    def do_import(self):
        if not self._ctls:
            return
        skipped = [c for c in self._ctls if not c.axes()]
        target = [c for c in self._ctls if c.axes()]
        if not target:
            QtWidgets.QMessageBox.warning(
                self, "取り込み",
                "軸容量が読めた号機がありません。フィールド名の対応が合っていない可能性が"
                "あります（下の『未対応フィールド』を教えてください＝別名を足して確実に"
                "合わせます）。")
            return
        if QtWidgets.QMessageBox.question(
                self, "取り込みの確認",
                f"{len(target)} 号機を制御装置マスタへ取り込みます"
                f"（同じ号機は上書き）。よろしいですか？"
                + (f"\n※軸容量が空の {len(skipped)} 件は取り込みません。" if skipped else "")
        ) != QtWidgets.QMessageBox.Yes:
            return
        if self._backup:
            self._backup()
        added = updated = 0
        for c in target:
            try:
                result = controllers.upsert_controller(self.master_path, c)
                if result == "added":
                    added += 1
                else:
                    updated += 1
            except Exception:
                pass
        self.note.setText(f"取り込み完了: 追加 {added} 件 / 更新 {updated} 件")
        QtWidgets.QMessageBox.information(
            self, "取り込み完了", f"追加 {added} 件 / 更新 {updated} 件")


class ParamViewerDialog(QtWidgets.QDialog):
    """パラメータ閲覧・比較。番地ごとの値を一覧（スクロール）、2ファイルの差分を強調、
    番号範囲(例 1000〜3000)や「差分のみ」で絞り込む。FANUC/ヘッダ+CSV両対応。
    """

    def __init__(self, parent, settings, file_a="", file_b=""):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("パラメータを見る／比較")
        fit_to_screen(self, 900, 640)
        v = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self.e_a = QtWidgets.QLineEdit(file_a)
        self.e_a.setPlaceholderText("ファイルA（BASIC / 製品データ / 作成した.prm）")
        form.addRow("ファイルA", self._browse_row(self.e_a))
        self.e_b = QtWidgets.QLineEdit(file_b)
        self.e_b.setPlaceholderText("ファイルB（比較する場合だけ。空＝Aだけ表示）")
        form.addRow("ファイルB（任意）", self._browse_row(self.e_b))
        v.addLayout(form)

        # 絞り込み行
        filt = QtWidgets.QHBoxLayout()
        filt.addWidget(QtWidgets.QLabel("番号"))
        self.e_lo = QtWidgets.QLineEdit(); self.e_lo.setPlaceholderText("例 1000")
        self.e_lo.setFixedWidth(80)
        self.e_hi = QtWidgets.QLineEdit(); self.e_hi.setPlaceholderText("例 3000")
        self.e_hi.setFixedWidth(80)
        filt.addWidget(self.e_lo); filt.addWidget(QtWidgets.QLabel("〜")); filt.addWidget(self.e_hi)
        for label, lo, hi in (("全部", "", ""), ("1000〜3000", "1000", "3000"),
                              ("2000番台", "2000", "2999")):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(lambda _=False, a=lo, z=hi: self._set_range(a, z))
            filt.addWidget(b)
        self.chk_diff = QtWidgets.QCheckBox("差分のみ")
        self.chk_diff.setToolTip("AとBで値が違う番地だけ表示（Bを指定したとき）")
        self.chk_diff.stateChanged.connect(self.refresh)
        filt.addWidget(self.chk_diff)
        b_show = QtWidgets.QPushButton("表示更新")
        b_show.setObjectName("primary"); b_show.clicked.connect(self.refresh)
        filt.addWidget(b_show)
        filt.addStretch(1)
        v.addLayout(filt)
        self.e_lo.returnPressed.connect(self.refresh)
        self.e_hi.returnPressed.connect(self.refresh)
        self.e_a.editingFinished.connect(self.refresh)
        self.e_b.editingFinished.connect(self.refresh)

        self.tbl = QtWidgets.QTableWidget(0, 5)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.tbl, 1)
        self.lbl = QtWidgets.QLabel("")
        v.addWidget(self.lbl)
        b_close = QtWidgets.QPushButton("閉じる"); b_close.clicked.connect(self.accept)
        rr = QtWidgets.QHBoxLayout(); rr.addStretch(1); rr.addWidget(b_close)
        v.addLayout(rr)
        self.refresh()

    def _browse_row(self, line):
        w = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0); h.addWidget(line, 1)
        b = QtWidgets.QPushButton("参照...")
        b.clicked.connect(lambda: self._browse(line))
        h.addWidget(b)
        return w

    def _browse(self, line):
        start = line.text() or self.settings.get("param_basic_dir", "") \
            or self.settings.get("param_product_dir", "")
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "パラメータファイル", start,
            "パラメータ (*.prm *.PRM *.txt *.dat *.DAT);;すべて (*.*)")
        if p:
            line.setText(p); self.refresh()

    def _set_range(self, lo, hi):
        self.e_lo.setText(lo); self.e_hi.setText(hi); self.refresh()

    @staticmethod
    def _read(path):
        from pathlib import Path
        if not path or not Path(path).is_file():
            return None
        try:
            return Path(path).read_bytes().decode("cp932", errors="replace")
        except Exception:
            return None

    def refresh(self, *_):
        lo = int(self.e_lo.text()) if self.e_lo.text().strip().isdigit() else None
        hi = int(self.e_hi.text()) if self.e_hi.text().strip().isdigit() else None
        a_text = self._read(self.e_a.text().strip())
        b_text = self._read(self.e_b.text().strip())
        compare = bool(self.e_b.text().strip())
        self.chk_diff.setEnabled(compare)
        if a_text is None and not compare:
            self.tbl.setRowCount(0); self.lbl.setText("ファイルAを指定してください。")
            return
        if compare:
            rows = param_view.compare(a_text or "", b_text or "")
            rows = param_view.filter_rows(rows, lo, hi, self.chk_diff.isChecked())
            cols = ["番号", "軸", "A の値", "B の値", "説明"]
        else:
            rows = param_view.filter_rows(param_view.read_rows(a_text), lo, hi)
            cols = ["番号", "軸", "値", "説明"]
        self.tbl.setColumnCount(len(cols))
        self.tbl.setHorizontalHeaderLabels(cols)
        self.tbl.setRowCount(len(rows))
        ndiff = 0
        for r, row in enumerate(rows):
            if compare:
                differ = row.get("differ")
                ndiff += 1 if differ else 0
                cells = [row["num"], row["label"],
                         "" if row["a"] is None else row["a"],
                         "" if row["b"] is None else row["b"], row["desc"]]
            else:
                cells = [row["num"], row["label"], row["value"], row["desc"]]
                differ = False
            for c, t in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(str(t))
                it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEditable)
                if differ:
                    it.setBackground(QtGui.QBrush(QtGui.QColor("#fef3c7")))
                    if compare and c in (2, 3):
                        it.setForeground(QtGui.QBrush(QtGui.QColor("#dc2626")))
                self.tbl.setItem(r, c, it)
        self.tbl.resizeColumnsToContents()
        self.tbl.horizontalHeader().setStretchLastSection(True)
        rng = (f"  番号 {lo if lo is not None else '先頭'}〜{hi if hi is not None else '末尾'}"
               if (lo is not None or hi is not None) else "")
        self.lbl.setText((f"{len(rows)} 行" + rng
                          + (f"　差分 {ndiff} 件" if compare else "")))


class ParamWizardDialog(QtWidgets.QDialog):
    """受注番号から かんたん作成（ガイド付き）。

    ① 受注伝票番号(Seiban)を入れて「探す」→ 傾斜(T)/回転(R)の製品データを自動で探す。
    ② 作るもの（両方／必要な方）を選ぶ → 製品データから必要容量を読み、作れる制御装置
       （号機）を絞って一覧に出す。1つ選ぶと使う軸も自動で割り当てる。
    ③ 出力先を確認して「作成」。2軸テーブルなら両軸を1ファイル(TR<Seiban>.prm)にする。
    むずかしい設定（フォルダ・CSV・手動指定）は「詳細設定…」の従来画面で行う。
    """

    def __init__(self, parent, settings, *, model=""):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("受注番号から かんたん作成")
        fit_to_screen(self, 1060, 600)    # 縦長を避け、横長(2カラム)で見やすく
        self._files = []          # 見つかった製品ファイル [{kind,prefix,path,name,meta,chk}]
        mpath = settings.get("controller_master_csv", "")
        if mpath and not Path(mpath).is_absolute():
            mpath = str(app_dir() / mpath)
        self._mpath = mpath
        self._controller_master_path = mpath
        self._controllers = controllers.load_controllers(mpath)
        # モーター→容量 対応表（製品データに Servo Amp Model が無いとき容量を引く）
        cpath = settings.get("motor_capacity_csv", "")
        if cpath and not Path(cpath).is_absolute():
            cpath = str(app_dir() / cpath)
        self._motor_caps = seiban_flow.load_motor_caps(cpath)
        # 特注パラ索引（型式で引く。再索引で更新。毎回フォルダを解析しないため）
        ipath = settings.get("param_custom_index_csv", "")
        if ipath and not Path(ipath).is_absolute():
            ipath = str(app_dir() / ipath)
        self._cindex_path = ipath
        self._custom_index = xls_param.read_index(ipath)
        # パラメータDB（実機ダンプ由来などの登録済み変更点）。型式/受注番号で候補に混ぜる
        dbpath = settings.get("param_change_csv", "")
        if dbpath and not Path(dbpath).is_absolute():
            dbpath = str(app_dir() / dbpath)
        self._db_path = dbpath
        self._db_entries = nc_param.load_entries(dbpath)
        self._cand = []           # 候補 [(controller, [軸文字,...])]

        v = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("受注番号から かんたん作成")
        title.setStyleSheet("font-size:16px; font-weight:bold;")
        v.addWidget(title)
        flow = QtWidgets.QLabel("① 受注番号で探す → ② 号機を選ぶ → ③ 作成。"
                                "（特注/DD・登録(DB)は『型式で探す』でも出ます）")
        flow.setStyleSheet("color:#475569;")
        v.addWidget(flow)
        # 「次にやること」を常に表示（初心者でも迷わないライブガイド）
        self.lbl_guide = QtWidgets.QLabel("")
        self.lbl_guide.setWordWrap(True)
        v.addWidget(self.lbl_guide)

        # --- 場所（最初に一度だけ。設定済みなら折りたためる） ---
        box0 = QtWidgets.QGroupBox("場所（共有サーバ・最初に一度だけ／クリックで開閉）")
        box0.setCheckable(True)
        b0v = QtWidgets.QVBoxLayout(box0)
        inner0 = QtWidgets.QWidget(); b0v.addWidget(inner0)
        box0.toggled.connect(inner0.setVisible)
        f0 = QtWidgets.QFormLayout(inner0)
        self.e_pdir = QtWidgets.QLineEdit(str(settings.get("param_product_dir", "")))
        self.e_pdir.setPlaceholderText(r"製品データ(R/T〇〇.prm)を置くフォルダ")
        self.e_pdir.editingFinished.connect(self._save_dirs)
        f0.addRow("製品データの場所", self._dir_row(self.e_pdir, "param_product_dir"))
        self.e_bdir = QtWidgets.QLineEdit(str(settings.get("param_basic_dir", "")))
        self.e_bdir.setPlaceholderText(r"BASIC(F〇〇BASIC)を置くフォルダ")
        self.e_bdir.editingFinished.connect(self._save_dirs)
        f0.addRow("BASICの場所", self._dir_row(self.e_bdir, "param_basic_dir"))
        crow = QtWidgets.QWidget(); ch = QtWidgets.QHBoxLayout(crow)
        ch.setContentsMargins(0, 0, 0, 0)
        self.e_cdir = QtWidgets.QLineEdit(str(settings.get("param_custom_dir", "")))
        self.e_cdir.setPlaceholderText(r"特注パラ(Excel)の場所＝MKPRMで作れない/DD等（任意）")
        self.e_cdir.editingFinished.connect(self._save_dirs)
        ch.addWidget(self.e_cdir, 1)
        b_cb = QtWidgets.QPushButton("参照..."); b_cb.clicked.connect(
            lambda: self._browse_dir(self.e_cdir, "param_custom_dir"))
        ch.addWidget(b_cb)
        b_reidx = QtWidgets.QPushButton("索引を更新")
        b_reidx.setToolTip("特注パラ(Excel)を全件読み込んで索引に登録。フォルダ更新時に押す（少し時間がかかる）")
        b_reidx.clicked.connect(self._reindex_custom)
        ch.addWidget(b_reidx)
        f0.addRow("特注パラの場所", crow)
        self.lbl_cidx = QtWidgets.QLabel("")
        self.lbl_cidx.setStyleSheet("color:#6b7280; font-size:11px;")
        f0.addRow("", self.lbl_cidx)
        # 製品データ・BASIC が両方設定済みなら畳んでおく（日常は①②③に集中）
        _set = bool(self.e_pdir.text().strip()) and bool(self.e_bdir.text().strip())
        box0.setChecked(not _set)
        inner0.setVisible(not _set)
        v.addWidget(box0)

        # 横長レイアウト: 場所(上・全幅) の下を 左(検索＋結果) / 右(制御装置＋出力) に分割
        mid = QtWidgets.QHBoxLayout()
        col_left = QtWidgets.QVBoxLayout()
        col_right = QtWidgets.QVBoxLayout()
        mid.addLayout(col_left, 3)
        mid.addLayout(col_right, 2)
        v.addLayout(mid, 1)

        # --- ① 受注伝票番号 ---
        box1 = QtWidgets.QGroupBox("① 受注伝票番号(Seiban) を入れて「探す」")
        f1 = QtWidgets.QVBoxLayout(box1)
        srow = QtWidgets.QHBoxLayout()
        self.e_seiban = QtWidgets.QLineEdit()
        self.e_seiban.setPlaceholderText("受注伝票番号（例 50013078）")
        self.e_seiban.returnPressed.connect(self.search)
        b_search = QtWidgets.QPushButton("探す")
        b_search.setObjectName("primary"); b_search.clicked.connect(self.search)
        srow.addWidget(QtWidgets.QLabel("Seiban")); srow.addWidget(self.e_seiban, 1)
        srow.addWidget(b_search)
        f1.addLayout(srow)
        # 型式でも探せる（特注パラ＝製番が無いので型式で引く）
        mrow = QtWidgets.QHBoxLayout()
        self.e_model = QtWidgets.QLineEdit()
        self.e_model.setPlaceholderText("型式で特注パラを探す（例 RTT-135 / MZF-50010）")
        self.e_model.returnPressed.connect(self.search_by_model)
        b_msearch = QtWidgets.QPushButton("型式で探す")
        b_msearch.clicked.connect(self.search_by_model)
        mrow.addWidget(QtWidgets.QLabel("型式")); mrow.addWidget(self.e_model, 1)
        mrow.addWidget(b_msearch)
        f1.addLayout(mrow)
        self.found_box = QtWidgets.QWidget()
        self.found_lay = QtWidgets.QVBoxLayout(self.found_box)
        self.found_lay.setContentsMargins(0, 0, 0, 0)
        self.lbl_found = QtWidgets.QLabel("製品データの場所から、傾斜(T)・回転(R)を探します。")
        self.lbl_found.setStyleSheet("color:#6b7280;")
        self.found_lay.addWidget(self.lbl_found)
        # 結果一覧は件数が多いとき用にスクロール（左カラムが縦に伸びすぎないように）
        self.found_scroll = QtWidgets.QScrollArea()
        self.found_scroll.setWidgetResizable(True)
        self.found_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.found_scroll.setWidget(self.found_box)
        f1.addWidget(self.found_scroll, 1)
        col_left.addWidget(box1, 1)

        # --- ② 制御装置 ---
        box2 = QtWidgets.QGroupBox("② 作れる制御装置（号機）を選ぶ")
        f2 = QtWidgets.QFormLayout(box2)
        self.cmb_cap = QtWidgets.QComboBox()
        self.cmb_cap.addItem("（自動：製品データから判定）", "")
        for c in controllers.all_capacities(self._controllers):
            self.cmb_cap.addItem(c, c)
        self.cmb_cap.setToolTip("製品データから容量を自動判定します。判定できないときだけ手で選んでください")
        self.cmb_cap.currentIndexChanged.connect(self._update_candidates)
        f2.addRow("必要容量", self.cmb_cap)
        self.lbl_need = QtWidgets.QLabel("")
        self.lbl_need.setWordWrap(True)
        self.lbl_need.setStyleSheet("color:#1d4ed8;")
        f2.addRow("選択 → 必要軸", self.lbl_need)
        self.cmb_ctrl = QtWidgets.QComboBox()
        self.cmb_ctrl.setToolTip("製品の必要容量を満たす制御装置だけ出します。選ぶと使う軸を自動割当。"
                                 "傾斜＋回転を両方選ぶと2軸とも載る号機、片方なら1軸でよい号機が出ます")
        self.cmb_ctrl.currentIndexChanged.connect(self._on_ctrl_changed)
        f2.addRow("制御装置", self.cmb_ctrl)
        self.lbl_assign = QtWidgets.QLabel("")
        self.lbl_assign.setWordWrap(True)
        self.lbl_assign.setStyleSheet("color:#374151;")
        f2.addRow("軸の割当", self.lbl_assign)
        b_scan = QtWidgets.QPushButton("制御装置マスタ（登録/編集）…")
        b_scan.setToolTip("号機の一覧を管理。手入力で追加・編集・削除、BASICから自動取り込みもできる")
        b_scan.clicked.connect(self._scan_basics)
        f2.addRow("", b_scan)
        col_right.addWidget(box2)

        # --- ③ 出力 ---
        box3 = QtWidgets.QGroupBox("③ 出力先を確認して「作成」")
        f3 = QtWidgets.QFormLayout(box3)
        self.e_basic = QtWidgets.QLineEdit()
        self.e_basic.setPlaceholderText("使うBASIC（制御装置を選ぶと自動で入ります）")
        f3.addRow("使うBASIC", self._with_browse(self.e_basic, self._browse_basic))
        self.e_out = QtWidgets.QLineEdit(str(settings.get("param_out_folder", "")
                                            or settings.get("nc_send_folder", "")))
        self.e_out.setPlaceholderText(r"出力先（カード E:\ や LAN共有 \\192.168.0.10\nc）")
        f3.addRow("出力先", self._with_browse(self.e_out, self._browse_out))
        # 作成時オプション（チェック→作成前プレビューで旧→新を確認できる）
        zp = settings.get("motor_zero_param", "2000")
        zb = settings.get("motor_zero_bit", 1)
        self.chk_zero = QtWidgets.QCheckBox(
            f"DGPR（{zp} #{zb}）を0にする（モーター番号変更時のサーボ再初期化）")
        self.chk_zero.setToolTip(
            f"チェックすると作成する軸の {zp} #{zb}(DGPR) を 0 にします（他ビットは保持）。"
            "FANUC: DGPR=0 → 電源再投入で自動的に1へ。モーターを変えたときに使用")
        f3.addRow("オプション", self.chk_zero)
        op, ob = settings.get("origin_param", "1815"), settings.get("origin_bit", 5)
        self.cmb_origin = QtWidgets.QComboBox()
        self.cmb_origin.addItem("変更しない", "")
        self.cmb_origin.addItem(f"原点を確立する（{op} #{ob} = ON）", "on")
        self.cmb_origin.addItem(f"原点を確立しない（{op} #{ob} = OFF）", "off")
        self.cmb_origin.setToolTip("レファレンス点復帰/原点確立のビットをON/OFFします。"
                                   "既定は1815 #5(APZ)。違うビットなら設定で変更可")
        f3.addRow("原点確立", self.cmb_origin)
        zp = "・".join(str(x) for x in settings.get(
            "zero_individual_params", ["1850", "1851", "1852"]))
        self.chk_zero_indiv = QtWidgets.QCheckBox(
            f"グリッドシフト・バックラッシ補正（{zp}）を0にする")
        self.chk_zero_indiv.setChecked(bool(settings.get("zero_individual", True)))
        self.chk_zero_indiv.setToolTip(
            "機械個体の実測値なので、出荷するファイルには前の機械の値を残さない。\n"
            "作る軸だけでなく全軸を0にする（他軸に値が残ったまま出荷されるのを防ぐ）。\n"
            "0にする箇所は作成前プレビューに出る")
        f3.addRow("", self.chk_zero_indiv)
        col_right.addWidget(box3)
        col_right.addStretch(1)             # 右カラムは上詰め

        self.lbl_basic_dir = QtWidgets.QLabel("")
        self.lbl_basic_dir.setStyleSheet("color:#6b7280; font-size:11px;")
        v.addWidget(self.lbl_basic_dir)

        row = QtWidgets.QHBoxLayout()
        b_adv = QtWidgets.QPushButton("詳細設定（フォルダ/CSV/手動）…")
        b_adv.setToolTip("BASICや製品データの場所、変更表CSV、軸の手動指定などの従来画面を開く")
        b_adv.clicked.connect(self._open_advanced)
        b_view = QtWidgets.QPushButton("パラメータを見る/比較…")
        b_view.setToolTip("BASIC・製品・作成した.prm の中身を番地ごとに一覧。2つ選んで差分比較も")
        b_view.clicked.connect(self._open_viewer)
        b_make = QtWidgets.QPushButton("作成 → 出力先")
        b_make.setObjectName("primary"); b_make.clicked.connect(self.create)
        b_close = QtWidgets.QPushButton("閉じる"); b_close.clicked.connect(self.accept)
        row.addWidget(b_adv); row.addWidget(b_view)
        row.addStretch(1); row.addWidget(b_make); row.addWidget(b_close)
        v.addLayout(row)
        # 文字サイズを上げても画面に収まるよう、中身をスクロールへ入れる
        # （ボタン列だけ外に残して常に押せるようにする）
        wrap_long_labels(self)
        wrap_scrollable(self, keep_bottom=1)

        if model:
            self.e_model.setText(model)   # 本体の型式を「型式で探す」に初期表示
        for w in (self.e_seiban, self.e_basic, self.e_out):
            w.textChanged.connect(self._update_guide)
        self.e_seiban.setFocus()
        self._refresh_basic_dir_note()
        self._refresh_cidx_note()
        self._update_guide()

    # ----- 補助 -----
    def _with_browse(self, line, slot):
        w = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(line, 1)
        b = QtWidgets.QPushButton("参照..."); b.clicked.connect(slot)
        h.addWidget(b)
        return w

    def _dir_row(self, line, key):
        """フォルダ入力＋参照ボタン。参照で選んだら即保存する。"""
        w = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0); h.addWidget(line, 1)
        b = QtWidgets.QPushButton("参照...")
        b.clicked.connect(lambda: self._browse_dir(line, key))
        h.addWidget(b)
        return w

    def _browse_dir(self, line, key):
        p = QtWidgets.QFileDialog.getExistingDirectory(self, "フォルダを選択", line.text())
        if p:
            line.setText(p); self._save_dirs()

    def _save_dirs(self):
        """ウィザードの場所欄を設定へ保存（詳細設定を開かなくても反映・永続化）。"""
        self.settings["param_product_dir"] = self.e_pdir.text().strip()
        self.settings["param_basic_dir"] = self.e_bdir.text().strip()
        self.settings["param_custom_dir"] = self.e_cdir.text().strip()
        try:
            from .settings import save_settings
            save_settings(self.settings)
        except Exception:
            pass
        self._refresh_basic_dir_note()

    def _abs_dir(self, key):
        # ウィザードの場所欄があればそれを優先（最新の入力値）。無ければ設定値。
        if key == "param_product_dir" and hasattr(self, "e_pdir"):
            return self.e_pdir.text().strip()
        if key == "param_basic_dir" and hasattr(self, "e_bdir"):
            return self.e_bdir.text().strip()
        if key == "param_custom_dir" and hasattr(self, "e_cdir"):
            return self.e_cdir.text().strip()
        return str(self.settings.get(key, "") or "")

    def _refresh_basic_dir_note(self):
        bd = self._abs_dir("param_basic_dir")
        pd = self._abs_dir("param_product_dir")
        miss = []
        if not pd or not Path(pd).is_dir():
            miss.append("製品データの場所")
        if not bd or not Path(bd).is_dir():
            miss.append("BASICの場所")
        if miss:
            self.lbl_basic_dir.setText(
                "※ " + "・".join(miss) + " が未設定/存在しません。上の「場所」欄で指定してください。")
        else:
            self.lbl_basic_dir.setText(f"製品データ: {pd}　／　BASIC: {bd}")

    def _update_guide(self, *_):
        """『次にやること』を状態から自動表示（誰でも迷わないライブガイド）。"""
        pd = self._abs_dir("param_product_dir")
        sel = self._selected_files()
        ok = False
        if not (pd and Path(pd).is_dir()):
            msg = "まず『場所』で製品データのフォルダを指定（右上の▶で開く）"
        elif not self._files:
            msg = "受注番号を入れて『探す』（特注/DDは『型式で探す』）"
        elif not sel:
            msg = "見つかった中から、作るもの（傾斜／回転）にチェック"
        elif any(f["meta"].get("system", "FANUC") != "FANUC" for f in sel):
            msg = "⚠ FANUC以外が選ばれています。FANUC製品だけにチェックしてください"
        elif self.cmb_ctrl.currentData() in (None, -1):
            msg = "② 制御装置（号機）を選んでください"
        elif not self.e_basic.text().strip():
            msg = "③ 使うBASIC を指定してください（号機選択で自動で入ります）"
        elif not self.e_out.text().strip():
            msg = "③ 出力先 を指定してください"
        elif not self.e_seiban.text().strip():
            msg = "③ Seiban（出力ファイル名用）を入れてください"
        else:
            msg, ok = "準備OK。右下の『作成 → 出力先』を押してください", True
        self.lbl_guide.setText(("✓ " if ok else "▶ ") + "次にやること： " + msg)
        self.lbl_guide.setStyleSheet(
            "padding:6px; border-radius:4px; "
            + ("background:#dcfce7; color:#166534; font-weight:bold;" if ok
               else "background:#eff6ff; color:#1d4ed8;"))

    def _zero_params(self):
        return zero_params_for(self.settings, getattr(self, "chk_zero_indiv", None))

    def _browse_basic(self):
        start = self.e_basic.text() or self._abs_dir("param_basic_dir")
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "使うBASIC .prm", start, "パラメータ (*.prm *.PRM *.DAT *.dat *.txt);;すべて (*.*)")
        if p:
            self.e_basic.setText(p)

    def _browse_out(self):
        p = QtWidgets.QFileDialog.getExistingDirectory(self, "出力先", self.e_out.text())
        if p:
            self.e_out.setText(p)

    def _open_advanced(self):
        self._save_dirs()                       # 現在の場所欄を先に保存して引き継ぐ
        dlg = ParamDialog(self.parent() or self, self.settings)
        dlg.e_seiban.setText(self.e_seiban.text().strip())
        dlg.exec()
        # 詳細画面で場所が変わっていれば、ウィザードの欄へ反映
        self.e_pdir.setText(str(self.settings.get("param_product_dir", "")))
        self.e_bdir.setText(str(self.settings.get("param_basic_dir", "")))
        self._refresh_basic_dir_note()

    def _open_viewer(self):
        """パラメータ閲覧/比較。A=使うBASIC、B=選択中の製品データ を初期値にする。"""
        a = self.e_basic.text().strip()
        sel = self._selected_files() or self._files
        # 特注(Excel)は番地一覧ビューア(製品.prm前提)では開けないので .prm/.txt のものだけ
        b = next((f["path"] for f in sel
                  if f.get("path")
                  and Path(f["path"]).suffix.lower() in (".prm", ".txt")), "")
        ParamViewerDialog(self, self.settings, file_a=a, file_b=b).exec()

    def _scan_basics(self):
        """制御装置マスタの管理（手入力 追加/編集/削除・BASICから取り込み）を開く。"""
        ControllerMasterDialog(self, self._mpath, self._abs_dir("param_basic_dir"),
                               settings=self.settings).exec()
        # マスタが変わったかもしれないので読み直して候補を更新
        self._controllers = controllers.load_controllers(self._mpath)
        self.cmb_cap.blockSignals(True)
        cur = self.cmb_cap.currentData()
        self.cmb_cap.clear(); self.cmb_cap.addItem("（自動：製品データから判定）", "")
        for c in controllers.all_capacities(self._controllers):
            self.cmb_cap.addItem(c, c)
        i = self.cmb_cap.findData(cur)
        if i >= 0:
            self.cmb_cap.setCurrentIndex(i)
        self.cmb_cap.blockSignals(False)
        self._update_candidates()

    # ----- ① 探す -----
    def search(self):
        seiban = self.e_seiban.text().strip()
        self._clear_found()
        if not seiban:
            self.lbl_found.setText("Seiban を入力してください（型式だけで探すなら下の『型式で探す』）。")
            self._update_candidates(); return
        pd = self._abs_dir("param_product_dir")
        items = []
        models = set()
        # ① 製品データ(.prm) を探す（フォルダ名一致だけ＝速い）。型式が分かる。
        if pd and Path(pd).is_dir():
            for fdict in seiban_flow.find_seiban_files(pd, seiban):
                try:
                    text = Path(fdict["path"]).read_text(encoding="cp932", errors="replace")
                except Exception:
                    text = ""
                fdict["meta"] = seiban_flow.read_product_meta(
                    text, kind=fdict["kind"], motor_caps=self._motor_caps)
                if fdict["meta"].get("model"):
                    models.add(fdict["meta"]["model"])
                items.append(fdict)
        # ② 特注パラ＝索引から「製品の型式」で引く（特注は製番が無い）＋製番直一致も
        recs = []
        for m in models:
            recs += xls_param.search_index(self._custom_index, model=m)
        recs += xls_param.search_index(self._custom_index, seiban=seiban)
        for rec in self._dedup_recs(recs):
            items.append(self._custom_item(rec))
        # ③ パラメータDB（実機ダンプ由来など登録済みの変更点）も型式・受注番号で候補に
        db_hits = []
        for m in models:
            db_hits += self._db_by_model(m)
        db_hits += self._db_by_seiban(seiban)
        for e in self._dedup_entries(db_hits):
            items.append(self._db_item(e))
        if not items:
            self.lbl_found.setText(self._not_found_msg(seiban, models))
            self._update_candidates(); return
        extra = []
        if models and recs:
            extra.append(f"特注は型式 {('・'.join(sorted(models)))} で照合")
        if db_hits:
            extra.append("DB＝登録済みの変更点（実機吸い取り等）")
        note = ("見つかったものにチェック（両方／必要な方）"
                + (f"　※{' / '.join(extra)}" if extra else ""))
        self.lbl_found.setText(note)
        for fdict in items:
            self.found_lay.addWidget(self._make_found_checkbox(fdict))
            self._files.append(fdict)
        self._update_candidates()

    def search_by_model(self):
        """型式で特注パラ索引＋パラメータDBを引いて候補を出す（受注番号が無い/特注やDBを直接出す）。"""
        model = self.e_model.text().strip()
        self._clear_found()
        if not model:
            self.lbl_found.setText("型式を入力してください。")
            self._update_candidates(); return
        recs = self._dedup_recs(xls_param.search_index(self._custom_index, model=model))
        db_hits = self._dedup_entries(self._db_by_model(model))
        if not recs and not db_hits:
            n = len(self._custom_index)
            self.lbl_found.setText(
                f"型式『{model}』の特注パラ・登録(DB)は見つかりません（特注索引 {n}件）。"
                + ("" if n else "　特注を使うなら、まず『索引を更新』を押してください。"))
            self._update_candidates(); return
        bits = []
        if recs:
            bits.append(f"特注パラ {len(recs)}件")
        if db_hits:
            bits.append(f"登録(DB) {len(db_hits)}件")
        self.lbl_found.setText(
            f"型式『{model}』の {('・'.join(bits))}。チェックして作成（出力名用にSeibanも入力）:")
        for rec in recs:
            fdict = self._custom_item(rec)
            self.found_lay.addWidget(self._make_found_checkbox(fdict))
            self._files.append(fdict)
        for e in db_hits:
            fdict = self._db_item(e)
            self.found_lay.addWidget(self._make_found_checkbox(fdict))
            self._files.append(fdict)
        self._update_candidates()

    def _clear_found(self):
        for f in self._files:
            if f.get("chk"):
                f["chk"].setParent(None)
        self._files = []

    @staticmethod
    def _dedup_recs(recs):
        seen, out = set(), []
        for r in recs:
            k = (r.get("path", ""), r.get("sheet", ""))
            if k in seen:
                continue
            seen.add(k); out.append(r)
        return out

    def _not_found_msg(self, seiban, models):
        pd = self._abs_dir("param_product_dir")
        if not pd or not Path(pd).is_dir():
            return ("製品データの場所が未設定です。上の「場所」欄で指定してください。"
                    "（特注パラだけ探すなら『型式で探す』）")
        base = f"Seiban『{seiban}』の製品データ・登録(DB)が見つかりませんでした。"
        if not self._custom_index:
            return base + "\n特注パラを使うなら『索引を更新』を押してから『型式で探す』。"
        return base + "\n特注パラ・登録(DB)は『型式で探す』で型式から探せます。"

    def _custom_item(self, rec):
        """特注パラ索引レコード → 検索結果アイテム（値は作成時に1枚だけ読む＝速い）。"""
        motor = rec.get("motor", "")
        cap = seiban_flow.standard_capacity(motor)         # DiSは""→手入力
        src = "標準" if cap else ""
        if not cap and self._motor_caps:
            cap = seiban_flow.capacity_for_motor(self._motor_caps, "", motor)
            src = "対応表" if cap else ""
        meta = {"model": rec.get("model", ""), "kind": rec.get("kind", "") or "回転",
                "motor": motor, "system": "FANUC", "capacity": cap, "capacity_src": src,
                "voltage": seiban_flow.motor_voltage(motor), "dd": str(rec.get("dd", "")) == "1"}
        nm = rec.get("name", "") + (f"：{rec['sheet']}" if rec.get("sheet") else "")
        return {"kind": meta["kind"], "name": nm, "meta": meta, "source": "custom",
                "path": rec.get("path", ""), "sheet_name": rec.get("sheet", ""),
                "values": dict(rec.get("values") or {})}   # 索引に取り込んだ数値（フォルダ不要）

    def _db_item(self, e):
        """パラメータDBエントリ → 検索結果アイテム（登録済みの変更点をそのまま使う）。

        実機ダンプ−BASICで起こして登録した変更点（source=="diff"）や手入力登録など。
        値はDBが持っているので、製品データ/特注フォルダが無くても作成できる。
        """
        motor = e.motor or ""
        cap = seiban_flow.standard_capacity(motor)         # DiSは""→手入力
        src = "標準" if cap else ""
        if not cap and self._motor_caps:
            cap = seiban_flow.capacity_for_motor(self._motor_caps, "", motor)
            src = "対応表" if cap else ""
        kind = e.kind or "回転"
        meta = {"model": e.model, "kind": kind, "motor": motor, "system": "FANUC",
                "capacity": cap, "capacity_src": src,
                "voltage": seiban_flow.motor_voltage(motor),
                "dd": xls_param.is_dd(e.values(), motor=motor, gear=e.gear)}
        tail = "　".join(b for b in (e.controller, e.axis, e.seiban, e.date) if b)
        nm = "DB" + (f"：{tail}" if tail else f"：{e.model}")
        return {"kind": kind, "name": nm, "meta": meta, "source": "db",
                "values": dict(e.values())}

    def _db_by_model(self, model):
        """パラメータDBを型式（前方一致を含む）で引く。特注索引と同じ照合にする。"""
        nm = xls_param._normmodel(model)
        if not nm:
            return []
        out = []
        for e in self._db_entries:
            em = xls_param._normmodel(e.model)
            if em and (em == nm or em.startswith(nm) or nm.startswith(em)):
                out.append(e)
        return out

    def _db_by_seiban(self, seiban):
        s = (seiban or "").strip()
        return [e for e in self._db_entries if s and (e.seiban or "").strip() == s]

    @staticmethod
    def _dedup_entries(entries):
        seen, out = set(), []
        for e in entries:
            k = e.id or id(e)
            if k in seen:
                continue
            seen.add(k); out.append(e)
        return out

    def _reindex_custom(self):
        """特注パラフォルダを全件読み込んで索引を作り直す（少し時間がかかる）。"""
        cd = self._abs_dir("param_custom_dir")
        if not cd or not Path(cd).is_dir():
            QtWidgets.QMessageBox.warning(self, "索引", "特注パラの場所を指定してください。")
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            recs = xls_param.index_records(cd)
            if self._cindex_path:
                p = Path(self._cindex_path)
                if p.parent and not p.parent.exists():
                    p.parent.mkdir(parents=True, exist_ok=True)
                xls_param.write_index(self._cindex_path, recs)
            self._custom_index = recs
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._refresh_cidx_note()
        QtWidgets.QMessageBox.information(
            self, "索引",
            f"特注パラを索引に登録しました（{len(self._custom_index)}件）。\n"
            "パラメータの数値も索引に取り込んだので、登録後はフォルダが無くても作成できます。\n"
            "元Excelの場所も索引に残しているので、何かあればすぐ調べられます。\n"
            "※ 新しい特注パラが入った／中身を直したときは、もう一度この『索引を更新』を押してください。")

    def _refresh_cidx_note(self):
        n = len(self._custom_index)
        self.lbl_cidx.setText(f"特注パラ索引: {n}件" + ("（『索引を更新』で再作成）" if n
                              else "　← まだ空。『索引を更新』を押して登録してください"))

    def _make_found_checkbox(self, fdict):
        meta = fdict["meta"]
        is_fanuc = meta.get("system", "FANUC") == "FANUC"
        src = fdict.get("source")
        if src == "custom":
            tag = "FANUC特注" + ("DD" if meta.get("dd") else "")
        elif src == "db":
            tag = "登録(DB)" + ("DD" if meta.get("dd") else "")
        else:
            tag = meta.get("system") or "FANUC"
        parts = [f"型式 {meta.get('model') or '—'}", tag, fdict["kind"]]
        if meta.get("motor"):
            parts.append(meta["motor"])
        if is_fanuc:
            parts.append(f"容量 {meta['capacity']}（{meta.get('capacity_src') or '自動'}）"
                         if meta.get("capacity") else "容量不明→手で選択")
            if meta.get("voltage"):
                parts.append(meta["voltage"])
        else:
            parts.append("⚠ 今はFANUCのみ作成可")
        chk = QtWidgets.QCheckBox(" / ".join(parts) + f"　（{fdict['name']}）")
        # 同じ種別（傾斜/回転）が既にチェック済みなら2件目以降は外して出す。
        # 製品ファイル＋同型式のDB登録などの重複を「2軸ぶん必要」と誤解釈しないため
        # （必要ならユーザーがチェックを付け替える）。
        covered = {f["kind"] for f in self._files
                   if f.get("chk") and f["chk"].isChecked()}
        chk.setChecked(is_fanuc and fdict["kind"] not in covered)
        if not is_fanuc:
            chk.setStyleSheet("color:#b45309;")
        elif src == "custom":
            chk.setStyleSheet("color:#7c3aed;")    # 特注は紫で区別
        elif src == "db":
            chk.setStyleSheet("color:#0f766e;")    # 登録(DB)は緑で区別
        chk.stateChanged.connect(self._update_candidates)
        fdict["chk"] = chk
        return chk

    def _selected_files(self):
        return [f for f in self._files if f.get("chk") and f["chk"].isChecked()]

    def _needs(self, sel):
        """選択ファイルごとの必要容量リスト。手動指定があれば不明分を補う。"""
        manual = self.cmb_cap.currentData() or ""
        return [(f["meta"].get("capacity") or manual) for f in sel]

    # ----- ② 候補制御装置 -----
    def _update_candidates(self, *_):
        sel = self._selected_files()
        self.cmb_ctrl.blockSignals(True)
        self.cmb_ctrl.clear()
        self._cand = []
        if not sel:
            self.cmb_ctrl.addItem("（①で作るものを選んでください）", -1)
            self.lbl_need.setText("")
            self.cmb_ctrl.blockSignals(False)
            self._on_ctrl_changed(); return
        if not self._controllers:
            self.cmb_ctrl.addItem("（制御装置マスタが未設定）", -1)
            self.lbl_need.setText("")
            self.cmb_ctrl.blockSignals(False)
            self._on_ctrl_changed(); return
        needs = self._needs(sel)
        # 製品の電圧（HV→400V）。選択ファイルの電圧で号機を絞る（400V製品は400V号機のみ）
        volts = {f["meta"].get("voltage") for f in sel if f["meta"].get("voltage")}
        voltage = volts.pop() if len(volts) == 1 else ""
        self._cand = seiban_flow.capable_controllers(self._controllers, needs, voltage)
        # 選択内容→必要な軸数の要約（両方なら2軸とも載る号機、片方なら1軸でよい号機）
        kinds = "＋".join(f["kind"] for f in sel)
        capstr = "・".join(n or "?" for n in needs)
        vtxt = f"・{voltage}" if voltage else ""
        self.lbl_need.setText(
            f"{kinds}（{len(sel)}軸／容量 {capstr}{vtxt}）を載せられる号機 … {len(self._cand)}台")
        if not self._cand:
            self.cmb_ctrl.addItem(f"（容量 {capstr}{vtxt} を満たす制御装置がありません）", -1)
        else:
            self.cmb_ctrl.addItem("（制御装置を選択）", -1)
            for i, (c, asg) in enumerate(self._cand):
                amap = "  ".join(f"{nc_param.axis_name(seiban_flow_axis(a))}:{c.caps[a]}"
                                 for a in asg)
                self.cmb_ctrl.addItem(f"{c.label()}  →  {amap}", i)
        self.cmb_ctrl.blockSignals(False)
        self._on_ctrl_changed()

    def _on_ctrl_changed(self, *_):
        idx = self.cmb_ctrl.currentData()
        sel = self._selected_files()
        if idx is None or idx < 0 or idx >= len(self._cand):
            self.lbl_assign.setText("")
            self._update_guide()
            return
        c, asg = self._cand[idx]
        parts = []
        for f, a in zip(sel, asg):
            parts.append(f"{f['kind']} → {a}軸（第{seiban_flow_axis(a)}軸／{c.caps[a]}）")
        self.lbl_assign.setText("　".join(parts))
        # BASICを号機から自動セット
        bd = self._abs_dir("param_basic_dir")
        path = controllers.basic_file_for_unit(bd, c.unit) if bd else None
        if path:
            self.e_basic.setText(path)
        elif not self.e_basic.text().strip():
            self.lbl_assign.setText(self.lbl_assign.text()
                                    + f"\n※ 号機 {c.unit} のBASICが見つかりません。『使うBASIC』を指定してください。")
        self._update_guide()

    # ----- ③ 作成 -----
    def create(self):
        sel = self._selected_files()
        if not sel:
            QtWidgets.QMessageBox.warning(self, "作成", "①で作るもの（傾斜/回転）を選んでください")
            return
        # FANUC以外（三菱/安川TPC等）はFANUCのBASICに適用できない＝今は作成不可で弾く
        non_fanuc = [f for f in sel if f["meta"].get("system", "FANUC") != "FANUC"]
        if non_fanuc:
            names = "、".join(f"{f['name']}（{f['meta'].get('system')}系）" for f in non_fanuc)
            QtWidgets.QMessageBox.warning(
                self, "作成",
                f"次の製品はFANUC制御ではないため、今は作成できません:\n・{names}\n\n"
                "現在パラメータ作成はFANUCのみ対応です（三菱・安川等は後日対応）。"
                "FANUC製品だけを選んで作成してください。")
            return
        idx = self.cmb_ctrl.currentData()
        if idx is None or idx < 0 or idx >= len(self._cand):
            QtWidgets.QMessageBox.warning(self, "作成", "②で制御装置を選んでください")
            return
        ctl, asg = self._cand[idx]
        master = self.e_basic.text().strip()
        out = self.e_out.text().strip()
        seiban = self.e_seiban.text().strip()
        if not seiban:
            # 型式検索から来ると未入力のことがある。空のまま作ると「R.prm」の
            # ような名無しファイル・Seiban無しの履歴ができてしまうので必須にする
            QtWidgets.QMessageBox.warning(
                self, "作成", "Seiban（受注伝票番号）を入力してください（出力ファイル名になります）")
            return
        if not master or not Path(master).is_file():
            QtWidgets.QMessageBox.warning(self, "作成", "使うBASIC（.prm）を指定してください")
            return
        if not out or not Path(out).is_dir():
            QtWidgets.QMessageBox.warning(self, "作成", "出力先（存在するフォルダ）を指定してください")
            return
        try:
            raw = param_build.read_master(master)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "作成", f"BASIC を読めません:\n{e}")
            return
        # 各ファイル → 変更値（平坦）→ 割当軸番号
        axis_values, per_meta = {}, {}
        for f, a in zip(sel, asg):
            axnum = seiban_flow_axis(a)
            if f.get("source") == "db":
                vals = dict(f.get("values") or {})         # パラメータDBの登録済み変更点
            elif f.get("source") == "custom":
                vals = dict(f.get("values") or {})         # 索引に取り込んだ数値（フォルダ不要）
                if not vals:                               # 古い索引（値未取込）→元Excelを読む
                    sh = xls_param.sheet_values(f.get("path", ""), f.get("sheet_name", ""))
                    vals = dict(sh.get("values", {})) if sh else {}
            else:
                try:
                    ptext = Path(f["path"]).read_text(encoding="cp932", errors="replace")
                except Exception as e:
                    QtWidgets.QMessageBox.warning(self, "作成", f"製品データを読めません:\n{e}")
                    return
                vals = param_build.product_change_values(raw, ptext)
            if not vals:
                QtWidgets.QMessageBox.warning(
                    self, "作成",
                    f"{f['kind']}（{f['name']}）から変更値を取り出せませんでした。"
                    "BASICと製品/特注データの形式が合っているか確認してください。")
                return
            # 作成時オプション（2000を0／原点確立 ON/OFF）を反映。旧→新はプレビューで確認
            vals = param_build.with_servo_options(
                raw, axnum, vals,
                zero_motor=self.chk_zero.isChecked(),
                zero_param=self.settings.get("motor_zero_param", "2000"),
                zero_bit=int(self.settings.get("motor_zero_bit", 1)),
                origin=(self.cmb_origin.currentData() or None),
                origin_param=self.settings.get("origin_param", "1815"),
                origin_bit=int(self.settings.get("origin_bit", 5)))
            axis_values[axnum] = vals
            per_meta[axnum] = (f, a)
        # 頭文字・ファイル名（T+R が選択順で並ぶ。種別不明や重複は "TR"）
        if len(sel) >= 2:
            kinds = {"傾斜": "T", "回転": "R"}
            parts = [kinds.get(f["kind"], "") for f in sel]
            prefix = ("".join(parts) if all(parts) and len(set(parts)) == len(parts)
                      else "TR")
        else:
            prefix = seiban_flow.KIND_PREFIX.get(sel[0]["kind"], "T")
        fname = param_build.filename(prefix, seiban)
        axis_label = "＋".join(f"{nc_param.axis_name(ax)}" for ax in sorted(axis_values))
        # プレビュー（軸つき）
        rows = [(num, nc_param.axis_name(ax) if ax else "共通", old, new)
                for (num, ax, old, new) in param_build.preview_rows_multi(raw, axis_values)]
        zparams = self._zero_params()
        rows += [(num, "全軸", old, new)
                 for (num, old, new) in param_build.preview_zero_rows(raw, zparams)]
        sub = ("＋".join(f["kind"] for f in sel)
               + f" → {fname}（{ctl.label()}）")
        if not ParamPreviewDialog(self, rows, subtitle=sub).exec():
            return
        try:
            out_path, missing, fmt = param_build.create_file_multi(
                master, out, axis_values, prefix=prefix, seiban=seiban,
                ext=self.settings.get("param_out_ext", ".DAT"),
                eob=self.settings.get("nc_eob"), zero_params=zparams)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "作成に失敗", str(e))
            return
        controller = nc_param.controller_from_basic(master)
        total = sum(len(v) for v in axis_values.values())
        reg = []
        csv_path = self._db_path      # 読み込みと同じ解決済みパスへ登録（app_dir基準）
        for axnum, vals in axis_values.items():
            f, _a = per_meta[axnum]
            meta = f.get("meta", {})
            mode, _eff = param_build.detect_mode(
                raw, vals, axnum,
                number=self.settings.get("closed_loop_number", "1815"),
                bit=int(self.settings.get("closed_loop_bit", 1)),
                full_when=int(self.settings.get("closed_loop_full", 1)))
            n_miss = sum(1 for (_m, a) in missing if a == axnum)
            _log_param_creation(
                self.settings, model=meta.get("model", ""), kind=f["kind"], mode=mode,
                motor=meta.get("motor", ""), controller=controller,
                axis=nc_param.axis_name(axnum), seiban=seiban,
                basic=Path(master).name, out=fname,
                applied=len(vals) - n_miss, total=len(vals))
            if csv_path:
                entry = nc_param.ParamEntry(
                    model=meta.get("model", ""), kind=f["kind"], mode=mode,
                    motor=meta.get("motor", ""), motor_no=meta.get("motor_no", ""),
                    direction=meta.get("direction", ""), gear=meta.get("gear", ""),
                    controller=controller, axis=nc_param.axis_name(axnum), seiban=seiban,
                    date=QtCore.QDate.currentDate().toString("yyyy/MM/dd"),
                    basic=Path(master).name,
                    items=[(n, vv, "") for n, vv in vals.items() if vv != ""])
                try:
                    _, action = nc_param.upsert_entry(csv_path, entry)
                    verb = {"added": "登録", "updated": "更新"}.get(action, "")
                    if verb:
                        reg.append(f"・{f['kind']}（{nc_param.axis_name(axnum)}軸）を履歴に{verb}")
                except Exception:
                    pass
        try:
            from .settings import save_settings
            self.settings["param_out_folder"] = out
            save_settings(self.settings)
        except Exception:
            pass
        msg = (f"作成しました:\n・{out_path.name}\n"
               f"（{Path(master).name} の {axis_label} 軸へ製品値を書き込み）\n"
               f"値の反映: {total - len(missing)} / {total} 件")
        if len(sel) >= 2:
            msg += "\n（2軸テーブル: 傾斜＋回転を1ファイルにまとめました）"
        if missing:
            miss = ", ".join(f"{n}({nc_param.axis_name(a)})" if a else str(n)
                             for n, a in missing[:12])
            msg += f"\n⚠ BASICに無い番号（未反映）: {miss}"
        if reg:
            msg += "\nデータベース履歴:\n" + "\n".join(reg)
        msg += "\n\n機械側での入力は人が実施（PWE/電源再投入に注意）。"
        QtWidgets.QMessageBox.information(self, "作成しました", msg)


def seiban_flow_axis(letter):
    """軸文字(X/Y/Z/A/B/C) → 軸番号(1〜6)。ParamWizardDialog 用の薄いラッパ。"""
    return nc_param.axis_number(letter)


class BasicOriginDialog(QtWidgets.QDialog):
    """BASICフォルダの各ファイルが「実機が出したもの」か「PC製」かの一覧。

    製品ファイルは実機のものを元に作るのが確実。PC側のマスタには、その制御装置が
    持っていない番号が入っていることがあり、書き戻すと取込が止まる。
    """

    HEADERS = ("ファイル", "種類", "出どころ", "区切り(EOB)", "行数", "CNC ID",
               "判定の根拠")

    def __init__(self, parent, rows, folder, cov=None):
        super().__init__(parent)
        self.rows = rows
        self.cov = cov
        self.setWindowTitle("BASICの出どころ判定")
        v = QtWidgets.QVBoxLayout(self)
        head = QtWidgets.QLabel(
            f"<b>{folder}</b><br>{param_origin.summarize(rows)}<br>"
            "拡張子ではなく中身のバイトで判定しています。"
            "実機のパンチ形式は区切りが <code>LF CR CR</code>、PC製は <code>LF</code>／"
            "<code>CRLF</code> です。<b>製品ファイルは「実機」の行を元に作ってください。</b>"
            "<br><span style='color:#b45309'>※分かるのは「実機と同じ形式か」までです。"
            "アプリの出力も同じ形式になるので、<b>このフォルダにアプリで作った"
            "ファイルを置かないでください</b>（実機のものと区別できなくなります）。</span>")
        head.setWordWrap(True)
        v.addWidget(head)

        # 号機マスタと突き合わせた「足りないBASIC」。次に実機から何を取ってくれば
        # よいかが、この一覧を見なくても分かるように先頭へ出す。
        if cov and (cov["pc_only"] or cov["missing"]):
            need = QtWidgets.QLabel(self._coverage_html(cov))
            need.setWordWrap(True)
            need.setTextFormat(QtCore.Qt.RichText)
            need.setStyleSheet(
                "color:#b91c1c; background:#fef2f2; border:1px solid #fecaca;"
                "border-radius:6px; padding:6px 9px;")
            v.addWidget(need)
        elif cov:
            okmsg = QtWidgets.QLabel(
                f"号機マスタの {len(cov['ok'])}台すべてに実機のBASICがあります。"
                "実機から取ってくる必要のあるものはありません。")
            okmsg.setWordWrap(True)
            okmsg.setStyleSheet(
                "color:#15803d; background:#f0fdf4; border:1px solid #bbf7d0;"
                "border-radius:6px; padding:6px 9px;")
            v.addWidget(okmsg)

        # 中身が同じ組。BASICは「基本パラメータ」なので、仕様が同じ号機なら
        # 中身も同じになるのが正常。問題になるのは「仕様が違うのに中身が同じ」
        # 場合だけなので、号機マスタの仕様と突き合わせて分ける。
        groups = param_origin.cross_unit_duplicates(rows)
        ctls = list(getattr(parent_ctls, "_controllers", []) or []) \
            if (parent_ctls := self.parent()) is not None else []
        judged = controllers.classify_duplicate_groups(groups, ctls)
        odd = [(g, why) for g, kind, why in judged if kind != "same_spec"]
        normal = [g for g, kind, _w in judged if kind == "same_spec"]
        if odd:
            lines = ["<b>中身が同じなのに仕様が違う組があります（要確認）</b>"]
            for g, why in odd:
                lines.append("・" + " ＝ ".join(g) + f"　{why}")
            lines.append("仕様が同じなら中身が同じで正常です。ここに出るのは"
                         "仕様が食い違う組なので、どちらが正しいか確認してください。")
            warn = QtWidgets.QLabel("<br>".join(lines))
            warn.setWordWrap(True)
            warn.setTextFormat(QtCore.Qt.RichText)
            warn.setStyleSheet(
                "color:#7c2d12; background:#fff7ed; border:1px solid #fdba74;"
                "border-radius:6px; padding:6px 9px;")
            v.addWidget(warn)
        if normal:
            note = QtWidgets.QLabel(
                "仕様が同じで中身も同じ組（正常）: "
                + "、".join(" ＝ ".join(g) for g in normal))
            note.setWordWrap(True)
            note.setStyleSheet("color:#475569;")
            v.addWidget(note)

        self.table = QtWidgets.QTableWidget(len(rows), len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        for r, (name, info, p) in enumerate(rows):
            reasons = list(info["reasons"])
            if info.get("individual"):
                reasons.insert(0, "★個体データ入り: " + " ／ ".join(info["individual"]))
            cells = (name, info["kind"], info["label"], info["eob"],
                     str(info["lines"]), info.get("cnc_id", ""),
                     " / ".join(reasons))
            for c, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                if info["verdict"] == "machine":
                    item.setBackground(QtGui.QColor("#dcfce7"))   # 実機＝緑
                elif info["verdict"] == "converted":
                    item.setBackground(QtGui.QColor("#fef9c3"))   # 実機だがPC経由＝黄
                elif info["verdict"] == "pc":
                    item.setBackground(QtGui.QColor("#fee2e2"))   # PC製＝赤
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        v.addWidget(self.table, 1)

        buttons = QtWidgets.QHBoxLayout()
        b_fix = QtWidgets.QPushButton("マスタをBASICに合わせる...")
        b_fix.setToolTip(
            "実機が出したBASICのアンプ最大電流(N2165)から軸ごとの容量を読み取り、\n"
            "号機マスタの容量を実機に合わせる。PC製のファイルは根拠にしない")
        b_fix.clicked.connect(self.fix_master)
        b_csv = QtWidgets.QPushButton("CSV出力")
        b_csv.clicked.connect(self.save_csv)
        b_close = QtWidgets.QPushButton("閉じる")
        b_close.clicked.connect(self.accept)
        buttons.addWidget(b_fix)
        buttons.addWidget(b_csv)
        buttons.addStretch(1)
        buttons.addWidget(b_close)
        v.addLayout(buttons)

        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1280, 800)
        self.resize(min(1100, avail.width() - 80), min(640, avail.height() - 80))

    def fix_master(self):
        """実機のBASICに合わせて号機マスタの容量を直す（確認してから書く）。"""
        parent = self.parent()
        ctls = list(getattr(parent, "_controllers", []) or [])
        path = getattr(parent, "_controller_master_path", "")
        if not ctls or not path:
            QtWidgets.QMessageBox.information(
                self, "マスタをBASICに合わせる",
                "制御装置マスタが読み込まれていません（設定の「制御装置マスタ」を確認）")
            return
        folder = str(Path(self.rows[0][2]).parent) if self.rows else ""
        fixes = controllers.master_fix_rows(
            folder, ctls, (parent.settings.get("amp_current_map")
                           if hasattr(parent, "settings") else None))
        if not fixes:
            QtWidgets.QMessageBox.information(
                self, "マスタをBASICに合わせる",
                "実機のBASICと号機マスタの容量は一致しています。直すところはありません。")
            return
        lines = [f"F{u}　{ax}軸　{cur or '(空)'} → {cap}　（{name}）"
                 for u, ax, cur, cap, name in fixes]
        ok = QtWidgets.QMessageBox.question(
            self, "マスタをBASICに合わせる",
            f"実機のBASICから読んだ容量に合わせて、号機マスタを{len(lines)}箇所"
            "直します:\n\n" + "\n".join(lines[:20])
            + ("\n…" if len(lines) > 20 else "")
            + f"\n\n書き込み先: {path}\n実行しますか？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if ok != QtWidgets.QMessageBox.Yes:
            return
        try:
            n = controllers.apply_master_fixes(path, ctls, fixes)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "マスタをBASICに合わせる",
                                          f"書き込みに失敗しました:\n{e}")
            return
        QtWidgets.QMessageBox.information(
            self, "マスタをBASICに合わせる",
            f"{n}台ぶんを直しました。制御装置の一覧は「更新」で読み直せます。")

    @staticmethod
    def _coverage_html(cov):
        """号機マスタと突き合わせた「足りないBASIC」の文面。"""
        parts = ["<b>実機から取ってくる必要があるBASIC</b>"]
        if cov["pc_only"]:
            items = "、".join(f"<b>F{u}</b>（今あるのは {'・'.join(names)}）"
                              for u, names in cov["pc_only"])
            parts.append(f"・PC製しか無い {len(cov['pc_only'])}台: {items}")
        if cov["missing"]:
            parts.append("・BASICが1本も無い "
                         f"{len(cov['missing'])}台: "
                         + "、".join(f"<b>F{u}</b>" for u in cov["missing"]))
        parts.append(f"（実機のBASICが揃っているのは {len(cov['ok'])}台）")
        return "<br>".join(parts)

    def save_csv(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "判定結果を保存", "BASIC出どころ判定.csv", "CSV (*.csv)")
        if not path:
            return
        import csv
        with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
            w = csv.writer(f)
            w.writerow(self.HEADERS)
            for name, info, p in self.rows:
                w.writerow([name, info["kind"], info["label"], info["eob"],
                            info["lines"], info.get("cnc_id", ""),
                            " / ".join(info["reasons"])])
            ctls = list(getattr(self.parent(), "_controllers", []) or []) \
                if self.parent() is not None else []
            judged = controllers.classify_duplicate_groups(
                param_origin.cross_unit_duplicates(self.rows), ctls)
            if judged:
                w.writerow([])
                w.writerow(["中身が同じ組", "判定", "説明"])
                for g, kind, why in judged:
                    w.writerow([" = ".join(g),
                                {"same_spec": "正常", "diff_spec": "要確認",
                                 "unknown": "判定できない"}[kind], why])
            if self.cov:
                w.writerow([])
                w.writerow(["実機から取ってくる必要があるBASIC"])
                for u, names in self.cov["pc_only"]:
                    w.writerow([f"F{u}", "PC製しか無い", " / ".join(names)])
                for u in self.cov["missing"]:
                    w.writerow([f"F{u}", "BASICが1本も無い", ""])
                w.writerow(["実機のBASICが揃っている台数", len(self.cov["ok"])])
        QtWidgets.QMessageBox.information(self, "保存", f"保存しました:\n{path}")


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
        fit_to_screen(self, 740, 600)
        # 制御装置マスタ（号機・容量など）。あれば号機一覧＋必要容量で絞り込みに使う
        mpath = settings.get("controller_master_csv", "")
        if mpath and not Path(mpath).is_absolute():
            mpath = str(app_dir() / mpath)
        self._controllers = controllers.load_controllers(mpath)
        # 縦に長い画面なので全体をスクロール可能に（下のボタンは固定で常時表示）
        outer = QtWidgets.QVBoxLayout(self)
        scroll = QtWidgets.QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        # 明示的に小さい最小を与えないと、中身の最小がそのまま
        # ダイアログの最小になり、文字を大きくすると画面に収まらなくなる
        scroll.setMinimumSize(320, 200)
        inner = QtWidgets.QWidget(); scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        v = QtWidgets.QVBoxLayout(inner)
        intro = QtWidgets.QLabel(
            "詳細設定／手動作成の画面です。<b>普段は『かんたん作成』でOK</b>。"
            "ここはフォルダ設定・手動の軸指定・変更表CSV運用など細かい操作をするときに使います。")
        intro.setTextFormat(QtCore.Qt.RichText)
        intro.setWordWrap(True)
        intro.setStyleSheet("background:#eff6ff; color:#1d4ed8; padding:6px; border-radius:4px;")
        v.addWidget(intro)

        # 画面を3つに分ける: ①場所(共有サーバ・一度だけ) ②今回の作業 ③任意/CSV運用。
        # 各 self.e_* の参照名はそのまま（reload/find_product/make_prm から使うため）。
        form_place = QtWidgets.QFormLayout()
        form_job = QtWidgets.QFormLayout()
        form_csv = QtWidgets.QFormLayout()

        # --- ① 共有サーバの場所（最初に一度だけ設定すればOK） ---
        self.e_basic_dir = QtWidgets.QLineEdit(str(settings.get("param_basic_dir", "")))
        self.e_basic_dir.setPlaceholderText(r"BASIC(.prm)を置く共有フォルダ 例 \\server\param\BASIC")
        self.e_basic_dir.setToolTip("制御装置の基本パラメータ(BASIC)を置くフォルダ。"
                                    "一度入れれば下の「使うBASIC」選択の起点になる")
        basic_row = self._with_browse(self.e_basic_dir, self._browse_basic_dir)
        b_origin = QtWidgets.QPushButton("出どころ判定...")
        b_origin.setToolTip(
            "フォルダ内のBASICを1つずつ中身で調べ、実機（制御装置）が出したものか\n"
            "PCで作られたものかを判定する。拡張子は当てにならないので中身で見る。\n"
            "製品ファイルは実機のものを元に作るのが確実（PC側は実機に無い番号が\n"
            "入っていることがあり、制御装置が読み込めない）")
        b_origin.clicked.connect(self._show_basic_origin)
        basic_row.layout().addWidget(b_origin)
        form_place.addRow("BASICの場所(フォルダ)", basic_row)

        self.e_product_dir = QtWidgets.QLineEdit(str(settings.get("param_product_dir", "")))
        self.e_product_dir.setPlaceholderText(r"Seibanごとの製品データを置く共有フォルダ 例 \\server\param\製品")
        self.e_product_dir.setToolTip("受注伝票番号(Seiban)ごとの製品データを置くフォルダ。"
                                      "「Seibanで探す」がここから探す")
        form_place.addRow("製品データの場所(フォルダ)",
                          self._with_browse(self.e_product_dir, self._browse_product_dir))

        # --- ② 今回の作業（受注ごとに入れる） ---
        self.e_seiban = QtWidgets.QLineEdit()
        self.e_seiban.setPlaceholderText("受注伝票番号（ファイル名 <頭文字><Seiban>.prm に使用）")
        form_job.addRow("Seiban", self.e_seiban)

        self.e_master_prm = QtWidgets.QLineEdit(str(settings.get("param_master_prm", "")))
        self.e_master_prm.setPlaceholderText("今回使う制御装置のBASIC .prm（例 F30BASIC.PRM）")
        self.e_master_prm.setToolTip("容量・空き軸で決まった制御装置の BASIC ファイル。これを元に作る")
        # 必要容量で制御装置を絞り込む（製品の容量に合う軸を持つ号機だけ表示）
        self.cmb_cap = QtWidgets.QComboBox()
        self.cmb_cap.setToolTip("製品の必要容量。選ぶと、その容量の軸を持つ制御装置だけ一覧に出る")
        self.cmb_cap.addItem("（指定なし）", "")
        for c in controllers.all_capacities(self._controllers):
            self.cmb_cap.addItem(c, c)
        self.cmb_cap.currentIndexChanged.connect(self._scan_basic_controllers)
        if self._controllers:
            form_job.addRow("必要容量", self.cmb_cap)
        # 制御装置（号機）を一覧から選ぶ
        brow = QtWidgets.QHBoxLayout()
        brow.setContentsMargins(0, 0, 0, 0)
        self.cmb_basic = QtWidgets.QComboBox()
        self.cmb_basic.setToolTip("制御装置マスタ（号機・容量）または BASICの場所のファイルから一覧表示。"
                                  "選ぶと『使うBASIC』に自動で入り、必要容量に合う軸を提案する")
        self.cmb_basic.currentIndexChanged.connect(self._on_basic_selected)
        brow.addWidget(self.cmb_basic, 1)
        b_rescan = QtWidgets.QPushButton("更新")
        b_rescan.setToolTip("制御装置マスタ／BASICの場所を読み直して一覧を更新")
        b_rescan.clicked.connect(self._scan_basic_controllers)
        brow.addWidget(b_rescan)
        form_job.addRow("制御装置（一覧）", brow)
        form_job.addRow("使うBASIC",
                        self._with_browse(self.e_master_prm, self._browse_master_prm))
        # 選ばれたBASICが実機のものかを、その場に出す（黙って選ばない）
        self.lbl_basic_origin = QtWidgets.QLabel()
        self.lbl_basic_origin.setTextFormat(QtCore.Qt.RichText)
        self.lbl_basic_origin.setWordWrap(True)
        self.lbl_basic_origin.setVisible(False)
        form_job.addRow("", self.lbl_basic_origin)

        prod_w = QtWidgets.QWidget()
        prod_h = QtWidgets.QHBoxLayout(prod_w)
        prod_h.setContentsMargins(0, 0, 0, 0)
        self.e_product = QtWidgets.QLineEdit()
        self.e_product.setPlaceholderText("Seibanに対応する製品データ（空＝変更表CSVを使用）")
        prod_h.addWidget(self.e_product, 1)
        b_find = QtWidgets.QPushButton("Seibanで探す")
        b_find.setToolTip("製品データの場所から、Seiban を名前に含むファイルを探す")
        b_find.clicked.connect(self.find_product)
        prod_h.addWidget(b_find)
        b_pp = QtWidgets.QPushButton("参照...")
        b_pp.clicked.connect(self._browse_product)
        prod_h.addWidget(b_pp)
        form_job.addRow("製品データ", prod_w)

        axis_row = QtWidgets.QHBoxLayout()
        axis_row.setContentsMargins(0, 0, 0, 0)
        self.cmb_axis = QtWidgets.QComboBox()
        for n in range(1, 7):
            self.cmb_axis.addItem(f"{nc_param.axis_name(n)}（第{n}軸）", n)
        self.cmb_axis.setCurrentIndex(3)  # 既定 第4軸＝A
        self.cmb_axis.setToolTip("容量(20A/40A…)で決まった、その制御装置で使う軸（X/Y/Z/A/B/C）")
        axis_row.addWidget(self.cmb_axis, 1)
        axis_row.addWidget(QtWidgets.QLabel("頭文字"))
        self.cmb_prefix = QtWidgets.QComboBox()
        self.cmb_prefix.addItem("T（傾斜）", "T")
        self.cmb_prefix.addItem("R（回転）", "R")
        axis_row.addWidget(self.cmb_prefix)
        form_job.addRow("対象軸", axis_row)

        self.e_out = QtWidgets.QLineEdit(str(settings.get("param_out_folder", "")
                                             or settings.get("nc_send_folder", "")))
        self.e_out.setPlaceholderText(r"出力先（カード E:\ や LAN共有 \\192.168.0.10\nc）")
        form_job.addRow("出力先", self._with_browse(self.e_out, self._browse_out))

        # --- ③ 任意・変更表CSV運用（製品データが無いときに使う） ---
        self.e_model = QtWidgets.QLineEdit(model)
        self.e_model.setPlaceholderText("型式（変更表CSVの『型式』と照合）")
        self.e_model.editingFinished.connect(self.reload)
        form_csv.addRow("型式", self.e_model)

        self.cmb_ctrl = QtWidgets.QComboBox()
        self.cmb_ctrl.setToolTip("MELDAS/FANUC など制御ごとに番号が違うため切替える")
        self.cmb_ctrl.currentIndexChanged.connect(self._on_ctrl_changed)
        form_csv.addRow("制御", self.cmb_ctrl)

        self.e_csv = QtWidgets.QLineEdit(str(settings.get("param_change_csv", "")))
        self.e_csv.setPlaceholderText("製品データの代わりに使う変更表CSV（型式,制御,番号,変更値）")
        form_csv.addRow("変更表CSV", self._with_browse(self.e_csv, self._browse_csv))

        self.e_master = QtWidgets.QLineEdit(str(settings.get("param_master_backup", "")))
        self.e_master.setPlaceholderText("任意: 確認表に『旧値』を出すための実機バックアップ。空でOK")
        self.e_master.setToolTip("確認表で『旧値→新値』を見せたいときだけ指定する任意項目。"
                                 ".prm の作成には不要")
        form_csv.addRow("旧値バックアップ(任意)",
                        self._with_browse(self.e_master, self._browse_master))

        for title, inner in (
            ("① 共有サーバの場所（最初に一度だけ設定すればOK）", form_place),
            ("② 今回の作業（受注ごとに入れる）", form_job),
            ("③ 任意・変更表CSV運用（製品データが無いときに使う）", form_csv),
        ):
            box = QtWidgets.QGroupBox(title)
            box.setLayout(inner)
            v.addWidget(box)

        hint = QtWidgets.QLabel(
            "流れ:  Seiban を入れて「Seibanで探す」→ 使うBASIC を選ぶ → 対象軸と頭文字"
            "（傾斜T／回転R）を選ぶ → 出力先を確認 →「FANUC .prm 作成」。\n"
            "・製品データ（完成済み.prm）があれば、それと BASIC の差分を自動で当てます。"
            "無いときだけ ③ の変更表CSV（列: 型式, 制御, 番号, 軸, 変更値, メモ）を使います。\n"
            "・2軸テーブル: 完成済み.prm が傾斜軸と回転軸の両方を変えていれば自動で検知し、"
            "両軸を1つのBASICへ入れて1ファイル（TR<Seiban>.prm）にします。"
            "登録済みエントリから作る場合は「パラメータDB…」→「2軸で作成」。\n"
            "・実機への入力は人が行います（PWE=1。番号により電源再投入が必要）。")
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
        b_both.clicked.connect(self.export_both)
        b_prm = QtWidgets.QPushButton("FANUC .prm 作成→出力先")
        b_prm.setObjectName("primary")
        b_prm.setToolTip("マスタ.prm を元に Seiban と一部の値を差し替えて "
                         "<軸><Seiban>.prm を出力先へ作成（FANUC）")
        b_prm.clicked.connect(self.make_prm)
        b_db = QtWidgets.QPushButton("パラメータDB…")
        b_db.setToolTip("登録済みの変更を検索・フィルタし、リピート品を瞬時に作成"
                        "（制御/軸の変更可）。セミ/フルも表示")
        b_db.clicked.connect(self.open_db)
        b_view = QtWidgets.QPushButton("パラメータを見る/比較…")
        b_view.setToolTip("BASIC・製品・作成した.prm の中身を番地ごとに一覧。2つ選んで差分比較も")
        b_view.clicked.connect(self._open_viewer)
        b_close = QtWidgets.QPushButton("閉じる")
        b_close.clicked.connect(self.accept)
        # 左＝表示/ツール、右＝出力/作成(主) と分けて見やすく（機能はそのまま）
        for b in (b_reload, b_view, b_db):
            row.addWidget(b)
        row.addStretch(1)
        for b in (b_check, b_file, b_both, b_prm, b_close):
            row.addWidget(b)
        # ボタンはスクロール外＝常に見える位置に固定。ただしボタンが8個あるので、
        # 文字を大きくすると横に並びきらずダイアログごと画面をはみ出す。
        # 主画面のツールバーと同じく、横スクロールに入れて幅を要求させない。
        btn_box = QtWidgets.QWidget()
        btn_box.setLayout(row)
        btn_scroll = QtWidgets.QScrollArea()
        btn_scroll.setWidgetResizable(True)
        btn_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        btn_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        btn_scroll.setWidget(btn_box)
        btn_scroll.setMinimumWidth(280)
        btn_scroll.setFixedHeight(btn_box.sizeHint().height() + 4)
        outer.addWidget(btn_scroll)
        # 長い説明・エラー文のラベルを折り返す。折り返さないと文字を大きくしたとき
        # ダイアログごと横に広がって画面をはみ出す
        wrap_long_labels(self)

        self._all = {}
        self._changes = []
        self.e_basic_dir.editingFinished.connect(self._scan_basic_controllers)
        self._scan_basic_controllers()   # 起動時にBASICの場所から制御装置一覧を作る
        self.reload()

    def showEvent(self, event):
        # あとから setText された長いラベル（エラー文など）にも折り返しを効かせる
        super().showEvent(event)
        wrap_long_labels(self)

    def _scan_basic_controllers(self):
        """制御装置プルダウンを作り直す。マスタがあれば号機一覧（必要容量で絞り込み）、
        無ければ BASICの場所フォルダのファイル名から。"""
        from pathlib import Path
        cur = self.e_master_prm.text().strip()
        cap = self.cmb_cap.currentData() if hasattr(self, "cmb_cap") else ""
        self.cmb_basic.blockSignals(True)
        self.cmb_basic.clear()
        if self._controllers:
            ctls = controllers.filter_by_capacity(self._controllers, cap or "")
            if ctls:
                self.cmb_basic.addItem("（制御装置を選択）", "")
                for c in ctls:
                    self.cmb_basic.addItem(c.label(), c.unit)
            else:
                self.cmb_basic.addItem("（条件に合う制御装置がありません）", "")
        else:
            folder = self.e_basic_dir.text().strip()
            items = []
            if folder and Path(folder).is_dir():
                for p in sorted(Path(folder).iterdir()):
                    if p.is_file() and p.suffix.lower() in seiban_flow.PARAM_EXTS:
                        items.append((nc_param.controller_from_basic(p.name) or p.stem,
                                      str(p)))
            self.cmb_basic.addItem("（制御装置を選択）" if items
                                   else "（BASICの場所にBASICがありません）", "")
            for name, path in items:
                self.cmb_basic.addItem(f"{name}（{Path(path).name}）", path)
            idx = self.cmb_basic.findData(cur) if cur else -1
            if idx >= 0:
                self.cmb_basic.setCurrentIndex(idx)
        self.cmb_basic.blockSignals(False)

    def _on_basic_selected(self, *_):
        """制御装置プルダウンの選択 → 使うBASICへ反映。マスタ運用なら号機からBASICを
        探し、必要容量に合う軸も提案する。"""
        data = self.cmb_basic.currentData()
        if not data:
            return
        if self._controllers:
            ctl = next((c for c in self._controllers if c.unit == data), None)
            path, info = controllers.basic_choice_for_unit(
                self.e_basic_dir.text().strip(), data)
            if path:
                self.e_master_prm.setText(path)
                self._show_basic_origin_hint(path, info)
            else:
                QtWidgets.QMessageBox.information(
                    self, "BASIC",
                    f"号機 {data} のBASICが『BASICの場所』に見つかりません。\n"
                    "『使うBASIC』を手動で指定してください。")
            cap = self.cmb_cap.currentData() or ""
            if ctl and cap:
                axes = ctl.axes_with_capacity(cap)
                if axes:
                    i = self.cmb_axis.findData(nc_param.axis_number(axes[0]))
                    if i >= 0:
                        self.cmb_axis.setCurrentIndex(i)
        else:
            self.e_master_prm.setText(data)
            self._show_basic_origin_hint(data)

    def _browse_master_prm(self):
        start = self.e_master_prm.text() or self.e_basic_dir.text()
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "BASIC .prm（FANUC）", start, "パラメータ (*.prm *.PRM *.DAT *.dat *.txt);;すべて (*.*)")
        if p:
            self.e_master_prm.setText(p)
            self._show_basic_origin_hint(p)

    def _show_basic_origin_hint(self, path, info=None):
        """選ばれたBASICが実機のものかを、その場に出す（黙って選ばない）。

        号機を選べばBASICは自動で決まるが、どれが選ばれてそれが実機のものかは
        画面に出ないと分からない。以前は拡張子で決めていて、F23 のように
        実機の .DAT とPC製の .prm が並んでいると .prm を掴んでいた。
        """
        if info is None:
            try:
                with Path(path).open("rb") as f:
                    info = param_origin.classify(f.read(65536))
            except Exception:
                info = None
        if not info:
            self.lbl_basic_origin.setText("")
            self.lbl_basic_origin.setVisible(False)
            return
        color = {"machine": "#15803d", "converted": "#a16207",
                 "pc": "#b91c1c"}.get(info["verdict"], "#6b7280")
        extra = ("　※実機のバックアップがあるならそちらを使ってください"
                 if info["verdict"] == "pc" else "")
        self.lbl_basic_origin.setText(
            f"<span style='color:{color}'>{Path(path).name} … "
            f"{info['label']}（{info['eob']}）{extra}</span>")
        self.lbl_basic_origin.setVisible(True)

    def _show_basic_origin(self):
        """BASICフォルダの各ファイルが実機のものかPC製かを一覧で出す。"""
        folder = self.e_basic_dir.text().strip()
        if not folder or not Path(folder).is_dir():
            QtWidgets.QMessageBox.warning(
                self, "出どころ判定", "BASICの場所（フォルダ）を指定してください")
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            rows = param_origin.scan_folder(folder)
            # 機械の個体データ（原点・グリッドシフト）が入っているものに印を付ける
            for _name, info, path in rows:
                try:
                    info["individual"] = fanuc_param.individual_data(
                        Path(path).read_bytes().decode("cp932", errors="replace"))
                except Exception:
                    info["individual"] = []
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if not rows:
            QtWidgets.QMessageBox.information(
                self, "出どころ判定", "フォルダにファイルがありません")
            return
        cov = (controllers.basic_coverage(folder, [c.unit for c in self._controllers])
               if self._controllers else None)
        BasicOriginDialog(self, rows, folder, cov).exec()

    def _browse_basic_dir(self):
        p = QtWidgets.QFileDialog.getExistingDirectory(
            self, "BASICの場所（フォルダ）", self.e_basic_dir.text())
        if p:
            self.e_basic_dir.setText(p)
            self._scan_basic_controllers()   # フォルダを選んだら一覧を作り直す

    def _browse_product_dir(self):
        p = QtWidgets.QFileDialog.getExistingDirectory(
            self, "製品データの場所（フォルダ）", self.e_product_dir.text())
        if p:
            self.e_product_dir.setText(p)

    def _browse_product(self):
        start = self.e_product.text() or self.e_product_dir.text()
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "製品データ", start, "パラメータ/CSV (*.prm *.PRM *.csv *.txt);;すべて (*.*)")
        if p:
            self.e_product.setText(p)

    def find_product(self):
        """製品データの場所から、Seiban を名前に含むファイルを探して設定する。"""
        from pathlib import Path
        folder = self.e_product_dir.text().strip()
        seiban = self.e_seiban.text().strip()
        if not folder or not Path(folder).is_dir():
            QtWidgets.QMessageBox.warning(self, "製品データ", "製品データの場所（フォルダ）を指定してください")
            return
        if not seiban:
            QtWidgets.QMessageBox.warning(self, "製品データ", "Seiban を入力してください")
            return
        hits = [p for p in Path(folder).iterdir()
                if p.is_file() and seiban in p.name]
        if not hits:
            QtWidgets.QMessageBox.warning(
                self, "製品データ", f"『{seiban}』を名前に含むファイルが見つかりません:\n{folder}")
            return
        self.e_product.setText(str(hits[0]))
        if len(hits) > 1:
            QtWidgets.QMessageBox.information(
                self, "製品データ",
                f"{len(hits)}件見つかりました。先頭を使用:\n{hits[0].name}")

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

    def _selected_controller(self):
        """制御コンボの選択値（『（すべて）』のときは空文字＝全制御）。"""
        data = self.cmb_ctrl.currentData()
        return data or ""

    def _on_ctrl_changed(self, *_):
        self._apply_filter()

    def reload(self):
        """CSVを読み直し、制御コンボを作り直してから表に反映する。"""
        self._all = {}
        csv_path = self.e_csv.text().strip()
        if csv_path:
            try:
                self._all = nc_param.load_changes(csv_path)
            except Exception as e:
                self.table.setRowCount(0)
                self.lbl.setText(f"変更表CSVを読めません: {e}")
                self._changes = []
                return
        # 型式に対応する制御の一覧でコンボを作り直す（信号は止めて再帰を防ぐ）
        model = self.e_model.text().strip()
        ctrls = nc_param.controllers_for_model(self._all, model) if model else []
        prev = self._selected_controller()
        self.cmb_ctrl.blockSignals(True)
        self.cmb_ctrl.clear()
        self.cmb_ctrl.addItem("（すべて）", "")
        for c in ctrls:
            self.cmb_ctrl.addItem(c, c)
        idx = self.cmb_ctrl.findData(prev)
        self.cmb_ctrl.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_ctrl.blockSignals(False)
        self._apply_filter()

    def _apply_filter(self):
        """選択中の型式・制御で変更を絞り、表に表示する。"""
        self._changes = []
        model = self.e_model.text().strip()
        if not model or not self.e_csv.text().strip():
            self.table.setRowCount(0)
            self.lbl.setText("型式と変更表CSVを指定してください。")
            return
        controller = self._selected_controller()
        self._changes = nc_param.changes_for_model(self._all, model, controller or None)
        rows = nc_param.checklist_rows(self._changes, self._master_table())
        self.table.setRowCount(len(rows))
        for i, (num, axis, old, new, note) in enumerate(rows):
            for j, val in enumerate((num, axis, old, new, note)):
                it = QtWidgets.QTableWidgetItem(str(val))
                if j == 3:
                    it.setForeground(QtGui.QBrush(QtGui.QColor("#dc2626")))
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()
        cname = controller or "全制御"
        if self._changes:
            self.lbl.setText(f"型式『{model}』／{cname}の変更点数: {len(self._changes)} 件")
        else:
            self.lbl.setText(f"型式『{model}』／{cname}に一致する変更がCSVにありません。")

    def _basename(self):
        from .ncsend import _safe_component
        ctrl = self._selected_controller()
        name = (self.e_model.text().strip() or self.machine or "param")
        if ctrl:
            name = f"{name}_{ctrl}"
        name = _safe_component(name)
        return name or "param"

    def _checklist_text(self):
        return nc_param.format_checklist(
            self.e_model.text().strip(), self._changes, self._master_table(),
            date=QtCore.QDate.currentDate().toString("yyyy/MM/dd"),
            controller=self._selected_controller())

    def save_checklist(self):
        if not self._ensure_changes():
            return
        p, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "確認表を保存", f"{self._basename()}_パラメータ確認表.txt",
            "テキスト (*.txt)")
        if not p:
            return
        nc_param.save_checklist(p, self._checklist_text())
        QtWidgets.QMessageBox.information(self, "保存", f"確認表を保存しました:\n{p}")

    def save_param_file(self):
        if not self._ensure_changes():
            return
        p, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "差分（参考）を保存", f"{self._basename()}_差分参考.txt",
            "テキスト (*.txt);;すべて (*.*)")
        if not p:
            return
        nc_param.save_param_file(p, self._changes, controller=self._selected_controller())
        QtWidgets.QMessageBox.information(
            self, "保存",
            f"差分（参考・要検証）を保存しました:\n{p}\n\n"
            "※これは汎用形の参考ファイルです。FANUCはマスタ.prmから"
            "「FANUC .prm 作成」で実ファイルを作ってください。")

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
            prm = d / f"{base}_差分参考.txt"
            nc_param.save_checklist(chk, self._checklist_text())
            nc_param.save_param_file(prm, self._changes,
                                     controller=self._selected_controller())
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "出力に失敗", str(e))
            return
        self._persist()
        QtWidgets.QMessageBox.information(
            self, "出力しました",
            f"出力先に置きました:\n・{chk.name}\n・{prm.name}\n\n"
            "機械側で PWE=1 にして、確認表を見ながら入力してください"
            "（差分ファイルは書式確認後に使用）。")

    def make_prm(self):
        """FANUC: マスタ.prm を元に Seiban と一部の値を差し替えて出力先へ作成する。

        マスタの構造はそのまま（書式バイト一致）。値はCSVの選択中の変更を反映し、
        ファイル名は <Axis><Seiban>.prm（マスタの Axis＝T等＋入力した Seiban）。
        """
        from pathlib import Path
        master_path = self.e_master_prm.text().strip()
        out = self.e_out.text().strip()
        seiban = self.e_seiban.text().strip()
        if not master_path:
            QtWidgets.QMessageBox.warning(self, "FANUC .prm", "BASIC(.prm) を指定してください")
            return
        d = Path(out)
        if not out or not d.is_dir():
            QtWidgets.QMessageBox.warning(
                self, "FANUC .prm", "出力先フォルダ/カードを指定してください（存在する場所）")
            return
        if not seiban:
            QtWidgets.QMessageBox.warning(self, "FANUC .prm", "Seiban（受注伝票番号）を入力してください")
            return
        try:
            raw = param_build.read_master(master_path)  # CRLFを保持して読む（バイト一致）
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "FANUC .prm", f"BASIC を読めません:\n{e}")
            return
        # 使うBASICそのものを先に点検する（作った後ではなく、選んだ時点で気づけるように）
        if not self._check_basic(master_path):
            return
        # 2軸テーブル自動判定: 完成製品.prm が複数軸を変更していれば、傾斜軸・回転軸の
        # 両方を1つのBASICへ入れて1ファイルにする（片軸ずつ別ファイルだと、もう片方が
        # 読込時にBASIC値へ戻ってしまうため、1ファイルに両軸を入れるのが正しい）。
        split = self._product_axis_split(raw)
        if split and len(split[0]) >= 2:
            self._make_prm_multi(raw, master_path, d, seiban, split)
            return
        try:
            values, source = self._product_values(raw)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "製品データ", f"製品データを読めません:\n{e}")
            return
        if not values:
            QtWidgets.QMessageBox.warning(
                self, "FANUC .prm",
                "適用する製品データがありません（製品データ または 型式・制御・変更表CSV を確認）。")
            return
        prefix = self.cmb_prefix.currentData() or "T"
        axis = int(self.cmb_axis.currentData() or 4)
        # 作成前プレビュー（旧値→新値）。中止なら書き込まない
        rows = param_build.preview_rows(raw, values, axis)
        rows += [(num, old, new) for (num, old, new)
                 in param_build.preview_zero_rows(raw, self._zero_params())]
        fname = param_build.filename(prefix, seiban, self._param_ext())
        if not ParamPreviewDialog(
                self, rows,
                subtitle=f"{self.e_model.text().strip() or '—'} → {fname}"
                         f"（{nc_param.axis_name(axis)} 軸）").exec():
            return
        try:
            newtext, missing, fmt = param_build.build_text(
                raw, values, axis, seiban, self._zero_params())
            self._dropped_numbers = []
            newtext = self._confirm_prm(newtext, fmt, fname)
            if newtext is None:
                return
            out_path = d / fname
            param_build.write_text(out_path, newtext, fmt, self._param_eob())
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "FANUC .prm", f"保存に失敗:\n{e}")
            return
        note = (f"BASIC {Path(master_path).name} の {nc_param.axis_name(axis)} 軸を製品値に変更"
                if fmt == "fanuc" else f"{Path(master_path).name} を元に差替え")
        mode, eff = self._detect_mode(raw, values, axis)
        self._persist()
        _log_param_creation(
            self.settings, model=self.e_model.text().strip(),
            kind=nc_param.kind_from_prefix(prefix), mode=mode,
            controller=nc_param.controller_from_basic(master_path),
            axis=nc_param.axis_name(axis), seiban=seiban,
            basic=Path(master_path).name, out=fname,
            applied=len(values) - len(missing), total=len(values))
        # 製品データ（完成済み.prm）から抽出した「変更点」だけをデータベースへ登録し、
        # 次回は同じ型式を製品データ無しでも作れるようにする（source=="diff" のときだけ）。
        reg_msg = ""
        if source == "diff":
            # 製品データ/BASIC がヘッダ＋CSV形式ならヘッダ情報(方向・ギア比・モーター等)も拾う
            hinfo = {}
            ppath = self.e_product.text().strip()
            try:
                if ppath:
                    hinfo = param_build.header_info(
                        Path(ppath).read_text(encoding="cp932", errors="replace"))
                if not hinfo:
                    hinfo = param_build.header_info(raw)
            except Exception:
                hinfo = {}
            reg_msg = self._register_to_db(
                values, mode=mode, seiban=seiban, axis=axis, prefix=prefix,
                basic=Path(master_path).name, header=hinfo)
        msg = (f"FANUC .prm を作成しました:\n・{fname}\n（{note}）"
               f"\n値の反映: {len(values) - len(missing)} / {len(values)} 件")
        if mode:
            msg += f"\nクローズドループ: {mode}クロ（1815={eff}）"
        if missing:
            msg += f"\n⚠ BASICに無い番号（未反映）: {', '.join(missing[:12])}"
            if len(missing) > 12:
                msg += f" 他{len(missing) - 12}件"
        msg += reg_msg
        msg += "\n\n機械側での入力は人が実施（PWE/電源再投入に注意）。"
        QtWidgets.QMessageBox.information(self, "作成しました", msg)

    def _product_axis_split(self, raw):
        """製品データが完成 .prm(FANUC N形式) のとき、BASICとの差分を軸ごとに分けて返す。

        戻り値 (per_axis: {軸番号:{番号:値}}, common:{番号:値})。
        製品データが無い／CSV／ヘッダ＋CSV形式なら None（2軸自動判定の対象外）。
        """
        from pathlib import Path
        path = self.e_product.text().strip()
        if not path or not Path(path).is_file():
            return None
        try:
            text = Path(path).read_text(encoding="cp932", errors="replace")
        except Exception:
            return None
        if not (fanuc_param.looks_like_fanuc_prm(text)
                and fanuc_param.looks_like_fanuc_prm(raw)):
            return None
        return param_build.product_axis_values(raw, text)

    def _make_prm_multi(self, raw, master_path, out_dir, seiban, split):
        """2軸テーブル: 完成製品.prm の差分（複数軸）を 1つのBASICへ入れて1ファイル作る。

        傾斜軸・回転軸の両方を同じ制御装置(BASIC)へ書き込む。作成前プレビューで両軸を
        確認し、作成後は軸ごとに作成ログ＋データベース登録（履歴）を残す。
        """
        from pathlib import Path
        per_axis, common = split
        axes = sorted(per_axis)
        # 出力名は両軸を1ファイルにするので「TR」(傾斜+回転)を既定の頭文字にする
        prefix = "TR"
        fname = param_build.filename(prefix, seiban, self._param_ext())
        axis_label = "＋".join(nc_param.axis_name(a) for a in axes)
        # 作成前プレビュー（軸つき）。中止なら書き込まない
        rows = [(num, nc_param.axis_name(ax) if ax else "共通", old, new)
                for (num, ax, old, new) in param_build.preview_rows_multi(raw, per_axis, common)]
        rows += [(num, "全軸", old, new) for (num, old, new)
                 in param_build.preview_zero_rows(raw, self._zero_params())]
        if not ParamPreviewDialog(
                self, rows,
                subtitle=f"2軸テーブル（{axis_label} 軸を1ファイルへ）  "
                         f"{self.e_model.text().strip() or '—'} → {fname}").exec():
            return
        try:
            text_multi, missing, fmt = param_build.build_text_multi(
                raw, per_axis, common, seiban, self._zero_params())
            self._dropped_numbers = []
            text_multi = self._confirm_prm(text_multi, fmt, fname)
            if text_multi is None:
                return
            out_path = Path(out_dir) / fname
            param_build.write_text(out_path, text_multi, fmt, self._param_eob())
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "FANUC .prm", f"保存に失敗:\n{e}")
            return
        self._persist()
        controller = nc_param.controller_from_basic(master_path)
        model = self.e_model.text().strip()
        total = sum(len(v) for v in per_axis.values()) + len(common)
        applied = total - len(missing)
        # 軸ごとにモード判定・ログ・DB登録（制御/軸が違えば別エントリ＝履歴になる）
        reg_lines = []
        for ax in axes:
            mode, _eff = self._detect_mode(raw, per_axis[ax], ax)
            n_miss = sum(1 for (_m, a) in missing if a == ax)
            _log_param_creation(
                self.settings, model=model, mode=mode,
                controller=controller, axis=nc_param.axis_name(ax),
                seiban=seiban, basic=Path(master_path).name, out=fname,
                applied=len(per_axis[ax]) - n_miss, total=len(per_axis[ax]))
            csv_path = self.e_csv.text().strip()
            if model and csv_path:
                entry = nc_param.ParamEntry(
                    model=model, mode=mode, controller=controller,
                    axis=nc_param.axis_name(ax), seiban=seiban,
                    date=QtCore.QDate.currentDate().toString("yyyy/MM/dd"),
                    basic=Path(master_path).name,
                    items=[(n, v, "") for n, v in per_axis[ax].items() if v != ""])
                try:
                    _, action = nc_param.upsert_entry(csv_path, entry)
                    verb = {"added": "登録", "updated": "更新"}.get(action, "")
                    if verb:
                        reg_lines.append(f"・{nc_param.axis_name(ax)}軸を履歴に{verb}"
                                         f"（{mode}クロ）" if mode else
                                         f"・{nc_param.axis_name(ax)}軸を履歴に{verb}")
                except Exception:
                    pass
        msg = (f"2軸テーブルの FANUC .prm を作成しました:\n・{fname}\n"
               f"（{Path(master_path).name} の {axis_label} 軸を1ファイルに書き込み）\n"
               f"値の反映: {applied} / {total} 件")
        if missing:
            miss = ", ".join(f"{n}({nc_param.axis_name(a)})" if a else str(n)
                             for n, a in missing[:12])
            msg += f"\n⚠ BASICに無い番号（未反映）: {miss}"
        if reg_lines:
            msg += "\nデータベース履歴:\n" + "\n".join(reg_lines)
        msg += ("\n\n※ ファイル名の頭文字は『TR』(傾斜+回転)にしています。"
                "運用名が違う場合は出力後にリネームしてください。"
                "\n機械側での入力は人が実施（PWE/電源再投入に注意）。")
        self.reload()
        QtWidgets.QMessageBox.information(self, "作成しました", msg)

    def _detect_mode(self, raw, values, axis):
        """設定の番号/ビットでクローズドループ種別を判定（'フル'/'セミ'/''）と実効値。"""
        return param_build.detect_mode(
            raw, values, axis,
            number=self.settings.get("closed_loop_number", "1815"),
            bit=int(self.settings.get("closed_loop_bit", 1)),
            full_when=int(self.settings.get("closed_loop_full", 1)))

    def _register_to_db(self, values: dict, *, mode="", seiban="", axis="",
                        prefix="", basic="", header=None) -> str:
        """抽出した変更をデータベース（変更表CSV）へ1エントリとして登録し、結果文を返す。

        型式が空なら登録しない。制御は『使うBASIC』名から推定（無ければ制御プルダウン）。
        header があれば モーター/方向/ギア比 等のヘッダ情報も保存する。
        """
        model = self.e_model.text().strip()
        csv_path = self.e_csv.text().strip()
        if not model:
            return "\n（型式が空のため、データベースへの登録はスキップしました）"
        if not csv_path:
            return "\n（変更表CSVが未指定のため、登録はスキップしました）"
        controller = (nc_param.controller_from_basic(basic) if basic
                      else self._selected_controller())
        h = header or {}
        entry = nc_param.ParamEntry(
            model=model, kind=nc_param.kind_from_prefix(prefix), mode=mode,
            controller=controller, axis=nc_param.axis_name(axis) if axis else "",
            seiban=seiban, date=QtCore.QDate.currentDate().toString("yyyy/MM/dd"),
            basic=basic, motor=h.get("motor", ""), motor_no=h.get("motor_no", ""),
            direction=h.get("direction", ""), gear=h.get("gear", ""),
            items=[(num, val, "") for num, val in values.items() if val != ""])
        try:
            eid, action = nc_param.upsert_entry(csv_path, entry)
        except Exception as e:
            return f"\n⚠ データベースへの登録に失敗: {e}"
        self.reload()
        modestr = f"／{mode}クロ" if mode else ""
        verb = {"added": "新規登録", "updated": "更新", "unchanged": "変更なし"}.get(action, action)
        return (f"\nデータベースへ{verb}: 型式『{model}』{modestr}（ID {eid}）"
                f"\n（次回は製品データ無しでも型式から作れます）")

    def _product_values(self, basic_text: str) -> tuple:
        """製品データ {番号: 値} と、その取得元を返す。戻り値 (values, source)。

        source: "diff"     完成済み.prm と BASIC の差分＝その製品の「変更点」
                "headercsv" ヘッダ＋CSV形式の値一覧（変更点ではなく全値）
                "csv"       製品データがCSV（型式・制御で絞った変更）
                "changes"   製品データ無し → ③の変更表CSVの選択中の変更
        変更表CSVへ登録してよいのは "diff"（本当の変更点）だけ。
        """
        from pathlib import Path
        path = self.e_product.text().strip()
        if not path:
            vals = {c.number: c.value for c in self._changes if c.number and c.value != ""}
            return vals, "changes"
        text = Path(path).read_text(encoding="cp932", errors="replace")
        if fanuc_param.looks_like_fanuc_prm(text):
            # 完成済み製品ファイル → BASICとの差分が「その製品の変更値」
            vals = {num: newv for (num, _lab, _old, newv) in
                    fanuc_param.diff(basic_text, text) if newv is not None}
            return vals, "diff"
        # ヘッダ＋CSV形式（System Version=… / "番号",…）。これは全値なので登録はしない
        try:
            doc = prm_format.parse_prm(text)
            vals = {num: v for (num, v, _jp, _en) in prm_format.iter_params(doc) if v != ""}
            if vals:
                return vals, "headercsv"
        except Exception:
            pass
        # CSV（型式,制御,番号,変更値）
        changes = nc_param.changes_for_model(
            nc_param.parse_changes(text), self.e_model.text().strip(),
            self._selected_controller() or None)
        vals = {c.number: c.value for c in changes if c.number and c.value != ""}
        return vals, "csv"

    def _ensure_changes(self):
        if not self._changes:
            QtWidgets.QMessageBox.warning(
                self, "パラメータ", "出力する変更がありません（型式・CSVを確認）。")
            return False
        return True

    def open_db(self):
        """パラメータ作成データベース（登録済みCSVの検索・リピート作成）を開く。"""
        self._persist()
        ParamDBDialog(self).exec()
        self.reload()  # DB側で登録が増えた場合に表へ反映

    def _open_viewer(self):
        """パラメータ閲覧/比較。A=使うBASIC、B=製品データ を初期値にする。"""
        ParamViewerDialog(self, self.settings,
                          file_a=self.e_master_prm.text().strip(),
                          file_b=self.e_product.text().strip()).exec()

    def _zero_params(self):
        return zero_params_for(self.settings, getattr(self, "chk_zero_indiv", None))

    def _param_ext(self):
        return param_out_ext(self.settings)

    def _param_eob(self):
        return param_eob(self.settings)

    def _reference_backup(self, master_path=None):
        """番号照合に使う参照を (テキスト, 出どころの説明) で返す。無ければ ("", "")。

        探す順:
          1. 選んだBASIC自体が実機のものなら、それが実機のバックアップそのもの
             （番号は定義上一致するので照合は不要）
          2. 同じ号機の実機ファイルが同じフォルダにあれば それ
             （例: F23BASIC.prm を選んだとき隣の F23BASIC.DAT）
          3. 同じ号機の実機が無い（PC側のBASICしか無い）とき: フォルダにある
             実機BASIC全部の「番号の和集合」。どの実機も持っていない番号だけが
             引っかかるので、号機ごとの差でむやみに警告しない
          4. 設定の「マスタ/バックアップ」
        """
        if master_path:
            try:
                p = Path(master_path)
                raw = p.read_bytes()
                if param_origin.classify(raw)["verdict"] in ("machine", "converted"):
                    return "", ""        # 実機そのもの＝照合する相手が要らない
                unit = re.match(r"[A-Za-z]*\d+", p.stem)
                if unit:
                    for sib in sorted(p.parent.iterdir()):
                        if sib == p or not sib.is_file():
                            continue
                        if not sib.stem.upper().startswith(unit.group(0).upper()):
                            continue
                        if param_origin.classify(sib.read_bytes())["verdict"] in (
                                "machine", "converted"):
                            return (sib.read_bytes().decode("cp932", errors="replace"),
                                    f"同じ号機の実機 {sib.name}")
                # 同じ号機の実機が無い＝PC側しか無い場合
                text = raw.decode("cp932", errors="replace")
                ref, used = param_build.machine_reference(p.parent, like=text,
                                                          exclude=p)
                if ref:
                    return ref, (f"フォルダの実機BASIC {len(used)}本の番号を合わせたもの"
                                 f"（{', '.join(used[:3])}{' ほか' if len(used) > 3 else ''}）")
            except Exception:
                pass
        path = str(self.settings.get("param_master_backup") or "").strip()
        if not path:
            return "", ""
        try:
            return (Path(path).read_bytes().decode("cp932", errors="replace"),
                    f"設定のマスタ/バックアップ {Path(path).name}")
        except Exception:
            return "", ""

    def _check_basic(self, master_path):
        """選んだBASICが制御装置向きかを見て、問題があれば確認を取る。

        BASICは元データなので、これ自体を機械へ入れるわけではない。見るのは
        「そこから作る製品ファイルが読めるか」に効く点だけ:
          ・実機が出したものか（PC製は、その制御装置に無い番号が混じることがある）
          ・実機のバックアップと番号がずれていないか
        区切り(EOB)はアプリが出力時に実機の形へそろえるので、ここでは問わない。
        """
        try:
            data = Path(master_path).read_bytes()
        except Exception:
            return True
        info = param_origin.classify(data)
        text = data.decode("cp932", errors="replace")
        problems = []
        # その機械の個体データ（原点・グリッドシフト）が入っていないか。
        # 仕様が同じ号機でも原点は据付けごとに違うので、流用してはいけない。
        # 号機マスタの容量と、ファイルのアンプ最大電流が合っているか。
        # 合わなければ「その号機のファイルではない」か「マスタが古い」。
        unit = param_origin.unit_of(Path(master_path).name)
        ctl = next((c for c in self._controllers
                    if str(c.unit).strip() == unit), None) if unit else None
        cap_ng = controllers.check_capacity_match(
            text, ctl, self.settings.get("amp_current_map"))
        if cap_ng:
            problems.append("号機マスタの容量と合いません（" + " ／ ".join(cap_ng)
                            + "）。実機のファイルが正ならマスタが古いので、"
                              "制御装置マスタの「BASICに合わせる…」で直せます")
        # 個体データ（原点・グリッドシフト）。作成時に0にする設定なら、
        # そちらで直るものは警告しない（自動で直すものを毎回聞かない）。
        indiv = fanuc_param.individual_data(text)
        zp = self._zero_params() or ()
        if zp:
            zero_nums = {fanuc_param._norm_num(x) for x in zp}
            indiv = [x for x in indiv
                     if not any(f"N{n}" in x for n in zero_nums)]
        if indiv:
            problems.append(
                "このBASICには機械の個体データが入っています（"
                + " ／ ".join(indiv)
                + "）。別の号機に使うと原点がずれます")
        if info["verdict"] == "pc":
            problems.append(
                f"このBASICはPCで作られたものです（{info['reasons'][0]}）。"
                "実機が出したバックアップを使う方が確実です")
        ref, how = self._reference_backup(master_path)
        if ref:
            extra = fanuc_param.unknown_numbers(text, ref)
            if extra:
                problems.append(
                    f"実機に無い番号が {len(extra)}個 あります"
                    f"（N{extra[0]:05d}〜N{extra[-1]:05d}／照合元: {how}）。"
                    "作成のときに除くこともできます")
        problems += [p for p in fanuc_param.validate_prm(text)
                     if "実機のバックアップに無い" not in p]
        if not problems:
            return True
        return QtWidgets.QMessageBox.question(
            self, "BASICの点検",
            f"{Path(master_path).name} に気になる点があります:\n\n・"
            + "\n・".join(problems[:6])
            + "\n\nこのBASICで作成を続けますか？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes

    def _confirm_prm(self, text, fmt, fname):
        """書き込む前に点検する。戻り値は「書き出すテキスト」、中止なら None。

        実機に無い番号が見つかったら、その場で除いて作れるようにする。
        実機のバックアップが手元に無く、PC側のBASICしか無くても、
        制御装置が取り込めるファイルを作れるようにするため。
        除くのは「フォルダのどの実機BASICにも無い番号」だけなので、
        その制御装置が持っている番号を落とすことはない。
        """
        if fmt != "fanuc":
            return text
        ref, how = self._reference_backup(self.e_master_prm.text().strip())
        problems = fanuc_param.validate_prm(text, ref)
        if not problems:
            return text
        extra = fanuc_param.unknown_numbers(text, ref) if ref else []
        msg = (f"{fname} に、制御装置が取り込めない可能性のある点があります:\n\n・"
               + "\n・".join(problems[:6]))
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("パラメータの点検")
        box.setIcon(QtWidgets.QMessageBox.Warning)
        if extra:
            box.setText(msg + f"\n\n照合元: {how}")
            b_drop = box.addButton(f"実機に無い{len(extra)}個を除いて作成",
                                   QtWidgets.QMessageBox.AcceptRole)
            b_keep = box.addButton("そのまま作成", QtWidgets.QMessageBox.DestructiveRole)
            box.addButton("中止", QtWidgets.QMessageBox.RejectRole)
            box.setDefaultButton(b_drop)
            box.exec()
            if box.clickedButton() is b_drop:
                kept, removed = fanuc_param.drop_numbers(text, extra)
                self._dropped_numbers = removed
                return kept
            return text if box.clickedButton() is b_keep else None
        box.setText(msg + "\n\nこのまま作成しますか？")
        box.addButton("作成", QtWidgets.QMessageBox.AcceptRole)
        box.addButton("中止", QtWidgets.QMessageBox.RejectRole)
        box.exec()
        return text if box.buttonRole(box.clickedButton()) == \
            QtWidgets.QMessageBox.AcceptRole else None

    def _persist(self):
        try:
            from .settings import save_settings
            self.settings.update(dict(
                param_change_csv=self.e_csv.text().strip(),
                param_master_backup=self.e_master.text().strip(),
                param_master_prm=self.e_master_prm.text().strip(),
                param_basic_dir=self.e_basic_dir.text().strip(),
                param_product_dir=self.e_product_dir.text().strip(),
                param_out_folder=self.e_out.text().strip(),
            ))
            save_settings(self.settings)
        except Exception:
            pass

    def done(self, result):
        """閉じる/OK/×のどれで閉じても、入力した場所(フォルダ等)を必ず保存する。"""
        self._persist()
        super().done(result)


class ParamEntryEditDialog(QtWidgets.QDialog):
    """データベースのエントリを手入力で新規登録／編集する。

    メタ情報（型式・種別・モード・モーター・制御・軸・Seiban・使用BASIC）を入れ、
    番号と変更値を順に足していくと一覧に出る。問題なければOKで確定（呼び出し側が保存）。
    """

    KINDS = ["", "傾斜", "回転"]
    MODES = ["", "フル", "セミ"]

    def __init__(self, parent, entry=None, defaults=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle("エントリの入力／編集")
        fit_to_screen(self, 580, 620)
        self.result_entry = None
        self._existing = existing or []     # 重複チェック用の既存エントリ
        defaults = defaults or {}
        v = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self.e_model = QtWidgets.QLineEdit(entry.model if entry else defaults.get("model", ""))
        form.addRow("型式", self.e_model)
        self.cmb_kind = QtWidgets.QComboBox(); self.cmb_kind.addItems(self.KINDS)
        self.cmb_mode = QtWidgets.QComboBox(); self.cmb_mode.addItems(self.MODES)
        self.e_motor = QtWidgets.QLineEdit(entry.motor if entry else "")
        self.e_motor.setPlaceholderText("モーター型式（任意）")
        self.e_motor_no = QtWidgets.QLineEdit(entry.motor_no if entry else "")
        self.e_motor_no.setPlaceholderText("モーター番号（任意）")
        self.e_direction = QtWidgets.QLineEdit(entry.direction if entry else "")
        self.e_direction.setPlaceholderText("方向（任意）")
        self.e_gear = QtWidgets.QLineEdit(entry.gear if entry else "")
        self.e_gear.setPlaceholderText("ギア比（任意 例 1/36）")
        self.e_ctrl = QtWidgets.QLineEdit(entry.controller if entry else defaults.get("controller", ""))
        self.e_ctrl.setPlaceholderText("制御装置（例 F30）")
        self.cmb_axis = QtWidgets.QComboBox()
        self.cmb_axis.addItems([""] + nc_param.AXIS_NAMES)  # X/Y/Z/A/B/C
        self.e_seiban = QtWidgets.QLineEdit(entry.seiban if entry else "")
        self.e_seiban.setPlaceholderText("受注伝票番号（任意）")
        self.e_basic = QtWidgets.QLineEdit(entry.basic if entry else "")
        self.e_basic.setPlaceholderText("使用BASIC名（任意）")
        if entry:
            self._set_combo(self.cmb_kind, entry.kind)
            self._set_combo(self.cmb_mode, entry.mode)
            self._set_combo(self.cmb_axis, entry.axis)
        kr = QtWidgets.QHBoxLayout(); kr.setContentsMargins(0, 0, 0, 0)
        kr.addWidget(self.cmb_kind); kr.addWidget(QtWidgets.QLabel("モード"))
        kr.addWidget(self.cmb_mode); kr.addWidget(QtWidgets.QLabel("軸")); kr.addWidget(self.cmb_axis)
        form.addRow("種別", kr)
        form.addRow("モーター", self.e_motor)
        mr = QtWidgets.QHBoxLayout(); mr.setContentsMargins(0, 0, 0, 0)
        mr.addWidget(self.e_motor_no, 1)
        mr.addWidget(QtWidgets.QLabel("方向")); mr.addWidget(self.e_direction, 1)
        mr.addWidget(QtWidgets.QLabel("ギア比")); mr.addWidget(self.e_gear, 1)
        form.addRow("モーター番号", mr)
        form.addRow("制御装置", self.e_ctrl)
        form.addRow("Seiban", self.e_seiban)
        form.addRow("使用BASIC", self.e_basic)
        v.addLayout(form)

        # 番号・値の入力（指定番地に値を順に登録 → 一覧に追加）
        inrow = QtWidgets.QHBoxLayout()
        self.e_num = QtWidgets.QLineEdit(); self.e_num.setPlaceholderText("番号 例 1815")
        self.e_val = QtWidgets.QLineEdit(); self.e_val.setPlaceholderText("変更値 例 00100000")
        self.e_memo = QtWidgets.QLineEdit(); self.e_memo.setPlaceholderText("メモ（任意）")
        b_add = QtWidgets.QPushButton("追加")
        b_add.clicked.connect(self._add_row)
        self.e_val.returnPressed.connect(self._add_row)
        for w in (QtWidgets.QLabel("番号"), self.e_num, QtWidgets.QLabel("値"),
                  self.e_val, self.e_memo, b_add):
            inrow.addWidget(w)
        v.addLayout(inrow)

        self.tbl = QtWidgets.QTableWidget(0, 3)
        self.tbl.setHorizontalHeaderLabels(["番号", "変更値", "メモ"])
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.verticalHeader().setVisible(False)
        v.addWidget(self.tbl, 1)
        b_del = QtWidgets.QPushButton("選択行を削除")
        b_del.clicked.connect(self._del_row)
        v.addWidget(b_del, 0, QtCore.Qt.AlignLeft)
        if entry:
            for (n, val, memo) in entry.items:
                self._append(n, val, memo)

        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._entry_id = entry.id if entry else ""

    def _set_combo(self, cmb, text):
        i = cmb.findText(text or "")
        cmb.setCurrentIndex(i if i >= 0 else 0)

    def _append(self, num, val, memo=""):
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        for c, t in enumerate((num, val, memo)):
            self.tbl.setItem(r, c, QtWidgets.QTableWidgetItem(str(t)))

    def _add_row(self):
        num = self.e_num.text().strip()
        if not num:
            return
        self._append(num, self.e_val.text().strip(), self.e_memo.text().strip())
        self.e_num.clear(); self.e_val.clear(); self.e_memo.clear()
        self.e_num.setFocus()

    def _del_row(self):
        r = self.tbl.currentRow()
        if r >= 0:
            self.tbl.removeRow(r)

    def _accept(self):
        # 入力欄に残った1件も取り込む
        if self.e_num.text().strip():
            self._add_row()
        model = self.e_model.text().strip()
        items = []
        for r in range(self.tbl.rowCount()):
            num = self.tbl.item(r, 0).text().strip() if self.tbl.item(r, 0) else ""
            val = self.tbl.item(r, 1).text().strip() if self.tbl.item(r, 1) else ""
            memo = self.tbl.item(r, 2).text().strip() if self.tbl.item(r, 2) else ""
            if num:
                items.append((num, val, memo))
        if not model:
            QtWidgets.QMessageBox.warning(self, "入力", "型式を入力してください")
            return
        if not items:
            QtWidgets.QMessageBox.warning(self, "入力", "番号と変更値を1件以上入れてください")
            return
        # 形式チェック（番号は数字、値はビット列/数値らしいか）。怪しければ警告して続行可
        bad = []
        for (num, val, _m) in items:
            if not re.fullmatch(r"\d{1,5}", num):
                bad.append(f"番号『{num}』は数字ではありません")
            elif val and not self._looks_value(val):
                bad.append(f"番号 {num} の値『{val}』が数値/ビット列に見えません")
        if bad:
            msg = "次の入力を確認してください:\n・" + "\n・".join(bad[:8])
            if len(bad) > 8:
                msg += f"\n…他{len(bad) - 8}件"
            msg += "\n\nこのまま登録しますか？"
            if QtWidgets.QMessageBox.question(self, "入力チェック", msg) != \
                    QtWidgets.QMessageBox.Yes:
                return
        entry = nc_param.ParamEntry(
            model=model, kind=self.cmb_kind.currentText(),
            mode=self.cmb_mode.currentText(), motor=self.e_motor.text().strip(),
            motor_no=self.e_motor_no.text().strip(),
            direction=self.e_direction.text().strip(), gear=self.e_gear.text().strip(),
            controller=self.e_ctrl.text().strip(), axis=self.cmb_axis.currentText(),
            seiban=self.e_seiban.text().strip(),
            date=QtCore.QDate.currentDate().toString("yyyy/MM/dd"),
            basic=self.e_basic.text().strip(), items=items, id=self._entry_id)
        # 重複（同じ 型式・モード・制御・軸・Seiban）が別IDで既にあれば確認
        dup = next((e for e in self._existing
                    if e.config_key() == entry.config_key() and e.id != self._entry_id), None)
        if dup:
            if QtWidgets.QMessageBox.question(
                    self, "重複の確認",
                    f"同じ構成（型式『{entry.model}』{('／'+entry.mode+'クロ') if entry.mode else ''}"
                    f"／制御 {entry.controller or '—'}／{entry.axis or '—'} 軸"
                    f"／Seiban {entry.seiban or '—'}）が既に登録されています（ID {dup.id}）。\n"
                    "上書き更新しますか？") != QtWidgets.QMessageBox.Yes:
                return
        self.result_entry = entry
        self.accept()

    @staticmethod
    def _looks_value(v: str) -> bool:
        """値がビット列(0/1) か 符号付き数値 らしいか。先頭の ' は許容。"""
        s = v.lstrip("'").strip()
        if not s:
            return True
        if re.fullmatch(r"[01]{1,8}", s):
            return True
        return bool(re.fullmatch(r"[-+]?\d+(\.\d+)?", s))


class TwoAxisCreateDialog(QtWidgets.QDialog):
    """2軸テーブル: 傾斜エントリ＋回転エントリの2つを選び、両軸を1ファイルに作る。

    1つの制御装置(BASIC)に傾斜軸・回転軸の両方を入れて 1つの .prm を出力する。
    （フルbackupを軸ごとに2ファイル作ると、片方の読込でもう片方がBASIC値に戻るため、
    1ファイルに両軸を入れるのが正しい。）OKで (傾斜軸番号, 回転軸番号, 頭文字) を返す。
    """

    def __init__(self, parent, primary, partner_entries):
        super().__init__(parent)
        self.setWindowTitle("2軸テーブルで作成（傾斜＋回転）")
        fit_to_screen(self, 560, 280)
        self.partner = None
        self._partners = list(partner_entries)
        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(QtWidgets.QLabel(
            "1つの制御装置(BASIC)に2軸ぶんを入れて1ファイルにします。\n"
            "選択中エントリ＝1軸目、相手エントリ＝2軸目。各軸の番号を確認してください。"))
        form = QtWidgets.QFormLayout()
        # 1軸目（選択中エントリ）
        self.lbl_primary = QtWidgets.QLabel(self._entry_label(primary))
        self.lbl_primary.setWordWrap(True)
        form.addRow("1軸目（選択中）", self.lbl_primary)
        self.cmb_axis1 = QtWidgets.QComboBox()
        for n in range(1, 7):
            self.cmb_axis1.addItem(f"{nc_param.axis_name(n)}（第{n}軸）", n)
        self._set_axis(self.cmb_axis1, primary.axis, default=4)
        form.addRow("1軸目の軸", self.cmb_axis1)
        # 2軸目（相手エントリ）
        self.cmb_partner = QtWidgets.QComboBox()
        for e in self._partners:
            self.cmb_partner.addItem(self._entry_label(e), e.id)
        self.cmb_partner.currentIndexChanged.connect(self._on_partner_changed)
        form.addRow("2軸目（相手）", self.cmb_partner)
        self.cmb_axis2 = QtWidgets.QComboBox()
        for n in range(1, 7):
            self.cmb_axis2.addItem(f"{nc_param.axis_name(n)}（第{n}軸）", n)
        form.addRow("2軸目の軸", self.cmb_axis2)
        v.addLayout(form)
        self._primary = primary
        self._on_partner_changed()
        bb = QtWidgets.QDialogButtonBox()
        ok = bb.addButton("この2軸で作成", QtWidgets.QDialogButtonBox.AcceptRole)
        ok.setObjectName("primary")
        bb.addButton("中止", QtWidgets.QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    @staticmethod
    def _entry_label(e):
        bits = [e.model or "—"]
        if e.kind:
            bits.append(e.kind)
        if e.mode:
            bits.append(e.mode + "クロ")
        if e.axis:
            bits.append(e.axis + "軸")
        if e.seiban:
            bits.append("Seiban " + e.seiban)
        return f"ID {e.id}: " + " / ".join(bits) + f"（{e.count()}件）"

    @staticmethod
    def _set_axis(cmb, axis_name, default=4):
        n = nc_param.axis_number(axis_name) or default
        i = cmb.findData(n)
        cmb.setCurrentIndex(i if i >= 0 else default - 1)

    def _on_partner_changed(self, *_):
        e = self._current_partner()
        if e:
            self._set_axis(self.cmb_axis2, e.axis, default=4)

    def _current_partner(self):
        pid = self.cmb_partner.currentData()
        return next((e for e in self._partners if e.id == pid), None)

    def _accept(self):
        partner = self._current_partner()
        if not partner:
            QtWidgets.QMessageBox.warning(self, "2軸作成", "相手エントリを選んでください")
            return
        a1 = int(self.cmb_axis1.currentData())
        a2 = int(self.cmb_axis2.currentData())
        if a1 == a2:
            QtWidgets.QMessageBox.warning(
                self, "2軸作成", "1軸目と2軸目に同じ軸は指定できません。別の軸にしてください。")
            return
        self.partner = partner
        self.axis1, self.axis2 = a1, a2
        # 頭文字: 種別から T(傾斜)/R(回転) を組み立て、決まらなければ TR
        kinds = {"傾斜": "T", "回転": "R"}
        p1 = kinds.get(self._primary.kind, "")
        p2 = kinds.get(partner.kind, "")
        self.prefix = (p1 + p2) if (p1 and p2 and p1 != p2) else "TR"
        self.accept()


class ParamDBDialog(QtWidgets.QDialog):
    """パラメータ作成データベース（エントリ単位）。

    登録済みのエントリ（型式・種別・モード・モーター・制御・軸・Seiban・日付・使用BASIC
    ＋変更パラメータ）を検索・フィルタし、選んだエントリからリピート品を作成する
    （制御＝BASICや軸を変更可、作成すると履歴として登録）。新規手入力・編集・削除、
    制御装置からの吸い出し差分登録もできる。セミ/フルは 1815 で判定して表示。
    2軸テーブルは傾斜＋回転の2エントリを選んで1ファイルにまとめて作成できる。
    """

    COLS = ["ID", "型式", "種別", "モード", "モーター", "モーター番号", "方向",
            "ギア比", "制御", "軸", "Seiban", "登録日", "件数", "使用BASIC"]

    def _param_ext(self):
        return param_out_ext(self.settings)

    def _param_eob(self):
        return param_eob(self.settings)

    def _zero_params(self):
        return zero_params_for(self.settings, getattr(self, "chk_zero_indiv", None))

    def __init__(self, owner: "ParamDialog"):
        super().__init__(owner)
        self.owner = owner
        self.settings = owner.settings
        self.setWindowTitle("パラメータ作成データベース")
        fit_to_screen(self, 1060, 680)
        self._entries = []
        v = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "登録済みの作成履歴から<b>リピート作成</b>する画面です。"
            "上で<b>検索／絞り込み</b> → 下の表で<b>1件選ぶ</b> → 下部のボタンで作成。"
            "（同じ制御装置なら『前回と同じ…』、別号機なら『制御装置を選んで…』、2軸は『2軸で作成』）")
        intro.setTextFormat(QtCore.Qt.RichText); intro.setWordWrap(True)
        intro.setStyleSheet("background:#eff6ff; color:#1d4ed8; padding:6px; border-radius:4px;")
        v.addWidget(intro)

        # --- 検索・フィルタ ---
        filt = QtWidgets.QHBoxLayout()
        self.e_search = QtWidgets.QLineEdit()
        self.e_search.setPlaceholderText("検索（型式・制御・モーター・Seiban・番号などを横断）")
        self.e_search.textChanged.connect(self._refresh_table)
        filt.addWidget(self.e_search, 1)
        filt.addWidget(QtWidgets.QLabel("制御"))
        self.cmb_fctrl = QtWidgets.QComboBox()
        self.cmb_fctrl.currentIndexChanged.connect(self._refresh_table)
        filt.addWidget(self.cmb_fctrl)
        filt.addWidget(QtWidgets.QLabel("モード"))
        self.cmb_fmode = QtWidgets.QComboBox()
        self.cmb_fmode.addItem("（すべて）", ""); self.cmb_fmode.addItem("フルクロ", "フル")
        self.cmb_fmode.addItem("セミクロ", "セミ")
        self.cmb_fmode.currentIndexChanged.connect(self._refresh_table)
        filt.addWidget(self.cmb_fmode)
        filt.addWidget(QtWidgets.QLabel("種別"))
        self.cmb_fkind = QtWidgets.QComboBox()
        self.cmb_fkind.addItem("（すべて）", ""); self.cmb_fkind.addItem("傾斜", "傾斜")
        self.cmb_fkind.addItem("回転", "回転")
        self.cmb_fkind.currentIndexChanged.connect(self._refresh_table)
        filt.addWidget(self.cmb_fkind)
        b_reload = QtWidgets.QPushButton("再読込"); b_reload.clicked.connect(self.reload)
        filt.addWidget(b_reload)
        v.addLayout(filt)

        # --- 一覧（左）と選択エントリの中身（右） ---
        split = QtWidgets.QHBoxLayout()
        self.tbl = QtWidgets.QTableWidget(0, len(self.COLS))
        self.tbl.setHorizontalHeaderLabels(self.COLS)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl.setSortingEnabled(True)
        self.tbl.itemSelectionChanged.connect(self._on_select)
        split.addWidget(self.tbl, 3)
        self.detail = QtWidgets.QTableWidget(0, 3)
        self.detail.setHorizontalHeaderLabels(["番号", "変更値", "メモ"])
        self.detail.verticalHeader().setVisible(False)
        self.detail.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.detail.horizontalHeader().setStretchLastSection(True)
        split.addWidget(self.detail, 2)
        v.addLayout(split, 1)

        # --- エントリ操作 ---
        erow = QtWidgets.QHBoxLayout()
        for label, slot in (("新規入力…", self.new_entry), ("編集…", self.edit_entry),
                            ("複製…", self.dup_entry), ("削除", self.delete_entry),
                            ("作成ログ…", self.show_log)):
            b = QtWidgets.QPushButton(label); b.clicked.connect(slot)
            erow.addWidget(b)
        erow.addStretch(1)
        v.addLayout(erow)

        # --- リピート作成（制御＝BASIC・軸を変更可。作成すると履歴に登録） ---
        box = QtWidgets.QGroupBox("選択エントリでリピート作成（制御＝BASIC・軸の変更可。作成＝履歴登録）")
        f = QtWidgets.QFormLayout(box)
        self.e_basic = QtWidgets.QLineEdit(str(owner.e_master_prm.text()))
        self.e_basic.setPlaceholderText("使うBASIC .prm（別の制御装置にするなら差し替える）")
        # 制御装置を BASICの場所 から一覧化して選ぶ（別の制御装置に変えて作成しやすく）
        self.cmb_basic = QtWidgets.QComboBox()
        self.cmb_basic.setToolTip("BASICの場所フォルダの制御装置一覧。選ぶと使うBASICが変わる")
        self.cmb_basic.currentIndexChanged.connect(self._on_db_basic_selected)
        f.addRow("制御装置（一覧）", self.cmb_basic)
        f.addRow("使うBASIC", owner._with_browse(self.e_basic, self._browse_basic))
        self._fill_basic_combo()
        axr = QtWidgets.QHBoxLayout(); axr.setContentsMargins(0, 0, 0, 0)
        self.cmb_axis = QtWidgets.QComboBox()
        for n in range(1, 7):
            self.cmb_axis.addItem(f"{nc_param.axis_name(n)}（第{n}軸）", n)
        self.cmb_axis.setCurrentIndex(owner.cmb_axis.currentIndex())
        axr.addWidget(self.cmb_axis, 1)
        axr.addWidget(QtWidgets.QLabel("頭文字"))
        self.cmb_prefix = QtWidgets.QComboBox()
        self.cmb_prefix.addItem("T（傾斜）", "T"); self.cmb_prefix.addItem("R（回転）", "R")
        self.cmb_prefix.setCurrentIndex(owner.cmb_prefix.currentIndex())
        axr.addWidget(self.cmb_prefix)
        f.addRow("対象軸", axr)
        self.e_seiban = QtWidgets.QLineEdit()
        self.e_seiban.setPlaceholderText("新しい受注伝票番号（出力名 <頭文字><Seiban>.prm）")
        f.addRow("Seiban", self.e_seiban)
        self.e_out = QtWidgets.QLineEdit(str(owner.e_out.text()))
        self.e_out.setPlaceholderText(r"出力先（カード E:\ や LAN共有）")
        f.addRow("出力先", owner._with_browse(self.e_out, self._browse_out))
        v.addWidget(box)

        # --- 制御装置から吸い出して差分登録（セミ等で製品データ無し） ---
        dbox = QtWidgets.QGroupBox("制御装置から吸い出して差分だけ登録（上の『使うBASIC』と比較）")
        df = QtWidgets.QFormLayout(dbox)
        self.e_dump = QtWidgets.QLineEdit()
        self.e_dump.setPlaceholderText("機械から吸い出したパラメータファイル（.prm/.txt）")
        df.addRow("吸い出しファイル", owner._with_browse(self.e_dump, self._browse_dump))
        drow = QtWidgets.QHBoxLayout(); drow.setContentsMargins(0, 0, 0, 0)
        self.e_dmodel = QtWidgets.QLineEdit(str(owner.e_model.text()))
        self.e_dmodel.setPlaceholderText("型式")
        self.e_dseiban = QtWidgets.QLineEdit(); self.e_dseiban.setPlaceholderText("Seiban（任意）")
        drow.addWidget(QtWidgets.QLabel("型式")); drow.addWidget(self.e_dmodel, 1)
        drow.addWidget(QtWidgets.QLabel("Seiban")); drow.addWidget(self.e_dseiban, 1)
        df.addRow("登録先", drow)
        v.addWidget(dbox)

        self.lbl = QtWidgets.QLabel("")
        v.addWidget(self.lbl)

        row = QtWidgets.QHBoxLayout()
        b_repeat = QtWidgets.QPushButton("前回と同じ制御装置で即作成")
        b_repeat.setToolTip("選択エントリに登録された制御装置(BASIC)・軸をそのまま使い、"
                            "Seibanだけ変えて即作成（リピート品の最短手順）")
        b_repeat.clicked.connect(self.repeat_same_controller)
        b_make = QtWidgets.QPushButton("制御装置を選んで作成→出力先")
        b_make.setObjectName("primary"); b_make.clicked.connect(self.make_from_entry)
        b_make2 = QtWidgets.QPushButton("2軸で作成（傾斜＋回転）…")
        b_make2.setToolTip("2軸テーブル用。選択中エントリ＋相手エントリの2軸を、"
                           "1つのBASICへ入れて1ファイルにまとめて作成します")
        b_make2.clicked.connect(self.make_two_axis)
        b_dump = QtWidgets.QPushButton("吸い出し差分を登録")
        b_dump.clicked.connect(self.register_dump)
        b_close = QtWidgets.QPushButton("閉じる"); b_close.clicked.connect(self.accept)
        row.addWidget(b_repeat); row.addWidget(b_make); row.addWidget(b_make2)
        row.addWidget(b_dump)
        row.addStretch(1); row.addWidget(b_close)
        v.addLayout(row)

        self.reload()

    # ----- 読み込み・表示 -----
    def _csv_path(self):
        return self.owner.e_csv.text().strip()

    def reload(self):
        path = self._csv_path()
        self._entries = []
        if path:
            try:
                self._entries = nc_param.load_entries(path)
            except Exception as e:
                self.lbl.setText(f"データベースを読めません: {e}")
        prev = self.cmb_fctrl.currentData()
        self.cmb_fctrl.blockSignals(True)
        self.cmb_fctrl.clear(); self.cmb_fctrl.addItem("（すべて）", "")
        for c in sorted({e.controller for e in self._entries if e.controller}):
            self.cmb_fctrl.addItem(c, c)
        i = self.cmb_fctrl.findData(prev)
        self.cmb_fctrl.setCurrentIndex(i if i >= 0 else 0)
        self.cmb_fctrl.blockSignals(False)
        self._refresh_table()

    def _filtered(self):
        return nc_param.search_entries(
            self._entries, self.e_search.text(),
            controller=self.cmb_fctrl.currentData() or "",
            mode=self.cmb_fmode.currentData() or "",
            kind=self.cmb_fkind.currentData() or "")

    def _refresh_table(self):
        self.tbl.setSortingEnabled(False)
        rows = self._filtered()
        self._rows = rows
        self.tbl.setRowCount(len(rows))
        for i, e in enumerate(rows):
            cells = [e.id, e.model, e.kind, e.mode or "—", e.motor, e.motor_no,
                     e.direction, e.gear, e.controller, e.axis, e.seiban, e.date,
                     str(e.count()), e.basic]
            for j, text in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(text)
                if j == 3 and e.mode == "フル":
                    it.setForeground(QtGui.QBrush(QtGui.QColor("#1d4ed8")))
                elif j == 3 and e.mode == "セミ":
                    it.setForeground(QtGui.QBrush(QtGui.QColor("#b45309")))
                self.tbl.setItem(i, j, it)
        self.tbl.resizeColumnsToContents()
        self.tbl.setSortingEnabled(True)
        self.lbl.setText(f"{len(rows)} 件（全 {len(self._entries)} エントリ）")
        self.detail.setRowCount(0)

    def _selected_entry(self):
        r = self.tbl.currentRow()
        if r < 0:
            return None
        idcell = self.tbl.item(r, 0)         # 並べ替えに強いよう ID で引く
        if not idcell:
            return None
        return nc_param.get_entry(self._entries, idcell.text())

    def _on_select(self):
        e = self._selected_entry()
        self.detail.setRowCount(0)
        if not e:
            return
        self.detail.setRowCount(len(e.items))
        for i, (num, val, memo) in enumerate(e.items):
            self.detail.setItem(i, 0, QtWidgets.QTableWidgetItem(num))
            self.detail.setItem(i, 1, QtWidgets.QTableWidgetItem(val))
            self.detail.setItem(i, 2, QtWidgets.QTableWidgetItem(memo))
        self.e_dmodel.setText(e.model)
        # 作成パネルの軸・頭文字をエントリに合わせる（変更は自由）
        num = nc_param.axis_number(e.axis)
        i = self.cmb_axis.findData(num)
        if i >= 0:
            self.cmb_axis.setCurrentIndex(i)
        if e.kind in ("傾斜", "回転"):
            j = self.cmb_prefix.findData("T" if e.kind == "傾斜" else "R")
            if j >= 0:
                self.cmb_prefix.setCurrentIndex(j)

    # ----- 参照 -----
    def _fill_basic_combo(self):
        """BASICの場所（親画面の設定）から制御装置を一覧化してプルダウンに入れる。"""
        from pathlib import Path
        folder = self.owner.e_basic_dir.text().strip()
        cur = self.e_basic.text().strip()
        items = []
        if folder and Path(folder).is_dir():
            for p in sorted(Path(folder).iterdir()):
                if p.is_file() and p.suffix.lower() in seiban_flow.PARAM_EXTS:
                    items.append((nc_param.controller_from_basic(p.name) or p.stem, str(p)))
        self.cmb_basic.blockSignals(True)
        self.cmb_basic.clear()
        self.cmb_basic.addItem("（制御装置を選択）" if items
                               else "（BASICの場所にBASICがありません）", "")
        for name, path in items:
            self.cmb_basic.addItem(f"{name}（{Path(path).name}）", path)
        idx = self.cmb_basic.findData(cur) if cur else -1
        if idx >= 0:
            self.cmb_basic.setCurrentIndex(idx)
        self.cmb_basic.blockSignals(False)

    def _on_db_basic_selected(self, *_):
        path = self.cmb_basic.currentData()
        if path:
            self.e_basic.setText(path)

    def _browse_basic(self):
        start = self.e_basic.text() or self.owner.e_basic_dir.text()
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "使うBASIC .prm", start, "パラメータ (*.prm *.PRM *.DAT *.dat *.txt);;すべて (*.*)")
        if p:
            self.e_basic.setText(p)

    def _browse_out(self):
        p = QtWidgets.QFileDialog.getExistingDirectory(self, "出力先", self.e_out.text())
        if p:
            self.e_out.setText(p)

    def _browse_dump(self):
        start = self.e_dump.text() or self.owner.e_product_dir.text()
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "吸い出しパラメータ", start,
            "パラメータ (*.prm *.PRM *.txt *.csv);;すべて (*.*)")
        if p:
            self.e_dump.setText(p)

    # ----- 新規・編集・複製・削除 -----
    def _require_csv(self):
        if not self._csv_path():
            QtWidgets.QMessageBox.warning(self, "DB", "変更表CSV（保存先）が未指定です")
            return False
        return True

    def _backup_csv(self):
        from pathlib import Path
        import shutil
        p = Path(self._csv_path())
        if p.exists():
            try:
                shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))
            except Exception:
                pass

    def new_entry(self):
        if not self._require_csv():
            return
        dlg = ParamEntryEditDialog(self, defaults={
            "model": self.owner.e_model.text().strip(),
            "controller": self.owner._selected_controller()},
            existing=self._entries)
        if dlg.exec() and dlg.result_entry:
            self._backup_csv()
            eid, action = nc_param.upsert_entry(self._csv_path(), dlg.result_entry)
            self.reload()
            QtWidgets.QMessageBox.information(self, "登録", f"エントリを{('追加' if action=='added' else '更新')}しました（ID {eid}）")

    def edit_entry(self):
        e = self._selected_entry()
        if not e:
            QtWidgets.QMessageBox.warning(self, "編集", "エントリを選んでください")
            return
        dlg = ParamEntryEditDialog(self, entry=e, existing=self._entries)
        if dlg.exec() and dlg.result_entry:
            self._backup_csv()
            nc_param.update_entry(self._csv_path(), dlg.result_entry)
            self.reload()

    def dup_entry(self):
        e = self._selected_entry()
        if not e:
            QtWidgets.QMessageBox.warning(self, "複製", "複製元のエントリを選んでください")
            return
        copy = nc_param.ParamEntry(
            model=e.model, kind=e.kind, mode=e.mode, motor=e.motor,
            motor_no=e.motor_no, direction=e.direction, gear=e.gear,
            controller=e.controller, axis=e.axis, seiban="", basic=e.basic,
            items=list(e.items))      # idは空・Seiban空で新規あつかい
        dlg = ParamEntryEditDialog(self, entry=copy, existing=self._entries)
        if dlg.exec() and dlg.result_entry:
            self._backup_csv()
            nc_param.upsert_entry(self._csv_path(), dlg.result_entry)
            self.reload()

    def delete_entry(self):
        e = self._selected_entry()
        if not e:
            QtWidgets.QMessageBox.warning(self, "削除", "削除するエントリを選んでください")
            return
        if QtWidgets.QMessageBox.question(
                self, "削除の確認",
                f"型式『{e.model}』{('／'+e.mode+'クロ') if e.mode else ''} "
                f"(ID {e.id}, {e.count()}件) を削除しますか？\n"
                "（削除前に .bak バックアップを作成します）"
        ) != QtWidgets.QMessageBox.Yes:
            return
        self._backup_csv()
        nc_param.delete_entry(self._csv_path(), e.id)
        self.reload()

    def show_log(self):
        """作成ログ（追記式の厳密な履歴）を開く。"""
        path = self.settings.get("param_log_csv", "")
        p = Path(path)
        if path and not p.is_absolute():
            from .settings import app_dir
            p = app_dir() / p
        ParamLogDialog(self, str(p)).exec()

    # ----- リピート作成（作成すると履歴に登録） -----
    def repeat_same_controller(self):
        """リピート最短手順: 選択エントリの制御装置(BASIC)・軸をそのまま使い即作成。

        登録された使用BASIC名を『BASICの場所』から解決して使うBASICへ入れ、軸・頭文字も
        エントリの値に合わせてから、通常の作成（make_from_entry）を呼ぶ。"""
        from pathlib import Path
        e = self._selected_entry()
        if not e:
            QtWidgets.QMessageBox.warning(self, "作成", "エントリを選んでください")
            return
        # 使用BASIC名 → BASICの場所 で実ファイルへ解決（無ければ今の『使うBASIC』のまま）
        if e.basic:
            bd = str(self.settings.get("param_basic_dir", "") or "")
            cand = Path(bd, e.basic) if bd else None
            if cand and cand.is_file():
                self.e_basic.setText(str(cand))
            elif not self.e_basic.text().strip():
                QtWidgets.QMessageBox.information(
                    self, "作成",
                    f"登録BASIC『{e.basic}』が『BASICの場所』に見つかりません。\n"
                    "『使うBASIC』を指定してください。")
                return
        # 軸・頭文字をエントリに合わせる
        if e.axis:
            i = self.cmb_axis.findData(nc_param.axis_number(e.axis))
            if i >= 0:
                self.cmb_axis.setCurrentIndex(i)
        pfx = seiban_flow.KIND_PREFIX.get(e.kind, "")
        if pfx:
            j = self.cmb_prefix.findData(pfx)
            if j >= 0:
                self.cmb_prefix.setCurrentIndex(j)
        self.make_from_entry()

    def make_from_entry(self):
        from pathlib import Path
        e = self._selected_entry()
        if not e:
            QtWidgets.QMessageBox.warning(self, "作成", "エントリを選んでください")
            return
        master = self.e_basic.text().strip()
        out = self.e_out.text().strip()
        seiban = self.e_seiban.text().strip()
        if not master:
            QtWidgets.QMessageBox.warning(self, "作成", "使うBASIC を指定してください")
            return
        if not out or not Path(out).is_dir():
            QtWidgets.QMessageBox.warning(self, "作成", "出力先（存在するフォルダ）を指定してください")
            return
        if not seiban:
            QtWidgets.QMessageBox.warning(self, "作成", "Seiban（受注伝票番号）を入力してください")
            return
        values = e.values()
        if not values:
            QtWidgets.QMessageBox.warning(self, "作成", "このエントリに変更値がありません")
            return
        axis = int(self.cmb_axis.currentData() or 4)
        prefix = self.cmb_prefix.currentData() or "T"
        # 作成前プレビュー（旧値→新値）。中止なら作らない
        try:
            raw = param_build.read_master(master)
        except Exception as ex:
            QtWidgets.QMessageBox.warning(self, "作成", f"BASIC を読めません:\n{ex}")
            return
        rows = param_build.preview_rows(raw, values, axis)
        fname = param_build.filename(prefix, seiban, self._param_ext())
        if not ParamPreviewDialog(
                self, rows,
                subtitle=f"{e.model} → {fname}（{nc_param.axis_name(axis)} 軸）").exec():
            return
        try:
            out_path, missing, fmt = param_build.create_file(
                master, out, values, axis=axis, prefix=prefix, seiban=seiban,
                ext=self._param_ext(), eob=self._param_eob(),
                zero_params=self._zero_params())
        except Exception as ex:
            QtWidgets.QMessageBox.warning(self, "作成に失敗", str(ex))
            return
        _log_param_creation(
            self.settings, model=e.model, kind=nc_param.kind_from_prefix(prefix),
            mode=e.mode, motor=e.motor,
            controller=nc_param.controller_from_basic(master),
            axis=nc_param.axis_name(axis), seiban=seiban, basic=Path(master).name,
            out=out_path.name, applied=len(values) - len(missing), total=len(values))
        # 作成＝履歴登録（制御/軸/Seibanが変われば別エントリとして残る）
        reg = ""
        if self._csv_path():
            hist = nc_param.ParamEntry(
                model=e.model, kind=nc_param.kind_from_prefix(prefix), mode=e.mode,
                motor=e.motor, controller=nc_param.controller_from_basic(master),
                axis=nc_param.axis_name(axis), seiban=seiban,
                date=QtCore.QDate.currentDate().toString("yyyy/MM/dd"),
                basic=Path(master).name, items=list(e.items))
            try:
                _, action = nc_param.upsert_entry(self._csv_path(), hist)
                self.reload()
                reg = f"\n履歴に{('登録' if action=='added' else '更新')}（制御 {hist.controller}／{hist.axis}）"
            except Exception:
                reg = "\n（履歴登録に失敗しました）"
        msg = (f"作成しました:\n・{out_path.name}\n"
               f"（{Path(master).name} の {nc_param.axis_name(axis)} 軸へ {e.model}"
               f"{'／' + e.mode + 'クロ' if e.mode else ''} の設定を適用）\n"
               f"値の反映: {len(values) - len(missing)} / {len(values)} 件{reg}")
        if missing:
            msg += f"\n⚠ BASICに無い番号（未反映）: {', '.join(missing[:12])}"
        msg += "\n\n機械側での入力は人が実施（PWE/電源再投入に注意）。"
        QtWidgets.QMessageBox.information(self, "作成しました", msg)

    def make_two_axis(self):
        """2軸テーブル: 選択エントリ＋相手エントリの2軸を、1つのBASICへ入れて1ファイルに作る。"""
        from pathlib import Path
        e = self._selected_entry()
        if not e:
            QtWidgets.QMessageBox.warning(self, "2軸作成", "1軸目のエントリを選んでください")
            return
        partners = [x for x in self._entries if x.id != e.id and x.values()]
        if not partners:
            QtWidgets.QMessageBox.warning(
                self, "2軸作成",
                "相手（2軸目）にできるエントリがありません。\n"
                "傾斜と回転の両方をデータベースに登録してから実行してください。")
            return
        master = self.e_basic.text().strip()
        out = self.e_out.text().strip()
        seiban = self.e_seiban.text().strip()
        if not master:
            QtWidgets.QMessageBox.warning(self, "2軸作成", "使うBASIC を指定してください")
            return
        if not out or not Path(out).is_dir():
            QtWidgets.QMessageBox.warning(self, "2軸作成", "出力先（存在するフォルダ）を指定してください")
            return
        if not seiban:
            QtWidgets.QMessageBox.warning(self, "2軸作成", "Seiban（受注伝票番号）を入力してください")
            return
        dlg = TwoAxisCreateDialog(self, e, partners)
        if not dlg.exec():
            return
        partner, a1, a2, prefix = dlg.partner, dlg.axis1, dlg.axis2, dlg.prefix
        per_axis = {a1: e.values(), a2: partner.values()}
        try:
            raw = param_build.read_master(master)
        except Exception as ex:
            QtWidgets.QMessageBox.warning(self, "2軸作成", f"BASIC を読めません:\n{ex}")
            return
        fname = param_build.filename(prefix, seiban)
        axis_label = f"{nc_param.axis_name(a1)}＋{nc_param.axis_name(a2)}"
        rows = [(num, nc_param.axis_name(ax) if ax else "共通", old, new)
                for (num, ax, old, new) in param_build.preview_rows_multi(raw, per_axis)]
        if not ParamPreviewDialog(
                self, rows,
                subtitle=f"2軸テーブル（{axis_label} 軸を1ファイルへ）  "
                         f"{e.model} ＋ {partner.model} → {fname}").exec():
            return
        try:
            out_path, missing, fmt = param_build.create_file_multi(
                master, out, per_axis, prefix=prefix, seiban=seiban,
                ext=self.settings.get("param_out_ext", ".DAT"),
                eob=self.settings.get("nc_eob"),
                zero_params=self._zero_params())
        except Exception as ex:
            QtWidgets.QMessageBox.warning(self, "作成に失敗", str(ex))
            return
        controller = nc_param.controller_from_basic(master)
        total = sum(len(v) for v in per_axis.values())
        # 軸ごとにログ＋履歴登録（制御/軸/Seibanが変われば別エントリ＝履歴）
        reg = []
        for ent, ax in ((e, a1), (partner, a2)):
            n_miss = sum(1 for (_m, a) in missing if a == ax)
            _log_param_creation(
                self.settings, model=ent.model, kind=ent.kind, mode=ent.mode,
                motor=ent.motor, controller=controller,
                axis=nc_param.axis_name(ax), seiban=seiban, basic=Path(master).name,
                out=out_path.name, applied=len(ent.values()) - n_miss,
                total=len(ent.values()))
            if self._csv_path():
                hist = nc_param.ParamEntry(
                    model=ent.model, kind=ent.kind, mode=ent.mode, motor=ent.motor,
                    motor_no=ent.motor_no, direction=ent.direction, gear=ent.gear,
                    controller=controller, axis=nc_param.axis_name(ax), seiban=seiban,
                    date=QtCore.QDate.currentDate().toString("yyyy/MM/dd"),
                    basic=Path(master).name, items=list(ent.items))
                try:
                    _, action = nc_param.upsert_entry(self._csv_path(), hist)
                    verb = {"added": "登録", "updated": "更新"}.get(action, "")
                    if verb:
                        reg.append(f"・{nc_param.axis_name(ax)}軸（{ent.model}）を履歴に{verb}")
                except Exception:
                    pass
        self.reload()
        msg = (f"2軸テーブルの .prm を作成しました:\n・{out_path.name}\n"
               f"（{Path(master).name} の {axis_label} 軸を1ファイルに書き込み）\n"
               f"値の反映: {total - len(missing)} / {total} 件")
        if missing:
            miss = ", ".join(f"{n}({nc_param.axis_name(a)})" if a else str(n)
                             for n, a in missing[:12])
            msg += f"\n⚠ BASICに無い番号（未反映）: {miss}"
        if reg:
            msg += "\nデータベース履歴:\n" + "\n".join(reg)
        msg += "\n\n機械側での入力は人が実施（PWE/電源再投入に注意）。"
        QtWidgets.QMessageBox.information(self, "作成しました", msg)

    # ----- 吸い出し→差分登録 -----
    def register_dump(self):
        from pathlib import Path
        dump = self.e_dump.text().strip()
        master = self.e_basic.text().strip()
        model = self.e_dmodel.text().strip()
        if not dump or not Path(dump).is_file():
            QtWidgets.QMessageBox.warning(self, "登録", "吸い出しファイルを指定してください")
            return
        if not master or not Path(master).is_file():
            QtWidgets.QMessageBox.warning(self, "登録", "比較する『使うBASIC』を指定してください")
            return
        if not model:
            QtWidgets.QMessageBox.warning(self, "登録", "型式を入力してください")
            return
        if not self._require_csv():
            return
        try:
            basic = param_build.read_master(master)
            dumptext = Path(dump).read_bytes().decode("cp932", errors="replace")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "登録", f"読み込みに失敗:\n{e}")
            return
        if not fanuc_param.looks_like_fanuc_prm(dumptext) or \
                not fanuc_param.looks_like_fanuc_prm(basic):
            QtWidgets.QMessageBox.warning(
                self, "登録", "N形式（実機ネイティブ）の吸い出し＋BASICで差分を取ります。"
                "形式が一致しているか確認してください。")
            return
        changed = [(num, newv) for (num, _lab, _old, newv)
                   in fanuc_param.diff(basic, dumptext) if newv is not None]
        if not changed:
            QtWidgets.QMessageBox.information(self, "登録", "BASICとの差分はありませんでした")
            return
        mode = self._dump_mode(dumptext)
        entry = nc_param.ParamEntry(
            model=model, mode=mode,
            controller=nc_param.controller_from_basic(master),
            seiban=self.e_dseiban.text().strip(),
            date=QtCore.QDate.currentDate().toString("yyyy/MM/dd"),
            basic=Path(master).name,
            items=[(num, val, "") for num, val in changed])
        self._backup_csv()
        try:
            eid, action = nc_param.upsert_entry(self._csv_path(), entry)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "登録に失敗", str(e))
            return
        self.reload()
        QtWidgets.QMessageBox.information(
            self, "登録しました",
            f"型式『{model}』{'／' + mode + 'クロ' if mode else ''} に差分 {len(changed)} 件を"
            f"{('登録' if action=='added' else '更新')}（ID {eid}）。\n"
            f"差分は BASIC {Path(master).name} との比較で自動抽出しました。")

    def _dump_mode(self, dumptext):
        number = self.settings.get("closed_loop_number", "1815")
        bit = int(self.settings.get("closed_loop_bit", 1))
        full = int(self.settings.get("closed_loop_full", 1))
        modes = []
        for ax in ("A1", "A2", "A3", "A4", "L1", None):
            val = fanuc_param.get_value(dumptext, number, ax)
            if val is not None:
                modes.append(fanuc_param.closed_loop_mode(val, bit, full))
        if "フル" in modes:
            return "フル"
        if "セミ" in modes:
            return "セミ"
        return ""


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
        fit_to_screen(self, 660, 480)
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
        fit_to_screen(self, 1180, 560)
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
        self.e_count.setValue(int(win.settings.get("recent_count") or 5))
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

    _cmp_done = QtCore.Signal(int, list)   # 検索世代, 結果（過去データと同じ非同期方式）

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("分析")
        fit_to_screen(self, 1040, 720)
        self.records = []
        self.headers = []
        self.rows = []
        self.metric_cols = []
        self.single_result_rows = []
        self.single_series = None
        self._models_loaded = False
        self._cmp_gen = 0
        self._cmp_done.connect(self._on_compare_done)

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
        self.sp_count.setValue(int(self.win.settings.get("recent_count") or 5))
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

        # 出力ボタンは上に置く（下だと画面に収まらず見えないことがあるため）
        btns = QtWidgets.QHBoxLayout()
        for label, slot in (("CSV出力", self.export_compare_csv),
                            ("Excel出力", self.export_compare_xlsx),
                            ("印刷", self.print_compare)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch(1)
        v.addLayout(btns)

        self.cmp_table = QtWidgets.QTableWidget(0, 0)
        self.cmp_table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.cmp_table.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        v.addWidget(self.cmp_table, 3)

        self.cmp_plot = pg.PlotWidget()
        self.cmp_plot.showGrid(x=True, y=True, alpha=0.3)
        self.cmp_plot.setLabel("left", "値", units='"')
        self.cmp_plot.getAxis("left").enableAutoSIPrefix(False)
        v.addWidget(self.cmp_plot, 2)
        return w

    def reload_compare(self, *args):
        """過去データ検索をバックグラウンドで実行する。

        ネットワーク共有の走査は数秒〜固まりうるので、過去データ画面と同じく
        ワーカースレッド＋世代番号で行う（古い検索結果は捨てる）。
        """
        root = str(self.win.settings.get("bs_save_root") or "").strip()
        self._cmp_gen += 1
        gen = self._cmp_gen
        if not root:
            self.win.statusBar().showMessage(
                "分析できません：設定の「.BS/.KS保存先」が空です")
            self._cmp_done.emit(gen, [])
            return
        if not Path(root).exists():
            self.win.statusBar().showMessage(
                f"分析できません：保存先フォルダが存在しません（{root}）")
            self._cmp_done.emit(gen, [])
            return
        self.win.statusBar().showMessage("過去データを検索中...")
        model = self.e_cmp_model.text()
        machine = self.e_cmp_machine.text()
        limit = self.sp_count.value()

        def work():
            try:
                recs = report.search_inspection(
                    root, model=model, machine=machine, limit=limit)
            except Exception:
                recs = []
            self._cmp_done.emit(gen, recs)

        threading.Thread(target=work, daemon=True).start()

    def _on_compare_done(self, gen, records):
        if gen != self._cmp_gen:
            return                      # 新しい検索が始まっている＝古い結果は捨てる
        self.records = records
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
        if self.records:
            self.win.statusBar().showMessage(f"分析: {len(self.records)}件")

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
        # 出力ボタンは上に（下だと画面に収まらず見えないことがある）
        btns = QtWidgets.QHBoxLayout()
        for label, slot in (("CSV出力", self.export_single_csv),
                            ("Excel出力", self.export_single_xlsx),
                            ("印刷", self.print_single)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch(1)
        v.addLayout(btns)
        # グラフはホイールとウォームに分ける（凡例は各グラフの上＝カーブに被らない）
        self.single_plot_wheel = pg.PlotWidget(title="ホイール")
        self.single_plot_worm = pg.PlotWidget(title="ウォーム")
        for plot in (self.single_plot_wheel, self.single_plot_worm):
            plot.setLabel("bottom", "指令角度", units="°")
            plot.setLabel("left", "偏差", units='"')
            plot.showGrid(x=True, y=True, alpha=0.3)
            for axis in ("left", "bottom"):
                plot.getAxis(axis).enableAutoSIPrefix(False)
        self.single_legend_wheel = _legend_label()
        self.single_legend_worm = _legend_label()
        plots = QtWidgets.QHBoxLayout()
        plots.setContentsMargins(0, 0, 0, 0)
        plots.addWidget(_plot_box(self.single_legend_wheel, self.single_plot_wheel), 7)
        plots.addWidget(_plot_box(self.single_legend_worm, self.single_plot_worm), 3)
        v.addLayout(plots, 3)
        self.single_table = QtWidgets.QTableWidget(0, 2)
        self.single_table.setHorizontalHeaderLabels(["項目", "値"])
        self.single_table.horizontalHeader().setStretchLastSection(True)
        self.single_table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        v.addWidget(self.single_table, 2)
        return w

    def load_single(self):
        win = self.win
        self.single_plot_wheel.clear()
        self.single_plot_worm.clear()
        self.single_legend_wheel.setText("")
        self.single_legend_worm.setText("")
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
        colors = {"wheel_cw": "#1f77b4", "wheel_ccw": "#d62728",
                  "worm_cw": "#2ca02c", "worm_ccw": "#ff7f0e"}
        if series:
            wheel_leg, worm_leg = [], []
            for key, style in CURVE_STYLES.items():
                ser = series.get(key)
                if ser and ser[0]:
                    target = (self.single_plot_wheel if key.startswith("wheel")
                              else self.single_plot_worm)
                    target.plot(ser[0], ser[1], **style)
                    mark_adjacent_peak(target, ser[0], ser[1])  # 隣接最大の位置を表示
                    pair = (SERIES_LABELS.get(key, key), colors.get(key, "#333"))
                    (wheel_leg if key.startswith("wheel") else worm_leg).append(pair)
            self.single_legend_wheel.setText(_legend_html(wheel_leg))
            self.single_legend_worm.setText(_legend_html(worm_leg))
        else:
            # 再現性は系列が分かれないので、ホイール側に1枚で出す（数値は下表）
            self.single_plot_wheel.setTitle("再現性（数値は下表を参照）")

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
        fit_to_screen(self, 920, 720)
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
        b_fanuc = QtWidgets.QPushButton("FANUCパラメータ生成")
        b_fanuc.setToolTip("この補正表から実機用のピッチエラー補正パラメータ"
                           "（No.3620〜＋補正点データ）を作り、.PRMで保存します")
        b_close = QtWidgets.QPushButton("閉じる")
        b_csv.clicked.connect(self.export_csv)
        b_print.clicked.connect(self.print_table)
        b_fanuc.clicked.connect(self.open_fanuc_params)
        b_close.clicked.connect(self._close)
        btns.addStretch(1)
        btns.addWidget(b_csv)
        btns.addWidget(b_print)
        btns.addWidget(b_fanuc)
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

    def open_fanuc_params(self):
        """この補正表からFANUCピッチエラー補正パラメータを生成するダイアログを開く。"""
        if not self._rows:
            self.win.statusBar().showMessage("補正表がありません（分割データが必要）")
            return
        FanucPitchParamDialog(self, self._rows, self.sp_interval.value()).exec()

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


class FanucPitchParamDialog(QtWidgets.QDialog):
    """補正表 → FANUC ピッチエラー補正パラメータ（No.3620〜＋補正点データ）を生成。

    実機に入る値なので、機種差を吸収できるよう軸・検出単位・倍率・回転/符号・データ
    先頭番号を画面で変えられる。実機の補正設定済みサンプルが来たら既定を合わせる。
    生成した .PRM は人が PWE=1・電源再投入に注意して取り込む（値の捏造はしない）。
    """

    def __init__(self, parent, rows, interval_deg):
        super().__init__(parent)
        self.parent_dlg = parent
        self.win = parent.win
        self._rows = rows
        self._interval = interval_deg
        self._params = None
        self.setWindowTitle("FANUCピッチエラー補正パラメータ生成")
        fit_to_screen(self, 760, 640)
        layout = QtWidgets.QVBoxLayout(self)

        info = QtWidgets.QLabel(
            f"補正間隔 {interval_deg:g}° の補正表（{len(rows)}点）から、実機用の"
            "ピッチエラー補正パラメータを作ります。機種に合わせて下の値を調整してください。")
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QtWidgets.QGridLayout()
        self.sp_axis = QtWidgets.QSpinBox(); self.sp_axis.setRange(1, 8)
        self.sp_axis.setValue(int(self.win.settings.get("pcorr_axis", 4)))
        self.sp_detect = QtWidgets.QDoubleSpinBox(); self.sp_detect.setDecimals(5)
        self.sp_detect.setRange(0.00001, 1.0); self.sp_detect.setSuffix(" °")
        self.sp_detect.setValue(float(self.win.settings.get("pcorr_detect", 0.001)))
        self.sp_mag = QtWidgets.QSpinBox(); self.sp_mag.setRange(1, 100)
        self.sp_mag.setValue(int(self.win.settings.get("pcorr_mag", 1)))
        self.c_rotary = QtWidgets.QCheckBox("回転軸（1回転で閉じる）")
        self.c_rotary.setChecked(bool(self.win.settings.get("pcorr_rotary", True)))
        self.sp_perrev = QtWidgets.QDoubleSpinBox(); self.sp_perrev.setRange(0.1, 3600.0)
        self.sp_perrev.setDecimals(3); self.sp_perrev.setSuffix(" °")
        self.sp_perrev.setValue(float(self.win.settings.get("pcorr_perrev", 360.0)))
        self.cmb_sign = QtWidgets.QComboBox(); self.cmb_sign.addItems(["+（標準）", "−（反転）"])
        self.sp_base = QtWidgets.QSpinBox(); self.sp_base.setRange(1, 99999)
        self.sp_base.setValue(int(self.win.settings.get("pcorr_data_base", 10000)))
        r = 0
        for label, w in (("補正軸 A", self.sp_axis), ("検出単位", self.sp_detect),
                         ("補正倍率", self.sp_mag), ("", self.c_rotary),
                         ("1回転あたり", self.sp_perrev), ("補正値の符号", self.cmb_sign),
                         ("データ先頭番号", self.sp_base)):
            if label:
                form.addWidget(QtWidgets.QLabel(label), r // 2, (r % 2) * 2)
            form.addWidget(w, r // 2, (r % 2) * 2 + 1)
            r += 1
        layout.addLayout(form)
        for w in (self.sp_axis, self.sp_detect, self.sp_mag, self.sp_perrev, self.sp_base):
            w.valueChanged.connect(self.recompute)
        self.c_rotary.toggled.connect(self.recompute)
        self.cmb_sign.currentIndexChanged.connect(self.recompute)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["補正点番号", "角度[°]", "絶対補正[検出単位]", "増分[検出単位]"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        layout.addWidget(self.table, 2)

        self.preview = QtWidgets.QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setStyleSheet("font-family: monospace;")
        layout.addWidget(self.preview, 2)
        self.notes = QtWidgets.QLabel(""); self.notes.setWordWrap(True)
        self.notes.setStyleSheet("color:#b45309;")
        layout.addWidget(self.notes)

        row = QtWidgets.QHBoxLayout()
        b_save = QtWidgets.QPushButton(".PRMで保存")
        b_default = QtWidgets.QPushButton("この設定を既定にする")
        b_close = QtWidgets.QPushButton("閉じる")
        b_save.clicked.connect(self.save_prm)
        b_default.clicked.connect(self.save_defaults)
        b_close.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(b_default)
        row.addWidget(b_save)
        row.addWidget(b_close)
        layout.addLayout(row)

        self.recompute()

    def _build(self):
        return fanuc_pcorr.build_pitch_params(
            self._rows, axis=self.sp_axis.value(), interval_deg=self._interval,
            detect_unit_deg=self.sp_detect.value(), magnification=self.sp_mag.value(),
            rotary=self.c_rotary.isChecked(), per_rev_deg=self.sp_perrev.value(),
            sign=1 if self.cmb_sign.currentIndex() == 0 else -1,
            data_base=self.sp_base.value())

    def recompute(self, *args):
        self.sp_perrev.setEnabled(self.c_rotary.isChecked())
        self._params = self._build()
        pts = self._params["points"]
        self.table.setRowCount(len(pts))
        for i, (no, ang, cum, incr) in enumerate(pts):
            for j, text in enumerate((str(no), f"{ang:g}", str(cum), f"{incr:+d}")):
                self.table.setItem(i, j, QtWidgets.QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        self.preview.setPlainText(fanuc_pcorr.to_prm_text(self._params, newline="\n"))
        self.notes.setText("　".join(self._params.get("notes", [])))

    def save_defaults(self):
        self.win.settings["pcorr_axis"] = self.sp_axis.value()
        self.win.settings["pcorr_detect"] = self.sp_detect.value()
        self.win.settings["pcorr_mag"] = self.sp_mag.value()
        self.win.settings["pcorr_rotary"] = self.c_rotary.isChecked()
        self.win.settings["pcorr_perrev"] = self.sp_perrev.value()
        self.win.settings["pcorr_data_base"] = self.sp_base.value()
        try:
            save_settings(self.win.settings)
            self.win.statusBar().showMessage("ピッチエラー補正の生成設定を既定として保存しました")
        except Exception:
            pass

    def save_prm(self):
        if not self._params or not self._params.get("data"):
            self.win.statusBar().showMessage("生成できる補正データがありません")
            return
        machine = self.win.e_machine.text().strip() or "pcorr"
        ext = str(self.win.settings.get("param_out_ext") or ".DAT")
        default = str(resolve_save_root(self.win.settings)
                      / f"{machine}_ピッチ補正{ext}")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "FANUCパラメータで保存", default,
            "FANUC (*.DAT *.PRM *.prm)")
        if not path:
            return
        try:
            # 実機が自分で出力するファイルと同じ区切り（既定 LF CR CR）で書く。
            # PCの改行のままだと制御装置が読み込めない（実機で確認）
            text = fanuc_pcorr.to_prm_text(self._params, newline="\n")
            eob = self.win.settings.get("nc_eob") or fanuc.DEFAULT_EOB
            Path(path).write_bytes(fanuc_param.prm_bytes(text, eob))
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "保存", f"失敗しました:\n{e}")
            return
        self.win.statusBar().showMessage(f"FANUCピッチエラー補正パラメータを保存しました: {path}")


class BatchProgramDialog(QtWidgets.QDialog):
    """型式ごとの測定プログラムをまとめて作る。

    測定条件が型式で決まっているので、型式を選べばプログラムは一意に決まる。
    メモリカードのサブフォルダを開けない制御装置があるため、
    「型式ごとのフォルダ」と「1つのフォルダに平置き」を選べるようにしてある。
    """

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("型式ごとに測定プログラムを一括作成")
        v = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "測定条件マスタの型式ぶんの測定プログラムをまとめて作ります。"
            "刻みが登録されていない型式は作らず、理由を一覧に出します。")
        intro.setWordWrap(True)      # 折り返さないと文字を大きくしたとき横に伸びる
        v.addWidget(intro)
        self._wrap_labels_later = True

        form = QtWidgets.QFormLayout()
        self.e_out = QtWidgets.QLineEdit(str(settings.get("param_out_folder", "")))
        self.e_out.setPlaceholderText(r"出力先（カード E:\ や 共有 \\server\NC）")
        form.addRow("出力先", self._browse_row(self.e_out))
        self.cmb_layout = QtWidgets.QComboBox()
        for label, value in batch_build.LAYOUTS:
            self.cmb_layout.addItem(label, value)
        self.cmb_layout.setToolTip(
            "メモリカードのサブフォルダを開けない制御装置があります。\n"
            "見えないときは「1つのフォルダに並べる」を選んでください")
        form.addRow("並べ方", self.cmb_layout)
        self.e_filter = QtWidgets.QLineEdit()
        self.e_filter.setPlaceholderText("空＝全型式。例 RTH と入れると RTH… だけ")
        form.addRow("型式で絞る", self.e_filter)
        self.c_over = QtWidgets.QCheckBox("既にあるファイルも上書きする")
        form.addRow("", self.c_over)
        v.addLayout(form)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("型式", "クローズ", "結果", "出力先/理由"))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        v.addWidget(self.table, 1)
        self.lbl = QtWidgets.QLabel()
        v.addWidget(self.lbl)

        row = QtWidgets.QHBoxLayout()
        b_run = QtWidgets.QPushButton("作成")
        b_run.setObjectName("primary")
        b_run.clicked.connect(self.run)
        b_close = QtWidgets.QPushButton("閉じる")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_run)
        row.addStretch(1)
        row.addWidget(b_close)
        v.addLayout(row)

        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1280, 800)
        self.resize(min(960, avail.width() - 80), min(620, avail.height() - 80))

    def _browse_row(self, line):
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(line, 1)
        b = QtWidgets.QPushButton("参照...")

        def pick():
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "出力先", line.text())
            if d:
                line.setText(d)
        b.clicked.connect(pick)
        h.addWidget(b)
        return w

    def _entries(self):
        path = str(self.settings.get("conditions_csv") or r"マスタ/測定条件.csv")
        if not Path(path).is_absolute():
            path = str(app_dir() / path)
        conds = load_conditions(path)
        want = self.e_filter.text().strip().upper()
        out = [c for c in conds.values()
               if not want or want in str(c.get("model", "")).upper()]
        return sorted(out, key=lambda c: (str(c.get("model", "")),
                                          str(c.get("close", ""))))

    def run(self):
        out = self.e_out.text().strip()
        if not out or not Path(out).is_dir():
            QtWidgets.QMessageBox.warning(
                self, "一括作成", "出力先フォルダを指定してください（存在する場所）")
            return
        entries = self._entries()
        if not entries:
            QtWidgets.QMessageBox.information(self, "一括作成", "対象の型式がありません")
            return
        if QtWidgets.QMessageBox.question(
                self, "一括作成",
                f"{len(entries)}型式ぶんの測定プログラムを作ります。\n出力先: {out}\n"
                "よろしいですか？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        cfg = FanucConfig.from_settings(self.settings)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            rep = batch_build.batch_programs(
                entries, cfg, out,
                layout=self.cmb_layout.currentData() or "folder",
                ext=str(self.settings.get("nc_out_ext", ".NC")),
                eob=self.settings.get("nc_eob"),
                overwrite=self.c_over.isChecked())
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "一括作成", f"失敗しました:\n{e}")
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        self.table.setRowCount(len(rep))
        for r, (model, close, state, where) in enumerate(rep):
            for c, text in enumerate((model, close, state, where)):
                item = QtWidgets.QTableWidgetItem(str(text))
                if state == "作れない":
                    item.setBackground(QtGui.QColor("#fee2e2"))
                elif state == "作成":
                    item.setBackground(QtGui.QColor("#dcfce7"))
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        self.lbl.setText(batch_build.summarize(rep))


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
                    ys[dirn].append((rep_unwrap(angle, v) - angle) * 3600.0)
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
                    mark_adjacent_peak(target, ser[0], ser[1])  # 隣接最大の位置を表示
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


class Iso230Dialog(QtWidgets.QDialog):
    """JIS B 6190-2（ISO 230-2）の位置決め精度評価を表示する（読み取り専用）。

    再現性測定のデータから A（双方向位置決め精度）/ R（繰返し性）/ E / M / B を
    計算して一覧にする。既存の再現性の数値・保存・.RS出力には影響しない追加表示。
    """

    def __init__(self, win, stats, meta=None):
        super().__init__(win)
        self.setWindowTitle("JIS B 6190-2（ISO 230-2）評価")
        fit_to_screen(self, 760, 560)
        self._stats = stats
        self._meta = meta or {}
        v = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "再現性測定の読みを JIS B 6190-2（ISO 230-2）の式で評価した結果です。"
            "検査成績書・お客様への提出値に使えます（↑=CW、↓=CCW、単位は秒[\"]）。")
        intro.setWordWrap(True)
        intro.setStyleSheet("background:#eff6ff; color:#1e40af; border:1px solid #bfdbfe;"
                            "border-radius:4px; padding:6px;")
        v.addWidget(intro)

        # 軸まとめ（A/R/E/M/B）
        rows = iso230.axis_rows(stats)
        t1 = QtWidgets.QTableWidget(len(rows), 2)
        t1.setHorizontalHeaderLabels(["項目", "値"])
        t1.verticalHeader().setVisible(False)
        t1.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for i, (label, val) in enumerate(rows):
            it0 = QtWidgets.QTableWidgetItem(label)
            it1 = QtWidgets.QTableWidgetItem(val)
            if i in (0, 3):                       # A と R は主要値なので太字
                f = it0.font(); f.setBold(True)
                it0.setFont(f); it1.setFont(f)
            t1.setItem(i, 0, it0); t1.setItem(i, 1, it1)
        t1.resizeColumnsToContents()
        t1.horizontalHeader().setStretchLastSection(True)
        v.addWidget(t1, 2)

        # 位置別（x̄i↑/2si↑/Bi/Ri）
        headers = ["角度", "n↑", "x̄i↑", "2si↑", "n↓", "x̄i↓", "2si↓", "Bi", "Ri"]
        prow = iso230.position_rows(stats)
        t2 = QtWidgets.QTableWidget(len(prow), len(headers))
        t2.setHorizontalHeaderLabels(headers)
        t2.verticalHeader().setVisible(False)
        t2.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for i, row in enumerate(prow):
            for j, text in enumerate(row):
                t2.setItem(i, j, QtWidgets.QTableWidgetItem(text))
        t2.resizeColumnsToContents()
        v.addWidget(t2, 3)

        notes = QtWidgets.QLabel("\n".join(f"※ {n}" for n in stats["notes"]))
        notes.setWordWrap(True)
        notes.setStyleSheet("color:#64748b;")
        v.addWidget(notes)

        bottom = QtWidgets.QHBoxLayout()
        b_csv = QtWidgets.QPushButton("CSVで保存…")
        b_csv.setToolTip("この評価表をCSVに保存（Excelで開いて成績書に貼れる）")
        b_csv.clicked.connect(self._save_csv)
        bottom.addWidget(b_csv)
        bottom.addStretch(1)
        b_close = QtWidgets.QPushButton("閉じる")
        b_close.clicked.connect(self.accept)
        bottom.addWidget(b_close)
        v.addLayout(bottom)

    def _save_csv(self):
        name = "JIS評価"
        if self._meta.get("機番"):
            name = f"{self._meta['機番']}_JIS評価"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "JIS評価をCSVで保存", f"{name}.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
                f.write(iso230.to_csv_text(self._stats, self._meta))
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "保存", f"保存に失敗しました:\n{e}")
            return
        self.parent().statusBar().showMessage(f"JIS評価を保存しました: {path}")


class HelpDialog(QtWidgets.QDialog):
    """アプリ全体＋新機能の詳細ヘルプ（左に見出し一覧・右に本文）。"""

    def __init__(self, parent=None, start_title=None):
        super().__init__(parent)
        self.setWindowTitle("詳細ヘルプ")
        fit_to_screen(self, 860, 620)
        layout = QtWidgets.QVBoxLayout(self)

        split = QtWidgets.QHBoxLayout()
        self.list = QtWidgets.QListWidget()
        self.list.setMaximumWidth(240)
        self.body = QtWidgets.QTextBrowser()
        self.body.setOpenExternalLinks(False)
        # ヘルプ本文中の <img src="xxx.png"> を nd287_app/help_images から解決する
        self.body.setSearchPaths([str(Path(__file__).resolve().parent / "help_images")])
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
        fit_to_screen(self, 820, 600)
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


class _NoWheelFilter(QtCore.QObject):
    """フォーカスが無い入力欄のホイールを、値の増減ではなく親のスクロールに回す。

    測定条件をスクロール領域に入れたため、パネルをスクロールしようとしたホイールが
    ポインタ下のスピンボックス/コンボの値を無言で書き換えてしまう（刻み・ブロック数・
    主点評価・バックラッシ補正・さらにはモードそのもの）。測定条件が黙って変わるのは
    測定アプリとして致命的なので、フォーカスが当たっていないときは値を変えず、
    親のスクロール領域へイベントを渡す。クリック/Tabでフォーカスしていれば従来どおり
    ホイールで増減できる。
    """

    def eventFilter(self, obj, event):
        if event.type() != QtCore.QEvent.Wheel or obj.hasFocus():
            return False
        parent = obj.parentWidget()
        while parent is not None:
            if isinstance(parent, QtWidgets.QAbstractScrollArea):
                QtWidgets.QApplication.sendEvent(parent.viewport(), event)
                return True
            parent = parent.parentWidget()
        return True   # スクロール先が無くても値は変えない


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
        fit_to_screen(self, 1200, 800)
        self.dev = device
        self.settings = settings
        self._connecting = False  # 接続スレッド実行中はシリアルに触らない
        self._conn_dev = None     # 接続を試みているデバイス（切替の取り残し検出用）
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
            "0°位置のバックラッシ量を実測に合わせる補正（CCWを上下に平行移動）。\n"
            "回転: 0°位置の実測値を入力（例 今10\"・実測15\"→「15」と入力）。0=補正なし。\n"
            "傾斜: 減らす量を入力（例 今20\"・実測12\"→「8」と入力＝8\"減）。\n"
            "入力後『補正適用』。判定・保存も補正後の値になります。"
        )
        self.b_corr = QtWidgets.QPushButton("補正適用")
        self.b_corr.setEnabled(False)
        self.b_corr.clicked.connect(self.apply_correction)
        self.applied_blcorr = 0.0  # 補正適用ボタンで確定した実シフト量
        self.applied_blcorr_field = 0.0  # そのとき入力欄にあった値（未適用判定用）
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

        # 評価範囲1・2は横に並べる（縦積みで右に空白を作らない）。
        # ホイールの開始/終了角度は「ホイール」群（刻みの真下）へ移したのでここには置かない。
        ranges_h.addWidget(range_block(self.c_r1, self.e_r1s, self.e_r1e),
                           0, QtCore.Qt.AlignTop)
        ranges_h.addWidget(range_block(self.c_r2, self.e_r2s, self.e_r2e),
                           0, QtCore.Qt.AlignTop)
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
        # モードとコメントは同じ行に置く（1行ぶん詰めてグラフに場所を回す）
        mode_row = QtWidgets.QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        lbl_mode = QtWidgets.QLabel("モード")
        lbl_mode.setMinimumWidth(52)
        mode_row.addWidget(lbl_mode)
        mode_row.addWidget(self.mode_combo, 2)
        lbl_comment = QtWidgets.QLabel("コメント")
        mode_row.addSpacing(8)
        mode_row.addWidget(lbl_comment)
        mode_row.addWidget(self.e_comment, 3)
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
        # 傾斜分割では 刻み の真下に 開始/終了角度 を置く（刻みと範囲を近くに）
        wg.addWidget(cond_header("ホイール"), 0, 0, 1, 4)
        wg.addWidget(QtWidgets.QLabel("刻み"), 1, 0)
        wg.addWidget(self.e_wheel, 1, 1)
        self.l_wstart2 = QtWidgets.QLabel("開始角度")
        self.l_wend2 = QtWidgets.QLabel("終了角度")
        wg.addWidget(self.l_wstart2, 2, 0)
        wg.addWidget(self.e_wstart, 2, 1)
        wg.addWidget(self.l_wend2, 2, 2)
        wg.addWidget(self.e_wend, 2, 3)
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
        # ISO 230-2 モードのときだけ出す：目標位置に擬似ランダムオフセットを与える
        self.c_iso_offset = QtWidgets.QCheckBox("目標位置に擬似ランダムオフセット（JIS推奨）")
        self.c_iso_offset.setChecked(True)
        self.c_iso_offset.setToolTip(
            "周期的な誤差成分と目標位置が一致するのを避けるため、内側の目標を少し"
            "ずらします（両端は範囲を保つため固定）。同じ設定なら常に同じ位置。")
        self.c_iso_offset.setVisible(False)
        rg.addWidget(self.c_iso_offset, 3, 0, 1, 4)
        rg.setColumnStretch(4, 1)

        # 測定順（分割系）: ホイール/ウォームのCW/CCWを画面で並べ替え・測る/測らないを選ぶ
        self.order_group = QtWidgets.QWidget()
        og = QtWidgets.QGridLayout(self.order_group)
        og.setContentsMargins(0, 0, 0, 0)
        og.setHorizontalSpacing(4)
        og.setVerticalSpacing(3)
        og.addWidget(cond_header("測定順"), 0, 0, 1, 2)
        self.order_list = QtWidgets.QListWidget()
        self.order_list.setToolTip(
            "上から順に測定します。チェックを外すとその系列は測りません。\n"
            "↑↓で並べ替え。型式マスタに測定順があれば初期値に入ります。")
        self.order_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.order_list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        # 4行ぶんの高さは _populate_order_list が実フォントに合わせて確定させる。
        # 行スパンは使わない（余った行に高さを吸われて末尾が切れるのを避ける）。
        og.addWidget(self.order_list, 1, 0, 2, 1)
        b_up = QtWidgets.QToolButton(); b_up.setText("↑")
        b_up.setToolTip("選んだ系列を上へ（先に測る）")
        b_up.clicked.connect(lambda: self._move_order(-1))
        b_dn = QtWidgets.QToolButton(); b_dn.setText("↓")
        b_dn.setToolTip("選んだ系列を下へ（後で測る）")
        b_dn.clicked.connect(lambda: self._move_order(1))
        og.addWidget(b_up, 1, 1, QtCore.Qt.AlignBottom)
        og.addWidget(b_dn, 2, 1, QtCore.Qt.AlignTop)
        self._populate_order_list()

        # 群は2段のグリッドに折り返す。横1列に全部並べると「傾斜分割+再現」で幅が
        # 画面を超え、入力欄が潰れて読めなくなり右の結果欄も見えなくなるため。
        #   1段目: ホイール / ウォーム / 測定順（縦に2段ぶん）
        #   2段目: 再現     / 主点評価・バックラッシ
        # モードで隠れた群の行・列は自動で詰まる（再現系は2段目だけになる）。
        groups_grid = QtWidgets.QGridLayout()
        groups_grid.setContentsMargins(0, 0, 0, 0)
        groups_grid.setHorizontalSpacing(14)
        groups_grid.setVerticalSpacing(6)
        _tl = QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft
        groups_grid.addWidget(self.wheel_group, 0, 0, _tl)
        groups_grid.addWidget(self.worm_group, 0, 1, _tl)
        # 測定順は4行ぶん縦に高いので2段にまたがらせる（上段に空白を作らない）
        groups_grid.addWidget(self.order_group, 0, 2, 2, 1, _tl)
        groups_grid.addWidget(self.repeat_group, 1, 0, _tl)
        groups_grid.setColumnStretch(3, 1)
        cond_v.addLayout(groups_grid)

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
        # 主点評価・バックラッシ補正は2段目・再現の隣（分割系のみ表示）。
        groups_grid.addWidget(self.eval_group, 1, 1, _tl)

        cond_v.addWidget(self.box_ranges)  # 評価範囲（傾斜のみ）は全幅
        # コメントはモード行に同居させたのでここには置かない

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
        self.b_pdf = QtWidgets.QPushButton("PDF成績書")
        self.b_pdf.setToolTip("検査記録（印刷と同じ内容）を検査成績書PDFとして保存します")
        self.b_pdf.clicked.connect(self.save_report_pdf)
        self.b_raw = QtWidgets.QPushButton("生データ")
        self.b_raw.clicked.connect(self.show_raw_data)
        self.b_past = QtWidgets.QPushButton("過去データ")
        self.b_past.clicked.connect(self.show_past_data)
        self.b_cond = QtWidgets.QPushButton("条件編集")
        self.b_cond.clicked.connect(self.show_condition_editor)
        self.b_pcorr = QtWidgets.QPushButton("ピッチエラー補正")
        self.b_pcorr.setToolTip("提出用のピッチエラー補正表＋補正後グラフを表示・CSV保存・印刷")
        self.b_pcorr.clicked.connect(self.show_pitch_correction)
        # 分析・プログラム作成・パラメータ・アラームは「ツール ▾」メニューへまとめる
        # （下のツールバー組み立てで作る）
        b_load = QtWidgets.QPushButton("ロード")
        b_settings = QtWidgets.QPushButton("設定")
        b_help = QtWidgets.QPushButton("ヘルプ")
        b_help.setToolTip("アプリ全体と新機能（クランプ分割・機械へ送信など）の詳細ヘルプ")
        self.b_save.clicked.connect(self.save)
        self.b_print.clicked.connect(self.print_report)
        b_load.clicked.connect(self.load)
        b_settings.clicked.connect(self.open_settings)
        b_help.clicked.connect(self.show_help)
        # ロード名・取込手順は最上部ツールバーの「アラーム」の右に出す（場所を取らない）
        self.guide = QtWidgets.QLabel("―")
        self.guide.setObjectName("guide")
        self.guide.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
        self.guide.setStyleSheet("padding:1px 10px;")
        toolbar = QtWidgets.QToolBar("操作")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(QtCore.Qt.TopToolBarArea, toolbar)
        # 取込開始・自動測定を左上（セーブの横）に置く
        toolbar.addWidget(self.b_start)
        toolbar.addWidget(self.b_auto)
        toolbar.addWidget(self.b_partial)
        toolbar.addSeparator()
        for b in (self.b_save, self.b_print, self.b_pdf, b_load):
            toolbar.addWidget(b)
        toolbar.addSeparator()
        for b in (self.b_raw, self.b_past):
            toolbar.addWidget(b)
        toolbar.addSeparator()
        # 分析・プログラム作成・パラメータ・アラームは1つのメニューボタンにまとめる
        # （ツールバーを短くして、測定条件とグラフに場所を回す）
        self.b_tools = QtWidgets.QToolButton()
        self.b_tools.setText("ツール ▾")
        self.b_tools.setToolTip("分析／プログラム作成／パラメータ／アラーム")
        self.b_tools.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        tools_menu = QtWidgets.QMenu(self.b_tools)
        for text, slot, tip in (
            ("分析…", self.show_analysis,
             "過去データの横断比較・1件詳細をグラフ/表で見てCSV・Excel・印刷"),
            ("プログラム作成…", self.show_program_dialog,
             "現在の測定条件からFANUC測定プログラム(Gコード)を作成"),
            ("パラメータ…", self.show_param_dialog,
             "受注番号から かんたん作成（詳細設定・パラメータDBも中から）"),
            ("型式ごとに一括作成…", self.show_batch_dialog,
             "測定条件マスタの型式ぶんの測定プログラムをまとめて作る"
             "（フォルダ分け／カード向けの平置きを選べる）"),
            ("アラーム…", self.show_alarm_help,
             "FANUCのアラーム番号・メッセージから意味と対処の目安を調べる"),
        ):
            act = tools_menu.addAction(text)
            act.setToolTip(tip)
            act.triggered.connect(slot)
        self.b_tools.setMenu(tools_menu)
        toolbar.addWidget(self.b_tools)
        for b in (self.b_cond, self.b_pcorr):
            toolbar.addWidget(b)
        toolbar.addSeparator()
        toolbar.addWidget(self.guide)        # ロード名・取込手順（アラームの右）
        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                             QtWidgets.QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        toolbar.addWidget(b_settings)
        toolbar.addWidget(b_help)

        # ===== 受信値・データ数 =====
        self.live = QtWidgets.QLabel("")
        self.live.setStyleSheet("color:#64748b; padding:2px;")
        self.counts = QtWidgets.QLabel("")
        # 受信点数はグラフ拡大ボタンの右に小さめで出す（例 ホイール CW 3/36）。
        # 4系列で横に長くなるので、フォントは控えめ(9pt)にして場所を取らない。
        self.counts.setStyleSheet(
            "color:#1d4ed8; padding:2px 6px; font-weight:bold; font-size:9pt;")

        # ===== グラフ（分割: 左ホイール/右ウォーム 7:3。再現性: 左のみ）=====
        self.plot_wheel = pg.PlotWidget(title="ホイール")
        self.plot_worm = pg.PlotWidget(title="ウォーム")
        for plot in (self.plot_wheel, self.plot_worm):
            # 凡例はグラフ内に置くとカーブに被るので addLegend は使わず、各グラフの上に
            # 色つきラベル（_plot_box）で出す。
            plot.setLabel("bottom", "指令角度", units="°")
            plot.setLabel("left", "偏差", units='"')
            plot.showGrid(x=True, y=True, alpha=0.3)
            # pyqtgraphの自動SI接頭辞を無効化。有効のままだと値が大きいとき
            # 単位が「k"」（キロ秒角）等に化けて読み違いのもとになる
            for axis_name in ("left", "bottom"):
                plot.getAxis(axis_name).enableAutoSIPrefix(False)
        self.legend_wheel = _legend_label()
        self.legend_worm = _legend_label()
        self.curves = {}
        self.main_markers = {}  # 主点（1/N）グリッドの強調マーカー
        plots_widget = QtWidgets.QWidget()
        plots = QtWidgets.QHBoxLayout(plots_widget)
        plots.setContentsMargins(0, 0, 0, 0)
        plots.addWidget(_plot_box(self.legend_wheel, self.plot_wheel), 7)
        plots.addWidget(_plot_box(self.legend_worm, self.plot_worm), 3)
        self.update_legends()

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
        self.b_before = QtWidgets.QPushButton("補正前")
        self.b_before.setToolTip("生データ（補正なし）の偏差・精度を表示")
        self.b_after = QtWidgets.QPushButton("補正後")
        self.b_after.setToolTip("傾き補正：始点と終点の偏差を一致させた（傾き成分を除いた）"
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
        # 隣接誤差が最大の位置をグラフ上にマーク（◆）するトグル
        self.b_adjmark = QtWidgets.QPushButton("隣接位置")
        self.b_adjmark.setCheckable(True)
        self.b_adjmark.setChecked(True)
        self.b_adjmark.setToolTip("各系列の隣接誤差が最大の点をグラフ上に◆で示す")
        self.b_adjmark.toggled.connect(lambda _: self.redraw())
        self._adj_items = []
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
        # 測定条件はスクロール領域に入れる。モード・フォント・画面幅がどうでも
        # 「入力欄が潰れる／末尾が切れる／グラフと右の結果を押し潰す」が起きない。
        # 中身が小さいモードでは中身ぶんの高さしか取らない（_fit_cond_box が調整）。
        self.cond_scroll = QtWidgets.QScrollArea()
        self.cond_scroll.setWidget(cond_group)
        self.cond_scroll.setWidgetResizable(True)
        self.cond_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.cond_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.cond_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.cond_scroll.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                       QtWidgets.QSizePolicy.Preferred)
        # 最小幅を小さくしておく。こうしないと中身の最小幅がそのまま左カラムの最小幅に
        # なり、狭い画面や大きいフォントで右の測定結果が画面外へ押し出される。
        # 足りないぶんは横スクロールで見る（潰さない）。
        self.cond_scroll.setMinimumWidth(240)
        # スクロール領域の中の入力欄は、フォーカスが無いときホイールで値を変えない
        # （パネルをスクロールしたつもりで測定条件が黙って変わるのを防ぐ）
        self._nowheel = _NoWheelFilter(self)
        for _w in (cond_group.findChildren(QtWidgets.QAbstractSpinBox)
                   + cond_group.findChildren(QtWidgets.QComboBox)):
            _w.setFocusPolicy(QtCore.Qt.StrongFocus)
            _w.installEventFilter(self._nowheel)

        top_left = QtWidgets.QHBoxLayout()
        top_left.setSpacing(8)
        top_left.addWidget(info_group, 0, QtCore.Qt.AlignTop)
        top_left.addWidget(self.cond_scroll, 1)

        # グラフのすぐ上に「取込中の操作・補正前/後・グラフ拡大・データ数」を常時表示。
        # データ数はグラフ拡大ボタンのすぐ右に小さめで置く（左カラム内なので精度結果に被らない）。
        self.counts.setWordWrap(False)
        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(2, 0, 2, 0)
        bar.setSpacing(10)
        self.b_jis = QtWidgets.QPushButton("JIS評価")
        self.b_jis.setToolTip(
            "再現性データを JIS B 6190-2（ISO 230-2）の A/R/E/M/B で評価して表示"
            "（再現性・分割+再現の測定後／ロード後に使えます）")
        self.b_jis.clicked.connect(self.show_iso230)
        bar.addWidget(ops_group)
        bar.addWidget(self.corr_bar)        # 補正前/後（分割系のみ表示）
        bar.addWidget(self.b_zoom)          # グラフ拡大（全モードで常に表示）
        bar.addWidget(self.b_jis)           # JIS B 6190-2 評価（再現データがあるとき）
        bar.addWidget(self.b_adjmark)       # 隣接位置マークの表示トグル
        bar.addWidget(self.counts)          # データ数（グラフ拡大の右・小さめ）
        bar.addWidget(self.live)
        bar.addStretch(1)
        # 操作バーもスクロール領域に入れる。ボタンが並ぶこの帯の最小幅がそのまま
        # 左カラムの最小幅になり、大きいフォント・狭い画面で右の測定結果を画面外へ
        # 押し出してしまうため。入りきらないときは横スクロールで出す（潰さない）。
        bar_widget = QtWidgets.QWidget()
        bar_widget.setLayout(bar)
        self.bar_scroll = QtWidgets.QScrollArea()
        self.bar_scroll.setWidget(bar_widget)
        self.bar_scroll.setWidgetResizable(True)
        self.bar_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.bar_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.bar_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.bar_scroll.setMinimumWidth(240)
        plots_widget.setMinimumHeight(240)
        plots_widget.setMinimumWidth(240)

        # 左カラム: 測定情報/条件 → 操作バー(データ数を含む) → グラフ。
        # （ロード名/取込手順のガイドは最上部ツールバーへ移したのでここには置かない）
        left_col = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left_col)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(4)
        lv.addLayout(top_left, 0)
        lv.addWidget(self.bar_scroll, 0)
        lv.addWidget(plots_widget, 1)       # グラフは左下で縦に大きく

        # 右カラム（縦長）: 精度PP・傾き → 単一・隣接 → バックラッシ を上下に積み、
        # 上へ寄せる。各表は中身ぶんの高さ（_fit_table_height）で全行見える。
        for _t in (self.table_series, self.table_series2, self.table_misc):
            _t.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                             QtWidgets.QSizePolicy.Fixed)
        self.right_col = right_col = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right_col)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(2)  # 3表を近づけて精度結果をコンパクトに
        rv.addWidget(self.table_series, 0)   # 精度PP・傾き
        rv.addWidget(self.table_series2, 0)  # 単一・隣接
        rv.addWidget(self.table_misc, 0)     # バックラッシ
        rv.addStretch(1)                     # 表は上に寄せ、余白は下へ

        # 精度結果は必ずスクロール領域に入れる。フォント/DPI/OSで内容が縦に伸びても
        # 縦スクロールで全行（任意誤差・主点精度・バックラッシ）に必ず到達できる＝
        # どんな環境でも切れない（固定高さの当てずっぽうに依存しない根本対策）。
        self.right_scroll = QtWidgets.QScrollArea()
        self.right_scroll.setWidget(right_col)
        self.right_scroll.setWidgetResizable(True)
        self.right_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.right_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.right_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

        # 左（条件＋グラフ）と右（精度結果の縦長列）を横に並べる。右カラムの幅は
        # フォントに合わせて固定し（apply_ui_fonts で設定）、実機のフォントが大きく
        # ても数値やバックラッシ値が切れない。余りはすべて左（グラフ）が使う。
        page = QtWidgets.QWidget()
        ph = QtWidgets.QHBoxLayout(page)
        ph.setContentsMargins(6, 4, 6, 6)
        ph.setSpacing(8)
        ph.addWidget(left_col, 1)           # グラフ側が残り幅をすべて使う
        ph.addWidget(self.right_scroll, 0)  # 右は精度結果（スクロール付き縦長列）
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
        self._cmd_poll_busy = False    # 指令ポーリングの多重起動防止（二重実行対策）
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
        # 前回のワーカーが生きている間は起動しない（list_documents は最長20秒。
        # ポーリング3秒毎に重ねて起動すると、同じ pending 指令を2本のワーカーが
        # 同時に見つけて二重実行＝SwitchBot二度押し・測定作り直しになる）
        if getattr(self, "_cmd_poll_busy", False):
            return
        self._cmd_poll_busy = True
        station = str(self.settings.get("webapp_station") or "").strip()

        def work():
            try:
                try:
                    docs = self.cmd_bus.list_documents()
                except Exception:
                    return
                for doc_id, fields in docs:
                    if (str(fields.get("station") or "") == station
                            and str(fields.get("status") or "") == "pending"
                            and doc_id not in self._handled_commands):
                        # 判定と同時に処理済みへ（判定→後で追加、の隙間を無くす）
                        self._handled_commands.add(doc_id)
                        self._command_signal.emit({"id": doc_id, **fields})
                # 肥大化防止（statusはdoneに更新されるので古いIDは再実行されない）
                if len(self._handled_commands) > 2000:
                    self._handled_commands.clear()
            finally:
                self._cmd_poll_busy = False

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
        # フォント変更後に右カラムの幅と各表の高さを実寸へ合わせ直す
        # （幅だけだと、行が新フォントで高くなったとき固定高が足りず最終行が切れる）
        QtCore.QTimer.singleShot(0, self._shrink_result_tables)
        # 測定順リストと測定条件の高さも新フォントの実寸に合わせ直す
        QtCore.QTimer.singleShot(0, self._fit_order_list)
        QtCore.QTimer.singleShot(0, self._fit_cond_box)

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
            self._populate_order_list()   # 測定順は既定（画面で変更可）
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
            self._populate_order_list()   # 測定順は既定（画面で変更可）
            self.statusBar().showMessage(f"{text} ユーザー回転条件を適用")
            return

        cond = find_entry(self.masters["conditions"], text)
        judge = find_entry(self.masters["judgement"], text)
        self.master_cond = cond
        self.master_judge = judge
        # 測定順の初期値をマスタ（HR/WR/WL/HL）から入れる。無ければ既定順。
        sections = (cond.get("order") if cond else None) or []
        self._populate_order_list(
            [SECTION_TO_SERIES[s] for s in sections if s in SECTION_TO_SERIES] or None)
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

    def is_iso(self):
        """ISO 230-2（JIS B 6190-2）位置決め精度の専用測定モードか。

        再現性の測定プラミングをそのまま使う（回転軸・双方向・複数回接近）ので
        is_repeat() にも含める。ISO固有なのは目標位置の作り方と既定値だけ。
        """
        return self.current_mode() == ISO230_MODE

    def is_repeat(self):
        return self.current_mode() in ("回転再現性", "傾斜再現性") or self.is_iso()

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
        self.order_group.setVisible(show_division)   # 測定順（分割系のみ）
        self.repeat_group.setVisible(show_repeat)
        # ホイールの開始/終了角度は傾斜分割のときだけ（回転は0〜360固定）
        for w in (self.l_wstart2, self.e_wstart, self.l_wend2, self.e_wend):
            w.setVisible(is_tilt)
        self.box_ranges.setVisible(is_tilt and show_division)
        # 単一誤差・隣接誤差の表は分割系のみ（再現性単独では出さない）
        self.table_series2.setVisible(show_division)
        self.corr_bar.setVisible(show_division)  # 補正前/後は分割系のみ
        self.plot_worm.setVisible(show_division)
        self.legend_worm.setVisible(show_division)
        self.update_legends()
        # ISO 230-2 モードの追加UI（目標オフセット）と既定値
        self.c_iso_offset.setVisible(self.is_iso())
        if self.is_iso():
            self._apply_iso_defaults()
        self.plot_wheel.setTitle(
            ("位置決め精度（ISO 230-2 / JIS B 6190-2）" if self.is_iso()
             else "再現性（ブロックごとのばらつき）") if is_repeat else "ホイール")
        # モードを変えたら取込中の測定はキャンセル
        self.view_kind = "repeat" if is_repeat else ("combined" if is_combined else "indexing")
        self.discard_measurement()
        # 群の表示/非表示で測定条件の高さ・右カラムの必要幅が変わるので合わせ直す
        QtCore.QTimer.singleShot(0, self._fit_cond_box)
        QtCore.QTimer.singleShot(0, self._fit_right_col_width)

    def discard_measurement(self):
        """取込中の測定を破棄して初期状態に戻す"""
        self.seq = None
        self._live_shown = ()   # 逐次表示した完了系列の記録をリセット
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
        self.applied_blcorr_field = 0.0
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
        # 分割+再現（combined）も分割データを持つので判定対象にする
        # （indexing 限定だと複合モードでNGでも「OK」と報告してしまう）
        if (self.view_kind not in ("indexing", "combined") or not self.data
                or not self.master_judge):
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
        if self.view_kind not in ("indexing", "combined") or not self.data:
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

    def update_legends(self):
        """凡例ラベルの内容をモードに合わせて更新（色はカーブと一致）。"""
        if self.is_repeat():
            self.legend_wheel.setText(_legend_html([("再現性 CW", "#1f77b4"),
                                                    ("再現性 CCW", "#d62728")]))
            self.legend_worm.setText("")
        else:
            self.legend_wheel.setText(_legend_html([
                (SERIES_LABELS["wheel_cw"], "#1f77b4"),
                (SERIES_LABELS["wheel_ccw"], "#d62728")]))
            self.legend_worm.setText(_legend_html([
                (SERIES_LABELS["worm_cw"], "#2ca02c"),
                (SERIES_LABELS["worm_ccw"], "#ff7f0e")]))

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
        self._frame_plots()

    def _frame_plots(self):
        """グラフの表示範囲を自動できれいに合わせる。

        分割系: ホイールのX軸（指令角度）を測定範囲（回転=0〜360°／傾斜=開始〜終了）に
        固定し、目盛りを 10°刻み（補助5°）で出す。Y（偏差["]）はデータに自動追従。
        ウォームは範囲が細かいので両軸とも自動。マウスでの部分拡大はそのまま使える
        （右クリック→View All、またはダブルクリックで自動範囲へ戻せる）。
        """
        if self.view_kind == "repeat":
            for p in (self.plot_wheel, self.plot_worm):
                vb = p.getViewBox()
                vb.setLimits(xMin=None, xMax=None, yMin=None, yMax=None)
                vb.enableAutoRange(x=True, y=True)
                p.getAxis("bottom").setTickSpacing()   # 自動目盛りへ戻す
            return
        # ホイール: X=角度域に固定・10°刻み、Yは自動
        if self.is_tilt():
            lo, hi = self.e_wstart.value(), self.e_wend.value()
        else:
            lo, hi = 0.0, 360.0
        if hi <= lo:
            hi = lo + 1.0
        span = hi - lo
        vb = self.plot_wheel.getViewBox()
        vb.setLimits(xMin=None, xMax=None)     # 先に制限を外してから範囲を決める
        vb.enableAutoRange(x=False, y=True)
        self.plot_wheel.setXRange(lo, hi, padding=0.02)
        # 測定範囲から大きく離れてパンできないよう制限（拡大縮小は自由・余裕は1範囲ぶん）
        vb.setLimits(xMin=lo - span, xMax=hi + span)
        self.plot_wheel.getAxis("bottom").setTickSpacing(10, 5)  # 主10°・補助5°
        # ウォーム: 自動（細かいので目盛りも自動）
        vw = self.plot_worm.getViewBox()
        vw.enableAutoRange(x=True, y=True)
        vw.setLimits(xMin=None, xMax=None, yMin=None, yMax=None)
        self.plot_worm.getAxis("bottom").setTickSpacing()

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
        self._conn_dev = dev

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
        if self._conn_dev is not None and self._conn_dev is not self.dev:
            # 接続中にプロファイル切替/設定変更で self.dev が差し替わった。
            # 旧デバイスが開いたままだとポートを掴み続け、「接続:」表示なのに
            # 実際は未接続になる。旧を閉じて新しいデバイスで接続し直す
            try:
                self._conn_dev.close()
            except Exception:
                pass
            self._conn_dev = None
            self.connect_device()
            return
        self._conn_dev = None
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

    def show_batch_dialog(self):
        """型式ごとの測定プログラムを一括作成する画面を開く。"""
        BatchProgramDialog(self, self.settings).exec()

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

    def _apply_iso_defaults(self):
        """ISO 230-2 モードに入ったとき、規格推奨のサイクルを既定値として入れる。

        JIS B 6190-2：各方向5回接近、360°軸で 0/90/180/270°を含む8点以上。
        既定 8点・5回・0〜315°（等間隔8点＝0/45/…/315、擬似ランダムオフセット併用）。
        ロード時は setCurrentText の直後に meta 値で上書きされるので影響しない。
        """
        self.e_blocks.setValue(8)
        self.e_repeats.setValue(5)
        self.e_rstart.setValue(0.0)
        self.e_rend.setValue(315.0)

    def repeat_blocks(self):
        """現在の設定での再現ブロック角度リスト。

        再現開始・再現終了・ブロック数で「両端を含む等間隔」を作る。
        例: 開始0・終了270・4箇所 → 0,90,180,270

        ISO 230-2 モードでオフセットが有効なときは、JIS推奨に沿って内側の目標を
        擬似ランダムにずらす（analysis 等の既存モードには影響しない）。
        """
        base = tilt_blocks(self.e_rstart.value(), self.e_rend.value(),
                           self.e_blocks.value())
        if self.is_iso() and self.c_iso_offset.isChecked():
            return iso230.recommended_targets(
                base, self.e_rstart.value(), self.e_rend.value())
        return base

    def _populate_order_list(self, order=None):
        """測定順リストを（順番の系列キー列で）作り直す。順に無い系列は末尾・未チェック。"""
        order = [k for k in (order or SERIES_KEYS) if k in SERIES_KEYS]
        rest = [k for k in SERIES_KEYS if k not in order]
        self.order_list.clear()
        for key in order + rest:
            it = QtWidgets.QListWidgetItem(SERIES_LABELS[key])
            it.setData(QtCore.Qt.UserRole, key)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked if key in order else QtCore.Qt.Unchecked)
            self.order_list.addItem(it)
        self._fit_order_list()

    def _fit_order_list(self):
        """測定順リストを「4行が必ず全部見える」大きさに確定させる。

        行の高さ・文字幅は実フォントから測る（フォント設定を変えても切れない）。
        余白を多めに取り、スクロールバーは出さない（全行が常に見える）。
        """
        lst = getattr(self, "order_list", None)
        if lst is None or lst.count() == 0:
            return
        fm = lst.fontMetrics()
        rows = [lst.sizeHintForRow(i) for i in range(lst.count())]
        row_h = max(max(rows), fm.height() + 6, 16)
        frame = 2 * lst.frameWidth()
        lst.setFixedHeight(row_h * lst.count() + frame + 6)
        text_w = max(fm.horizontalAdvance(SERIES_LABELS[k]) for k in SERIES_KEYS)
        lst.setFixedWidth(text_w + 52 + frame)   # チェックボックス＋余白ぶん

    def _move_order(self, delta):
        row = self.order_list.currentRow()
        if row < 0:
            return
        new = row + delta
        if not (0 <= new < self.order_list.count()):
            return
        self.order_list.insertItem(new, self.order_list.takeItem(row))
        self.order_list.setCurrentRow(new)

    def current_measure_order(self):
        """画面の測定順のうちチェックされた系列キー列（分割系）。空なら既定順。"""
        order = []
        for i in range(self.order_list.count()):
            it = self.order_list.item(i)
            if it.checkState() == QtCore.Qt.Checked:
                order.append(it.data(QtCore.Qt.UserRole))
        return order or list(SERIES_KEYS)

    def build_division_sequence(self):
        wheel_start = self.e_wstart.value() if self.is_tilt() else 0.0
        wheel_end = self.e_wend.value() if self.is_tilt() else 360.0
        # 測定順は画面の並び（測る/測らない含む）に従う。初期値はマスタから入れている。
        return IndexingSequence(
            self.e_wheel.value(),
            self.e_worm.value(),
            self.e_range.value(),
            self.e_start.value(),
            wheel_start,
            wheel_end,
            order=self.current_measure_order(),
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
        self.applied_blcorr_field = 0.0
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
            self.render_live_series()   # 測り終わった系列だけ即結果表示
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
        """バックラッシ手動補正を確定し、結果と合否判定を再計算する。

        補正欄の意味はモードで異なる（旧アプリ準拠）:
          回転 … 入力＝0°位置の実測バックラッシ量。0°位置がその値になるよう全体をシフト
                 （シフト量 = 入力 − 現在の0°位置バックラッシ）。0入力＝補正なし。
          傾斜 … 入力＝減らす量。全体をその分だけ下げる（シフト量 = −入力）。
        いずれも CCW を上下に平行移動するのと同じで、確定後は applied_blcorr に
        「実シフト量」を入れて以降の計算（summarize・総合BL・保存）で共通に足す。
        """
        field = self.e_blcorr.value()
        self.applied_blcorr_field = field                      # 未適用判定は入力欄値で
        if not field:
            self.applied_blcorr = 0.0
        elif self.is_tilt():
            self.applied_blcorr = -field                       # 傾斜: 入れた分だけ減らす
        else:
            b0 = composite_backlash_at_zero(self.data)         # 回転: 0°位置を実測値へ
            self.applied_blcorr = field - b0 if b0 is not None else field
        self.finish()
        if field:
            mode = "傾斜（減算）" if self.is_tilt() else "回転（0°位置を実測値に）"
            self.statusBar().showMessage(
                f"バックラッシ補正 {mode}: 入力{field:+.2f}\" → "
                f"シフト{self.applied_blcorr:+.2f}\" を適用"
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
        data = self.data or {}                 # 測定前(None)に呼ばれても落ちない
        for key in SERIES_KEYS:
            targets, measured = data.get(key, ([], []))
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
                    ys[dirn].append((rep_unwrap(angle, v) - angle) * 3600.0)
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
        self._update_adjacent_markers(devs)

    def _update_adjacent_markers(self, devs):
        """各系列の隣接誤差が最大の点を◆でマーク（トグルON時）。本画面は値ラベル無し。"""
        for plot, it in getattr(self, "_adj_items", []):
            try:
                plot.removeItem(it)
            except Exception:
                pass
        self._adj_items = []
        if not self.b_adjmark.isChecked():
            return
        for key, ser in (devs or {}).items():
            if not ser or len(ser[0]) < 3:
                continue
            plot = self.plot_wheel if key.startswith("wheel") else self.plot_worm
            for it in mark_adjacent_peak(plot, ser[0], ser[1], with_label=False):
                self._adj_items.append((plot, it))

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
        self._frame_plots()   # ロード/測定完了時に表示範囲をきれいに合わせ直す

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

    def _render_accuracy_tables(self, devs, allowed=None, extra=None):
        """精度PP＋傾き表・単一＋隣接表を描く共通処理。

        allowed を渡すとその系列キーだけを描く（測定中の逐次表示用）。extra は上の表の
        末尾に足す (見出し, [(項目,値)]) の並び（主点精度・任意誤差など）。
        """
        judge = self.master_judge or {}
        groups = []  # (glabel, slope_limit, specs[4], series[(label,pp,single,adj,slope)])
        for grp, glabel in (("wheel", "ホイール"), ("worm", "ウォーム")):
            keys = [k for k in (f"{grp}_cw", f"{grp}_ccw")
                    if k in devs and (allowed is None or k in allowed)]
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

        self._render_metric_table(self.table_series, PP_SLOPE_HEADERS, build(0, 3, extra))
        self._render_metric_table(self.table_series2, SINGLE_ADJ_HEADERS, build(1, 2))

    def render_live_series(self):
        """測定中、測り終わった系列だけ精度PP/単一/隣接/傾きを即表示する（分割系）。

        全系列そろう前に「ホイールCWの結果」などをその場で出す。完了系列が変わった
        ときだけ描き直す（毎点は描かない）。最後の1系列は finish 側で全体表として出る。
        """
        if self.view_kind != "indexing" or self.seq is None:
            return
        required = {}
        for step in getattr(self.seq, "steps", []):
            required[step[0]] = required.get(step[0], 0) + 1
        completed = tuple(
            k for k in SERIES_KEYS
            if required.get(k) and len(self.data.get(k, ([], []))[0]) >= required[k])
        if completed == getattr(self, "_live_shown", ()):
            return
        self._live_shown = completed
        if not completed:
            return
        devs = {k: (list(self.data[k][0]), list(deviation_sec(*self.data[k])))
                for k in completed}
        self._render_accuracy_tables(devs, allowed=set(completed))

    def finish_indexing(self):
        self.b_corr.setEnabled(True)
        summary, _ = summarize(self.data, self.applied_blcorr)
        devs = self.display_series_devs()
        # 上の表＝精度PP＋傾き。単一値の主点精度・任意誤差もこちら（精度なので）
        extra = [("― 主点精度（1/N）―", self.main_grid_rows())]
        if self.is_tilt():
            # 画面の任意誤差は補正前/後トグルに連動（ホイール/ウォームPPと同じ基準）
            extra.append(("― 任意誤差（精度=H+W）―",
                          self.tilt_accuracy_rows(corrected=self.show_corrected)))
        self._render_accuracy_tables(devs, extra=extra)

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

    def tilt_accuracy_rows(self, corrected=None):
        """傾斜分割の任意誤差評価（精度 = ホイール精度 + ウォーム精度）。

        corrected=None: 旧アプリ・.KS と同じ固定式（素H＋傾き補正W）。印刷・保存・記録用。
        corrected=False: 画面の補正前（素H＋素W）。
        corrected=True : 画面の補正後（傾き補正H＋傾き補正W）。
        画面の補正前/後トグルに合わせると、ホイール/ウォームPPと同じ基準になる。
        """
        if corrected is None:
            dh, dw = False, True       # 旧アプリ・.KS 互換（固定）
        else:
            dh = dw = bool(corrected)  # 画面の補正前/後トグルに連動
        specs = [("全範囲", None)]
        if self.c_r1.isChecked():
            specs.append(("範囲1", (self.e_r1s.value(), self.e_r1e.value())))
        if self.c_r2.isChecked():
            specs.append(("範囲2", (self.e_r2s.value(), self.e_r2e.value())))
        rows = []
        for label, range_ in specs:
            acc = tilt_accuracy(self.data, range_=range_, detrend_h=dh, detrend_w=dw)
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
        # ISO 230-2 モードは規格の主要値も一覧に出す（詳細は「JIS評価」ボタン）
        if self.is_iso():
            axis = iso230.iso_stats(self.rep_points, self.rep_data)["axis"]
            rows.append(("― JIS B 6190-2 ―", ""))
            for k, label in (("A", "A 双方向位置決め精度"), ("R", "R 双方向繰返し性"),
                             ("E", "E 双方向系統誤差"), ("M", "M 平均双方向誤差"),
                             ("B", "B 反転値")):
                v = axis.get(k)
                rows.append((label, f'{v:.2f}"' if v is not None else "―"))
        self.fill_misc_table(rows)
        if self.is_iso():
            self.statusBar().showMessage(
                "測定完了：ISO 230-2 の主要値を表示中。「JIS評価」で位置別の詳細と"
                "CSV保存（成績書提出値）ができます")

    def _fit_table_height(self, table):
        """全行が確実に見える高さに固定する（空でもヘッダ分だけ＝場所を食わない）。

        各行の高さを明示設定してから、その合計を固定高にする。こうすると OS や
        フォント（実機の游ゴシック等）で行が測定時より高く描画されても、描画高＝
        計算高となり、縦スクロールバー無しの表でも最終行が切れない。
        """
        table.resizeRowsToContents()
        # 縦ヘッダの最小セクションを小さくして、明示した行高がスタイルに押し上げ
        # られない（＝描画高＞計算高で末尾が切れる）ようにしておく
        table.verticalHeader().setMinimumSectionSize(1)
        fm = table.fontMetrics()
        floor = fm.height() + 6  # 文字が収まる最低限＋わずかな余白（詰めすぎない）
        header_h = max(table.horizontalHeader().sizeHint().height(), floor)
        height = header_h + 2 * table.frameWidth() + 4
        for r in range(table.rowCount()):
            rh = max(table.rowHeight(r), floor)
            table.setRowHeight(r, rh)  # 明示設定＝描画もこの高さ。測定との食い違い防止
            height += rh
        table.setFixedHeight(height)

    def _shrink_result_tables(self):
        """結果表を内容（行数）に合わせた高さにする。空ならヘッダ分だけ。"""
        self._fit_table_height(self.table_series)
        self._fit_table_height(self.table_series2)
        self._fit_table_height(self.table_misc)
        self._fit_right_col_width()

    def _fit_cond_box(self):
        """測定条件スクロールの高さを決める。

        中身が収まるモード（再現性など）は中身ぶんだけ＝余白を作らない。
        収まらないモード（傾斜分割+再現など）は画面高さの一定割合で頭打ちにし、
        あふれた分はスクロールで見る。こうするとグラフと右の結果の場所を必ず残せる。
        """
        sc = getattr(self, "cond_scroll", None)
        if sc is None:
            return
        need = self.cond_group.sizeHint().height() + 4
        if sc.horizontalScrollBar().isVisible():
            need += sc.horizontalScrollBar().sizeHint().height()
        # 測定条件はグラフを優先して控えめに（画面高さの約28%で頭打ち）。
        # あふれた分はスクロールで読む。
        cap = max(130, int(self.height() * 0.28))
        h = min(need, cap)
        # 最小・最大を同じ値にする。maximumHeight は「上限」であってウィジェットを
        # 伸ばす力が無く、左カラムの余りは stretch のグラフが全部持っていくため、
        # 最小を上げないと QScrollArea は sizeHint（Qtが内部で頭打ちする値）で止まり、
        # 場所が空いているのに評価範囲などが隠れてスクロール送りになってしまう。
        sc.setMinimumHeight(h)
        sc.setMaximumHeight(h)
        # 操作バーは中身1行ぶんの高さに固定（縦に伸びてグラフを削らない）
        bs = getattr(self, "bar_scroll", None)
        if bs is not None:
            bh = bs.widget().sizeHint().height()
            if bs.horizontalScrollBar().isVisible():
                bh += bs.horizontalScrollBar().sizeHint().height()
            bs.setFixedHeight(bh + 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 窓の大きさが変わったら測定条件の頭打ち高さ・右カラムの頭打ち幅を合わせ直す
        self._fit_cond_box()
        self._fit_right_col_width()

    def _fit_right_col_width(self):
        """右スクロール領域の幅を精度表（系列＋数値2列）の中身ぶんに合わせる。

        全列 ResizeToContents なので各列は中身ぶんの幅。その合計＋縦スクロールバー幅を
        スクロール領域に与えると、数値セルが無駄に大きくならず、縦スクロールバーが
        出ても表が隠れない。バックラッシ表は全幅1行なので幅決定には使わない。
        """
        sc = getattr(self, "right_scroll", None)
        if sc is None:
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
            sbw = sc.verticalScrollBar().sizeHint().width() or 16
            want = max(220, min(need, 480)) + sbw + 2
            # 画面幅の一定割合で頭打ちにする。大きいフォント・狭い画面で右カラムが
            # 画面外へはみ出して「結果が見えない」状態になるのを防ぐ。
            sc.setFixedWidth(min(want, max(240, int(self.width() * 0.34))))

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
        # 「未適用」は入力欄と“適用時の入力欄値”の比較で判定する。applied_blcorr は
        # 変換後の実シフト量（回転: 入力−0°実測、傾斜: −入力）なので、入力欄と
        # 直接比較すると適用済みでも毎回この確認が出てしまう
        if not self.is_repeat() and self.e_blcorr.value() != self.applied_blcorr_field:
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
        self.applied_blcorr_field = 0.0
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
            # 合体測定（分割+再現）は再現性が別CSV（<機番>_再現.csv）に入っている。
            # 一緒に読み戻さないと、モードは「+再現」なのに再現性の結果だけ
            # 静かに消えてしまう（画面・印刷・Web同期から欠落）
            if "+再現" in mode:
                p = Path(path)
                rep_path = p.with_name(f"{p.stem}_再現{p.suffix}")
                loaded_rep = False
                if rep_path.exists():
                    try:
                        _m, rkind, rpayload = load_measurement(str(rep_path))
                        if rkind == "repeat" and rpayload[1]:
                            self.rep_points, self.rep_data = rpayload
                            loaded_rep = True
                    except Exception:
                        pass
                if not loaded_rep:
                    self.statusBar().showMessage(
                        f"注意: 再現性データ（{rep_path.name}）が見つからず、分割のみ読み込みました")

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
        # 入力欄は「入力の意味」（回転=0°位置の実測値、傾斜=減らす量）に逆変換して
        # 復元する。実シフト量をそのまま入れると、ロード後に補正適用を押した際
        # 別の補正として再解釈され、値が黙って変わってしまう
        if not self.applied_blcorr:
            field = 0.0
        elif self.is_tilt():
            field = -self.applied_blcorr
        else:
            b0 = composite_backlash_at_zero(self.data)
            field = (self.applied_blcorr + b0 if b0 is not None
                     else self.applied_blcorr)
        self.e_blcorr.setValue(field)
        self.applied_blcorr_field = self.e_blcorr.value()  # スピンの丸め後の値で一致させる
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
        # 評価範囲1/2をヘッダから復元（保存と同じ DDMMSS パック。22.5°=223000）
        if doc.get("range1_start") is not None and doc.get("range1_end") is not None:
            self.e_r1s.setValue(unpack_dms(doc["range1_start"]))
            self.e_r1e.setValue(unpack_dms(doc["range1_end"]))
            self.c_r1.setChecked(True)
        if doc.get("range2_start") is not None and doc.get("range2_end") is not None:
            self.e_r2s.setValue(unpack_dms(doc["range2_start"]))
            self.e_r2e.setValue(unpack_dms(doc["range2_end"]))
            self.c_r2.setChecked(True)
        else:
            self.c_r2.setChecked(False)
        self.applied_blcorr = 0.0
        self.applied_blcorr_field = 0.0
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

    def show_iso230(self):
        """再現性データを JIS B 6190-2（ISO 230-2）で評価して表示する"""
        if not self.has_repeat_data():
            self.statusBar().showMessage(
                "JIS評価には再現性データが必要です（回転/傾斜再現性、または分割+再現の測定・ロード後）")
            return
        stats = iso230.iso_stats(self.rep_points, self.rep_data)
        meta = {"型式": self.e_model.text().strip(),
                "機番": self.e_machine.text().strip(),
                "測定日": self.e_date.date().toString("yyyy/MM/dd"),
                "測定者": self.e_operator.text().strip(),
                "温度": self.e_temp.text().strip()}
        Iso230Dialog(self, stats, meta).exec()

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
        # FANUCのコメントは英数字のみ。モード名はローマ字にしておく
        # （日本語のまま渡すと消えるか "?" になり、読取エラーの原因になる）
        params["title"] = f"{model} {MODE_TAGS.get(self.current_mode(), '')}".strip()
        params["machine"] = self.e_machine.text().strip()
        ProgramDialog(self, self.settings, params).exec()

    def show_param_dialog(self):
        """受注番号から かんたん作成（ガイド付き）。詳細は中の「詳細設定…」から。"""
        dlg = ParamWizardDialog(self, self.settings,
                                model=self.e_model.text().strip())
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

    def save_report_pdf(self):
        """検査記録（印刷と同じ内容）を検査成績書PDFとして保存する。

        印刷と同じ build_report_document を PDF 出力に流すだけなので、レイアウトは
        既存の印刷と完全に一致する（新しい体裁を作らない＝崩れない）。
        """
        if not self.has_view_data():
            self.statusBar().showMessage("PDFにする測定データがありません")
            return
        from PySide6.QtPrintSupport import QPrinter

        # 印刷と同じく、対象があるときだけピッチエラー補正ページを付けるか確認
        self.include_pcorr = False
        if self.view_kind in ("indexing", "combined") and not self.is_tilt():
            interval = float(self.settings.get("p_interval") or 100000) * 1e-4
            unit = float(self.settings.get("p_unit") or 0.001)
            if compensation_table(self.data, interval, unit):
                answer = QtWidgets.QMessageBox.question(
                    self, "PDF成績書",
                    "ピッチエラー補正（提出用）のページも付けますか？",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No,
                )
                self.include_pcorr = answer == QtWidgets.QMessageBox.Yes

        model = self.e_model.text().strip() or "検査成績書"
        machine = self.e_machine.text().strip()
        stem = f"{model}_{machine}" if machine else model
        default = str(resolve_save_root(self.settings) / f"{stem}_検査成績書.pdf")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "検査成績書PDFの保存", default, "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            printer = QPrinter(QPrinter.HighResolution)
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(path)
            printer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
            printer.setPageOrientation(QtGui.QPageLayout.Landscape)
            document = self.build_report_document()
            document.print_(printer)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "PDF成績書", f"保存に失敗しました:\n{e}")
            return
        self.statusBar().showMessage(f"検査成績書PDFを保存しました: {path}")

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

            def _sec(v):
                # 片方向しか読みが無い等で None のときは「―」（finish_repeatと同じ）
                return f'{v:.2f}"' if v is not None else "―"

            rows = [(f"ブロック{i + 1} ({b['angle']:g}°)",
                     f'CW {_sec(b["cw"])} / CCW {_sec(b["ccw"])}')
                    for i, b in enumerate(rsum["blocks"])
                    if b["cw"] is not None or b["ccw"] is not None]
            rows += [("再現性 CW（全ブロック最大）", _sec(rsum["cw"])),
                     ("再現性 CCW（全ブロック最大）", _sec(rsum["ccw"])),
                     ("再現性 総合", _sec(rsum["overall"]))]
            # ISO 230-2 モードは成績書提出値（A/R/E/M/B）も載せる
            if self.is_iso():
                axis = iso230.iso_stats(self.rep_points, self.rep_data)["axis"]
                rows.append(("― JIS B 6190-2（ISO 230-2）―", ""))
                for k, label in (("A", "A 双方向位置決め精度"), ("R", "R 双方向繰返し性"),
                                 ("E", "E 双方向系統誤差"), ("M", "M 平均双方向誤差"),
                                 ("B", "B 反転値")):
                    v = axis.get(k)
                    rows.append((label, f'{v:.2f}"' if v is not None else "―"))
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
