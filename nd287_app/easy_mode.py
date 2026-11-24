# -*- coding: utf-8 -*-
"""はじめてモード（本当の初心者用の画面。今の画面は「通常」として残す）。

2026-11-24 利用者: 無駄なボタンを押させない・一方通行（もどる／次へ）・制御装置の操作は絵で案内・測定と保存まで。
文字ばかりにせず、イラスト多め。今の画面のデザインにとらわれない。

中身は新しく作らない。今ある機能を呼ぶだけ:
  機械に入れる … かんたん作成（ParamWizardDialog）を1回開く（host.easy_open_wizard）
  取込開始・保存 … 測定の画面と同じ関数（host.start / host.save）
  機械の様子   … easy_machine.MachinePoller（読むだけ。非常停止・操作モード・運転中・アラーム）
手順の決め方は easy_flow（Qt なし）。絵は easy_art（SVG。Qt なし）。
"""
from PySide6 import QtCore, QtGui, QtSvg, QtWidgets

from . import easy_art, easy_flow, flow_guide
from .easy_flow import STEPS, INDEX, CHAPTERS

# 機械の「状態の数値」→ 絵のつまみの位置（easy_art.MODES の番号）
AUT_TO_DIAL = {0: 3, 1: 5, 3: 6, 4: 4, 5: 1, 9: 2}
WATCH_COLORS = {"wait": ("#E9EEF5", "#5A6876"), "ok": ("#E3F4EA", "#16864C"), "stop": ("#FBE9E7", "#C8362B")}
FALLBACK_SEC = 25        # 機械から「できた」を読めないまま、これだけたったら、人が押す小さなボタンを出す

QSS = """
QDialog#easy { background:#EEF1F4; }
QLabel { color:#17212B; }
QLabel#title { font-size:26px; font-weight:900; }
QLabel#line { font-size:17px; }
QLabel#who { font-size:13px; font-weight:bold; border-radius:11px; padding:3px 14px; }
QLabel#watch { font-size:16px; font-weight:bold; border-radius:10px; padding:10px 14px; }
QFrame#art { background:#F4F6F8; border-radius:14px; }
QFrame#card { background:#FFFFFF; border:1px solid #D5DBE1; border-radius:12px; }
QPushButton#next { background:#1560D4; color:#FFFFFF; font-size:19px; font-weight:bold; border-radius:12px; padding:12px 30px; border:0; }
QPushButton#next:disabled { background:#A9BFE6; }
QPushButton#next[go="true"] { background:#16864C; }
QPushButton#back { background:#FFFFFF; color:#5A6876; font-size:15px; border:2px solid #D5DBE1; border-radius:12px; padding:10px 18px; }
QPushButton#back:disabled { color:#B5BEC8; }
QPushButton#act { background:#F0A400; color:#2A1E00; font-size:19px; font-weight:bold; border-radius:12px; padding:14px 22px; border:0; }
QPushButton#act:disabled { background:#EBD9A8; }
QPushButton#sub { background:transparent; color:#5A6876; font-size:13px; text-decoration:underline; border:0; padding:4px; text-align:left; }
QLineEdit { font-size:21px; font-weight:bold; padding:7px 12px; border:2px solid #D5DBE1; border-radius:10px; background:#FFFFFF; color:#17212B; }
QLineEdit:focus { border-color:#1560D4; }
QProgressBar { border:0; border-radius:7px; background:#DCE2E8; height:14px; text-align:center; }
QProgressBar::chunk { background:#1560D4; border-radius:7px; }
"""


