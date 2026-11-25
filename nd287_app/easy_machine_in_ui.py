# -*- coding: utf-8 -*-
"""はじめてモード「機械に入れる」の画面（絵が主役・やさしい言葉）。決め方は easy_machine_in（Qt なし）。

画面の順: ① 伝票番号（製品）→ ② 号機（合う一覧から人が選ぶ）→ ③ つなぐ → ④ 決まったこと（機械を読んで軸を決める）
         → ⑤ 書く → 電源を入れ直す → 確かめる。どの制御装置かは製品が決まってから分かるので、つなぐのは号機が決まったあと。
号機は、合うものの一覧から人が選ぶ（アプリは決めない）。つながっている機械の号機が分かれば、一覧の先頭に ★ で出すだけ。
機械に書くのは、⑤ の「書けるか確かめる」「書く」を人が押したときだけ。それ以外は読むだけ（登録は手元のファイルに書く）。
かんたん作成の窓は、書く中身を作るためだけに、見えない形で使う（Planner）。
"""
import threading

from PySide6 import QtCore, QtWidgets

from . import controllers, easy_art, easy_machine_in as M, first_unit, focas_client, focas_find, focas_ops
from . import wizard_programs as WP

PAGES = ("order", "unit", "connect", "decide", "write", "power", "prog")
STAGES = ("伝票番号", "号機", "つなぐ", "決まったこと", "書く", "プログラム")            # ふつうの順
STAGE_OF = {"order": "伝票番号", "unit": "号機", "connect": "つなぐ", "decide": "決まったこと",
            "write": "書く", "power": "書く", "prog": "プログラム"}
BACK = {"unit": "order", "connect": "unit", "decide": "connect", "write": "decide"}                 # ひとつ前
RED = "background:#FBE9E7; color:#B02A20; border:2px solid #D43A2F; border-radius:10px; padding:8px 12px; font-size:15px; font-weight:bold;"
AMBER = "background:#FFF3CF; color:#7A5200; border-radius:10px; padding:8px 12px; font-size:14px; font-weight:bold;"
GREY = "color:#5A6876; font-size:12px;"
GREENB = "background:#E3F4EA; color:#16864C; border-radius:10px; padding:8px 12px; font-size:15px; font-weight:bold;"


class PlanError(Exception):
    pass


class Planner:
    """かんたん作成の窓を、見えない形で使って、製品を探し、書く中身（plan_fn）を作る（cli_write.build_plan_fn と同じ入口）。"""

    def __init__(self, settings):
        from . import gui
        self.settings = settings
        self.w = gui.ParamWizardDialog(None, settings)

    def controllers(self):
        return self.w._controllers

    def master_path(self):
        return self.w._mpath

    def reload_controllers(self):
        self.w._controllers = controllers.load_controllers(self.w._mpath)
        return self.w._controllers

    def basic_path(self, unit):
        bd = self.w._abs_dir("param_basic_dir")
        return controllers.basic_file_for_unit(bd, unit) if bd else None

    def search(self, seiban):
        """見つかった FANUC の製品（種別ごとに1つ）。戻り値 [{"kind","cap","voltage","motor_id","model","motor","name","values"}]。"""
        from . import wizard_probe
        w = self.w
        w.e_seiban.setText(str(seiban))
        w.search()
        out = []
        for f in w._files:
            if not (f.get("chk") and f["chk"].isChecked()):
                continue
            m = f.get("meta") or {}
            out.append({"kind": f["kind"], "cap": m.get("capacity") or "", "voltage": m.get("voltage") or "",
                        "motor_id": wizard_probe.file_motor_id(f), "model": m.get("model") or "",
                        "motor": m.get("motor") or "", "name": f.get("name") or "", "values": f.get("values") or {}})
        return out

    def candidates(self):
        """見つけた製品を載せられる号機（かんたん作成の候補と同じ計算: 容量・電圧（400V 品は 200V 機なら×2）・
        モーター末尾の規則 -D など。つながっている機械は見ない）。戻り値 {"units","assign","notes"}。"""
        from PySide6 import QtWidgets as W
        w = self.w
        mb = W.QMessageBox
        saved = {n: getattr(mb, n) for n in ("question", "warning", "information", "critical")}
        for n in saved:
            setattr(mb, n, staticmethod(lambda *a, **k: W.QMessageBox.Yes))
        try:
            w.cmb_motor.setCurrentIndex(w.cmb_motor.findData("product"))
            w._update_candidates()
        finally:
            for n, f in saved.items():
                setattr(mb, n, f)
        units, assign = [], {}
        for c, axes in w._cand:
            u = str(c.unit)
            if u not in assign:
                units.append(u)
                assign[u] = list(axes)
        import re

        def plain(n):                                      # 正規表現のまま出さない（^\s*(30|31)i → 30i・31i）
            return re.sub(r"\^\\s\*\(([\d|]+)\)i", lambda m: "・".join(x + "i" for x in m.group(1).split("|")) + " のみ", n)
        return {"units": units, "assign": assign, "notes": [plain(n) for n in getattr(w, "_used_rule_notes", []) or []]}

    def build(self, seiban, unit, rows):
        """決めた軸・モーターで、書く中身 plan_fn を作る。作れなければ PlanError（やさしい一文）。"""
        from PySide6 import QtWidgets as W
        w = self.w
        sel = [f for f in w._files if f.get("chk") and f["chk"].isChecked()]
        if len(sel) != len(rows) or not rows:
            raise PlanError("製品の数と、決めた軸の数が合いません")
        ctl = M.controller_of(w._controllers, unit)
        if ctl is None:
            raise PlanError(f"マスタに {unit}号機がありません")
        letters = [r["axis"] for r in rows]
        if any(not a for a in letters):
            raise PlanError("使う軸が決まっていません")
        miss = [a for a in letters if a not in ctl.caps or not ctl.caps[a]]
        if miss:
            raise PlanError(f"マスタの {unit}号機に {'・'.join(miss)}軸の容量がありません（上の赤い帯の「マスタを直す」を押してください）")
        temp = [r for r in rows if r["motor"] == "temp"]
        w.cmb_motor.setCurrentIndex(w.cmb_motor.findData("temp" if temp else "product"))
        if len(rows) == 1 and temp:
            w.e_motor_id.setText(str(temp[0]["motor_id"]))
        if len(rows) == 2:
            w._motor_kind = {r["kind"]: r["motor"] for r in rows} if temp and len(temp) != 2 else {}
        said = []

        def say(*a, **k):
            said.append(str(a[2]) if len(a) > 2 else "")
            return W.QMessageBox.Yes
        mb = W.QMessageBox
        saved = {n: getattr(mb, n) for n in ("question", "warning", "information", "critical")}
        for n in saved:
            setattr(mb, n, staticmethod(say))
        try:
            w.e_seiban.setText(str(seiban))
            w._update_candidates()
            w._cand = [(ctl, letters)]
            w.cmb_ctrl.blockSignals(True)
            w.cmb_ctrl.clear()
            w.cmb_ctrl.addItem("x", 0)
            w.cmb_ctrl.setCurrentIndex(0)
            w.cmb_ctrl.blockSignals(False)
            w._on_ctrl_changed()
            job = w._machine_job()
            if job is None:
                raise PlanError("書く中身を作れませんでした: " + "／".join(x for x in said if x)[:200])
            (_s, plan_fn), kw = job
            return plan_fn
        finally:
            for n, f in saved.items():
                setattr(mb, n, f)


