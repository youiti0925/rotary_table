"""登録の育ち具合の窓（「その他 ▾」→「登録の育ち具合…」）。数えて見せるだけ。"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from . import growth as GR
from . import settings as _settings

GREEN, GREY, AMBER = "#15803d", "#64748b", "#b45309"


def gather(host, search=None):
    """画面（host＝MainWindow）と設定から数える。開けないものは None。保存した結果だけは時間がかかるので別（count_saved）。"""
    from . import alarm_watch, focas_find, kind_runtime, live_guide as LG, live_guide_ui, result_guide as RG
    from . import result_guide_ui, wizard_programs as WP
    st = host.settings
    app = _settings.app_dir()
    model = host.e_model.text().strip()

    def safe(fn):
        try:
            return fn()
        except Exception:
            return None

    def kind_table():
        from . import kind_conditions as K
        p = Path(str(st.get(kind_runtime.SETTING_KEY) or ""))
        if not p.is_absolute():
            p = app / p
        return K.load(p)

    table = safe(kind_table)
    user = (getattr(host, "masters", None) or {}).get("user_rotary") or {}
    times = safe(lambda: LG.load_times(live_guide_ui.time_path(st)))
    fixes = safe(lambda: RG.load_fixes(result_guide_ui.fix_path(st)))
    tips = safe(lambda: RG.load_tips(result_guide_ui.tip_path(st)))
    alarms = safe(lambda: alarm_watch.read_records(app / alarm_watch.RECORD_FILE))
    machines = safe(lambda: focas_find.load_registry(focas_find.registry_path(st)))
    progs = safe(lambda: WP.read_log(st.get("prog_log_csv") or str(app / WP.LOG_NAME)))
    counts = dict(
        conditions=None if table is None else GR.count_models(table, user),
        machines=None if machines is None else len(machines),
        programs=None if progs is None else len(progs),
        times=None if times is None else len(times),
        fixes=None if fixes is None else len(fixes),
        tips=None if tips is None else len(tips),
        alarms=None if alarms is None else len(alarms),
    )
    has_cond = False
    if model:
        try:
            from . import kind_conditions as K
            has_cond = bool(table and (K.hit_rows(table, model, False)[0] or K.hit_rows(table, model, True)[0]))
        except Exception:
            has_cond = False
        has_cond = has_cond or bool(user.get(model.upper())) or getattr(host, "master_cond", None) is not None
    return dict(model=model, counts=counts, has_conditions=has_cond,
                times=len(GR.rows_for_model(times, model)), fixes=len(GR.rows_for_model(fixes, model)))


def count_saved(settings, model, search=None):
    """同じ型式の保存した結果の件数。保存先が無い・見られないときは None。"""
    from . import report
    from .result_guide_ui import _same_model
    root = str(settings.get("bs_save_root") or "").strip()
    if not root or not model or not Path(root).exists():
        return None
    try:
        recs = (search or report.search_inspection)(root, model, "", 400)
    except Exception:
        return None
    return sum(1 for r in recs if _same_model(r.get("型式"), model))


class GrowthDialog(QtWidgets.QDialog):
    _saved_ready = QtCore.Signal(object)

    def __init__(self, host, parent=None, *, search=None, threaded=True):
        super().__init__(parent or host)
        from .result_guide_ui import base_pt
        self._pt = base_pt()                  # 文字 7pt の PC でも読める大きさにする（結果を読む と同じ下限）
        _f = self.font()
        _f.setPointSize(self._pt)
        self.setFont(_f)
        self.host = host
        self.search = search
        self.setWindowTitle("登録の育ち具合")
        scr = self.screen() or QtWidgets.QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr else QtCore.QRect(0, 0, 1280, 700)
        self.setMaximumSize(avail.width(), avail.height())
        self.resize(min(980, avail.width() - 40), min(620, avail.height() - 50))
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        head = QtWidgets.QLabel("登録が増えるほど、アプリが自分でやることが増えて、人がやることが減ります。")
        head.setWordWrap(True)
        f = head.font()
        f.setBold(True)
        f.setPointSize(self._pt + 1)
        head.setFont(f)
        root.addWidget(head)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.body = QtWidgets.QWidget()
        self.lay = QtWidgets.QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 0, 6, 0)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)
        foot = QtWidgets.QHBoxLayout()
        note = QtWidgets.QLabel("数えているだけです。登録の中身は変えません。")
        note.setStyleSheet(f"color:{GREY};")
        note.setWordWrap(True)
        foot.addWidget(note, 1)
        b = QtWidgets.QPushButton("とじる")
        b.clicked.connect(self.accept)
        foot.addWidget(b)
        root.addLayout(foot)
        self.setStyleSheet("QFrame#grCard { background:white; border:1px solid #c8d0db; border-radius:8px; }"
                           "QFrame#grCard QLabel { background:transparent; }")
        self._saved = "wait"
        self._saved_ready.connect(self._on_saved)
        self.state = gather(host)
        self.fill()
        model = self.state["model"]
        if model:
            if threaded:
                threading.Thread(target=lambda: self._saved_ready.emit(count_saved(host.settings, model, self.search)),
                                 daemon=True).start()
            else:
                self._on_saved(count_saved(host.settings, model, self.search))

    def _on_saved(self, n):
        self._saved = n
        self.fill()

    def _card(self):
        card = QtWidgets.QFrame()
        card.setObjectName("grCard")
        v = QtWidgets.QVBoxLayout(card)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(4)
        return card, v

    def _label(self, text, *, bold=False, color="", delta=0, wrap=True):
        lb = QtWidgets.QLabel(text)
        lb.setWordWrap(wrap)
        if wrap:
            lb.setMinimumWidth(10)
        f = lb.font()
        f.setBold(bold)
        f.setPointSize(self._pt + delta)
        lb.setFont(f)
        if color:
            lb.setStyleSheet(f"color:{color};")
        return lb

    def fill(self):
        while self.lay.count():
            it = self.lay.takeAt(0)
            wd = it.widget()
            if wd is not None:
                wd.setParent(None)
                wd.deleteLater()
        s = self.state
        card, v = self._card()
        if not s["model"]:
            v.addWidget(self._label("いまの型式", color=GREY))
            v.addWidget(self._label("測定の画面の「型式」を入れると、その型式の育ち具合を出します。"))
            self.model_rows = []
        else:
            v.addWidget(self._label(f"いまの型式　{s['model']}", bold=True, delta=1))
            saved = 0 if self._saved == "wait" else self._saved
            rows = GR.for_model(s["model"], has_conditions=s["has_conditions"], saved=saved,
                                times=s["times"], fixes=s["fixes"])
            if self._saved == "wait":
                rows[1] = dict(name="保存した結果", ok=False, state="数えています…", next="")
            self.model_rows = rows
            grid = QtWidgets.QGridLayout()
            grid.setHorizontalSpacing(12)
            grid.setColumnStretch(2, 1)
            for i, r in enumerate(rows):
                grid.addWidget(self._label("✓" if r["ok"] else "○", bold=True, color=GREEN if r["ok"] else GREY, wrap=False), i, 0)
                grid.addWidget(self._label(r["name"], bold=True, wrap=False), i, 1)
                grid.addWidget(self._label(r["state"]), i, 2)
            v.addLayout(grid)
            nb = GR.next_best(rows)
            if nb:
                lb = self._label("次にやると楽になること:　" + nb, bold=True, color=AMBER)
                v.addWidget(lb)
            elif self._saved != "wait":
                v.addWidget(self._label("この型式は、登録がそろっています。", bold=True, color=GREEN))
        self.lay.addWidget(card)

        card, v = self._card()
        v.addWidget(self._label("このPCに登録してあるもの", bold=True, delta=1))
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(2, 3)
        grid.setColumnStretch(3, 3)
        for j, t in enumerate(("登録", "いま", "これで自動になること", "増やし方")):
            grid.addWidget(self._label(t, color=GREY), 0, j)
        for i, it in enumerate(GR.items(s["counts"]), start=1):
            name = self._label(it["name"], bold=True, wrap=False)      # 名前は途中で折らない（短い名前にしてある）
            grid.addWidget(name, i, 0, QtCore.Qt.AlignTop)
            n = self._label(GR.count_text(it), bold=True, color=GREY if not it["count"] else GREEN, wrap=False)
            grid.addWidget(n, i, 1, QtCore.Qt.AlignTop)
            grid.addWidget(self._label(it["gives"]), i, 2, QtCore.Qt.AlignTop)
            grid.addWidget(self._label(it["grow"], color=GREY), i, 3, QtCore.Qt.AlignTop)
        v.addLayout(grid)
        self.lay.addWidget(card)
        self.lay.addStretch(1)
        from .result_guide_ui import lift_fonts
        lift_fonts(self)


def open_growth(host):
    dlg = GrowthDialog(host)
    dlg.exec()
    return dlg
