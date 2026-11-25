# -*- coding: utf-8 -*-
"""「結果を読む」の窓（中身の決め方は result_guide.py。ここは見た目だけ）。

測り終わった（または開いた）結果を、(1) 自分のグラフで1枚ずつ教える (2) いつもの値と比べる (3) 次にやること、
の3つの見出しで出す。機械には何もしない。合否の決め方も足さない（画面の表と同じ規格・同じ比べ方）。
host（MainWindow）から使うのは: display_series_devs / current_series_devs / _spec_limit / _flow_ng_items /
show_corrected / e_model / e_machine / e_operator / settings / has_view_data / view_kind / show_partial_remeasure。
"""

import threading
from pathlib import Path

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from . import report, result_guide as RG
from . import settings as _settings

COLORS = {"wheel_cw": "#1f77b4", "wheel_ccw": "#d62728", "worm_cw": "#2ca02c", "worm_ccw": "#ff7f0e"}
MARK = "#f59e0b"
RED, GREEN, GREY = "#dc2626", "#15803d", "#64748b"
STAGE_COLOR = {"same": GREEN, "low": GREEN, "high": "#b45309", "highest": RED, "few": GREY}
STAGE_WORD = {"same": "いつもどおり", "low": "いつもより小さい", "high": "いつもより大きい", "highest": "これまでで一番大きい",
              "few": "まだ比べられません"}


def fix_path(settings=None):
    """記録帳（PC 側。マスタの外。アラーム記録と同じ置き方）。"""
    return _settings.app_dir() / RG.FIX_FILE


def tip_path(settings=None):
    p = str((settings or {}).get("result_tips_csv") or "").strip()
    if p:
        return Path(p) if Path(p).is_absolute() else _settings.app_dir() / p
    return _settings.app_dir() / "マスタ" / RG.TIP_FILE


def host_limits(host):
    """画面の表と同じ規格（host._spec_limit）を、result_guide の形にする。"""
    out = {}
    for grp in ("wheel", "worm"):
        for m in RG.METRICS:
            try:
                out[(grp, m)] = host._spec_limit(grp, m)
            except Exception:
                out[(grp, m)] = None
    return out


MIN_PT = 10       # この窓の字の下限。会社PC（文字 7pt）では小さすぎて読めなかった（2026-11-26 会社PCで確認）


def base_pt():
    """この窓の基準の字の大きさ: アプリの文字サイズ。ただし MIN_PT より小さくしない（窓に空きがあるため）。"""
    app = QtWidgets.QApplication.instance()
    pt = app.font().pointSize() if app else MIN_PT
    return max(MIN_PT, pt if pt > 0 else MIN_PT)


def lift_fonts(root):
    """root の中で、字が基準（base_pt）より小さい部品を基準まで上げる。
    アプリの見た目（スタイルシート）があると、窓に付けた字の大きさが中の部品へ伝わらないため、1つずつ付ける。"""
    pt = base_pt()
    for w in [root] + root.findChildren(QtWidgets.QWidget):
        f = w.font()
        if 0 < f.pointSize() < pt:
            f.setPointSize(pt)
            w.setFont(f)


def _bigger(widget, delta, bold=False):
    f = widget.font()
    f.setPointSize(max(6, base_pt() + delta))
    f.setBold(bold)
    widget.setFont(f)
    return widget


def host_state(h):
    """いま画面に出ている結果から (系列の値, 説明の札, 表の赤い字, 外れた項目の名前) を作る。
    値も合否も画面と同じもの（display_series_devs・表の赤い字）を使うだけ。"""
    try:
        devs = h.display_series_devs() if h.has_view_data() and h.view_kind != "repeat" else {}
    except Exception:
        devs = {}
    try:
        bl_rows = [r for r in h.current_result_rows() if "総合バックラッシ" in str(r[0])] if devs else []
    except Exception:
        bl_rows = []
    lessons = RG.lessons(devs, host_limits(h), corrected=bool(getattr(h, "show_corrected", False)),
                         backlash_rows=bl_rows)
    try:
        ng_items = list(h._flow_ng_items())
    except Exception:
        ng_items = []
    return devs, lessons, ng_items, RG.over_kinds(lessons, ng_items)