class _Worker(QtCore.QThread):
    done = QtCore.Signal(object)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            res = self.fn()
        except Exception as e:
            res = {"__error__": getattr(e, "message", None) or str(e), "__exc__": e}
        self.done.emit(res)


class MachineInPanel(QtWidgets.QWidget):
    """「機械に入れる」の画面一式。終わると finished(1) を出し、result() に結果を入れる。"""
    finished = QtCore.Signal(int)
    progress_sig = QtCore.Signal(str)

    def __init__(self, env, parent=None):
        super().__init__(parent)
        from .easy_mode import SvgView
        self.env = env
        self.settings = env["settings"]
        self.sync = bool(env.get("sync"))
        from .settings import app_dir
        self.factory = env.get("client_factory") or (
            lambda st: focas_client.make_client(st, log_path=str(app_dir() / "focas_helper.log")))
        self.port_open = env.get("port_open")
        self.planner = env.get("planner") or Planner(self.settings)
        self.page = "order"
        self.known_units = []        # 入ったとき読んで分かった、つながっている機械の号機（一覧の先頭に ★ を付けるだけ）
        self.cand = {"units": [], "assign": {}, "notes": []}      # 製品に合う号機（マスタ＋モーターの規則）
        self._rd_unit = ""           # いま読んだ機械を確かめた号機
        self.rd = None               # つないで読んだもの
        self.idn = {"units": [], "unit": "", "how": "無し", "unknown": True}
        self.unit = ""
        self.products, self.rows, self.job, self.seiban = [], [], None, ""
        self.trial_ok = False
        self._job_error = ""
        self._writing = False
        self._busy = False
        self._workers = []
        self._phase = 0
        self.power_needed, self.ref_axes, self.cycled = False, [], False
        self._outcome = {}
        self._checked = None
        self._k = 1.0                          # 画面が大きいときの文字の倍率
        self.params_skipped = False           # パラメータは書かない（プログラムだけ送る）
        self.prog_plan, self.prog_rd = None, None
        self.prog_results, self.prog_sent = {}, False
        self._prog_tilt, self._prog_choice = None, None
        self._prog_error = ""
        self._prog_checks = {}
        self._build(SvgView)
        self.progress_sig.connect(self._on_progress)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ---------------------------------------------------------------- 組み立て
    def _build(self, SvgView):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.lbl_stage = QtWidgets.QLabel("")
        self.lbl_stage.setStyleSheet("font-size:14px; color:#5A6876;")
        root.addWidget(self.lbl_stage)
        self.bands = QtWidgets.QVBoxLayout()
        self.bands.setSpacing(6)
        root.addLayout(self.bands)
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(18)
        self.art = SvgView()
        self.art.setMinimumSize(260, 200)
        frame = QtWidgets.QFrame()
        frame.setObjectName("art")
        fl = QtWidgets.QVBoxLayout(frame)
        fl.setContentsMargins(12, 12, 12, 12)
        fl.addWidget(self.art)
        body.addWidget(frame, 5)
        right_w = QtWidgets.QWidget()
        self.right_w = right_w
        right_w.setMinimumWidth(470)
        outer = QtWidgets.QVBoxLayout(right_w)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QtWidgets.QFrame()                    # 白い札にまとめて、上下まん中に置く
        card.setObjectName("rightcard")
        outer.addStretch(1)
        outer.addWidget(card)
        outer.addStretch(1)
        right = QtWidgets.QVBoxLayout(card)
        right.setContentsMargins(24, 20, 24, 20)
        right.setSpacing(8)
        self.lbl_title = QtWidgets.QLabel("")
        self.lbl_title.setObjectName("title")
        self.lbl_title.setWordWrap(True)
        right.addWidget(self.lbl_title)
        self.lbl_text = QtWidgets.QLabel("")
        self.lbl_text.setObjectName("line")
        self.lbl_text.setWordWrap(True)
        right.addWidget(self.lbl_text)
        self.extra = QtWidgets.QVBoxLayout()
        self.extra.setSpacing(8)
        right.addLayout(self.extra)
        self.lbl_msg = QtWidgets.QLabel("")
        self.lbl_msg.setWordWrap(True)
        self.lbl_msg.setVisible(False)
        right.addWidget(self.lbl_msg)
        body.addWidget(right_w, 6)
        root.addLayout(body, 1)
        foot = QtWidgets.QHBoxLayout()
        self.b_prev = QtWidgets.QPushButton("◀ ひとつ前")
        self.b_prev.setObjectName("back")
        self.b_prev.clicked.connect(self._prev)
        foot.addWidget(self.b_prev)
        self.lbl_prog = QtWidgets.QLabel("")
        self.lbl_prog.setStyleSheet(GREY)
        foot.addWidget(self.lbl_prog, 1)
        self.b_main = QtWidgets.QPushButton("次へ ▶")
        self.b_main.setObjectName("next")
        self.b_main.clicked.connect(self._main)
        foot.addWidget(self.b_main)
        root.addLayout(foot)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        k = round(max(1.0, min(1.4, min(self.width() / 950.0, self.height() / 540.0))), 2)
        if k != self._k:
            self._k = k
            if self.rd is not None or self.products:
                self._render()

    def _clear(self, lay):
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif it.layout() is not None:
                self._clear(it.layout())

    def _btn(self, text, slot, style="act"):
        b = QtWidgets.QPushButton(text)
        b.setObjectName(style)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.clicked.connect(lambda _c=False: slot())
        self.extra.addWidget(b)
        return b

    def _lab(self, text, style="", lay=None):
        lb = QtWidgets.QLabel(text)
        lb.setWordWrap(True)
        if style:
            lb.setStyleSheet(style)
        (lay or self.extra).addWidget(lb)
        return lb

    # ---------------------------------------------------------------- 裏で動かす
    def _spawn(self, fn, cb):
        if self.sync:                                  # 試験用: 同じ場所で動かす（失敗は、裏で動かすときと同じ形で渡す）
            try:
                res = fn()
            except Exception as e:
                res = {"__error__": getattr(e, "message", None) or str(e), "__exc__": e}
            cb(res)
            return
        wk = _Worker(fn)
        self._workers.append(wk)
        wk.done.connect(lambda res, w=wk: self._worker_done(w, cb, res))
        wk.start()

    def _worker_done(self, wk, cb, res):
        if wk in self._workers:
            self._workers.remove(wk)
        cb(res)

    def _on_progress(self, text):
        self.lbl_prog.setText(text)

    # ---------------------------------------------------------------- 外から使うもの
    @property
    def _writer(self):
        return self if self._writing else None

    def _close_writer(self, force=False):
        self.lbl_prog.setText("書いている最中です。終わるまで待ってください")
        return False

    def start(self):
        """伝票番号から始める。すでにつながっている機械があれば、こっそり読んで（読むだけ）、号機の一覧の ★ にだけ使う。"""
        self._go("order")
        self._spawn(lambda: M.read_machine(self.settings, self.factory, port_open=self.port_open), self._on_probe)

    def result(self):
        out = {"handoff": True, "cycled": bool(self.cycled), "model": self.env.get("model", ""),
               "unit": self.unit, "program_manual": not self.prog_sent, "panel": True}
        if self.prog_sent and self.prog_plan:                 # 送ったプログラムに合わせて、測定の画面の型式・測り方を入れる
            out["program_handoff"] = WP.handoff(self._masters(), self._model(), self.prog_plan)
        return out

    def reject(self):
        self._timer.stop()
        for wk in list(self._workers):
            try:
                wk.wait(2000)
            except Exception:
                pass

    def stages(self):
        return STAGES

    def stage_index(self):
        return self.stages().index(STAGE_OF[self.page])

    # ---------------------------------------------------------------- 画面の切り替え
    def _go(self, page):
        self.page = page
        self._render()

    def _prev(self):
        if self._busy or self._writing:
            return
        if self.page == "power":
            return
        if self.page == "prog":                      # パラメータを書いたあとは、もどれない（書き終わっている）
            if self.params_skipped and not self.prog_sent:
                self.params_skipped = False
                self._go("connect")
            return
        back = BACK.get(self.page)
        if back:
            self._go(back)

    def _main(self):
        if self._busy or self._writing:
            return
        if self.page == "order":
            self._after_order()
        elif self.page == "unit":
            self._go("connect")
        elif self.page == "connect":
            if self.rd and self.rd.get("ok") and self._rd_unit == self.unit:
                self._go("order" if not self.products else "decide")
            else:
                self._read()
        elif self.page == "decide":
            self._go("write")
        elif self.page == "write":
            self._do_write()
        elif self.page == "power":
            if self._checked and self._checked["all_ok"]:
                self._enter_prog()
            else:
                self._do_check()
        elif self.page == "prog":
            if self.prog_sent and M.prog_all_in(self.prog_plan, self.prog_rd["states"] if self.prog_rd else {}, self.prog_results):
                self._finish()
            else:
                self._prog_do_send()

    def _finish(self):
        self._timer.stop()
        self.finished.emit(1)

    def _tick(self):
        self._phase += 1
        if self.page == "power" and (self._checked is None or not self._checked["ok"]):
            self.art.set_svg(easy_art.power(False, True, (self._phase % 2) * 0.9))

    def _set_bands(self, items):
        """items: [(文, 色, [(ボタン文, 押したとき)])]。"""
        self._clear(self.bands)
        for text, style, buttons in items:
            row = QtWidgets.QHBoxLayout()
            lb = QtWidgets.QLabel(text)
            lb.setWordWrap(True)
            lb.setStyleSheet(style)
            row.addWidget(lb, 1)
            for t, slot in buttons:
                b = QtWidgets.QPushButton(t)
                b.setObjectName("back")
                b.setCursor(QtCore.Qt.PointingHandCursor)
                b.clicked.connect(lambda _c=False, s=slot: s())
                row.addWidget(b)
            self.bands.addLayout(row)

    def _msg(self, text, style=""):
        self.lbl_msg.setText(text)
        self.lbl_msg.setStyleSheet(style)
        self.lbl_msg.setVisible(bool(text))

    def _fs(self, px):
        return int(px * self._k)

    def _render(self):
        k = self._k
        self.right_w.setStyleSheet(
            f"QLabel#title{{font-size:{self._fs(26)}px;}} QLabel#line{{font-size:{self._fs(17)}px;}}"
            f"QPushButton#act{{font-size:{self._fs(19)}px;}} QPushButton#fallback{{font-size:{self._fs(15)}px;}}"
            f"QPushButton#back{{font-size:{self._fs(15)}px;}}")
        self.lbl_stage.setStyleSheet(f"font-size:{self._fs(14)}px; color:#5A6876;")
        self._clear(self.extra)
        self._msg("")
        self.lbl_text.setStyleSheet("")
        i = self.stage_index()
        self.lbl_stage.setText("　".join((f"【{n}】" if k == i else n) + (" ✓" if k < i else "") for k, n in enumerate(self.stages())))
        self.b_prev.setVisible(self.page in BACK
                               or (self.page == "prog" and self.params_skipped and not self.prog_sent))
        getattr(self, "_page_" + self.page)()

    # ---------------------------------------------------------------- ③ つなぐ（④ 機械を読んで確かめる）
    def _on_probe(self, res):
        """入ったときの、こっそり読み。つながっていなくても何も言わない。分かった号機は、一覧の ★ にだけ使う。"""
        if not isinstance(res, dict) or not res.get("ok"):
            return
        try:
            self.planner.reload_controllers()
            self.known_units = [str(u) for u in M.identify(res["probe"], self.planner.controllers())["units"]]
        except Exception:
            self.known_units = []
        if self.page in ("order", "unit"):
            self._render()

    def _read(self):
        self._busy = True
        self.page = "connect"
        self._render()
        self._spawn(lambda: M.read_machine(self.settings, self.factory, port_open=self.port_open), self._on_read)

    def _on_read(self, res):
        self._busy = False
        self.rd = res if isinstance(res, dict) and "ok" in res else {"ok": False, "error": str((res or {}).get("__error__"))}
        if self.rd["ok"]:
            self.planner.reload_controllers()
            self.idn = M.identify(self.rd["probe"], self.planner.controllers())
            self._rd_unit = self.unit
        self._render()

    def _page_connect(self):
        rd = self.rd
        self.b_main.setText("次へ ▶")
        if rd is None or self._busy:
            self._set_bands([])
            self.art.set_svg(easy_art.plug(False))
            if self._busy:
                self.lbl_title.setText("機械につないでいます")
                self.lbl_text.setText("機械を読んでいます（読むだけです。何も書きません）")
                self.b_main.setEnabled(False)
                return
            self.lbl_title.setText(f"{self.unit}号機につなぎます")
            self.lbl_text.setText(M.lan_guide(self.settings, self.unit)[0])
            for line in M.lan_guide(self.settings, self.unit)[1:]:
                self._lab(line, f"color:#5A6876; font-size:{self._fs(14)}px;")
            self._btn("つなぐ", self._read)
            self.b_main.setEnabled(False)
            return
        self.art.set_svg(easy_art.plug(bool(rd["ok"])))
        if not rd["ok"]:
            self._set_bands([])
            self.lbl_title.setText("機械につながりません")
            self.lbl_text.setText(rd["error"])
            for line in M.lan_guide(self.settings, self.unit)[:1]:
                self._lab(line, f"color:#5A6876; font-size:{self._fs(14)}px;")
            self._btn("もう一度つなぐ", self._read)
            self.b_main.setEnabled(False)
            return
        probe, alarms = rd["probe"], rd["alarms"]
        bands = []
        ctl = M.controller_of(self.planner.controllers(), self.unit) if (
            self.idn["how"] == "台帳" and M.fits_unit(self.idn, self.unit) == "ok") else None
        mm = M.mismatch_lines(probe, ctl)
        if mm["lines"]:
            bands.append(("機械の値が、マスタと違います:\n" + "\n".join(mm["lines"]), RED,
                          [(f"マスタの{self.unit}号機の行を直す", lambda mm=mm: self._fix_master(mm))]))
        fit = M.fits_unit(self.idn, self.unit)
        if fit == "other":
            bands += self._other_unit_bands()
        shown, hidden = M.split_alarms(alarms or [], self.settings, self.unit)
        bands += self._battery_bands(shown)
        others = M.other_alarm_lines([a for a in shown if not focas_ops_allowed(self.settings, self.unit, a)])
        if others:
            bands.append(("いま出ているアラーム（書く前に直してください）: " + "、".join(others), AMBER, []))
        if alarms is None:
            bands.append(("アラームを読めませんでした", AMBER, []))
        for a in hidden:
            code = a.get("code")
            bands.append((f"無視中: {code}", GREY, [("無視をやめる", lambda c=code: self._unignore(c))]))
        self._set_bands(bands)
        if fit == "unknown":
            self.lbl_title.setText("この機械は、登録がありません")
            self.lbl_text.setText(f"軸の容量: {M.axes_summary(probe) or '読めません'}")
            self._reg_box()
            self.b_main.setEnabled(False)
            return
        if fit == "other":
            self.lbl_title.setText(f"この機械は、{M.unit_phrase(self.idn)}です")
            self.lbl_text.setText(f"決めた号機は {self.unit}号機です。軸の容量: {M.axes_summary(probe)}")
            self._btn("もう一度つなぐ", self._read, "back")
            self._btn("号機を選び直す", lambda: self._go("unit"), "back")
            self.b_main.setEnabled(False)
            return
        self.lbl_title.setText(f"{self.unit}号機とつながりました")
        self.lbl_text.setText(f"軸の容量: {M.axes_summary(probe)}")
        self._lab(f"{self.unit}号機の機械です。この機械で、続けます。", GREENB)
        self._btn("パラメータは書かない（測定プログラムだけ送る）", self._skip_params, "fallback")
        self.b_main.setEnabled(True)

    def _other_unit_bands(self):
        """つないだ機械が、決めた号機ではなかったとき（赤い帯）。製品に合う別の号機なら、その号機で続けられる。"""
        us = [str(u) for u in self.idn.get("units") or []]
        mine = [u for u in us if u in self.cand["units"]]
        text = (f"つないだ機械は {M.unit_phrase(self.idn)} で、決めた {self.unit}号機ではありません。")
        if mine:
            return [(text + f"（{'・'.join(m + '号機' for m in mine)}は、この製品にも合います）", RED,
                     [(f"{mine[0]}号機として続ける", lambda u=mine[0]: self._use_machine_unit(u))])]
        return [(text + "この製品に合う号機ではありません。決めた号機の機械につなぎ直してください", RED, [])]

    def _use_machine_unit(self, unit):
        self._apply_unit(unit, keep_rd=True)
        self._rd_unit = unit
        self._render()

    def _reg_box(self):
        probe = self.rd["probe"]
        cands = M.unit_candidates_for_register(self.idn, self.planner.controllers(), probe)
        self._lab("BASIC（パラメータの控え）を機械から取って、何号機として登録しますか？（機械には書きません。読むだけです）", "font-size:15px;")
        first = self.unit if (self.unit in cands or not cands) else cands[0]
        e = QtWidgets.QLineEdit(first)
        e.setObjectName("fld")
        e.setMaximumWidth(200)
        e.setPlaceholderText("号機の数字")
        self.extra.addWidget(e)
        self.e_unit = e
        if cands:
            self._lab("容量が合う号機: " + "・".join(cands), GREY)
        self._btn("BASICを取って登録する", self._register)

    def _register(self):
        try:
            u = first_unit.clean_unit(self.e_unit.text())
        except first_unit.FirstUnitError as e:
            self._msg(str(e), RED)
            return
        probe = self.rd["probe"]
        self._busy = True
        self._msg("機械から控えを取っています…（読むだけです）", AMBER)

        def work():
            client = self.factory(self.settings)
            try:
                return first_unit.register_from_machine(
                    self.settings, client, u, host=probe["host"], port=probe["port"],
                    controllers_list=self.planner.controllers(),
                    progress=lambda d, t, n: self.progress_sig.emit(f"{n}"))
            finally:
                try:
                    client.close()
                except Exception:
                    pass
        self._spawn(work, self._on_registered)

    def _on_registered(self, res):
        self._busy = False
        if not isinstance(res, dict) or "__error__" in res:
            self._render()
            self._msg("登録できませんでした: " + str((res or {}).get("__error__") or ""), RED)
            return
        probe = self.rd["probe"]
        probe["unit"], probe["unit_from"] = res["unit"], "registry"
        self.planner.reload_controllers()
        self.idn = M.identify(probe, self.planner.controllers())
        if not self.unit:
            self.unit = self.idn["units"][0] if self.idn["units"] else res["unit"]
        self._rd_unit = self.unit
        self._render()
        self._msg(f"{res['unit']}号機として登録しました", GREENB)

    def _fix_master(self, mm):
        try:
            bak = first_unit.apply_master_fix(self.planner.master_path(), self.unit, mm["caps"], "", order=mm["order"])
        except Exception as e:
            self._msg(f"直せませんでした: {e}", RED)
            return
        self.planner.reload_controllers()
        self._render()
        self._msg(f"マスタの{self.unit}号機の行を直しました（前のものは {bak} に残してあります）", GREENB)

    def _battery_bands(self, alarms):
        """電池のアラームの赤い帯。帯ごとに［この機械では無視する］（押したときだけ。設定の alarm_ignore に残す）。"""
        out = []
        for a in alarms or []:
            lines = M.battery_lines([a])
            if lines:
                code = str(a.get("code") or "").upper()
                out.append((lines[0], RED, [("この機械では無視する", lambda c=code: self._ignore(c))]))
        return out

    def _ignore(self, code):
        M.add_ignore(self.settings, self.unit, code)
        cb = self.env.get("save_settings")
        if cb:
            cb()
        self._render()

    def _unignore(self, code):
        M.remove_ignore(self.settings, self.unit, code)
        cb = self.env.get("save_settings")
        if cb:
            cb()
        self._render()

    # ---------------------------------------------------------------- ② 伝票番号
    def _page_order(self):
        self._set_bands(self._keep_bands())
        if self._busy:                                     # 入ったとき、すでにつながっている機械があるか読んでいる
            self.lbl_title.setText("機械を確かめています")
            self.lbl_text.setText("すでにつながっている機械があるか、読んで確かめています（読むだけです。何も書きません）")
            self.art.set_svg(easy_art.plug(False))
            self.b_main.setEnabled(False)
            return
        self.lbl_title.setText("受注伝票番号を入れてください")
        self.lbl_text.setText("型式は、製品のデータから自動で入ります。")
        e = QtWidgets.QLineEdit(self.seiban or (self.env.get("seiban") or (lambda: ""))())
        e.setObjectName("fld")
        e.setMaximumWidth(340)
        e.setPlaceholderText("例 50014623")
        e.returnPressed.connect(self._search)
        self.extra.addWidget(e)
        self.e_seiban = e
        self._btn("さがす", self._search)
        if self.products:
            self._show_products()
            self.art.set_svg(easy_art.kinds([(p["kind"], f"{p['cap']} {p['voltage']}".strip()) for p in self.products]))
        else:
            self.art.set_svg(easy_art.product(False))
        self.b_main.setText("次へ ▶")
        self.b_main.setEnabled(bool(self.products))

    def _keep_bands(self):
        return []

    def _show_products(self):
        for p in self.products:
            self._lab(f"● {p['kind']}　{p['cap'] or '容量不明'}　{p['voltage']}　{p['name']}", GREENB)
        models = sorted({p["model"] for p in self.products if p["model"]})
        if models:
            self._lab("型式: " + "・".join(models) + "（製品のデータから入れました）", GREY)

    def _search(self):
        sb = self.e_seiban.text().strip()
        if not sb:
            self._msg("受注伝票番号を入れてください", AMBER)
            return
        self.seiban = sb
        self.products = self.planner.search(sb)
        self.job, self.rows, self._job_error = None, [], ""
        self.cand = self.planner.candidates() if self.products else {"units": [], "assign": {}, "notes": []}
        if self.unit not in self.cand["units"]:             # 製品を替えて、選んでいた号機が合わなくなった
            self.unit = ""
            self.rd, self._rd_unit = None, ""
        if not self.products:
            self._render()
            self._msg("この番号の製品データが見つかりません（FANUC の製品だけ、ここでは使えます）", RED)
            return
        models = [p["model"] for p in self.products if p["model"]]
        if models and self.env.get("set_model"):
            self.env["model"] = models[0]
            self.env["set_model"](models[0])
        self._render()

    def _after_order(self):
        """製品が決まった。合う号機の一覧を出す（選ぶのは人）。"""
        self._go("unit")

    # ---------------------------------------------------------------- ② 号機（合う一覧から人が選ぶ）
    def _page_unit(self):
        cand = self.cand
        self._set_bands([])
        self.art.set_svg(easy_art.kinds([(p["kind"], f"{self.unit}号機" if self.unit else "") for p in self.products],
                                        ok=bool(self.unit)))
        self.b_main.setText("つなぐ画面へ ▶")
        self.b_main.setEnabled(bool(self.unit) and not self._busy)
        units = [u for u in cand["units"] if u in self.known_units] + [u for u in cand["units"] if u not in self.known_units]
        why = f"必要な容量: {M.needs_line(self.products)}" + (("　" + "／".join(cand["notes"])) if cand["notes"] else "")
        if not units:
            self.lbl_title.setText("合う号機が、マスタにありません")
            self.lbl_text.setText(why)
            self._lab("この製品を載せられる制御装置が、制御装置マスタにありません（容量・電圧・モーターの規則で、全部外れました）", RED)
            self.b_main.setEnabled(False)
            return
        self.lbl_title.setText("使う号機を選んでください")
        self.lbl_text.setText(f"この製品を動かせる号機は {len(units)} 台です。目の前の機械の号機を選んでください" + ("（★ はいまつながっている機械）" if self.known_units and any(u in self.known_units for u in units) else ""))
        self._lab(why, f"color:#5A6876; font-size:{self._fs(13)}px;")
        lst = QtWidgets.QListWidget()
        lst.setObjectName("units")
        lst.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        lst.setStyleSheet(f"QListWidget{{font-size:{self._fs(17)}px; border:1px solid #D5DBE1; border-radius:8px;}}"
                          f"QListWidget::item{{padding:6px 10px;}} QListWidget::item:selected{{background:#DCEBFF; color:#16324F;}}")
        for u in units:
            ctl = M.controller_of(self.planner.controllers(), u)
            axes = cand["assign"].get(u) or []
            use = "　".join(f"{pr['kind']}→{a}軸" for pr, a in zip(self.products, axes))
            star = "★ " if u in self.known_units else ""
            tail = ""
            lab = f"{star}{u}号機　{(ctl.cnc if ctl is not None else '')}　{use}{tail}"
            it = QtWidgets.QListWidgetItem(lab)
            it.setData(QtCore.Qt.UserRole, u)
            lst.addItem(it)
            if u == self.unit:
                lst.setCurrentItem(it)
        lst.setFixedHeight(min(len(units), 6) * int(self._fs(40)) + 12)
        lst.currentItemChanged.connect(lambda cur, _prev: self._apply_unit(cur.data(QtCore.Qt.UserRole), render=True) if cur else None)
        self.extra.addWidget(lst)
        self.lst_unit = lst

    # ---------------------------------------------------------------- ④ 決まったこと
    def _page_decide(self):
        if not self.rows:
            ctl = M.controller_of(self.planner.controllers(), self.unit)
            self.rows = M.decide_rows(self.products, self.rd["probe"], ctl)
        self._set_bands([])
        self.lbl_title.setText("決まったこと")
        self.lbl_text.setText(M.card_text({"units": [self.unit]}, self.rows))
        self.lbl_text.setStyleSheet(f"font-size:{self._fs(20)}px; font-weight:bold; background:#FFFFFF; border:1px solid #D5DBE1; border-radius:12px; padding:14px;")
        bad = [r for r in self.rows if not r["axis"]]
        self.art.set_svg(easy_art.kinds([(r["kind"], f"{r['axis']}軸" if r["axis"] else "未定") for r in self.rows], ok=not bad))
        self._lab(M.reason_line(self.rows), f"color:#5A6876; font-size:{self._fs(13)}px;")
        if bad:
            self._lab("使う軸を選んでください（容量が合う軸が、機械にありません）", AMBER)
            self._change(render=False)
        elif M.changeable(self.rows):
            self._btn("変える", self._change, "back")
        self.b_main.setText("書く画面へ ▶")
        self.b_main.setEnabled(self.job is not None and not self._busy)
        if not self.planner.basic_path(self.unit):
            self._lab(f"{self.unit}号機の BASIC がありません。BASIC を取って登録すると、書く内容を作れます（この画面の先へは進めます）", AMBER)
            self._reg_inline()
        elif self.job is None and not bad and not self._busy and not self._job_error:
            self._make_job()
            return
        if self._job_error:
            self._lab(self._job_error, RED)
            self._btn("もう一度作る", self._retry_job, "back")
        if self._busy:
            self._lab("機械の今の中身を控えています（読むだけ。1分ほど）…", AMBER)
        if self.job is not None:
            self._list_box()

    def _retry_job(self):
        self._job_error = ""
        self._render()

    def _reg_inline(self):
        e = QtWidgets.QLineEdit(self.unit)
        e.setObjectName("fld")
        e.setMaximumWidth(160)
        self.extra.addWidget(e)
        self.e_unit = e
        self._btn("BASICを取って登録する", self._register_here, "back")

    def _register_here(self):
        self._register()

    def _change(self, render=True):
        if render:
            self._clear(self.extra)
        for k, r in enumerate(self.rows):
            if len(r["alternatives"]) > 1 or not r["axis"]:
                cmb = QtWidgets.QComboBox()
                if not r["axis"]:
                    cmb.addItem(f"{r['kind']} → 選んでください", "")
                for a in r["alternatives"]:
                    cmb.addItem(f"{r['kind']} → {a}軸", a)
                cmb.setCurrentIndex(cmb.findData(r["axis"]))
                cmb.currentIndexChanged.connect(lambda _i, k=k, c=cmb: self._apply_axis(k, c.currentData()))
                self.extra.addWidget(cmb)
        self._btn("決める", self._render, "back")
        if render is False:
            return

    def _apply_axis(self, k, axis):
        if not axis:
            return
        try:
            self.rows = M.change_axis(self.rows, k, axis, self.rd["probe"])
        except M.EasyError as e:
            self._msg(str(e), RED)
            return
        self.job, self._job_error = None, ""

    def _apply_unit(self, unit, keep_rd=False, render=False):
        """号機を替える。つないで確かめた機械は、号機が違えば確かめ直し（keep_rd は、同じ仕様の別の号機に替えるとき）。"""
        if not unit:
            return
        self.unit = unit
        self.job, self._job_error = None, ""
        self.rows = []
        if not keep_rd and self._rd_unit != unit:
            self.rd, self._rd_unit = None, ""
        if render:
            self.b_main.setEnabled(True)

    def _make_job(self):
        """書く内容を作る（かんたん作成と同じ中身）。控えを取る所は裏で、一覧は表で作る。"""
        try:
            plan_fn = self.planner.build(self.seiban, self.unit, self.rows)
        except PlanError as e:
            self._job_error = str(e)
            self._render()
            return
        probe = self.rd["probe"]
        keep = any(r["motor"] == "temp" for r in self.rows)
        self._busy = True
        self.job = None
        self.trial_ok = False
        self._outcome = {}
        self._job_error = ""
        self._render()

        def work():
            client = self.factory(self.settings)
            try:
                return M.backup_step(client, self.settings, unit=self.unit, host=probe["host"], port=probe["port"],
                                     controllers_list=self.planner.controllers(),
                                     progress=lambda d, t, n: self.progress_sig.emit(f"控え {d}/{t} {n}"),
                                     root=self.env.get("backup_root"))
            finally:
                try:
                    client.close()
                except Exception:
                    pass

        def done(bk):
            self._busy = False
            if not isinstance(bk, dict) or "__error__" in bk:
                self._job_error = "控えを取れませんでした: " + str((bk or {}).get("__error__") or "")
                self._render()
                return
            try:
                self.job = M.plan_step(bk, plan_fn, self.settings, seiban=self.seiban, unit=self.unit,
                                       axes=[r["axis"] for r in self.rows], keep_motor=keep, host=probe["host"],
                                       port=probe["port"])
            except Exception as e:
                self._job_error = str(e)
            self._render()
        self._spawn(work, done)

    def _list_box(self):
        rows = M.item_rows(self.job["items"])
        html = "<table cellspacing='4'>" + "".join(
            f"<tr><td>{n}</td><td>{lb}</td><td align='right'>{o}</td><td>→</td><td><b>{v}</b></td></tr>" for n, lb, o, v in rows) + "</table>"
        sc = QtWidgets.QScrollArea()
        sc.setWidgetResizable(True)
        sc.setMaximumHeight(170)
        lb = QtWidgets.QLabel(f"書く内容 {len(rows)} 件（番号・今→新）<br>" + html)
        lb.setStyleSheet("font-size:13px; padding:6px;")
        sc.setWidget(lb)
        self.extra.addWidget(sc)

    # ---------------------------------------------------------------- ④ 書く
    def _page_write(self):
        job = self.job
        needs = M.write_needs(job["items"])
        self.power_needed, self.ref_axes = needs["power_cycle"], needs["ref_axes"]
        self._set_bands([])
        self.lbl_title.setText("機械に書きます")
        self.lbl_text.setText(f"{len(job['items'])} 件を書きます。書く前に、機械の今の中身は控えてあります。")
        self.art.set_svg(easy_art.auto(False, "機械に書きます"))
        for line in needs["lines"]:
            self._lab(line, AMBER)
        self._lab("機械は、非常停止を押したままにしてください。", GREY)
        n = M.trial_count(job)
        if not self.trial_ok:
            self._btn(f"書けるか確かめる（今と同じ値を{n}件書きます）", self._do_trial)
            self.b_main.setText(f"書く（{len(job['items'])} 件）")
            self.b_main.setEnabled(False)
        else:
            self._lab("書けることを確かめました。", GREENB)
            self.b_main.setText(f"書く（{len(job['items'])} 件）")
            self.b_main.setEnabled(not self._outcome.get("state") == "done")
        if self._outcome.get("text"):
            self._msg(self._outcome["text"], RED if self._outcome.get("state") in ("protected", "failed", "stopped") else AMBER)

    def _log_path(self):
        return self.settings.get("focas_write_log_csv") or None

    def _do_trial(self):
        if self._busy:
            return
        self._busy = True
        self._msg("書けるか確かめています…", AMBER)

        def work():
            client = self.factory(self.settings)
            try:
                client.call("connect", {"host": self.job["host"], "port": self.job["port"],
                                        "timeout": focas_client.connect_timeout(self.settings),
                                        "families": dict(self.settings.get("focas_families") or {})})
                return M.run_trial(client, self.settings, self.job, ignore_unit=self.unit,
                                   save=lambda s: self.env.get("save_settings") and self.env["save_settings"](),
                                   log_path=self._log_path())
            finally:
                try:
                    client.close()
                except Exception:
                    pass

        def done(res):
            self._busy = False
            if not isinstance(res, dict) or "__error__" in res:
                res = {"state": "failed", "text": "書き試しができませんでした: " + str((res or {}).get("__error__"))}
            self._outcome = res
            self.trial_ok = res["state"] == "ok"
            self._render()
        self._spawn(work, done)

    def _do_write(self):
        if not self.trial_ok or self._busy:
            return
        self._busy = self._writing = True
        self.b_main.setEnabled(False)
        self._msg("書いています…（終わるまで機械にさわらないでください）", AMBER)

        def work():
            client = self.factory(self.settings)
            try:
                client.call("connect", {"host": self.job["host"], "port": self.job["port"],
                                        "timeout": focas_client.connect_timeout(self.settings),
                                        "families": dict(self.settings.get("focas_families") or {})})
                return M.run_write(client, self.settings, self.job, ignore_unit=self.unit, log_path=self._log_path(),
                                   on_item=lambda i, it: self.progress_sig.emit(f"{i + 1}/{len(self.job['items'])} N{it['no']:05d}"))
            finally:
                try:
                    client.close()
                except Exception:
                    pass

        def done(res):
            self._busy = self._writing = False
            if not isinstance(res, dict) or "__error__" in res:
                res = {"state": "stopped", "text": "書込が止まりました: " + str((res or {}).get("__error__")), "res": None}
            self._outcome = res
            if res["state"] == "protected":
                self.trial_ok = False
            if res["state"] == "done":
                r = res["res"]
                self.power_needed = self.power_needed or r.power_cycle
                if r.ref_return and not self.ref_axes:
                    self.ref_axes = [""]
                self._go("power")
                return
            self._render()
        self._spawn(work, done)

    # ---------------------------------------------------------------- 電源を入れ直す → 確かめる
    def _page_power(self):
        self._set_bands([])
        self.cycled = self.power_needed
        if self._checked is None:
            self.lbl_title.setText("電源を入れ直してください" if self.power_needed else "書けました。読み戻して確かめます")
            self.lbl_text.setText("機械の電源の「切」を押して、画面が消えたら「入」を押します。" if self.power_needed
                                  else "下のボタンで、書いた値を読み戻します。")
            self.art.set_svg(easy_art.power(False, True, 0.0))
            for line in M.write_needs(self.job["items"])["lines"][1:]:
                self._lab(line + "（このあとの手順で行います）", AMBER)
            self.b_main.setText("入れ直しました。確かめる" if self.power_needed else "確かめる")
            self.b_main.setEnabled(True)
            if self._outcome.get("text"):
                self._msg(self._outcome["text"], AMBER)
            return
        ck = self._checked
        if not ck["ok"]:
            self.lbl_title.setText("まだ確かめられません")
            self.lbl_text.setText(ck["error"])
            self.art.set_svg(easy_art.power(False, True, 0.0))
            self.b_main.setText("もう一度確かめる")
            self.b_main.setEnabled(True)
            return
        self.art.set_svg(easy_art.auto(ck["all_ok"], "確かめました" if ck["all_ok"] else "違う所があります"))
        self.lbl_title.setText("書いた値を確かめました" if ck["all_ok"] else "違う所があります")
        self.lbl_text.setText("")
        for r in ck["rows"]:
            mark = "✓" if r["ok"] else "✗"
            self._lab(f"{mark} N{r['no']:05d} {r['label'] or '-'}　機械 {r['now']}／書いた値 {r['want']}"
                      + (f"　（{r['note']}）" if r["note"] else ""), "font-size:13px; color:#16864C;" if r["ok"] else "font-size:13px; color:#B02A20; font-weight:bold;")
        self._set_bands(self._battery_bands(M.split_alarms(ck["alarms"], self.settings, self.unit)[0]))
        self.b_main.setText("次へ ▶" if ck["all_ok"] else "もう一度確かめる")
        self.b_main.setEnabled(True)

    def _do_check(self):
        if self._busy:
            return
        self._busy = True
        self.b_main.setEnabled(False)
        self._msg("機械につないで、読み戻しています…", AMBER)

        def done(res):
            self._busy = False
            if not isinstance(res, dict) or "__error__" in res:
                res = {"ok": False, "error": str((res or {}).get("__error__")), "rows": [], "alarms": []}
            res["all_ok"] = bool(res["ok"] and res["rows"] and all(r["ok"] for r in res["rows"]))
            self._checked = res
            self._render()
        self._spawn(lambda: M.check_step(self.settings, self.factory, self.job), done)

    # ---------------------------------------------------------------- 測定プログラムを送る
    # かんたん作成の段④と同じ中身（easy_machine_in.prog_*）。読むだけで状況を出し、［送る］を押したときだけ書く。
    def _masters(self):
        if getattr(self, "_masters_cache", None) is None:
            try:
                from . import masters
                self._masters_cache = masters.load_masters(self.settings)
            except Exception:
                self._masters_cache = {}
        return self._masters_cache

    def _model(self):
        m = str(self.env.get("model") or "").strip()
        if not m and self.env.get("model_fn"):
            try:
                m = str(self.env["model_fn"]() or "").strip()
            except Exception:
                m = ""
        return m

    def _skip_params(self):
        """［パラメータは書かない］: 書き込みも電源の入れ直しもしないで、測定プログラムだけ送る。"""
        if self._busy:
            return
        self.params_skipped = True
        self._enter_prog()

    def _enter_prog(self):
        self.prog_rd, self.prog_results, self.prog_sent = None, {}, False
        self._prog_error, self._prog_checks = "", {}
        self._prog_tilt, self._prog_choice = None, None
        self._prog_make_plan()
        self._go("prog")

    def _prog_make_plan(self):
        try:
            self.prog_plan = M.prog_plan(self.settings, self._model(), [p["kind"] for p in self.products],
                                         tilt=self._prog_tilt, choice=self._prog_choice, masters_dict=self._masters())
        except Exception as e:
            self.prog_plan = {"problems": [f"測定プログラムを作れませんでした: {e}"], "programs": [], "slots": [], "allowed": [],
                              "choice": "", "ambiguous": False, "tilt": False}

    def _prog_reset(self):
        self.prog_rd, self.prog_results, self.prog_sent, self._prog_error = None, {}, False, ""

    def _prog_kind_changed(self, v):
        self._prog_tilt = bool(v)
        self._prog_choice = None
        self._prog_reset()
        self._prog_make_plan()
        self._render()

    def _prog_choice_changed(self, key):
        if key == (self.prog_plan or {}).get("choice"):
            return
        self._prog_choice = key
        self._prog_reset()
        self._prog_make_plan()
        self._render()

    def _prog_host(self):
        if self.job:
            return self.job["host"], self.job["port"]
        pr = (self.rd or {}).get("probe") or {}
        return pr.get("host"), pr.get("port")

    def _prog_client_work(self, fn):
        def work():
            client = self.factory(self.settings)
            try:
                return fn(client)
            finally:
                try:
                    client.close()
                except Exception:
                    pass
        return work

    def _prog_read(self):
        """機械の中を読む（読むだけ）。つなぐ → 控え → 点検 → 機械の中の確認。"""
        host, port = self._prog_host()
        plan = self.prog_plan
        self._busy = True
        self._prog_error = ""
        self._render()

        def done(res):
            self._busy = False
            if not isinstance(res, dict) or "__error__" in res:
                self._prog_error = str((res or {}).get("__error__") or "機械を読めませんでした")
                self.prog_rd = None
            else:
                self.prog_rd = res
            self._render()
        self._spawn(self._prog_client_work(lambda c: M.prog_read(
            c, self.settings, plan, host=host, port=port, unit=self.unit, controllers_list=self.planner.controllers(),
            progress=lambda d, t, n: self.progress_sig.emit(f"控え {d}/{t} {n}"), root=self.env.get("backup_root"))), done)

    def _prog_reread(self):
        """もう一度確かめる（非常停止をもどしたあとなど）。控えは、取ったものをそのまま使う。"""
        if self._busy:
            return
        if self.prog_rd is None or self.prog_sent:
            self._prog_reset()
            self._render()
            return
        host, port = self._prog_host()
        plan, rd = self.prog_plan, self.prog_rd
        self._busy = True
        self._render()

        def done(res):
            self._busy = False
            if not isinstance(res, dict) or "__error__" in res:
                self._prog_error = str((res or {}).get("__error__") or "機械を読めませんでした")
            else:
                self.prog_rd = res
            self._render()
        self._spawn(self._prog_client_work(lambda c: M.prog_recheck(c, self.settings, plan, rd, host=host, port=port, unit=self.unit)), done)

    def _prog_skip(self):
        if self._busy:
            return
        self._finish()

    def _confirm_overwrite(self, numbers):
        """同じ番号を入れ替えてよいか（人が［入れ替える］を選んだとき、送る直前に確かめる）。試験では差しかえる。"""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("プログラムの入れ替え")
        box.setText("機械にある " + "・".join(f"O{n}" for n in numbers) + " は消えて、新しいものに入れ替わります。\n"
                    "消す前の中身は、いま取った控え（機械のプログラム一覧ごと）に入っています。\nよろしいですか？")
        yes = box.addButton("入れ替える", QtWidgets.QMessageBox.AcceptRole)
        no = box.addButton("やめる", QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is yes

    def _prog_do_send(self):
        """［送る］。1回だけ。機械に書くのは、ここだけ。"""
        plan, rd = self.prog_plan, self.prog_rd
        if self._busy or self.prog_sent or rd is None or rd["chk"]["reasons"] or not plan or plan["problems"]:
            return
        ow = sorted(n for n, ck in self._prog_checks.items() if ck.isChecked())
        if ow and not self._confirm_overwrite(ow):
            return
        host, port = self._prog_host()
        self._busy = self.prog_sent = True
        self._msg("送っています…（終わるまで機械にさわらないでください）", AMBER)
        self.b_main.setEnabled(False)

        def done(res):
            self._busy = False
            if not isinstance(res, dict) or "__error__" in res:
                e = (res or {}).get("__exc__")
                why = "／".join(getattr(e, "reasons", None) or [str((res or {}).get("__error__") or "")])
                self.prog_results = {}
                self._prog_error = "送れませんでした: " + why
                self._render()
                return
            self.prog_results = {it["number"]: it for it in res["items"]}
            self._prog_log(res)
            self._render()
        self._spawn(self._prog_client_work(lambda c: M.prog_send(
            c, self.settings, plan, rd, host=host, port=port, unit=self.unit, overwrite_numbers=ow,
            progress=lambda i, t, n: self.progress_sig.emit(f"{i}/{t} {n}"))), done)

    def _prog_log(self, res):
        plan = self.prog_plan or {}
        try:
            from .settings import app_dir
            path = self.settings.get("prog_log_csv") or str(app_dir() / WP.LOG_NAME)
            WP.append_log(path, self._model(), self.unit or "", plan.get("choice", ""), res.get("items", []),
                          WP.fingerprints(plan["programs"]))
        except Exception:
            pass
        if plan.get("choice"):
            self.settings["wizard_prog_choice"] = plan["choice"]
            cb = self.env.get("save_settings")
            if cb:
                try:
                    cb()
                except Exception:
                    pass

    def _prog_is_all_in(self):
        rd = self.prog_rd
        return bool(self.prog_sent and not self._busy
                    and M.prog_all_in(self.prog_plan, rd["states"] if rd else {}, self.prog_results))

    def _page_prog(self):
        plan = self.prog_plan
        rd = self.prog_rd
        self._set_bands([])
        self.lbl_title.setText("測定プログラムを機械に送ります")
        self.art.set_svg(easy_art.auto(False, "プログラムを送ります"))
        self.b_main.setText("送る")
        self.b_main.setEnabled(False)
        nums = M.prog_numbers(plan)
        if plan["problems"]:
            self.lbl_text.setText("送るプログラムを作れません。")
            for t in plan["problems"]:
                self._lab(t, RED)
            self._lab("型式が入っていないときは、通常の画面で型式を入れてから、もう一度来てください。", GREY)
            self._btn("プログラムは機械で用意する（送らない）", self._prog_skip, "fallback")
            return
        self.lbl_text.setText("送るのは " + "・".join(f"O{n:04d}" for n in nums) + "（型式 " + (self._model() or "-") + "）。"
                              "書く前に、機械の今の中身は控えます。")
        if plan["ambiguous"]:                                  # 製品が無いので、回転か傾斜かを選ぶ
            cb = QtWidgets.QComboBox()
            cb.addItem("回転として作る", False)
            cb.addItem("傾斜として作る", True)
            cb.setCurrentIndex(1 if plan["tilt"] else 0)
            cb.activated.connect(lambda _i, c=cb: self._prog_kind_changed(c.currentData()))
            self.extra.addWidget(cb)
            self._lab(plan["kind_note"], GREY)
        if len(plan["allowed"]) > 1:
            cc = QtWidgets.QComboBox()
            for key, label in WP.CHOICES:
                if key in plan["allowed"]:
                    cc.addItem(label, key)
            cc.setCurrentIndex(max(0, cc.findData(plan["choice"])))
            cc.activated.connect(lambda _i, c=cc: self._prog_choice_changed(c.currentData()))
            self.extra.addWidget(cc)
        self._prog_rows(plan, rd)
        if self._prog_error:
            self._lab(self._prog_error, RED)
            self._btn("もう一度確かめる", self._prog_reread, "back")
        elif self._busy and not self.prog_sent:
            self._lab("機械の今の中身を控えています（読むだけ。1分ほど）…", AMBER)
        elif rd is None and not self._busy:
            self._prog_read()
            return
        elif rd is not None and not self.prog_sent:
            reasons = rd["chk"]["reasons"]
            if reasons:
                self._lab("いまは送れません:\n・" + "\n・".join(reasons), RED)
                if any("非常停止" in r for r in reasons):
                    self._lab("プログラムは、非常停止をもどしてから送ります。", AMBER)
                self._btn("もう一度確かめる", self._prog_reread, "back")
            else:
                self._lab("プログラムは、非常停止をもどした状態で送ります（機械の運転はしません）。", GREY)
                self.b_main.setEnabled(not self._busy)
        if self.prog_sent and not self._busy and not self._prog_error:
            if self._prog_is_all_in():
                self._lab("測定プログラムが機械に入りました。", GREENB)
                self.art.set_svg(easy_art.auto(True, "入りました"))
                self.b_main.setText("次へ ▶")
                self.b_main.setEnabled(True)
            elif self.prog_results:
                held = [it for it in self.prog_results.values() if str(it.get("skip_reason") or "").startswith("hold")]
                if held:
                    self._lab("機械に同じ番号で中身が違うものがあるので、送っていません（本体とサブは、セットで送ります）。"
                              "入れ替えるときは、［入れ替える］にチェックを入れて、もう一度確かめてから送ります。", RED)
                else:
                    self._lab("入っていない、または読み戻しが違うものがあります。", RED)
                self._btn("もう一度確かめる", self._prog_reread, "back")
        if not self._prog_is_all_in():
            self._btn("プログラムは機械で用意する（送らない）", self._prog_skip, "fallback")

    def _prog_rows(self, plan, rd):
        """番号ごとに1行: 名前・いまの機械・結果。同じ番号で中身が違うものには［入れ替える］（選ばなければ書かない）。"""
        labels = M.prog_labels(plan)
        states = (rd or {}).get("states") or {}
        self._prog_checks = {}
        for n in M.prog_numbers(plan):
            lab = labels.get(n, "")
            name = lab if lab.startswith(f"O{n:04d}") else f"O{n:04d}　{lab}".strip()   # 名前が番号から始まるときは、番号を2度出さない
            it = self.prog_results.get(n)
            if it is not None:
                text, kind = WP.row_status(it)
                mark = {"ok": "✓", "bad": "✗", "skip": "－"}.get(kind, "")
                color = {"ok": "#16864C", "bad": "#B02A20", "skip": "#92400E"}.get(kind, "#5A6876")
                if it.get("skip_reason") == "same":
                    mark, color = "✓", "#16864C"
                self._lab(f"{mark} {name}　{text}", f"font-size:{self._fs(16)}px; font-weight:bold; color:{color};")
                continue
            st = states.get(n)
            if rd is None or st is None:
                self._lab(f"・{name}", f"font-size:{self._fs(16)}px;")
                continue
            self._lab(f"・{name}　{WP.state_text(st)}",
                      f"font-size:{self._fs(16)}px; color:" + ("#16864C" if st == "same" else "#92400E" if st != "new" else "#17212B") + ";")
            if st in ("differs", "unknown") and not self.prog_sent:
                ck = QtWidgets.QCheckBox(f"O{n:04d} を入れ替える（機械の O{n:04d} は消えます。消す前の中身は控えに入っています）")
                ck.setStyleSheet("font-size:13px;")
                self.extra.addWidget(ck)
                self._prog_checks[n] = ck


def focas_ops_allowed(settings, unit, alarm):
    """異常ではない（許したアラーム）か。SW0100 など。"""
    return str(alarm.get("code") or "").upper() in focas_ops.allowed_codes(settings, unit)
