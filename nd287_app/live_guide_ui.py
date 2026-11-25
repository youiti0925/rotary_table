"""測っているあいだの実況と見張りの札（測定の画面の右の列、精度の表が出る所の上）。

測っている間だけ出る。状態を読むだけで、測定には触らない（止めない・値を変えない）。
"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtWidgets

from . import live_guide as LG
from . import settings as _settings


def time_path(settings=None):
    return _settings.app_dir() / LG.TIME_FILE


class LivePanel(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("livePanel")
        self.setStyleSheet("""
            QFrame#livePanel { background:white; border:1px solid #c8d0db; border-radius:8px; }
            QLabel { background:transparent; }
            QLabel#liveWarn { background:#fff4d6; border:1px solid #e0b040; border-radius:6px; padding:4px 6px; color:#5a3d00; }
        """)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(4)
        self.lbl_cap = QtWidgets.QLabel("いま測っている所")
        self.lbl_cap.setStyleSheet("color:#64748b;")
        self.lbl_name = QtWidgets.QLabel("")
        f = self.lbl_name.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 1)
        self.lbl_name.setFont(f)
        self.lbl_count = QtWidgets.QLabel("")
        f2 = self.lbl_count.font()
        f2.setBold(True)
        f2.setPointSize(f2.pointSize() + 4)
        self.lbl_count.setFont(f2)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.lbl_remain = QtWidgets.QLabel("")
        self.lbl_remain.setFont(f)
        self.lbl_usual = QtWidgets.QLabel("")
        self.lbl_usual.setStyleSheet("color:#64748b;")
        self.lbl_note = QtWidgets.QLabel("")
        self.lbl_watch_cap = QtWidgets.QLabel("見張り")
        self.lbl_watch_cap.setStyleSheet("color:#64748b;")
        self.lbl_ok = QtWidgets.QLabel("")
        self.lbl_warn = QtWidgets.QLabel("")
        self.lbl_warn.setObjectName("liveWarn")
        self.lbl_stall = QtWidgets.QLabel("")
        self.lbl_stall.setObjectName("liveWarn")
        for w in (self.lbl_cap, self.lbl_name, self.lbl_count, self.bar, self.lbl_remain, self.lbl_usual, self.lbl_note,
                  self.lbl_watch_cap, self.lbl_ok, self.lbl_warn, self.lbl_stall):
            if isinstance(w, QtWidgets.QLabel):
                w.setWordWrap(True)
                w.setMinimumWidth(10)
            v.addWidget(w)

    def show_state(self, lines, done, total, ok_text, warn, stall):
        self.lbl_name.setText(lines.get("name", ""))
        self.lbl_count.setText(lines.get("count", ""))
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(done)
        self.lbl_remain.setText(lines.get("remain", ""))
        self.lbl_usual.setText(lines.get("usual", ""))
        self.lbl_note.setText(lines.get("note", ""))
        self.lbl_ok.setText(ok_text)
        self.lbl_warn.setText(warn)
        self.lbl_warn.setVisible(bool(warn))
        self.lbl_stall.setText(stall)
        self.lbl_stall.setVisible(bool(stall))


class LiveWatch(QtCore.QObject):
    """測定の画面の状態を1秒ごとに読んで、札を出し入れする。clock は試験で差し替える。"""

    def __init__(self, win, clock=time.monotonic):
        super().__init__(win)
        self.win = win
        self.clock = clock
        self.panel = LivePanel()
        self.panel.hide()
        col = getattr(win, "right_col", None)
        if col is not None and col.layout() is not None:
            col.layout().insertWidget(0, self.panel)
        self._seq = None
        self._reset()

    def _reset(self):
        self._t0 = None
        self._last_idx = 0
        self._last_t = None
        self._intervals = []
        self._warn = ""
        self._usual = (None, 0)
        self._recorded = False

    def tick(self):
        w = self.win
        seq = getattr(w, "seq", None)
        if seq is None:
            self.panel.hide()
            self._seq = None
            return
        now = self.clock()
        if seq is not self._seq:                     # 新しい測定が始まった
            self._seq = seq
            self._reset()
            self._t0 = self._last_t = now
            self._last_idx = int(getattr(seq, "idx", 0))
            self._usual = LG.usual_seconds(LG.load_times(time_path(w.settings)), w.e_model.text(),
                                           self._mode(), len(seq))
        idx = int(getattr(seq, "idx", 0))
        if idx > self._last_idx:
            if idx - self._last_idx == 1 and self._last_idx > 0:
                self._intervals.append(now - self._last_t)
            self._last_t = now
            self._warn = self._step_warning(seq)
        elif idx < self._last_idx:                   # 1点戻る
            self._warn = ""
            self._last_t = now
        self._last_idx = idx
        if seq.done():
            self.panel.hide()
            if not self._recorded:
                self._recorded = True
                self._record(seq, now)
            return
        cur = seq.current()
        key = cur[0] if cur else ""
        lines = LG.narration(key, idx, len(seq), elapsed=now - self._t0, usual=self._usual[0], usual_n=self._usual[1],
                             intervals=self._intervals)
        stall = LG.stall_watch(now - self._last_t, self._intervals)
        over = self._count_over(seq)
        ok = ("値は順に入っています。" if idx else "まだ値が入っていません。機械の前で測定プログラムを起動してください。")
        if over:
            ok = f"ここまでで、1歩の変化が単一誤差の規格を超えた所が {over} か所あります。このまま最後まで測ってください。"
        self.panel.show_state(lines, idx, len(seq), ok, self._warn, stall)
        self.panel.show()

    # ----- 読むだけの小さな係
    def _mode(self):
        try:
            return str(self.win.current_mode())
        except Exception:
            return ""

    def _limit(self, key):
        try:
            return self.win._spec_limit("wheel" if str(key).startswith("wheel") else "worm", "single")
        except Exception:
            return None

    def _series(self, seq):
        """いま値が入った系列の (指令角度, 測った角度)。分割の測定だけ（再現性は見ない）。"""
        if not hasattr(seq, "order") or seq.idx <= 0:
            return None, None
        key = seq.steps[seq.idx - 1][0]
        return key, seq.data.get(key)

    def _step_warning(self, seq):
        key, d = self._series(seq)
        if not d:
            return ""
        return LG.step_watch(d[0], d[1], self._limit(key))

    def _count_over(self, seq):
        if not hasattr(seq, "order"):
            return 0
        n = 0
        for key, (t, m) in seq.data.items():
            n += LG.count_steps_over(t, m, self._limit(key))
        return n

    def _record(self, seq, now):
        w = self.win
        if getattr(getattr(w, "dev", None), "dummy", False) and not getattr(self, "keep_dummy", False):     # ダミーの表示器（練習）の時間は残さない
            return
        try:
            LG.add_time(time_path(w.settings), model=w.e_model.text(), mode=self._mode(), points=len(seq),
                        seconds=now - self._t0, name=w.e_operator.text())
        except OSError:
            pass
