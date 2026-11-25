# -*- coding: utf-8 -*-
"""はじめてモード（本当の初心者用の画面。今の画面は「通常」として残す）。

2026-11-24 利用者: 無駄なボタンを押させない・一方通行（もどる／次へ）・制御装置の操作は絵で案内・測定と保存まで。
文字ばかりにせず、イラスト多め。今の画面のデザインにとらわれない。

中身は新しく作らない。今ある機能を呼ぶだけ:
  機械に入れる … かんたん作成（ParamWizardDialog）を、この画面の中に入れる（host.easy_make_wizard）
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
QLabel#title { font-size:26px; font-weight:900; color:#17212B; }
QLabel#line { font-size:17px; color:#17212B; }
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
QLineEdit#fld { font-size:21px; font-weight:bold; padding:7px 12px; border:2px solid #D5DBE1; border-radius:10px; background:#FFFFFF; color:#17212B; }
QLineEdit#fld:focus { border-color:#1560D4; }
QProgressBar#pbar { border:0; border-radius:7px; background:#DCE2E8; height:14px; text-align:center; }
QProgressBar#pbar::chunk { background:#1560D4; border-radius:7px; }
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


class TileButton(QtWidgets.QAbstractButton):
    """入り口の大きなタイル（絵と短い言葉。押すと、その流れが始まる）。"""

    def __init__(self, title, sub, svg, parent=None, badge=""):
        super().__init__(parent)
        self._title, self._sub, self._badge = title, sub, badge
        self._r = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")), self)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setMinimumSize(220, 150)
        self.setFocusPolicy(QtCore.Qt.NoFocus)           # 青い枠は「マウスが乗っている」だけ（選んだ印と見まちがえない）
        self.setAccessibleName(title)

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(self.rect()).adjusted(2, 2, -2, -2)
        hot = self.underMouse()
        p.setPen(QtGui.QPen(QtGui.QColor("#1560D4" if hot else "#D5DBE1"), 3 if hot else 2))
        p.setBrush(QtGui.QColor("#EAF2FF" if hot else "#FFFFFF"))
        p.drawRoundedRect(r, 16, 16)
        pic_h = r.height() - 58
        size = self._r.defaultSize()
        if pic_h > 20 and not size.isEmpty():
            k = min((r.width() - 20) / size.width(), pic_h / size.height())
            w, h = size.width() * k, size.height() * k
            self._r.render(p, QtCore.QRectF(r.center().x() - w / 2, r.top() + 8, w, h))
        f = p.font()
        f.setPixelSize(20)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QtGui.QColor("#17212B"))
        p.drawText(QtCore.QRectF(r.left(), r.bottom() - 54, r.width(), 28), QtCore.Qt.AlignCenter, self._title)
        f.setPixelSize(13)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QtGui.QColor("#5A6876"))
        p.drawText(QtCore.QRectF(r.left(), r.bottom() - 28, r.width(), 22), QtCore.Qt.AlignCenter, self._sub)
        if self._badge:                                   # 「はじめてはこれ」の札
            f.setPixelSize(14)
            f.setBold(True)
            p.setFont(f)
            bw = p.fontMetrics().horizontalAdvance(self._badge) + 24
            br = QtCore.QRectF(r.left() + 12, r.top() + 12, bw, 28)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#16864C"))
            p.drawRoundedRect(br, 14, 14)
            p.setPen(QtGui.QColor("#FFFFFF"))
            p.drawText(br, QtCore.Qt.AlignCenter, self._badge)


class ChapterBar(QtWidgets.QWidget):
    """上の6つの章。済んだ章は緑、いまの章は青。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(46)
        self._names = list(CHAPTERS)
        self._p = [0.0] * len(CHAPTERS)
        self._cur = 0

    def set_progress(self, progress, cur, names=None):
        names = list(names or self._names)
        if progress != self._p or cur != self._cur or names != self._names:
            self._p, self._cur, self._names = list(progress), cur, names
            self.update()

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        n = max(1, len(self._names))
        gap = 8
        w = (self.width() - gap * (n - 1)) / n
        f = p.font()
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        for k, name in enumerate(self._names):
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
        self.flow = "home"               # home / measure / program / results / alarm
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
        self._wiz = None                 # 中に入れたかんたん作成
        self._wiz_closing = False
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
        self.right_w = QtWidgets.QWidget()
        right = QtWidgets.QVBoxLayout(self.right_w)
        right.setContentsMargins(0, 0, 0, 0)
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
        mid.addWidget(self.right_w, 5)
        self.embed = QtWidgets.QWidget()           # 「機械に入れる」: かんたん作成を、この画面の中に入れる
        el = QtWidgets.QVBoxLayout(self.embed)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(8)
        self.strip = QtWidgets.QFrame()              # 絵と短い言葉（かんたん作成の長い説明の代わり）
        self.strip.setObjectName("art")
        sl = QtWidgets.QHBoxLayout(self.strip)
        sl.setContentsMargins(12, 8, 12, 8)
        sl.setSpacing(16)
        self.v_strip = SvgView()
        self.v_strip.setFixedSize(200, 120)
        sl.addWidget(self.v_strip)
        sv = QtWidgets.QVBoxLayout()
        sv.setSpacing(4)
        self.lbl_s_title = QtWidgets.QLabel("")
        self.lbl_s_title.setStyleSheet("font-size:20px; font-weight:900; color:#17212B;")
        self.lbl_s_say = QtWidgets.QLabel("")
        self.lbl_s_say.setWordWrap(True)
        self.lbl_s_say.setObjectName("watch")
        self.lbl_s_detail = QtWidgets.QLabel("")
        self.lbl_s_detail.setWordWrap(True)
        self.lbl_s_detail.setStyleSheet("font-size:12px; color:#5A6876;")
        for wd in (self.lbl_s_title, self.lbl_s_say, self.lbl_s_detail):
            sv.addWidget(wd)
        sl.addLayout(sv, 1)
        el.addWidget(self.strip)
        self.embed.setVisible(False)
        mid.addWidget(self.embed, 1)
        self.home = QtWidgets.QWidget()               # 入り口: 絵のタイルから選ぶ
        hg = QtWidgets.QGridLayout(self.home)
        hg.setContentsMargins(0, 0, 0, 0)
        hg.setSpacing(18)
        pics = {"measure": easy_art.tile_measure, "program": easy_art.tile_program,
                "results": easy_art.tile_results, "alarm": easy_art.tile_alarm}
        self.tiles = {}
        for n, (fid, fl) in enumerate(easy_flow.FLOWS.items()):
            t = TileButton(fl["title"], fl["sub"], pics[fid](), badge=fl.get("badge", ""))
            t.clicked.connect(lambda _c=False, f=fid: self._start_flow(f))
            hg.addWidget(t, n // 2, n % 2)
            self.tiles[fid] = t
        self.home.setVisible(False)
        mid.addWidget(self.home, 1)
        root.addLayout(mid, 1)
        foot = QtWidgets.QHBoxLayout()
        self.b_back = QtWidgets.QPushButton("◀ もどる")
        self.b_back.setObjectName("back")
        self.b_back.clicked.connect(self._back)
        foot.addWidget(self.b_back)
        self.b_skip = QtWidgets.QPushButton("もう機械に入っている（飛ばす）")
        self.b_skip.setObjectName("sub")
        self.b_skip.setCursor(QtCore.Qt.PointingHandCursor)
        self.b_skip.clicked.connect(self._skip_machine)
        self.b_skip.setVisible(False)
        foot.addWidget(self.b_skip)
        self.lbl_count = QtWidgets.QLabel("")
        self.lbl_count.setStyleSheet("color:#5A6876; font-size:13px;")
        foot.addWidget(self.lbl_count)
        foot.addStretch(1)
        self.b_next = QtWidgets.QPushButton("次へ ▶")
        self.b_next.setObjectName("next")
        self.b_next.clicked.connect(self._next)
        foot.addWidget(self.b_next)
        root.addLayout(foot)
        self.resize(1100, 700)
        scr = QtWidgets.QApplication.primaryScreen()
        if scr is not None:                          # 小さい画面（1024x768・1366x768）に収める
            g = scr.availableGeometry()
            self.resize(min(1100, g.width() - 20), min(700, g.height() - 40))
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
        self._enter(INDEX["home"])

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

    def _start_flow(self, flow):
        """ホームのタイルが押された: その流れの最初の手順へ。"""
        c = self._all_ctx()
        j = easy_flow.first_index(self.st, c, flow)
        if j is None:
            return
        self.flow = flow
        self.history = [INDEX["home"]]
        if flow == "measure":
            self.ctx.update(handoff=False, skip_machine=False)
        self._enter(j)

    def _go_home(self):
        if self._wiz is not None and not self._close_wizard():
            return
        self.flow = "home"
        self.history = []
        self._enter(INDEX["home"])

    def _next(self):
        s = STEPS[self.i]
        c = self._all_ctx()
        if s["id"] == "finish":
            nm = c.get("next_mode")
            if nm:
                self._next_measurement(nm)
                return
            self._go_home()
            return
        j = easy_flow.next_index(self.i, self.st, c, self.flow)
        if j is None or s["id"] in ("output", "alarm_look", "prog_done"):
            self._go_home()
            return
        self.history.append(self.i)
        self._enter(j)

    def _back(self):
        if self._wiz is not None and not self._close_wizard():
            return
        if self.history:
            j = self.history.pop()
            if STEPS[j]["id"] == "home":
                self.flow = "home"
            self._enter(j)

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
        if s["id"] != "machine_in" and self._wiz is not None and not self._close_wizard():
            self.i = INDEX["machine_in"]            # 書いている最中にやめない、と答えたとき
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
            lines = ([f"外れた所が {ng} か所あります。"] if ng else
                     ["ぜんぶ規格の中です。"] if spec else
                     ["規格は画面に出ていません。値を見て、保存します。"])
            title = "測り終わりました"
        if sid == "view_result":
            ng, spec = c.get("view_ng", 0), c.get("view_spec", False)
            title = "規格から外れた所があります" if ng else ("ぜんぶ規格の中です" if spec else "結果")
            lines = [f"外れた所は {ng} か所です。通常の画面で、くわしく見られます。"] if ng else []
        if sid == "finish":
            path = c.get("saved_path") or ""
            lines = [("保存先: " + path) if path else "保存しました。"]
            if c.get("next_mode"):
                lines.append(f"つぎは「{c['next_mode']}」。")
        self.lbl_title.setText(title)
        self.lbl_lines.setText("\n".join(lines))
        self.lbl_lines.setVisible(bool(lines))
        if sid == "machine_in":
            self._open_wizard_inside()
        elif sid == "fields":
            self._build_fields()
        elif sid == "open_past":
            self._extra_widgets["act"] = self._act_button("結果をさがす…", self._open_past)
        elif sid == "output":
            self._act_button("印刷する", self._print)
            self._act_button("PDFにする", self._pdf)
        elif sid == "alarm_look":
            self._build_alarm()
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
            pg.setObjectName("pbar")
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
            e.setObjectName("fld")
            e.setMaximumWidth(340)
            e.textChanged.connect(lambda t, src=src: src.setText(t))
            if src is h.e_model:
                e.editingFinished.connect(lambda: self._model_entered())
            self.extra.addWidget(e)
            self._edits.append(e)

    def _open_past(self):
        try:
            self.host.easy_open_past()
        except Exception as e:
            self._notice = f"開けませんでした: {e}"
        self._refresh()

    def _print(self):
        try:
            self.host.easy_print()
        except Exception as e:
            self._notice = f"印刷できませんでした: {e}"

    def _pdf(self):
        try:
            self.host.easy_pdf()
        except Exception as e:
            self._notice = f"PDFにできませんでした: {e}"

    def _build_alarm(self):
        """番号を入れると、意味とやることが出る（アラーム検索と同じ中身）。"""
        lb = QtWidgets.QLabel("アラームの番号（例 PS0010。機械の画面に出ています）")
        lb.setStyleSheet("color:#5A6876; font-size:13px;")
        self.extra.addWidget(lb)
        e = QtWidgets.QLineEdit()
        e.setObjectName("fld")
        e.setMaximumWidth(340)
        self.extra.addWidget(e)
        self.lbl_alarm = QtWidgets.QLabel("")
        self.lbl_alarm.setWordWrap(True)
        self.lbl_alarm.setObjectName("line")
        self.extra.addWidget(self.lbl_alarm)
        self.e_alarm = e
        e.textChanged.connect(self._alarm_search)
        codes = [a["code"] for a in (self.st.get("alarms") or []) if a.get("code")]
        if codes:                                    # 機械に出ているアラームがあれば、最初から調べる
            e.setText(codes[0])
        else:
            self._alarm_search("")

    def _alarm_search(self, q):
        q = q.strip()
        if not q:
            self.lbl_alarm.setText("番号を入れてください。")
            self.v_main.set_svg(easy_art.alarm_screen(bool(self.st.get("alarms"))))
            return
        try:
            rows = self.host.easy_alarm_info(q)
        except Exception as e:
            self.lbl_alarm.setText(f"調べられませんでした: {e}")
            return
        if not rows:
            self.lbl_alarm.setText("見つかりませんでした。通常の画面の「アラーム検索」で、ことばでも探せます。")
            return
        code, title, cause, remedy = rows[0]
        self.lbl_alarm.setText(f"{code}  {title}\n原因: {cause}\nやること: {remedy}")

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
        if sid == "home":                            # 入り口: 絵のタイルだけ
            for w in (self.right_w, self.art, self.embed, self.b_next, self.b_back, self.b_skip, self.lbl_count, self.bar):
                w.setVisible(False)
            self.home.setVisible(True)
            self.b_back.setEnabled(False)
            return
        self.home.setVisible(False)
        self.bar.setVisible(True)
        self.b_back.setVisible(True)
        inside = sid == "machine_in" and self._wiz is not None
        self.right_w.setVisible(not inside)
        self.art.setVisible(not inside)
        self.embed.setVisible(inside)
        self.b_skip.setVisible(sid == "machine_in" and not c.get("handoff") and self.flow == "measure")
        self.b_next.setVisible(not inside)
        self.lbl_count.setVisible(not inside)
        if inside:                                   # 中のかんたん作成の操作（入れる・押す）はそのまま。案内は絵と短い言葉
            self._update_strip()
            self.b_back.setEnabled(bool(self.history))
            self._set_bar()
            return
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
            label, go, enabled = ("次の測定へ ▶" if c.get("next_mode") else "ホームへ"), True, True
        elif sid in ("output", "alarm_look", "prog_done"):
            label, go, enabled = "ホームへ", True, True
        elif sid == "open_past":
            label, enabled = "次へ ▶", bool(c.get("has_data"))
            go = enabled
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
        shown = [x for x in range(len(STEPS)) if x == self.i or (self.flow in STEPS[x]["fl"] and easy_flow.needed(STEPS[x]["id"], st, c))]
        self.lbl_count.setText(f"{shown.index(self.i) + 1} ／ {len(shown)}" if self.i in shown else "")
        self._set_bar()

    def _set_bar(self):
        f = self.flow if self.flow in easy_flow.FLOWS else "measure"
        self.bar.set_progress(easy_flow.chapter_progress(self.i, f), STEPS[self.i]["fl"].get(f, 0),
                              easy_flow.FLOWS[f]["chapters"])

    def _update_strip(self):
        w = self._wiz
        try:
            b = w.guide_brief()
        except Exception:
            return
        br = easy_flow.wizard_brief(b["step"], b["kind"], b["text"], b["missing"], b["done"])
        A = easy_art
        ph = (self._phase % 2) * 0.9
        pic = br["pic"]
        label = "機械に入れています" if b["step"] == 3 else "測定の動きを入れます"
        if pic == "product":
            svg = A.product(not b["missing"])
        elif pic == "table":
            svg = A.table_picture()
        elif pic == "power":
            svg = A.power(False, True, ph)
        elif pic == "scene":
            svg = A.scene()
        elif pic == "estop":
            svg = A.estop(False, ph)
        elif pic == "estop_press":
            svg = A.estop_press(False, ph)
        elif pic == "auto_done":
            svg = A.auto(True, "入りました")
        else:
            svg = A.auto(br["kind"] == "done", label)
        self.v_strip.set_svg(svg)
        self.lbl_s_title.setText(f"{br['title']}")
        bg, fg = WATCH_COLORS["wait" if br["kind"] == "do" else br["kind"]]
        if br["kind"] == "do":
            bg, fg = "#FFF3CF", "#7A5200"
        self.lbl_s_say.setStyleSheet(f"background:{bg}; color:{fg};")
        self.lbl_s_say.setText(br["say"])
        self.lbl_s_detail.setText(br["detail"])
        self.lbl_s_detail.setVisible(bool(br["detail"]))

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
            elif z == "estop_press":
                svg = A.estop_press(done, ph)
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
            if sid == "view_result":
                ng, spec = c.get("view_ng", 0), c.get("view_spec", False)
                svg = A.verdict(False if ng else (True if spec else None), saved=True)
            else:
                ng, spec = c.get("ng", 0), c.get("has_spec", False)
                svg = A.verdict(False if ng else (True if spec else None), saved=bool(c.get("saved")))
        elif art == "verdict_saved":
            svg = A.verdict(True, saved=True)
        elif art == "folder":
            svg = A.folder()
        elif art == "printer":
            svg = A.printer()
        elif art == "alarm":
            svg = A.alarm_screen(bool(st.get("alarms")))
        elif art == "auto_done":
            svg = A.auto(True, "入りました")

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
    def _skip_machine(self):
        if self._wiz is not None and not self._close_wizard():
            return
        self.ctx["skip_machine"] = True
        self._render()

    def _open_wizard_inside(self):
        """かんたん作成を、この画面の中に入れる（別の窓を開かない）。開いている間は、機械を読む係を止める。"""
        c = self._all_ctx()
        if self._wiz is not None or c.get("handoff") or c.get("skip_machine"):
            return
        w = self.host.easy_make_wizard(self.embed)
        w.embedded = True                                  # 長い説明は出さない（上の絵と短い言葉が案内する）
        w.setWindowFlags(QtCore.Qt.Widget)
        for name in ("b_mode", "b_close"):               # 「全部表示」への切り替えと「閉じる」は出さない（初心者用。進むのは「測定へ進む」）
            try:
                getattr(w, name).hide()
            except Exception:
                pass
        self.embed.layout().addWidget(w, 1)
        w.finished.connect(self._wizard_finished)
        w.show()
        try:
            w._apply_mode()                                # 長い説明を隠す
        except Exception:
            pass
        self._wiz = w
        if self.poller is not None:
            self.poller.pause()

    def _wizard_finished(self, _r):
        """中のかんたん作成の「測定へ進む」が押された（accept）。結果を受け取って、次へ進む。"""
        w = self._wiz
        if w is None or self._wiz_closing:
            return
        res = self.host.easy_wizard_result(w) or {}
        self._drop_wizard()
        if res.get("handoff"):
            self.ctx["handoff"] = True
        if res.get("cycled"):
            self.ctx["cycled_in_wizard"] = True
        if self.ctx.get("handoff"):
            QtCore.QTimer.singleShot(0, self._next)
        else:
            self._render()

    def _drop_wizard(self):
        w, self._wiz = self._wiz, None
        if self.poller is not None:
            self.poller.resume()
        if w is not None:
            w.setParent(None)
            w.deleteLater()

    def _close_wizard(self):
        """中のかんたん作成を片づける。書いている最中は、今までと同じ確認が出る。片づけたら True。"""
        w = self._wiz
        if w is None:
            return True
        try:
            if getattr(w, "_writer", None) is not None and not w._close_writer():
                return False
        except Exception:
            pass
        self._wiz_closing = True
        try:
            w.reject()
        except Exception:
            pass
        self._wiz_closing = False
        self._drop_wizard()
        return True

    def _save(self):
        try:
            self.host.easy_save()
        except Exception as e:
            self._notice = f"保存できませんでした: {e}"
        self._refresh()

    # ------------------------------------------------------------ 閉じる
    def done(self, r):
        if self._wiz is not None:
            self._close_wizard()
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