class SvgView(QtWidgets.QWidget):
    """SVG を縦横の比を保って、いっぱいに描く。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._r = QtSvg.QSvgRenderer(self)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setMinimumSize(150, 120)
        self._svg = ""

    def set_svg(self, svg):
        if svg != self._svg:
            self._svg = svg
            self._r.load(QtCore.QByteArray(svg.encode("utf-8")))
            self.update()

    def paintEvent(self, _e):
        if not self._r.isValid():
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        size = self._r.defaultSize()
        if size.isEmpty():
            return
        k = min(self.width() / size.width(), self.height() / size.height())
        w, h = size.width() * k, size.height() * k
        self._r.render(p, QtCore.QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h))


class ChapterBar(QtWidgets.QWidget):
    """上の6つの章。済んだ章は緑、いまの章は青。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(46)
        self._p = [0.0] * len(CHAPTERS)
        self._cur = 0

    def set_progress(self, progress, cur):
        if progress != self._p or cur != self._cur:
            self._p, self._cur = list(progress), cur
            self.update()

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        n = len(CHAPTERS)
        gap = 8
        w = (self.width() - gap * (n - 1)) / n
        f = p.font()
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        for k, name in enumerate(CHAPTERS):
            x = k * (w + gap)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#DCE2E8"))
            p.drawRoundedRect(QtCore.QRectF(x, 0, w, 6), 3, 3)
            done = k < self._cur
            col = QtGui.QColor("#16864C" if done else "#1560D4")
            if done or k == self._cur:
                p.setBrush(col)
                p.drawRoundedRect(QtCore.QRectF(x, 0, w * (1.0 if done else self._p[k]), 6), 3, 3)
            p.setPen(QtGui.QColor("#16864C" if done else ("#17212B" if k == self._cur else "#5A6876")))
            p.drawText(QtCore.QRectF(x, 12, w, 24), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                       f"{k + 1} {name}")


