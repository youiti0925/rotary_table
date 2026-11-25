# -*- coding: utf-8 -*-
"""はじめてモード「機械に入れる」の画面（絵が主役・やさしい言葉）。決め方は easy_machine_in（Qt なし）。

画面の順: ① つなぐ → ② 伝票番号 → ③ 決まったこと → ④ 書く → 電源を入れ直す → 確かめる。
機械に書くのは、④ の「書けるか確かめる」「書く」を人が押したときだけ。それ以外は読むだけ（登録は手元のファイルに書く）。
かんたん作成の窓は、書く中身を作るためだけに、見えない形で使う（Planner）。
"""
import threading

from PySide6 import QtCore, QtWidgets

from . import controllers, easy_art, easy_machine_in as M, first_unit, focas_client, focas_find, focas_ops

PAGES = ("connect", "order", "decide", "write", "power")
STAGE_NAMES = ("つなぐ", "伝票番号", "決まったこと", "書く")
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
        self.page = "connect"
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
        right_w.setMinimumWidth(470)
        right = QtWidgets.QVBoxLayout(right_w)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        right.addStretch(1)
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
        right.addStretch(1)
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
        if self.sync:
            cb(fn())
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
        self._read()

    def result(self):
        return {"handoff": True, "cycled": bool(self.cycled), "model": self.env.get("model", ""),
                "unit": self.unit, "program_manual": True}

    def reject(self):
        self._timer.stop()
        for wk in list(self._workers):
            try:
                wk.wait(2000)
            except Exception:
                pass

    def stage_index(self):
        return {"connect": 0, "order": 1, "decide": 2, "write": 3, "power": 3}[self.page]

    # ---------------------------------------------------------------- 画面の切り替え
    def _go(self, page):
        self.page = page
        self._render()

    def _prev(self):
        if self._busy or self._writing:
            return
        i = PAGES.index(self.page)
        if self.page == "power":
            return
        if i > 0:
            self._go(PAGES[i - 1])

    def _main(self):
        if self._busy or self._writing:
            return
        if self.page == "connect":
            self._go("order")
        elif self.page == "order":
            self._go("decide")
        elif self.page == "decide":
            self._go("write")
        elif self.page == "write":
            self._do_write()
        elif self.page == "power":
            if self._checked and self._checked["all_ok"]:
                self._finish()
            else:
                self._do_check()

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

    def _render(self):
        self._clear(self.extra)
        self._msg("")
        self.lbl_text.setStyleSheet("")
        i = self.stage_index()
        self.lbl_stage.setText("　".join((f"【{n}】" if k == i else n) + (" ✓" if k < i else "") for k, n in enumerate(STAGE_NAMES)))
        self.b_prev.setVisible(self.page in ("order", "decide", "write"))
        getattr(self, "_page_" + self.page)()

    # ---------------------------------------------------------------- ① つなぐ
    def _read(self):
        self.page = "connect"
        self._busy = True
        self._render()
        self.lbl_title.setText("機械につないでいます")
        self.lbl_text.setText("機械を読んでいます（読むだけです。何も書きません）")
        self.art.set_svg(easy_art.plug(False))
        self.b_main.setEnabled(False)
        self._spawn(lambda: M.read_machine(self.settings, self.factory, port_open=self.port_open), self._on_read)

    def _on_read(self, res):
        self._busy = False
        self.rd = res if isinstance(res, dict) and "ok" in res else {"ok": False, "error": str((res or {}).get("__error__"))}
        if self.rd["ok"]:
            self.planner.reload_controllers()
            self.idn = M.identify(self.rd["probe"], self.planner.controllers())
            us = self.idn["units"]
            self.unit = us[0] if us else ""
        self._render()

    def _page_connect(self):
        rd = self.rd
        self.art.set_svg(easy_art.plug(bool(rd and rd["ok"])))
        self.b_main.setText("次へ ▶")
        if rd is None or self._busy:
            self._set_bands([])
            self.lbl_title.setText("機械につないでいます")
            self.lbl_text.setText("機械を読んでいます（読むだけです。何も書きません）")
            self.b_main.setEnabled(False)
            return
        if not rd["ok"]:
            self._set_bands([])
            self.lbl_title.setText("機械につながりません")
            self.lbl_text.setText(rd["error"])
            self._btn("もう一度つなぐ", self._read)
            self.b_main.setEnabled(False)
            return
        probe, alarms = rd["probe"], rd["alarms"]
        bands = []
        ctl = M.controller_of(self.planner.controllers(), self.unit) if self.idn["how"] == "台帳" else None
        mm = M.mismatch_lines(probe, ctl)
        if mm["lines"]:
            bands.append(("機械の値が、マスタと違います:\n" + "\n".join(mm["lines"]), RED,
                          [(f"マスタの{self.unit}号機の行を直す", lambda mm=mm: self._fix_master(mm))]))
        shown, hidden = M.split_alarms(alarms or [], self.settings, self.unit)
        for line in M.battery_lines(shown):
            bands.append((line, RED, []))
        others = M.other_alarm_lines([a for a in shown if not focas_ops_allowed(self.settings, self.unit, a)])
        if others:
            bands.append(("いま出ているアラーム（書く前に直してください）: " + "、".join(others), AMBER, []))
        if alarms is None:
            bands.append(("アラームを読めませんでした", AMBER, []))
        for a in hidden:
            code = a.get("code")
            bands.append((f"無視中: {code}", GREY, [("無視をやめる", lambda c=code: self._unignore(c))]))
        self._set_bands(bands)
        if self.idn["unknown"]:
            self.lbl_title.setText("この機械は、登録がありません")
            self.lbl_text.setText(f"軸の容量: {M.axes_summary(probe) or '読めません'}")
            self._reg_box()
            self.b_main.setEnabled(False)
            return
        phrase = M.unit_phrase(self.idn)
        self.lbl_title.setText(f"{phrase}とつながりました")
        self.lbl_text.setText(f"軸の容量: {M.axes_summary(probe)}")
        self._lab("この機械で、続けます。", GREENB)
        self.b_main.setEnabled(True)

    def _reg_box(self):
        probe = self.rd["probe"]
        cands = M.unit_candidates_for_register(self.idn, self.planner.controllers(), probe)
        self._lab("BASIC（パラメータの控え）を機械から取って、何号機として登録しますか？（機械には書きません。読むだけです）", "font-size:15px;")
        e = QtWidgets.QLineEdit(cands[0] if cands else "")
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
        self.unit = self.idn["units"][0] if self.idn["units"] else res["unit"]
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

    def _unignore(self, code):
        M.remove_ignore(self.settings, self.unit, code)
        cb = self.env.get("save_settings")
        if cb:
            cb()
        self._render()

    # ---------------------------------------------------------------- ② 伝票番号
    def _page_order(self):
        self._set_bands(self._keep_bands())
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
        if not self.products:
            self._render()
            self._msg("この番号の製品データが見つかりません（FANUC の製品だけ、ここでは使えます）", RED)
            return
        models = [p["model"] for p in self.products if p["model"]]
        if models and self.env.get("set_model"):
            self.env["model"] = models[0]
            self.env["set_model"](models[0])
        self._render()

    # ---------------------------------------------------------------- ③ 決まったこと
    def _page_decide(self):
        if not self.rows:
            ctl = M.controller_of(self.planner.controllers(), self.unit)
            self.rows = M.decide_rows(self.products, self.rd["probe"], ctl)
        self._set_bands([])
        self.lbl_title.setText("決まったこと")
        self.lbl_text.setText(M.card_text(self.idn, self.rows))
        self.lbl_text.setStyleSheet("font-size:18px; font-weight:bold; background:#FFFFFF; border:1px solid #D5DBE1; border-radius:12px; padding:12px;")
        bad = [r for r in self.rows if not r["axis"]]
        self.art.set_svg(easy_art.kinds([(r["kind"], f"{r['axis']}軸" if r["axis"] else "未定") for r in self.rows], ok=not bad))
        self._lab(M.reason_line(self.rows), GREY)
        if bad:
            self._lab("使う軸を選んでください（容量が合う軸が、機械にありません）", AMBER)
            self._change(render=False)
        elif M.changeable(self.rows) or len(self.idn["units"]) > 1:
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
        if len(self.idn["units"]) > 1:
            cu = QtWidgets.QComboBox()
            for u in self.idn["units"]:
                cu.addItem(f"{u}号機", u)
            cu.setCurrentIndex(cu.findData(self.unit))
            cu.currentIndexChanged.connect(lambda _i, c=cu: self._apply_unit(c.currentData()))
            self.extra.addWidget(cu)
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

    def _apply_unit(self, unit):
        self.unit = unit
        self.job, self._job_error = None, ""

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
        bands = [(line, RED, []) for line in M.battery_lines(M.split_alarms(ck["alarms"], self.settings, self.unit)[0])]
        self._set_bands(bands)
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


def focas_ops_allowed(settings, unit, alarm):
    """異常ではない（許したアラーム）か。SW0100 など。"""
    return str(alarm.get("code") or "").upper() in focas_ops.allowed_codes(settings, unit)
