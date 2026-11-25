"""測る前の点検の札（測定の画面の右の列。まだ結果が無く、測ってもいないときだけ出る）。読むだけ。"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from . import growth as GR
from . import precheck as PC

COLOR = {"ok": "#15803d", "todo": "#b91c1c", "note": "#334155"}


class PrePanel(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("prePanel")
        self.setStyleSheet("QFrame#prePanel { background:white; border:1px solid #c8d0db; border-radius:8px; }"
                           "QFrame#prePanel QLabel { background:transparent; }")
        self.v = QtWidgets.QVBoxLayout(self)
        self.v.setContentsMargins(10, 8, 10, 8)
        self.v.setSpacing(4)
        cap = QtWidgets.QLabel("測る前の点検")
        cap.setStyleSheet("color:#64748b;")
        self.v.addWidget(cap)
        self.lbl_head = QtWidgets.QLabel("")
        self.lbl_head.setWordWrap(True)
        self.lbl_head.setMinimumWidth(10)
        f = self.lbl_head.font()
        f.setBold(True)
        self.lbl_head.setFont(f)
        self.v.addWidget(self.lbl_head)
        self.lbl_rows = QtWidgets.QLabel("")
        self.lbl_rows.setWordWrap(True)
        self.lbl_rows.setMinimumWidth(10)
        self.lbl_rows.setTextFormat(QtCore.Qt.RichText)
        self.v.addWidget(self.lbl_rows)
        self._key = None

    def show_rows(self, rows):
        key = tuple((r["level"], r["text"]) for r in rows)
        if key == self._key:
            return
        self._key = key
        head, todo = PC.summary(rows)
        self.lbl_head.setText(head)
        self.lbl_head.setStyleSheet(f"color:{COLOR['todo'] if todo else COLOR['ok']};")
        esc = lambda t: str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")   # noqa: E731
        self.lbl_rows.setText("".join(
            f'<p style="margin:0 0 5px 0; color:{COLOR[r["level"]]};"><b>{PC.MARK[r["level"]]}</b> {esc(r["text"])}</p>'
            for r in rows))


class PreWatch(QtCore.QObject):
    """測定の画面の状態を読んで、点検の札を出し入れする。"""

    _saved_ready = QtCore.Signal(object, object)

    def __init__(self, win, search=None, threaded=True):
        super().__init__(win)
        self.win = win
        self.search = search
        self.threaded = threaded
        self.panel = PrePanel()
        self.panel.hide()
        col = getattr(win, "right_col", None)
        if col is not None and col.layout() is not None:
            col.layout().insertWidget(0, self.panel)
        self._saved_key = None
        self._saved = (None, "")
        self._files = None
        self._files_at = 0
        self._saved_ready.connect(self._on_saved)

    def state(self):
        from . import live_guide as LG, live_guide_ui, result_guide as RG, result_guide_ui
        w = self.win
        g = w._flow_inputs()
        model = w.e_model.text().strip()
        self._tick_n = getattr(self, "_tick_n", 0) + 1
        if self._files is None or self._tick_n - self._files_at >= 15:      # 記録のファイルは 15 秒ごとに読み直す
            self._files_at = self._tick_n
            try:
                self._files = (RG.load_fixes(result_guide_ui.fix_path(w.settings)),
                               LG.load_times(live_guide_ui.time_path(w.settings)))
            except Exception:
                self._files = ([], [])
        fixes, times = self._files
        self._want_saved(model, w.e_machine.text().strip())
        return dict(nd_ok=bool(g.get("nd_ok")), nd_dummy=bool(g.get("nd_dummy")),
                    missing=list(w.missing_required_fields()), model=model,
                    has_conditions=bool(model) and not g.get("model_unknown") and self._has_conditions(model),
                    same_saved=self._saved[0], last_saved_date=self._saved[1],
                    fix_rows=GR.rows_for_model(fixes, model), time_rows=times, mode=g.get("mode", ""))

    def _has_conditions(self, model):
        w = self.win
        if getattr(w, "master_cond", None) is not None:
            return True
        m = getattr(w, "masters", None) or {}
        return any((m.get(k) or {}).get(model.upper()) for k in ("user_rotary", "user_tilt", "user_rotary_repeat",
                                                                 "user_tilt_repeat"))

    def _want_saved(self, model, machine):
        key = (model.upper(), machine.upper())
        if key == self._saved_key:
            return
        self._saved_key = key
        self._saved = (None, "")
        root = str(self.win.settings.get("bs_save_root") or "").strip()
        if not model or not machine or not root or not Path(root).exists():
            return

        def work():
            from . import report, result_guide as RG
            from .result_guide_ui import _same_model
            try:
                recs = (self.search or report.search_inspection)(root, model, "", 400)
                hit = [r for r in recs if _same_model(r.get("型式"), model) and RG._same_machine(r.get("機番"), machine)]
                res = (len(hit), str(hit[0].get("日付") or "") if hit else "")
            except Exception:
                res = (None, "")
            self._saved_ready.emit(key, res)

        if self.threaded:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()

    def _on_saved(self, key, res):
        if key == self._saved_key:
            self._saved = res

    def tick(self):
        w = self.win
        try:
            idle = getattr(w, "seq", None) is None and not w.has_view_data()
        except Exception:
            idle = False
        if not idle:
            self.panel.hide()
            return
        self.panel.show_rows(PC.checks(**self.state()))
        self.panel.show()