class EasyModeDialog(QtWidgets.QDialog):
    """はじめてモード。host（測定の画面）の easy_* を呼ぶだけ。poller は機械の様子を読む（読むだけ）。"""

    def __init__(self, host, poller, parent=None):
        super().__init__(parent if parent is not None else (host if isinstance(host, QtWidgets.QWidget) else None))
        self.setObjectName("easy")
        self.setWindowTitle("はじめてモード")
        self.setStyleSheet(QSS)
        self.host = host
        self.poller = poller
        self.i = 0
        self.history = []
        self.ctx = {"powered_on": False, "cycled": False, "cycled_in_wizard": False, "handoff": False,
                    "skip_machine": False, "counter_reset": True}
        self.track = {"offline": False}
        self.manual = False              # 人が「やりました」を押した（機械から読めない手順）
        self._entered_at = 0.0
        self._phase = 0
        self._shown_key = None
        self._notice = ""
        self._pending_next = False
        self.st = easy_flow.OFFLINE
        self._build()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        QtCore.QTimer.singleShot(0, self._first)

    # ------------------------------------------------------------ 組み立て
    def _build(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(12)
        self.bar = ChapterBar()
        root.addWidget(self.bar)
        mid = QtWidgets.QHBoxLayout()
        mid.setSpacing(22)
        self.art = QtWidgets.QFrame()
        self.art.setObjectName("art")
        al = QtWidgets.QHBoxLayout(self.art)
        al.setContentsMargins(12, 12, 12, 12)
        al.setSpacing(10)
        self.v_map = SvgView()
        self.v_main = SvgView()
        al.addWidget(self.v_map, 11)
        al.addWidget(self.v_main, 14)
        mid.addWidget(self.art, 6)
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(10)
        self.lbl_who = QtWidgets.QLabel("")
        self.lbl_who.setObjectName("who")
        self.lbl_who.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
        right.addWidget(self.lbl_who)
        self.lbl_title = QtWidgets.QLabel("")
        self.lbl_title.setObjectName("title")
        self.lbl_title.setWordWrap(True)
        right.addWidget(self.lbl_title)
        self.lbl_lines = QtWidgets.QLabel("")
        self.lbl_lines.setObjectName("line")
        self.lbl_lines.setWordWrap(True)
        right.addWidget(self.lbl_lines)
        self.extra = QtWidgets.QVBoxLayout()
        self.extra.setSpacing(8)
        right.addLayout(self.extra)
        self.lbl_watch = QtWidgets.QLabel("")
        self.lbl_watch.setObjectName("watch")
        self.lbl_watch.setWordWrap(True)
        right.addWidget(self.lbl_watch)
        self.b_manual = QtWidgets.QPushButton("機械から読めません。やったら、ここを押します")
        self.b_manual.setObjectName("sub")
        self.b_manual.setCursor(QtCore.Qt.PointingHandCursor)
        self.b_manual.clicked.connect(self._manual_done)
        right.addWidget(self.b_manual)
        right.addStretch(1)
        mid.addLayout(right, 5)
        root.addLayout(mid, 1)
        foot = QtWidgets.QHBoxLayout()
        self.b_back = QtWidgets.QPushButton("◀ もどる")
        self.b_back.setObjectName("back")
        self.b_back.clicked.connect(self._back)
        foot.addWidget(self.b_back)
        self.lbl_count = QtWidgets.QLabel("")
        self.lbl_count.setStyleSheet("color:#5A6876; font-size:13px;")
        foot.addWidget(self.lbl_count)
        foot.addStretch(1)
        self.b_next = QtWidgets.QPushButton("次へ ▶")
        self.b_next.setObjectName("next")
        self.b_next.clicked.connect(self._next)
        foot.addWidget(self.b_next)
        root.addLayout(foot)
        self.resize(1000, 640)
        self.setMinimumSize(900, 580)

    # ------------------------------------------------------------ 手順の移り変わり
    def _hctx(self):
        """測定の画面の状態（取込・結果・入力欄）。"""
        try:
            return self.host.easy_ctx()
        except Exception:
            return {}

    def _all_ctx(self):
        c = dict(self.ctx)
        c.update(self._hctx())
        return c

    def _first(self):
        self.st = self.poller.state() if self.poller else easy_flow.OFFLINE
        self._enter(0)

    def _enter(self, i):
        import time
        self.i = i
        s = STEPS[i]
        self.manual = False
        self._entered_at = time.time()
        self.track = {"offline": not self.st["online"]} if s["id"] == "cycle_power" else {"offline": False}
        if s["id"] == "power":
            self.ctx["powered_on"] = True
        if s["id"] == "start1" or (s["id"] == "start2" and False):
            self._start_capture()
        self._shown_key = None
        self._render()

    def _start_capture(self):
        """機械を動かす前に、PC の「取込」を始めておく（値を取りこぼさない。取込開始が先、起動があと）。"""
        h = self._hctx()
        if h.get("capturing"):
            return
        try:
            self.host.easy_start_capture()
        except Exception as e:                          # 取込を始められないとき: 理由を出す（黙らない）
            self._notice = f"取込を始められませんでした: {e}"

    def _next(self):
        s = STEPS[self.i]
        c = self._all_ctx()
        if s["id"] == "finish":
            nm = c.get("next_mode")
            if nm:
                self._next_measurement(nm)
                return
            self.accept()
            return
        j = easy_flow.next_index(self.i, self.st, c)
        if j is None:
            self.accept()
            return
        self.history.append(self.i)
        self._enter(j)

    def _back(self):
        if self.history:
            self._enter(self.history.pop())

    def _next_measurement(self, mode):
        """分割 → 再現性のように、続けて測る。測り方を切り替えて、プログラムの呼び出しからやり直す。"""
        try:
            self.host.easy_next_mode(mode)
        except Exception:
            pass
        self.ctx["finish_seen"] = False
        self.history.append(self.i)
        self._enter(INDEX["prog_select"])

    def _remeasure(self):
        self.history.append(self.i)
        self._enter(INDEX["prog_select"])

    def _manual_done(self):
        self.manual = True
        self._refresh()

    # ------------------------------------------------------------ 画面
    def _clear_extra(self):
        while self.extra.count():
            it = self.extra.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif it.layout() is not None:
                lay = it.layout()
                while lay.count():
                    x = lay.takeAt(0)
                    if x.widget():
                        x.widget().setParent(None)
                        x.widget().deleteLater()

    def _render(self):
        """手順が変わったとき: 文・ボタン・入力欄を作り直す。"""
        s = STEPS[self.i]
        c = self._all_ctx()
        self.lbl_who.setText(easy_flow.WHO.get("auto" if s["kind"] == "run" else s["kind"], "PC で")
                             if s["kind"] != "info" else "")
        self.lbl_who.setVisible(s["kind"] != "info")
        if s["kind"] == "machine":
            self.lbl_who.setStyleSheet("background:#F0A400; color:#2A1E00;")
        else:
            self.lbl_who.setStyleSheet("background:#E9EEF5; color:#1560D4;")
        self._clear_extra()
        self._extra_widgets = {}
        title, lines = s["title"], list(s["lines"])
        sid = s["id"]
        if sid == "prog_select":
            progs = c.get("programs") or ()
            if progs:
                title = f"{flow_guide._o(progs[:1])} を呼び出す"
        if sid == "result":
            ng, spec = c.get("ng", 0), c.get("has_spec", False)
            lines = ([f"規格から外れた所が {ng} か所あります（右の表の赤い字は通常の画面で見られます）。"] if ng else
                     ["規格と比べた所に、外れはありません。"] if spec else
                     ["この測定には、合否の規格が画面に出ていません。値を確かめてから保存してください。"])
            title = "測り終わりました"
        if sid == "finish":
            path = c.get("saved_path") or ""
            lines = ["保存しました" + (f"（{path}）" if path else "") + "。"]
            if c.get("next_mode"):
                lines.append(f"続けて「{c['next_mode']}」を測ります。")
        self.lbl_title.setText(title)
        self.lbl_lines.setText("\n".join(lines))
        self.lbl_lines.setVisible(bool(lines))
        if sid == "machine_in":
            b = self._act_button("受注伝票番号から入れる…", self._run_wizard)
            self._extra_widgets["act"] = b
            sk = QtWidgets.QPushButton("もう機械に入っている（飛ばす）")
            sk.setObjectName("sub")
            sk.setCursor(QtCore.Qt.PointingHandCursor)
            sk.clicked.connect(self._skip_machine)
            self.extra.addWidget(sk)
        elif sid == "fields":
            self._build_fields()
        elif sid == "result":
            b = self._act_button("保存する", self._save)
            self._extra_widgets["act"] = b
            if c.get("ng"):
                r = QtWidgets.QPushButton("もう一度測る")
                r.setObjectName("sub")
                r.setCursor(QtCore.Qt.PointingHandCursor)
                r.clicked.connect(self._remeasure)
                self.extra.addWidget(r)
        elif sid == "measuring":
            pg = QtWidgets.QProgressBar()
            pg.setTextVisible(False)
            self.extra.addWidget(pg)
            self._extra_widgets["pg"] = pg
        self._refresh()

    def _act_button(self, text, slot):
        b = QtWidgets.QPushButton(text)
        b.setObjectName("act")
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.clicked.connect(slot)
        self.extra.addWidget(b)
        return b

    def _build_fields(self):
        h = self.host
        specs = [("機番（保存するファイルの名前になります）", h.e_machine), ("名前（測る人）", h.e_operator),
                 ("温度（℃）", h.e_temp)]
        if not h.e_model.text().strip():
            specs.insert(0, ("型式（例 RWE-200）", h.e_model))
        self._edits = []
        for label, src in specs:
            lb = QtWidgets.QLabel(label)
            lb.setStyleSheet("color:#5A6876; font-size:13px;")
            self.extra.addWidget(lb)
            e = QtWidgets.QLineEdit(src.text())
            e.setMaximumWidth(340)
            e.textChanged.connect(lambda t, src=src: src.setText(t))
            if src is h.e_model:
                e.editingFinished.connect(lambda: self._model_entered())
            self.extra.addWidget(e)
            self._edits.append(e)

    def _model_entered(self):
        try:
            self.host.on_model_entered()
        except Exception:
            pass

    def _refresh(self):
        """毎回（0.5秒ごと）: 絵・機械の様子・「次へ」を、いまの状態に合わせる。"""
        s = STEPS[self.i]
        sid = s["id"]
        c = self._all_ctx()
        st = self.st
        done = easy_flow.done_now(sid, st, c, self.track) or (self.manual and s["kind"] == "machine")
        # 手順の絵
        ph = (self._phase % 2) * 0.9
        self._set_art(s, st, c, done, ph)
        # 機械の様子（読めたものだけ）
        wl = easy_flow.watch_line(sid, st, c, self.track)
        if self._notice:
            wl = ("stop", self._notice)
        if sid == "measuring":
            cur, total = c.get("count") or (0, 0)
            wl = ("wait", f"{cur} ／ {total} か所" if total else "値を待っています…")
            pg = self._extra_widgets.get("pg")
            if pg is not None:
                pg.setMaximum(max(1, total))
                pg.setValue(cur)
            if c.get("finished") and not self._pending_next:
                self._pending_next = True
                QtCore.QTimer.singleShot(1200, self._after_measuring)
        if sid == "start1" and not c.get("capturing") and not self._notice:
            wl = ("wait", "PC の取込を始めています…")
        if wl:
            bg, fg = WATCH_COLORS[wl[0]]
            self.lbl_watch.setStyleSheet(f"background:{bg}; color:{fg};")
            self.lbl_watch.setText(("● " if wl[0] == "wait" and self._phase % 2 else "● ") + wl[1])
            self.lbl_watch.setVisible(True)
        else:
            self.lbl_watch.setVisible(False)
        # 機械から読めないときだけ、人が押す小さなボタン
        import time
        d = s.get("detect")
        waited = time.time() - self._entered_at
        show_manual = bool(s["kind"] == "machine" and d and d != "online" and not done
                           and (not easy_flow.can_detect(d, st) and st["online"] or waited > FALLBACK_SEC))
        self.b_manual.setVisible(show_manual)
        # 「次へ」
        no_detect = s["kind"] == "machine" and not d
        nx = self.b_next
        label, go, enabled = "次へ ▶", False, False
        if sid in ("machine_in", "result"):
            label = "次へ ▶"
            enabled = bool(done)
        elif sid == "measuring":
            label, enabled = "測り終わるまで待ちます", False
        elif sid == "finish":
            label, go, enabled = ("次の測定へ ▶" if c.get("next_mode") else "終わる"), True, True
        elif no_detect:
            label, enabled = "やりました ▶", True
        elif s["kind"] == "info":
            label, enabled = s.get("button") or "次へ ▶", True
        else:
            enabled = bool(done)
            go = enabled
        if sid == "machine_in" and done:
            go = True
        nx.setText(label)
        nx.setEnabled(enabled)
        if nx.property("go") != go:
            nx.setProperty("go", go)
            nx.style().unpolish(nx)
            nx.style().polish(nx)
        act = self._extra_widgets.get("act") if hasattr(self, "_extra_widgets") else None
        if act is not None:
            if sid == "machine_in":
                act.setEnabled(not done)
            if sid == "result":
                act.setEnabled(not done)
        self.b_back.setEnabled(bool(self.history))
        shown = [x for x in range(len(STEPS)) if x == self.i or easy_flow.needed(STEPS[x]["id"], st, c)]
        self.lbl_count.setText(f"{shown.index(self.i) + 1} ／ {len(shown)}" if self.i in shown else "")
        self.bar.set_progress(easy_flow.chapter_progress(self.i), s["ch"])

    def _set_art(self, s, st, c, done, ph):
        sid = s["id"]
        A = easy_art
        aut = st["aut"]
        dial_now = AUT_TO_DIAL.get(aut, 5)
        if s.get("part"):
            self.v_map.setVisible(True)
            mode_idx = A.MODE_INDEX[s["to"]] if (s.get("to") and done) else dial_now
            self.v_map.set_svg(A.panel(s["part"], mode_idx, ph))
            z = s["zoom"]
            if z == "power":
                svg = A.power(done, False, ph)
            elif z == "power_cycle":
                svg = A.power(done, True, ph)
            elif z == "estop":
                svg = A.estop(done, ph)
            elif z == "dial":
                svg = A.dial(A.MODE_INDEX[s["to"]], dial_now, done, ph)
            elif z == "knob":
                svg = A.knob(self.manual, ph)
            elif z == "start":
                svg = A.start(done, False, ph)
            elif z == "start_again":
                svg = A.start(done, True, ph)
            elif z == "keys":
                progs = c.get("programs") or ()
                svg = A.keys(self.manual, flow_guide._o(progs[:1]) or "O0100")
            elif z == "jog":
                svg = A.jog(self.manual, ph)
            else:
                svg = A.blank()
            self.v_main.set_svg(svg)
            return
        self.v_map.setVisible(False)
        art = s.get("art")
        if sid == "counter0":
            svg = A.counter(self.manual, ph)
        elif art == "scene":
            svg = A.scene()
        elif art == "product":
            svg = A.product(bool(c.get("handoff") or c.get("skip_machine")))
        elif art == "table":
            svg = A.table_picture()
        elif art == "measure":
            cur, total = c.get("count") or (0, 0)
            svg = A.measure(cur, total or 36)
        elif art == "verdict":
            ng, spec = c.get("ng", 0), c.get("has_spec", False)
            svg = A.verdict(False if ng else (True if spec else None), saved=bool(c.get("saved")))
        elif art == "verdict_saved":
            svg = A.verdict(True, saved=True)
        else:
            svg = A.blank()
        self.v_main.set_svg(svg)

    # ------------------------------------------------------------ 0.5秒ごと
    def _tick(self):
        self._phase += 1
        if self.poller is not None:
            st = self.poller.state()
            if not st["online"]:
                self.track["offline"] = True
            self.st = st
        s = STEPS[self.i]
        # 電源を入れ直す手順が済んだら記録（次から出さない）
        if s["id"] == "cycle_power" and easy_flow.is_done("cycled", self.st, self.track):
            self.ctx["cycled"] = True
        # 機械の様子が変わって、いまの手順がもう要らなくなったときは、そのまま見せる（勝手に飛ばさない）
        self._refresh()

    def _after_measuring(self):
        self._pending_next = False
        if STEPS[self.i]["id"] == "measuring" and self._hctx().get("finished"):
            j = INDEX["result"]
            self.history.append(self.i)
            self._enter(j)

    # ------------------------------------------------------------ 押すもの
    def _run_wizard(self):
        """かんたん作成を1回開く。開いている間は、機械を読む係を止める（同じ機械に2本つながない）。"""
        if self.poller is not None:
            self.poller.pause()
        try:
            res = self.host.easy_open_wizard() or {}
        finally:
            if self.poller is not None:
                self.poller.resume()
        if res.get("handoff"):
            self.ctx["handoff"] = True
        if res.get("cycled"):
            self.ctx["cycled_in_wizard"] = True
        self._refresh()

    def _skip_machine(self):
        self.ctx["skip_machine"] = True
        self._refresh()

    def _save(self):
        try:
            self.host.easy_save()
        except Exception as e:
            self._notice = f"保存できませんでした: {e}"
        self._refresh()

    # ------------------------------------------------------------ 閉じる
    def done(self, r):
        if self.poller is not None:
            self.poller.stop()
        self._timer.stop()
        super().done(r)

    def reject(self):
        if self._hctx().get("capturing"):
            a = QtWidgets.QMessageBox.question(
                self, "はじめてモード", "測っている途中です。やめますか？（測った値は通常の画面に残ります）",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if a != QtWidgets.QMessageBox.Yes:
                return
        super().reject()
