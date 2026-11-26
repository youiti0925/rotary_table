# -*- coding: utf-8 -*-
"""はじめてモードの「測っています」と「こういう結果でした」の画面（2026-11-26 作り直し）。

利用者（2026-11-26）:「測定グラフも画面に出ない」「情報量が少なすぎる。画面だって空白だらけ」
「読み込んで測定がスタートしたら、画面にリアルタイムのグラフぐらい出て、終わった後は、こういう結果でしたって出る」。

- MeasureView … 左に**本物のグラフ**（1点入るたびに伸びる）、右に、何点目・どの向きで何を測っているか・あと何分・
  いま入った値とずれ・系列ごとの進み・見張り（大きく動いた所／値が来ない）。
- SummaryView … 合否の帯・グラフ・項目ごとの値と規格（外れは赤）・項目の「？」でことばのカード。

どちらも測定の画面（host）を**読むだけ**。値・外れ（赤い字）・実況・見張りは、測定の画面と同じもの
（display_series_devs・右の表・live_guide）を使う。測定・保存・機械には触らない。中身の決め方は easy_live.py（Qt なし）。
"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtGui, QtWidgets

from . import easy_live as EL
from . import live_guide as LG

INK, GREY, LINE = "#17212B", "#5A6876", "#D5DBE1"
TONE = {"ok": ("#E3F4EA", "#16864C"), "stop": ("#FBE9E7", "#C8362B"), "wait": ("#FFF3CF", "#7A5200"),
        "plain": ("#E9EEF5", "#17212B")}

QSS = """
QFrame#mcard { background:#FFFFFF; border:1px solid #D5DBE1; border-radius:12px; }
QLabel { background:transparent; color:#17212B; }
QWidget#gbox { background:#FFFFFF; }
QLabel#cap { color:#5A6876; font-size:12px; font-weight:700; }
QLabel#big { font-size:30px; font-weight:900; }
QLabel#mid { font-size:16px; font-weight:800; }
QLabel#txt { font-size:13px; }
QLabel#small { font-size:12px; color:#5A6876; }
QLabel#warn { background:#FFF3CF; border:1px solid #E0B040; border-radius:8px; padding:5px 8px; color:#5A3D00; font-size:13px; font-weight:700; }
QLabel#okline { background:#E3F4EA; border-radius:8px; padding:5px 8px; color:#16864C; font-size:13px; font-weight:700; }
QLabel#th { color:#5A6876; font-size:12px; font-weight:700; }
QLabel#td { font-size:14px; }
QLabel#tdname { font-size:13px; font-weight:700; }
QPushButton#qhead { background:#FFFFFF; color:#1560D4; font-size:12px; font-weight:800; border:1px solid #C5CCD3; border-radius:8px; padding:2px 6px; }
QPushButton#qhead:hover { border-color:#1560D4; background:#F3F7FD; }
QPushButton#second { background:#FFFFFF; color:#17212B; font-size:14px; font-weight:700; border:2px solid #C5CCD3; border-radius:10px; padding:7px 14px; }
QPushButton#second:hover { border-color:#1560D4; }
QProgressBar { border:0; border-radius:5px; background:#DCE2E8; }
QProgressBar::chunk { background:#1560D4; border-radius:5px; }
"""


def _card():
    f = QtWidgets.QFrame()
    f.setObjectName("mcard")
    v = QtWidgets.QVBoxLayout(f)
    v.setContentsMargins(12, 9, 12, 9)
    v.setSpacing(4)
    return f, v


def _label(text="", name="txt", wrap=True):
    lb = QtWidgets.QLabel(text)
    lb.setObjectName(name)
    lb.setWordWrap(wrap)
    lb.setMinimumWidth(10)
    return lb


def _legend(pairs):
    return "　".join(f'<span style="color:{c};">■</span> {n}' for n, c in pairs)


class LiveGraph(QtWidgets.QWidget):
    """測定の画面と同じ値を描くグラフ（横: 指令角度、縦: ずれ[秒]）。分割はホイールとウォーム、再現性は止める角度ごとの点。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        import pyqtgraph as pg
        self.pg = pg
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.boxes, self.plots, self.legends, self.heads = {}, {}, {}, {}
        self.titles = {}            # グラフの見出し（長い, 短い）。3つ並ぶときは短い方（切れないように）
        for name, title, stretch in (("wheel", "ホイール（1周を大きな刻みで）", 7), ("worm", "ウォーム（短い範囲を細かく）", 4),
                                     ("rep", "再現性（同じ所に何回も止める）", 7)):
            box = QtWidgets.QWidget()
            box.setObjectName("gbox")
            box.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            bv = QtWidgets.QVBoxLayout(box)
            bv.setContentsMargins(0, 0, 0, 0)
            bv.setSpacing(2)
            head = _label(title, "cap", wrap=False)
            self.heads[name], self.titles[name] = head, (title, title.split("（")[0])
            leg = _label("", "small", wrap=False)
            leg.setTextFormat(QtCore.Qt.RichText)
            pw = pg.PlotWidget(background="#FFFFFF")
            pw.setMenuEnabled(False)
            pw.setMouseEnabled(x=False, y=False)
            pw.hideButtons()
            pw.showGrid(x=True, y=True, alpha=0.25)
            for ax in ("left", "bottom"):
                a = pw.getAxis(ax)
                a.enableAutoSIPrefix(False)
                a.setPen(pg.mkPen("#9AA6B2"))
                a.setTextPen(pg.mkPen(INK))
                tf = QtGui.QFont(self.font())
                tf.setPixelSize(11)
                a.setStyle(tickFont=tf)
            pw.setLabel("bottom", "指令角度 [°]", **{"color": GREY, "font-size": "11px"})
            pw.setLabel("left", "ずれ [秒]", **{"color": GREY, "font-size": "11px"})
            pw.addLine(y=0, pen=pg.mkPen("#9AA6B2", width=1, style=QtCore.Qt.DashLine))
            pw.setMinimumSize(120, 110)
            bv.addWidget(head)
            bv.addWidget(leg)
            bv.addWidget(pw, 1)
            lay.addWidget(box, stretch)
            self.boxes[name], self.plots[name], self.legends[name] = box, pw, leg
        self.curves = {}
        for key in ("wheel_cw", "wheel_ccw", "worm_cw", "worm_ccw"):
            c = EL.SERIES_COLOR[key]
            self.curves[key] = self.plots[key.split("_")[0]].plot(
                [], [], pen=pg.mkPen(c, width=2), symbol="o", symbolSize=5, symbolBrush=c, symbolPen=None)
        for key, sym in (("cw", "o"), ("ccw", "t")):
            c = EL.SERIES_COLOR[key]
            self.curves["rep_" + key] = self.plots["rep"].plot([], [], pen=None, symbol=sym, symbolSize=8, symbolBrush=c, symbolPen=None)
        self.now_dot = {n: p.plot([], [], pen=None, symbol="o", symbolSize=15, symbolBrush=(240, 164, 0, 230),
                                  symbolPen=pg.mkPen(INK, width=2)) for n, p in self.plots.items()}
        self.legends["wheel"].setText(_legend([("時計回り（CW）", EL.SERIES_COLOR["wheel_cw"]), ("反時計回り（CCW）", EL.SERIES_COLOR["wheel_ccw"])]))
        self.legends["worm"].setText(_legend([("CW", EL.SERIES_COLOR["worm_cw"]), ("CCW", EL.SERIES_COLOR["worm_ccw"])]))
        self.legends["rep"].setText(_legend([("● CW から止めた", EL.SERIES_COLOR["cw"]), ("▼ CCW から止めた", EL.SERIES_COLOR["ccw"])]))
        self._range_key = None
        self.points = 0             # いま描いている点の数（試験が見る）

    def refresh(self, host, live=True):
        """測定の画面から読んで描き直す。live=測っているあいだ（いま入った点に印・横の幅を測る範囲に固定）。"""
        seq = getattr(host, "seq", None)
        kind = getattr(host, "view_kind", "") or ""
        try:
            devs = host.display_series_devs() if kind != "repeat" else {}
        except Exception:
            devs = {}
        div, rep = EL.parts(seq) if (live and seq is not None) else (None, None)
        rep_pts = {"cw": ([], []), "ccw": ([], [])}
        try:
            data, points = getattr(host, "rep_data", None) or {}, getattr(host, "rep_points", None) or []
            if kind in ("repeat", "combined") and data:
                for (dirn, i), vals in list(data.items()):
                    for v in vals:
                        rep_pts[dirn][0].append(points[i])
                        rep_pts[dirn][1].append(EL.dev_sec(points[i], v))
        except Exception:
            pass
        n = 0
        for key in ("wheel_cw", "wheel_ccw", "worm_cw", "worm_ccw"):
            t, d = devs.get(key, ([], []))
            self.curves[key].setData([float(x) for x in t], [float(x) for x in d])
            n += len(t)
        for key in ("cw", "ccw"):
            self.curves["rep_" + key].setData(rep_pts[key][0], rep_pts[key][1])
            n += len(rep_pts[key][0])
        self.points = n
        has_rep = bool(rep_pts["cw"][0] or rep_pts["ccw"][0]) or (rep is not None and div is None)
        show_div = kind != "repeat"
        rep_now = rep is not None and (div is None or div.done())
        self.boxes["wheel"].setVisible(show_div and not (kind == "combined" and rep_now))
        self.boxes["worm"].setVisible(show_div and (bool(devs.get("worm_cw") or devs.get("worm_ccw")) or
                                                    (div is not None and EL.x_range(seq, "worm") is not None))
                                      and not (kind == "combined" and rep_now))
        self.boxes["rep"].setVisible(kind == "repeat" or (kind == "combined" and (rep_now or (has_rep and not live))))
        many = sum(1 for b in self.boxes.values() if not b.isHidden()) >= 3
        for name, head in self.heads.items():
            head.setText(self.titles[name][1 if many else 0])
        self.legends["rep"].setText(_legend(
            [("● CW", EL.SERIES_COLOR["cw"]), ("▼ CCW", EL.SERIES_COLOR["ccw"])] if many else
            [("● CW から止めた", EL.SERIES_COLOR["cw"]), ("▼ CCW から止めた", EL.SERIES_COLOR["ccw"])]))
        # 横の幅: 測っているあいだは、はじめから測る範囲にしておく（線が右へ伸びていくのが見える）
        rk = (id(seq), kind, live)
        if rk != self._range_key:
            self._range_key = rk
            for name, prefix in (("wheel", "wheel"), ("worm", "worm"), ("rep", "rep")):
                vb = self.plots[name].getViewBox()
                r = EL.x_range(seq, prefix) if (live and seq is not None) else None
                if r:
                    pad = (r[1] - r[0]) * 0.03
                    vb.enableAutoRange(x=False, y=True)
                    self.plots[name].setXRange(r[0] - pad, r[1] + pad, padding=0)
                else:
                    vb.enableAutoRange(x=True, y=True)
        # いま入った点の印
        for d in self.now_dot.values():
            d.setData([], [])
        if live and seq is not None:
            v = EL.last_value(seq)
            if v:
                name = "rep" if v["key"] in ("cw", "ccw") else str(v["key"]).split("_")[0]
                y = v["dev"]
                if name != "rep":
                    t, d = devs.get(v["key"], ([], []))
                    y = float(d[-1]) if d else y          # 画面のグラフと同じ値（補正後の表示なら補正後）
                self.now_dot[name].setData([v["target"]], [y])


class _SeriesRow(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.name = _label("", "txt", wrap=False)
        self.name.setFixedWidth(118)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(9)
        self.num = _label("", "txt", wrap=False)
        self.num.setFixedWidth(74)
        self.num.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        h.addWidget(self.name)
        h.addWidget(self.bar, 1)
        h.addWidget(self.num)

    def set(self, label, cur, req, now):
        mark = "▶ " if now else ("✓ " if cur >= req else "　 ")
        self.name.setText(mark + label)
        self.name.setStyleSheet("font-weight:900; color:#1560D4;" if now else ("color:#16864C;" if cur >= req else ""))
        self.bar.setRange(0, max(1, req))
        self.bar.setValue(cur)
        self.num.setText(f"{cur} ／ {req}")


class MeasureView(QtWidgets.QWidget):
    """測っているあいだの画面。"""

    def __init__(self, parent=None, clock=time.monotonic):
        super().__init__(parent)
        self.setObjectName("measureView")
        self.setStyleSheet(QSS)
        self.clock = clock
        self._t0, self._seq = None, None
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        # ---- 左: グラフ
        gcard, gv = _card()
        top = QtWidgets.QHBoxLayout()
        self.lbl_gtitle = _label("測っているグラフ（1か所止まるたびに、点が1つ増えます）", "mid", wrap=False)
        top.addWidget(self.lbl_gtitle, 1)
        gv.addLayout(top)
        self.lbl_ghelp = _label("横が角度、縦がずれ（秒＝1度の 3600 分の1）。0 の線に近いほど、指令どおりに止まっています。黄色い丸が、いま入った点です。", "small")
        gv.addWidget(self.lbl_ghelp)
        self.graph = LiveGraph()
        gv.addWidget(self.graph, 1)
        gv.addWidget(_label("向きごとの進み（▶ がいま測っている所。✓ は測り終わり）", "cap", wrap=False))
        self.rows = [_SeriesRow() for _ in range(6)]
        rg = QtWidgets.QGridLayout()
        rg.setContentsMargins(0, 0, 0, 0)
        rg.setHorizontalSpacing(26)
        rg.setVerticalSpacing(2)
        for i, r in enumerate(self.rows):
            rg.addWidget(r, i // 2, i % 2)
        gv.addLayout(rg)
        self.lbl_interim = _label("", "txt")
        self.lbl_interim.setTextFormat(QtCore.Qt.RichText)
        gv.addWidget(self.lbl_interim)
        root.addWidget(gcard, 1)
        # ---- 右: いまの状況
        side = QtWidgets.QWidget()
        side.setFixedWidth(372)
        sv = QtWidgets.QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(8)
        self.lbl_care = _label("終わるまで、機械と測定器にさわらないでください。"
                               "小さなずれ（1度の 3600 分の1）を測っているので、ゆれが値に入ります。", "warn")
        sv.addWidget(self.lbl_care)
        c1, v1 = _card()
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        self.lbl_count = _label("", "big", wrap=False)
        self.lbl_remain = _label("", "mid", wrap=False)
        self.lbl_remain.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom)
        row.addWidget(self.lbl_count)
        row.addWidget(self.lbl_remain, 1)
        v1.addLayout(row)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(12)
        v1.addWidget(self.bar)
        self.lbl_dir = _label("", "mid")
        self.lbl_note = _label("", "txt")
        self.lbl_usual = _label("", "small")
        for w in (self.lbl_dir, self.lbl_note, self.lbl_usual):
            v1.addWidget(w)
        sv.addWidget(c1)
        c2, v2 = _card()
        v2.addWidget(_label("いま入った値", "cap", wrap=False))
        self.lbl_last = _label("", "txt")
        self.lbl_dev = _label("", "mid", wrap=False)
        v2.addWidget(self.lbl_last)
        v2.addWidget(self.lbl_dev)
        sv.addWidget(c2)
        c4, v4 = _card()
        v4.addWidget(_label("見張り（PC が見ています。測定は止めません）", "cap", wrap=False))
        self.lbl_ok = _label("", "okline")
        self.lbl_warn = _label("", "warn")
        self.lbl_stall = _label("", "warn")
        for w in (self.lbl_ok, self.lbl_warn, self.lbl_stall):
            v4.addWidget(w)
        sv.addWidget(c4)
        sv.addStretch(1)
        self.side_scroll = QtWidgets.QScrollArea()
        self.side_scroll.setWidgetResizable(True)
        self.side_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.side_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.side_scroll.setStyleSheet("QScrollArea{background:transparent;} QScrollArea > QWidget > QWidget{background:transparent;}")
        self.side_scroll.setWidget(side)
        self.side_scroll.setFixedWidth(372 + 14)
        root.addWidget(self.side_scroll, 0)

    # ---- 読むだけ
    def _live(self, host):
        b = getattr(host, "better", None)
        return getattr(b, "live", None) if b is not None else None

    def refresh(self, host, c):
        """0.5 秒ごと。測定の画面（host）の取込の箱と、実況・見張り（live_guide）から、いまの状況を出す。"""
        c = c or {}
        seq = getattr(host, "seq", None)
        cur, total = c.get("count") or (0, 0)
        now = self.clock()
        if seq is not self._seq:
            self._seq, self._t0 = seq, now
        self.lbl_count.setText(f"{cur} ／ {total} 点" if total else "値を待っています…")
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(cur)
        live = self._live(host)
        key = ""
        try:
            cu = seq.current() if seq is not None else None
            key = cu[0] if cu else ""
        except Exception:
            key = ""
        t0 = getattr(live, "_t0", None) if live is not None else None
        intervals = list(getattr(live, "_intervals", []) or []) if live is not None else []
        usual = getattr(live, "_usual", (None, 0)) if live is not None else (None, 0)
        lclock = getattr(live, "clock", self.clock) if live is not None else self.clock
        lnow = lclock()
        elapsed = (lnow - t0) if t0 is not None else (now - (self._t0 or now))
        lines = LG.narration(key, cur, total, elapsed=elapsed, usual=usual[0], usual_n=usual[1], intervals=intervals)
        self.lbl_remain.setText(lines.get("remain", ""))
        name = lines.get("name", "")
        self.lbl_dir.setText(("いま: " + name) if name else "")
        self.lbl_dir.setVisible(bool(name))
        self.lbl_note.setText(lines.get("note", ""))
        self.lbl_note.setVisible(bool(lines.get("note")))
        self.lbl_usual.setText(lines.get("usual", ""))
        a, b = EL.last_value_text(EL.last_value(seq) if seq is not None else None)
        self.lbl_last.setText(a)
        self.lbl_dev.setText(b)
        self.lbl_dev.setVisible(bool(b))
        rows = EL.series_progress(seq) if seq is not None else []
        for i, r in enumerate(self.rows):
            if i < len(rows):
                r.set(*rows[i])
            r.setVisible(i < len(rows))
        # 見張り（live_guide と同じ文）
        warn = stall = ""
        over = 0
        if live is not None and seq is not None:
            warn = getattr(live, "_warn", "") or ""
            try:
                last_t = getattr(live, "_last_t", None)
                stall = LG.stall_watch(lnow - last_t, intervals) if last_t is not None else ""
            except Exception:
                stall = ""
            try:
                over = live._count_over(seq)
            except Exception:
                over = 0
        if not cur:
            ok = "まだ値が入っていません。機械が1か所目に止まると、最初の値が入ります。"
        elif over:
            ok = f"1歩の変化が、単一誤差の規格を超えた所が {over} か所あります。このまま最後まで測ってください。"
        else:
            ok = "値は順に入っています。"
        self.lbl_ok.setText(ok)
        self.lbl_ok.setObjectName("warn" if over else "okline")
        self.lbl_ok.style().unpolish(self.lbl_ok)
        self.lbl_ok.style().polish(self.lbl_ok)
        self.lbl_warn.setText(warn)
        self.lbl_warn.setVisible(bool(warn))
        self.lbl_stall.setText(stall)
        self.lbl_stall.setVisible(bool(stall))
        try:
            self.graph.refresh(host, live=True)
        except Exception:
            pass
        self.lbl_interim.setText(interim_html(host))
        self.lbl_interim.setVisible(bool(self.lbl_interim.text()))


def interim_html(host):
    """測り終わった向きの、ここまでの結果（測定の画面の表に、もう出ている行だけ）。"""
    try:
        m = EL.merge_tables(EL.read_tables(host))
    except Exception:
        return ""
    rows = [r for r in m["rows"] if not str(r[0][0]).startswith("規格")]
    if not rows:
        return ""
    parts = []
    for r in rows[:4]:
        cells = []
        for h, (txt, color) in zip(m["headers"][1:], r[1:]):
            if txt:
                style = f"color:{EL.RED}; font-weight:900;" if color == EL.RED else ""
                cells.append(f'{h} <span style="{style}">{txt}</span>')
        parts.append(f"<b>{r[0][0]}</b>　" + "・".join(cells))
    return '<span style="color:#5A6876;">測り終わった向きの結果: </span>' + "　｜　".join(parts)


class SummaryView(QtWidgets.QWidget):
    """こういう結果でした。合否の帯・グラフ・項目ごとの値と規格。項目の「？」でことばのカード。"""

    card_requested = QtCore.Signal(str)
    read_requested = QtCore.Signal()
    remeasure_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("summaryView")
        self.setStyleSheet(QSS)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.banner = QtWidgets.QFrame()
        bh = QtWidgets.QHBoxLayout(self.banner)
        bh.setContentsMargins(16, 8, 12, 8)
        bh.setSpacing(14)
        bv = QtWidgets.QVBoxLayout()
        bv.setSpacing(1)
        self.lbl_verdict = QtWidgets.QLabel("")
        self.lbl_sub = QtWidgets.QLabel("")
        self.lbl_sub.setWordWrap(True)
        bv.addWidget(self.lbl_verdict)
        bv.addWidget(self.lbl_sub)
        bh.addLayout(bv, 1)
        self.b_read = QtWidgets.QPushButton("グラフの上で、くわしく読む")
        self.b_read.setObjectName("second")
        self.b_read.setCursor(QtCore.Qt.PointingHandCursor)
        self.b_read.clicked.connect(self.read_requested.emit)
        self.b_again = QtWidgets.QPushButton("もう一度測る")
        self.b_again.setObjectName("second")
        self.b_again.setCursor(QtCore.Qt.PointingHandCursor)
        self.b_again.clicked.connect(self.remeasure_requested.emit)
        bh.addWidget(self.b_read)
        bh.addWidget(self.b_again)
        root.addWidget(self.banner)
        mid = QtWidgets.QHBoxLayout()
        mid.setSpacing(12)
        gcard, gv = _card()
        gv.addWidget(_label("測ったグラフ", "mid", wrap=False))
        gv.addWidget(_label("横が角度、縦がずれ（秒）。線が上下に広いほど、ずれが大きいです。", "small"))
        self.graph = LiveGraph()
        gv.addWidget(self.graph, 1)
        mid.addWidget(gcard, 5)
        tcard, tv = _card()
        tv.addWidget(_label("項目ごとの値（赤い字が、規格から外れた所。「？」で、その項目の説明）", "cap"))
        self.table_w = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.table_w)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(3)
        sc = QtWidgets.QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QtWidgets.QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        sc.setStyleSheet("QScrollArea{background:transparent;} QScrollArea > QWidget > QWidget{background:transparent;}")
        inner = QtWidgets.QWidget()
        iv = QtWidgets.QVBoxLayout(inner)
        iv.setContentsMargins(0, 0, 4, 0)
        iv.setSpacing(6)
        iv.addWidget(self.table_w)
        self.notes_w = QtWidgets.QWidget()
        self.notes = QtWidgets.QVBoxLayout(self.notes_w)
        self.notes.setContentsMargins(0, 0, 0, 0)
        self.notes.setSpacing(2)
        iv.addWidget(self.notes_w)
        self.lbl_mean = _label("", "small")
        iv.addWidget(self.lbl_mean)
        iv.addStretch(1)
        sc.setWidget(inner)
        tv.addWidget(sc, 1)
        mid.addWidget(tcard, 4)
        root.addLayout(mid, 1)
        self._key = None
        self.cells = []            # [(文字, 赤いか)]（試験が見る）
        self.cell_labels = []      # 表のます（QLabel。試験が幅と高さを見る）

    def _clear(self, lay):
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def refresh(self, host, ng, has_spec, ng_items, can_remeasure=True):
        tone, title, sub = EL.verdict(ng, has_spec, ng_items)
        bg, fg = TONE[tone]
        self.banner.setStyleSheet(f"QFrame{{background:{bg}; border-radius:12px;}} QLabel{{background:transparent;}}")
        self.lbl_verdict.setStyleSheet(f"font-size:24px; font-weight:900; color:{fg};")
        self.lbl_verdict.setText(("✕ " if tone == "stop" else "✓ " if tone == "ok" else "") + title)
        self.lbl_sub.setStyleSheet(f"font-size:13px; font-weight:700; color:{fg};")
        self.lbl_sub.setText(sub)
        self.lbl_sub.setVisible(bool(sub))
        self.b_again.setVisible(bool(can_remeasure))
        can_read = True
        try:
            can_read = getattr(host, "view_kind", "") != "repeat"
        except Exception:
            pass
        self.b_read.setVisible(bool(can_read))
        try:
            tables = EL.read_tables(host)
        except Exception:
            tables = []
        m = EL.merge_tables(tables)
        if not m["rows"] and not m["notes"] and ng_items:      # 表を読めないとき: 外れた所の一覧だけでも出す
            m["notes"] = [(f"{n}　{v}".strip(), EL.RED) for n, v in ng_items]
        key = repr(m)
        if key != self._key:
            self._key = key
            self._fill(m)
        try:
            self.graph.refresh(host, live=False)
        except Exception:
            pass

    def _fill(self, m):
        self._clear(self.grid)
        self._clear(self.notes)
        self.cells = []
        self.cell_labels = []
        heads = m["headers"]
        used = []
        for j, h in enumerate(heads):
            if j == 0:
                self.grid.addWidget(_label("", "th", wrap=False), 0, 0)
                continue
            cid = EL.HEAD_CARD.get(h)
            if cid:
                b = QtWidgets.QPushButton(f"{h} ？")
                b.setObjectName("qhead")
                b.setCursor(QtCore.Qt.PointingHandCursor)
                b.setToolTip(EL.HEAD_MEANING.get(h, ""))
                b.clicked.connect(lambda _c=False, x=cid: self.card_requested.emit(x))
                self.grid.addWidget(b, 0, j, QtCore.Qt.AlignRight)
                used.append(h)
            else:
                lb = _label(h, "th", wrap=False)
                lb.setAlignment(QtCore.Qt.AlignRight)
                self.grid.addWidget(lb, 0, j)
        for i, row in enumerate(m["rows"], start=1):
            spec = str(row[0][0]).startswith("規格")
            for j, (txt, color) in enumerate(row):
                lb = _label(txt, "tdname" if j == 0 else "td", wrap=False)
                red = color == EL.RED
                if j:
                    lb.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                style = ""
                if spec:
                    style = "color:#5A6876; font-size:12px; font-weight:400;"
                if red:
                    style = "color:#C8362B; font-weight:900; background:#FBE9E7; border-radius:5px; padding:0 4px;"
                if style:
                    lb.setStyleSheet(style)
                self.grid.addWidget(lb, i, j)
                self.cells.append((txt, red))
                self.cell_labels.append(lb)
        self.grid.setColumnStretch(0, 1)
        self.table_w.setVisible(bool(m["rows"]))
        for txt, color in m["notes"]:
            if txt.startswith("―") or txt.startswith("—"):          # 区切りの見出し（― 主点精度 ―）は出さない（下の行に名前がある）
                continue
            red = color == EL.RED
            lb = _label(txt, "td")
            lb.setStyleSheet("color:#C8362B; font-weight:900;" if red else ("color:#16864C; font-weight:700;" if color == EL.GREEN else ""))
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            row.addWidget(lb, 1)
            head = next((h for h in EL.HEAD_MEANING if h in txt), None)
            if head and head not in used:                              # その項目の最初の行に「？」
                used.append(head)
                cid = EL.HEAD_CARD.get(head)
                if cid:
                    b = QtWidgets.QPushButton("？")
                    b.setObjectName("qhead")
                    b.setCursor(QtCore.Qt.PointingHandCursor)
                    b.setToolTip(EL.HEAD_MEANING.get(head, ""))
                    b.clicked.connect(lambda _c=False, x=cid: self.card_requested.emit(x))
                    row.addWidget(b, 0)
            holder = QtWidgets.QWidget()
            holder.setLayout(row)
            self.notes.addWidget(holder)
            self.cells.append((txt, red))
            self.cell_labels.append(lb)
        # 外れた項目だけ、ひとことの意味を下に出す（全部は「？」のカードで）
        heads_bad = set()
        for i, row in enumerate(m["rows"]):
            for h, (txt, color) in zip(heads[1:], row[1:]):
                if color == EL.RED:
                    heads_bad.add(h)
        for txt, color in m["notes"]:
            if color == EL.RED:
                heads_bad.update(h for h in EL.HEAD_MEANING if h in txt)
        order = [h for h in EL.HEAD_MEANING if h in heads_bad]
        self.lbl_mean.setText("外れた項目の意味\n" + "\n".join(f"・{h} … {EL.HEAD_MEANING[h]}" for h in order) if order else "")
        self.lbl_mean.setVisible(bool(order))
