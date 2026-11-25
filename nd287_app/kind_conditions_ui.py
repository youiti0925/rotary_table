# -*- coding: utf-8 -*-
"""測定条件（種類別）の登録画面。1つの型式の、セミ・モーター／セミ・駆動装置／フル・スケール付きの
条件（回転・傾斜、分割・再現性）と、測る予定を、1画面でまとめて保存する。
保存先は専用の Excel（kind_conditions.FILE_NAME）。Excel に直接書いても同じファイルとして読める。"""
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from . import kind_conditions as K
from .settings import app_dir

SETTING_KEY = "kind_conditions_xlsx"
DEFAULT_PATH = "マスタ/" + K.FILE_NAME

# 画面に出す欄（型式・測定の種類は画面の外側で決める）
ROTARY_FIELDS = [c for c in K.ROTARY_COLUMNS if c not in ("型式", "測定の種類")]
TILT_FIELDS = [c for c in K.TILT_COLUMNS if c not in ("型式", "測定の種類")]


def resolve_path(settings):
    p = Path(str((settings or {}).get(SETTING_KEY) or DEFAULT_PATH))
    return p if p.is_absolute() else app_dir() / p


class _Grid(QtWidgets.QTableWidget):
    """行=欄、列=3つの種類。空欄のままの種類は保存しない。"""

    def __init__(self, fields):
        super().__init__(len(fields), len(K.KINDS))
        self.fields = fields
        self.setHorizontalHeaderLabels(list(K.KINDS))
        self.setVerticalHeaderLabels(fields)
        self.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.setMinimumHeight(60)
        self.setMinimumWidth(300)
        self.setWordWrap(True)

    def clear_values(self):
        for r in range(self.rowCount()):
            for c in range(self.columnCount()):
                self.setItem(r, c, QtWidgets.QTableWidgetItem(""))

    def fill(self, table, model, tilt):
        self.clear_values()
        rows = [r for r in (table.get("tilt" if tilt else "rotary") or []) if r["型式"].upper() == model.upper()]
        for rec in rows:
            kind = rec.get("測定の種類", "")
            if kind not in K.KINDS:
                continue
            c = K.KINDS.index(kind)
            for r, f in enumerate(self.fields):
                self.item(r, c).setText(str(rec.get(f, "")))

    def by_kind(self):
        out = {}
        for c, kind in enumerate(K.KINDS):
            out[kind] = {f: (self.item(r, c).text().strip() if self.item(r, c) else "") for r, f in enumerate(self.fields)}
        return out


class KindConditionDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, settings=None, model=""):
        super().__init__(parent)
        self.settings = settings if settings is not None else {}
        self.setWindowTitle("測定条件を種類別に登録する")
        lay = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(
            "1つの型式に、セミ・モーター／セミ・駆動装置／フル・スケール付きの条件と、測る予定を、まとめて保存します。"
            "空欄の種類は保存しません。同じ内容を Excel に直接書いても使えます。")
        note.setWordWrap(True)
        lay.addWidget(note)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("型式"))
        self.e_model = QtWidgets.QLineEdit(model)
        self.e_model.editingFinished.connect(self.load_model)
        top.addWidget(self.e_model, 1)
        self.b_load = QtWidgets.QPushButton("登録済みを読み込む")
        self.b_load.clicked.connect(self.load_model)
        top.addWidget(self.b_load)
        lay.addLayout(top)
        self.tabs = QtWidgets.QTabWidget()
        self.g_rot, self.g_tilt = _Grid(ROTARY_FIELDS), _Grid(TILT_FIELDS)
        self.plan = {}
        for name, grid in (("回転", self.g_rot), ("傾斜", self.g_tilt)):
            page = QtWidgets.QWidget()
            pl = QtWidgets.QVBoxLayout(page)
            pl.setContentsMargins(0, 0, 0, 0)
            pl.addWidget(grid, 1)
            g = QtWidgets.QGridLayout()
            g.addWidget(QtWidgets.QLabel(f"{name}の測る予定　フルで"), 0, 0)
            full = QtWidgets.QComboBox()
            full.addItems(K.PLAN_CHOICES)
            g.addWidget(full, 0, 1)
            g.addWidget(QtWidgets.QLabel("セミで"), 1, 0)
            semi = QtWidgets.QComboBox()
            semi.addItems(K.PLAN_CHOICES)
            g.addWidget(semi, 1, 1)
            g.addWidget(QtWidgets.QLabel("セミの種類"), 2, 0)
            sk = QtWidgets.QComboBox()
            sk.addItems(["", "セミ・モーター", "セミ・駆動装置"])
            g.addWidget(sk, 2, 1)
            g.setColumnStretch(2, 1)
            if name == "傾斜":
                g.addWidget(QtWidgets.QLabel("（傾斜の予定が空なら、回転と同じ）"), 3, 0, 1, 3)
            pl.addLayout(g)
            self.plan[name] = {"フルで測る": full, "セミで測る": semi, "セミの種類": sk}
            self.tabs.addTab(page, name)
        self.cmb_full = self.plan["回転"]["フルで測る"]            # 以前の名前（回転の予定）
        self.cmb_semi = self.plan["回転"]["セミで測る"]
        self.cmb_semi_kind = self.plan["回転"]["セミの種類"]
        lay.addWidget(self.tabs, 1)
        self.lbl_msg = QtWidgets.QLabel("")
        self.lbl_msg.setWordWrap(True)
        lay.addWidget(self.lbl_msg)
        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        self.b_save = QtWidgets.QPushButton("この型式をまとめて保存")
        self.b_save.clicked.connect(self.save)
        btns.addWidget(self.b_save)
        b_close = QtWidgets.QPushButton("閉じる")
        b_close.clicked.connect(self.accept)
        btns.addWidget(b_close)
        lay.addLayout(btns)
        self.g_rot.clear_values()
        self.g_tilt.clear_values()
        if model:
            self.load_model()

        scr = QtWidgets.QApplication.primaryScreen()               # 開いたときの大きさ（3つの種類の列が全部見える広さ）
        g = scr.availableGeometry() if scr is not None else QtCore.QRect(0, 0, 1024, 768)
        self.resize(max(self.sizeHint().width(), min(900, g.width() - 60)), max(self.sizeHint().height(), min(640, g.height() - 80)))

    def path(self):
        return resolve_path(self.settings)

    def load_model(self, *_):
        model = self.e_model.text().strip()
        self.g_rot.clear_values()
        self.g_tilt.clear_values()
        for cs in self.plan.values():
            for c in cs.values():
                c.setCurrentIndex(0)
        if not model:
            return
        t = K.load(self.path())
        self.g_rot.fill(t, model, False)
        self.g_tilt.fill(t, model, True)
        for name, tilt in (("回転", False), ("傾斜", True)):
            plan = K.find_plan(t, model, False) if not tilt else self._own_plan(t, model)
            if plan:
                for key, cmb in self.plan[name].items():
                    cmb.setCurrentIndex(max(cmb.findText(plan.get(key, "")), 0))
        probs = t.get("problems", [])
        msg = (f"この表の気になる所 {len(probs)} 件:\n" + "".join(f"・{x}\n" for x in probs)) if probs else ""
        self.lbl_msg.setText(msg.strip())

    def _own_plan(self, table, model):
        """傾斜タブに出すのは、傾斜の行そのもの（無ければ空。回転と同じ扱いは読むときだけ）。"""
        m = model.upper()
        for r in table.get("plan") or []:
            if r["型式"].upper() == m and r.get("回転/傾斜") == "傾斜":
                return r
        return None

    def _confirm_delete(self, names):
        r = QtWidgets.QMessageBox.question(
            self, "行を消します", "画面で空にした種類の行を消します。\n\n" + "\n".join(names) + "\n\n消してよいですか？（元の表は「控え」フォルダに残ります）",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        return r == QtWidgets.QMessageBox.Yes

    def save(self):
        model = self.e_model.text().strip()
        if not model:
            self.lbl_msg.setText("型式を入れてください。")
            return False
        plan = {name: {k: c.currentText() for k, c in cs.items()} for name, cs in self.plan.items()}
        try:
            n = K.save_model(self.path(), model, self.g_rot.by_kind(), self.g_tilt.by_kind(), plan, confirm_delete=self._confirm_delete)
        except Exception as e:                       # 黙らない: 理由を画面に出す
            self.lbl_msg.setText(f"保存できませんでした: {e}")
            return False
        self.lbl_msg.setText(f"{model} を保存しました（{n}行）。保存先: {self.path()}（直前の内容は .bak に残しています）")
        return True