class RangeBar(QtWidgets.QWidget):
    """いつもの値の幅（帯）と、今回の値（しるし）を1本の線の上に描く。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.u, self.value, self.stage = {}, 0.0, "few"
        self.setMinimumSize(160, 26)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def set(self, u, value, stage):
        self.u, self.value, self.stage = u or {}, abs(float(value)), stage
        self.update()

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        y = h // 2
        p.setPen(QtGui.QPen(QtGui.QColor("#cbd5e1"), 2))
        p.drawLine(6, y, w - 6, y)
        u = self.u
        if "lo" not in u:
            p.end()
            return
        top = max(u["vmax"], self.value) * 1.15 or 1.0
        fx = lambda v: 6 + (w - 12) * max(0.0, min(1.0, v / top))   # noqa: E731
        p.setPen(QtGui.QPen(QtGui.QColor("#94a3b8"), 2))
        p.drawLine(int(fx(u["vmin"])), y, int(fx(u["vmax"])), y)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor("#93c5fd"))
        p.drawRoundedRect(QtCore.QRectF(fx(u["lo"]), y - 6, max(3.0, fx(u["hi"]) - fx(u["lo"])), 12), 4, 4)
        p.setBrush(QtGui.QColor(STAGE_COLOR.get(self.stage, GREY)))
        p.setPen(QtGui.QPen(QtGui.QColor("white"), 1.5))
        x = fx(self.value)
        p.drawEllipse(QtCore.QPointF(x, y), 6.5, 6.5)
        p.end()


class ResultGuideDialog(QtWidgets.QDialog):
    """結果を読む。"""

    _past_ready = QtCore.Signal(int, list)

    def __init__(self, host, parent=None, *, fixed_kinds=(), search=None):
        super().__init__(parent or host)
        _f = self.font()
        _f.setPointSize(base_pt())
        self.setFont(_f)                     # 中の部品（ボタン・説明の文）も、この大きさになる
        self.host = host
        self.search = search or report.search_inspection
        self.setWindowTitle("結果を読む")
        self._gen = 0
        self._past = None
        self._fixed_kinds = list(fixed_kinds or ())
        self._lessons = []
        self._idx = 0
        self._build()
        self._past_ready.connect(self._on_past)
        self.reload()

    # ------------------------------------------------------------ 作り
    def _build(self):
        scr = self.screen() or QtWidgets.QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr else QtCore.QRect(0, 0, 1280, 700)
        self.setMaximumSize(avail.width(), avail.height())
        self.resize(min(1120, avail.width() - 40), min(640, avail.height() - 50))
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        self.lbl_head = _bigger(QtWidgets.QLabel(""), 2, True)
        self.lbl_head.setWordWrap(True)
        root.addWidget(self.lbl_head)

        tabs = QtWidgets.QHBoxLayout()
        tabs.setSpacing(6)
        self.tab_buttons = []
        for i, text in enumerate(("① 自分のグラフで見る", "② いつもの値と比べる", "③ 次にやること")):
            b = QtWidgets.QPushButton(text)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setMinimumHeight(b.sizeHint().height() + 6)
            b.clicked.connect(lambda _=False, n=i: self.show_page(n))
            b.setMinimumWidth(10)                 # 大きい字でも窓の幅を押し広げない（3つで幅を分け合う）
            tabs.addWidget(b, 1)
            self.tab_buttons.append(b)
        root.addLayout(tabs)

        self.stack = QtWidgets.QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_graph_page())
        self.stack.addWidget(self._build_usual_page())
        self.stack.addWidget(self._build_next_page())

        foot = QtWidgets.QHBoxLayout()
        self.lbl_foot = QtWidgets.QLabel("値は、画面の右の表と同じです。合否の決め方は足していません。")
        self.lbl_foot.setStyleSheet(f"color:{GREY};")
        self.lbl_foot.setWordWrap(True)
        foot.addWidget(self.lbl_foot, 1)
        self.b_close = QtWidgets.QPushButton("とじる")
        self.b_close.clicked.connect(self.accept)
        foot.addWidget(self.b_close)
        root.addLayout(foot)
        self.setStyleSheet(self.styleSheet() + """
            QPushButton:checked { background:#1e3a8a; color:white; font-weight:bold; }
            QFrame#rgCard { background:white; border:1px solid #c8d0db; border-radius:8px; }
            QLabel#rgChip { border-radius:9px; padding:2px 10px; color:white; font-weight:bold; }
        """)

    def _build_graph_page(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.plot = pg.PlotWidget(background="w")
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("left", "ずれ（秒）")
        self.plot.setLabel("bottom", "指令した角度（度）")
        self.plot.setMinimumSize(320, 200)
        lay.addWidget(self.plot, 3)

        card = QtWidgets.QFrame()
        card.setObjectName("rgCard")
        card.setMinimumWidth(330)
        cl = QtWidgets.QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)
        top = QtWidgets.QHBoxLayout()
        self.lbl_step = QtWidgets.QLabel("")
        self.lbl_step.setStyleSheet(f"color:{GREY};")
        top.addWidget(self.lbl_step)
        top.addStretch(1)
        self.chip = QtWidgets.QLabel("")
        self.chip.setObjectName("rgChip")
        top.addWidget(self.chip)
        cl.addLayout(top)
        self.lbl_title = _bigger(QtWidgets.QLabel(""), 3, True)
        self.lbl_title.setWordWrap(True)
        cl.addWidget(self.lbl_title)
        self.lbl_value = _bigger(QtWidgets.QLabel(""), 8, True)
        cl.addWidget(self.lbl_value)
        self.lbl_lines = _bigger(QtWidgets.QLabel(""), 1)
        self.lbl_lines.setWordWrap(True)
        self.lbl_lines.setTextFormat(QtCore.Qt.RichText)
        self.lbl_lines.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        sc = QtWidgets.QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QtWidgets.QFrame.NoFrame)
        sc.setWidget(self.lbl_lines)
        sc.viewport().setStyleSheet("background:white;")
        self.lbl_lines.setStyleSheet("background:white;")
        cl.addWidget(sc, 1)
        self.lbl_others = QtWidgets.QLabel("")
        self.lbl_others.setWordWrap(True)
        self.lbl_others.setStyleSheet(f"color:{GREY};")
        cl.addWidget(self.lbl_others)
        nav = QtWidgets.QHBoxLayout()
        self.b_prev = QtWidgets.QPushButton("◀ 前へ")
        self.b_next = QtWidgets.QPushButton("次へ ▶")
        self.b_next.setDefault(True)
        self.b_prev.clicked.connect(lambda: self.show_lesson(self._idx - 1))
        self.b_next.clicked.connect(self._next_clicked)
        nav.addWidget(self.b_prev)
        nav.addStretch(1)
        nav.addWidget(self.b_next)
        cl.addLayout(nav)
        lay.addWidget(card, 2)
        return page

    def _build_usual_page(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        self.lbl_usual = _bigger(QtWidgets.QLabel("保存した結果を読んでいます…"), 1, True)
        self.lbl_usual.setWordWrap(True)
        lay.addWidget(self.lbl_usual)
        self.usual_box = QtWidgets.QFrame()
        self.usual_box.setObjectName("rgCard")
        self.usual_grid = QtWidgets.QGridLayout(self.usual_box)
        self.usual_grid.setContentsMargins(14, 10, 14, 10)
        self.usual_grid.setHorizontalSpacing(14)
        self.usual_grid.setVerticalSpacing(6)
        self.usual_grid.setColumnStretch(3, 1)
        sc = QtWidgets.QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(self.usual_box)
        lay.addWidget(sc, 1)
        self.lbl_usual_note = QtWidgets.QLabel(
            "青い帯 ＝ 保存した結果の8割が入る幅。灰色の線 ＝ これまでの一番小さい値から一番大きい値。丸 ＝ 今回。"
            "　いつもの値は目安で、合否ではありません。")
        self.lbl_usual_note.setWordWrap(True)
        self.lbl_usual_note.setStyleSheet(f"color:{GREY};")
        lay.addWidget(self.lbl_usual_note)
        return page

    def _build_next_page(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        self.lbl_next = _bigger(QtWidgets.QLabel(""), 1, True)
        self.lbl_next.setWordWrap(True)
        lay.addWidget(self.lbl_next)
        self.next_host = QtWidgets.QWidget()
        self.next_lay = QtWidgets.QVBoxLayout(self.next_host)
        self.next_lay.setContentsMargins(0, 0, 0, 0)
        self.next_lay.setSpacing(8)
        sc = QtWidgets.QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(self.next_host)
        lay.addWidget(sc, 1)
        row = QtWidgets.QHBoxLayout()
        self.b_remeasure = QtWidgets.QPushButton("外れた所だけ測り直す（部分再測定）…")
        self.b_remeasure.clicked.connect(self._remeasure)
        row.addWidget(self.b_remeasure)
        row.addStretch(1)
        lay.addLayout(row)
        return page

    # ------------------------------------------------------------ 中身を入れる
    def reload(self):
        self._devs, self._lessons, self._ng_items, self._kinds = host_state(self.host)
        self._fill_head()
        self._fill_next()
        self.show_page(0)
        self.show_lesson(0)
        self._start_past()
        lift_fonts(self)

    def _fill_head(self):
        model = self.host.e_model.text().strip()
        machine = self.host.e_machine.text().strip()
        who = f"{model}　機番 {machine}　" if model or machine else ""
        if not self._lessons:
            self.lbl_head.setText(who + "読める結果がありません（分割の結果を測るか、開いてください）。")
            self.lbl_head.setStyleSheet("")
            return
        if self._ng_items:
            self.lbl_head.setText(who + f"規格から外れた所が {len(self._ng_items)} か所（{len(self._kinds)} 項目）あります。"
                                  "①で場所を見て、③で次にやることを見ます。")
            self.lbl_head.setStyleSheet(f"color:{RED};")
        elif any(le["over"] is not None for le in self._lessons):
            self.lbl_head.setText(who + "規格から外れた所はありません。①で、それぞれの値の意味を見られます。")
            self.lbl_head.setStyleSheet(f"color:{GREEN};")
        else:
            self.lbl_head.setText(who + "規格が画面に出ていないので、合格とも外れとも言えません。①で値の意味を見られます。")
            self.lbl_head.setStyleSheet("")

    def show_page(self, n):
        n = max(0, min(2, int(n)))
        self.stack.setCurrentIndex(n)
        self.tab_buttons[n].setChecked(True)

    def _next_clicked(self):
        if self._idx >= len(self._lessons) - 1:
            self.show_page(1)
        else:
            self.show_lesson(self._idx + 1)

    def show_lesson(self, i):
        if not self._lessons:
            for w in (self.lbl_title, self.lbl_value, self.lbl_lines, self.lbl_others, self.lbl_step, self.chip):
                w.setText("")
            self.chip.hide()
            self.b_prev.setEnabled(False)
            self.b_next.setEnabled(False)
            self.plot.clear()
            return
        self._idx = i = max(0, min(len(self._lessons) - 1, int(i)))
        le = self._lessons[i]
        self.lbl_step.setText(f"{i + 1} ／ {len(self._lessons)} 枚")
        self.lbl_title.setText(le["title"])
        if le["value"] is None:
            self.lbl_value.setText(le.get("value_text", ""))
        else:
            sign = "+" if le["metric"] == "slope" and le["value"] > 0 else ""
            self.lbl_value.setText(f"{sign}{le['value']:.1f} 秒")
        if le["over"] is None:
            self.chip.setText("規格なし")
            self.chip.setStyleSheet(f"background:{GREY};")
            self.lbl_value.setStyleSheet("")
        elif le["over"]:
            self.chip.setText(f"規格 {le['limit']:g} 秒まで → 外れ" if le["limit"] else "外れ")
            self.chip.setStyleSheet(f"background:{RED};")
            self.lbl_value.setStyleSheet(f"color:{RED};")
        else:
            self.chip.setText(f"規格 {le['limit']:g} 秒まで → 中" if le["limit"] else "規格の中")
            self.chip.setStyleSheet(f"background:{GREEN};")
            self.lbl_value.setStyleSheet("")
        self.chip.show()
        lines = list(le["lines"])
        if le["over"] and le["metric"] in ("single", "adjacent") and RG.GROUP[le["series"]] == "wheel":
            extra = RG.same_place(self._devs, le["metric"])
            if extra:
                lines.append(extra)
        self.lbl_lines.setText("".join(f"<p style='margin:0 0 8px 0; line-height:135%'>{_esc(x)}</p>" for x in lines))
        if le.get("others"):
            parts = [f"{n} {v:.1f} 秒" + ("（外れ）" if o else "") for n, v, o in le["others"]]
            self.lbl_others.setText("もう片方: " + "、".join(parts))
        else:
            self.lbl_others.setText("")
        self.b_prev.setEnabled(i > 0)
        self.b_next.setText("次へ ▶" if i < len(self._lessons) - 1 else "② いつもの値と比べる ▶")
        self._draw(le)

    def _draw(self, le):
        pl = self.plot
        pl.clear()
        grp = RG.GROUP[le["series"]]
        xs = []
        for k, _g, _d in RG.SERIES:
            if RG.GROUP[k] != grp or k not in self._devs:
                continue
            t, d = self._devs[k]
            xs.extend(t)
            focus = k == le["series"] or bool(le.get("both"))
            col = QtGui.QColor(COLORS[k])
            if not focus:
                col.setAlpha(70)
            pl.plot(list(t), list(d), pen=pg.mkPen(col, width=3 if focus else 1.5), symbol="o",
                    symbolSize=6 if focus else 4, symbolBrush=col, symbolPen=None)
        if le["seg"]:
            sx, sy = [p[0] for p in le["seg"]], [p[1] for p in le["seg"]]
            pl.plot(sx, sy, pen=pg.mkPen(MARK, width=6))
        if le["marks"]:
            mx, my = [p[0] for p in le["marks"]], [p[1] for p in le["marks"]]
            pl.plot(mx, my, pen=None, symbol="o", symbolSize=17, symbolBrush=MARK, symbolPen=pg.mkPen("k", width=1.5))
            for j, (x, y) in enumerate(le["marks"]):
                txt = pg.TextItem(f"{x:g}°  {y:+.1f} 秒", color="#111827", anchor=(0.5, 1.5 if j % 2 == 0 else -0.5),
                                  fill=pg.mkBrush(255, 255, 255, 220))
                txt.setPos(x, y)
                pl.addItem(txt)
        pl.setTitle("ホイール　青＝時計回り（CW）、赤＝反時計回り（CCW）" if le.get("both")
                    else f"{RG.SERIES_NAME[le['series']]} を太い線で出しています", color="#334155", size="10pt")
        pl.enableAutoRange()
        vb = pl.getPlotItem().getViewBox()
        vb.setDefaultPadding(0.12)
        pl.autoRange()

    # ------------------------------------------------------------ いつもの値
    def _start_past(self):
        h = self.host
        root = str(h.settings.get("bs_save_root") or "").strip()
        model = h.e_model.text().strip()
        if not self._lessons:
            self.lbl_usual.setText("読める結果がありません。")
            return
        if not root or not model:
            self.lbl_usual.setText("保存した結果の場所（設定の「.BS/.KS保存先」）か型式が空なので、比べられません。")
            return
        self._gen += 1
        gen = self._gen
        self.lbl_usual.setText("保存した結果を読んでいます…")
        search = self.search

        def work():
            try:
                recs = search(root, model=model, machine="", limit=300)
                recs = [r for r in recs if _same_model(r.get("型式"), model)]
            except Exception:
                recs = []
            try:
                self._past_ready.emit(gen, recs)
            except RuntimeError:
                pass                      # 窓がもう閉じている

        threading.Thread(target=work, daemon=True).start()

    def _on_past(self, gen, recs):
        if gen != self._gen:
            return
        self._past = recs
        try:
            raw = self.host.current_series_devs() or {}
        except Exception:
            raw = {}
        rows = RG.usual_rows(raw, recs, exclude_machine=self.host.e_machine.text().strip())
        self._usual_rows = rows
        self.lbl_usual.setText(RG.usual_summary(rows) or "比べられる値がありません。")
        while self.usual_grid.count():
            it = self.usual_grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for i, r in enumerate(rows):
            name = QtWidgets.QLabel(r["name"])
            val = _bigger(QtWidgets.QLabel(f"{r['value']:.1f} 秒"), 1, True)
            word = QtWidgets.QLabel(STAGE_WORD[r["stage"]])
            word.setObjectName("rgChip")
            word.setStyleSheet(f"background:{STAGE_COLOR[r['stage']]};")
            word.setAlignment(QtCore.Qt.AlignCenter)
            bar = RangeBar()
            bar.set(r["u"], r["value"], r["stage"])
            u = r["u"]
            rng = QtWidgets.QLabel(f"いつも {u['lo']:.1f}〜{u['hi']:.1f} 秒（{u['n']} 件）" if "lo" in u else f"{u.get('n', 0)} 件")
            rng.setStyleSheet(f"color:{GREY};")
            for j, w in enumerate((name, val, word, bar, rng)):
                self.usual_grid.addWidget(w, i, j)
        self.usual_grid.setRowStretch(len(rows), 1)
        lift_fonts(self)

    # ------------------------------------------------------------ 次にやること
    def _fill_next(self):
        while self.next_lay.count():
            it = self.next_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        fixes = RG.load_fixes(fix_path(self.host.settings))
        tips = RG.load_tips(tip_path(self.host.settings))
        if self._fixed_kinds:
            self.next_lay.addWidget(self._fixed_card(self._fixed_kinds))
        if not self._kinds:
            self.lbl_next.setText("規格から外れた所はありません。このまま保存に進めます。" if self._lessons else "読める結果がありません。")
            self.b_remeasure.hide()
        else:
            self.lbl_next.setText(f"外れた {len(self._ng_items)} か所は、{len(self._kinds)} 項目にまとまります。上から順に見てください。")
            self.b_remeasure.setVisible(hasattr(self.host, "show_partial_remeasure"))
            for step in RG.next_steps(self._kinds, fixes, tips, self._lessons):
                self.next_lay.addWidget(self._step_card(step))
        self.next_lay.addStretch(1)
        lift_fonts(self)

    def _step_card(self, step):
        card = QtWidgets.QFrame()
        card.setObjectName("rgCard")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        head = QtWidgets.QHBoxLayout()
        t = _bigger(QtWidgets.QLabel(step["kind"]), 2, True)
        t.setStyleSheet(f"color:{RED};")
        head.addWidget(t)
        head.addStretch(1)
        b = QtWidgets.QPushButton("手引きを足す…")
        b.setToolTip("この外れ方のとき、見ること・やることを1行で登録します。次から、だれの画面にも出ます。")
        b.clicked.connect(lambda _=False, k=step["kind"]: self._add_tip(k))
        head.addWidget(b)
        lay.addLayout(head)
        for line in RG.step_lines(step):
            lb = _bigger(QtWidgets.QLabel("・" + line), 1)
            lb.setWordWrap(True)
            if line.startswith("前はこれで"):
                lb.setStyleSheet("font-weight:bold;")
            elif line.startswith("この外れ方の"):
                lb.setStyleSheet(f"color:{GREY};")
            lay.addWidget(lb)
        return card

    def _fixed_card(self, kinds):
        card = QtWidgets.QFrame()
        card.setObjectName("rgCard")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        t = _bigger(QtWidgets.QLabel("前は外れていた「" + "」「".join(kinds) + "」が、今回は規格に入りました。何をしましたか？"), 1, True)
        t.setWordWrap(True)
        t.setStyleSheet(f"color:{GREEN};")
        lay.addWidget(t)
        note = QtWidgets.QLabel("1回押すだけです。次に同じ外れ方が出たとき、「前はこれで直りました」と一番上に出します。")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{GREY};")
        lay.addWidget(note)
        row = QtWidgets.QHBoxLayout()
        self.fix_buttons = []
        for text in RG.FIX_CHOICES:
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(lambda _=False, s=text: self._record_fix(s))
            row.addWidget(b)
            self.fix_buttons.append(b)
        lay.addLayout(row)
        row2 = QtWidgets.QHBoxLayout()
        self.e_fix = QtWidgets.QLineEdit()
        self.e_fix.setPlaceholderText("ほかのやり方（一言で）")
        b2 = QtWidgets.QPushButton("これで残す")
        b2.clicked.connect(lambda: self._record_fix(self.e_fix.text()))
        row2.addWidget(self.e_fix, 1)
        row2.addWidget(b2)
        self.b_fix_skip = QtWidgets.QPushButton("残さない")
        self.b_fix_skip.clicked.connect(self._skip_fix)
        row2.addWidget(self.b_fix_skip)
        lay.addLayout(row2)
        return card

    def _record_fix(self, did):
        did = str(did or "").strip()
        if not did:
            return
        try:
            RG.add_fix(fix_path(self.host.settings), model=self.host.e_model.text().strip(),
                       machine=self.host.e_machine.text().strip(), kinds=self._fixed_kinds, did=did)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "結果を読む", f"記録を残せませんでした。\n{e}")
            return
        self._fixed_kinds = []
        self._fill_next()
        self.lbl_next.setText("残しました。次に同じ外れ方が出たとき、最初に出します。")

    def _skip_fix(self):
        self._fixed_kinds = []
        self._fill_next()

    def _add_tip(self, kind):
        text, ok = QtWidgets.QInputDialog.getText(
            self, "手引きを足す", f"「{kind}」が外れたとき、見ること・やること（1行）:")
        if not ok or not text.strip():
            return
        who = ""
        try:
            who = self.host.e_operator.text().strip()
        except Exception:
            pass
        try:
            RG.add_tip(tip_path(self.host.settings), kind, text, who)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "結果を読む", f"手引きを残せませんでした。\n{e}")
            return
        self._fill_next()

    def _remeasure(self):
        self.accept()
        QtCore.QTimer.singleShot(0, self.host.show_partial_remeasure)


def _esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _same_model(rec_model, model):
    a = "".join(str(rec_model or "").upper().split())
    b = "".join(str(model or "").upper().split())
    return bool(a) and bool(b) and (a == b or a.startswith(b) or b.startswith(a))


def open_result_guide(host, fixed_kinds=()):
    """結果を読む、を開く（host＝MainWindow）。"""
    dlg = ResultGuideDialog(host, fixed_kinds=fixed_kinds)
    dlg.exec()
    return dlg
