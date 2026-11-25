"""測定の画面に「もっと良くする」機能をつなぐ所（2026-11-26）。

gui.py には「ボタン1つ」と attach() の1行だけを足し、中身はここと、機能ごとの新しいファイルに置く
（gui.py は他の作業と重なりやすいため）。ここは今ある画面の状態を**読むだけ**で、測定・保存・機械には触らない。

- 結果を読む（result_guide.py / result_guide_ui.py）: 1段目「データを見る」の「結果を読む」。
  測り終わって規格から外れた所があれば、そのボタンに「今やる所」の色を付ける（窓は勝手に開かない。
  自動で続けて測る流れを止めないため）。前に外れていた項目が今回入っていたら、開いたときに1回だけ
  「何をしましたか？」と聞いて記録帳に残す。
"""
from __future__ import annotations

from PySide6 import QtCore

from . import main_top, result_guide as RG


class Better(QtCore.QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.tracker = RG.Tracker()
        self.fixed = []          # 前に外れていて、今回入った項目（開いたとき1回だけ聞く）
        self.kinds = []          # 今回 外れた項目
        self._was = False
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.tick()

    # 1秒ごと。状態を読むだけ
    def tick(self):
        w = self.win
        b = getattr(w, "b_read", None)
        try:
            can = bool(w.has_view_data()) and getattr(w, "view_kind", "") != "repeat"
        except Exception:
            can = False
        if b is not None:
            b.setEnabled(can)
        measured = bool(getattr(w, "_flow_measured", False)) and can
        if measured and not self._was:
            self.on_finished()
        elif not measured and self._was:
            self.kinds = []
            self._mark()
        self._was = measured

    def on_finished(self):
        """測り終わった直後に1回。外れた項目を覚え、前に外れていて今回入った項目を見つける。"""
        from . import result_guide_ui as U
        w = self.win
        try:
            _devs, _lessons, _ng, kinds = U.host_state(w)
        except Exception:
            kinds = []
        self.kinds = list(kinds)
        fixed = self.tracker.observe(w.e_machine.text(), kinds)
        if fixed:
            self.fixed = fixed
        self._mark()

    def _mark(self):
        b = getattr(self.win, "b_read", None)
        if b is None:
            return
        on = bool(self.kinds or self.fixed)
        if bool(b.property("now")) != on:
            main_top.set_now(b, on)
        if self.fixed:
            tip = "前に外れていた所が、今回は入りました。何をしたかを残せます（次の人に出ます）"
        elif self.kinds:
            tip = "規格から外れた所があります。どこが・どれだけ・次に何をするかを見られます"
        else:
            tip = "いまの結果を、自分のグラフで説明します（精度・単一誤差・隣接誤差・傾き）。いつもの値とも比べます"
        b.setToolTip(tip)

    def open_result(self):
        from . import result_guide_ui as U
        fixed, self.fixed = self.fixed, []
        dlg = U.open_result_guide(self.win, fixed)
        self._mark()
        return dlg


def attach(win):
    """MainWindow の最後で1回呼ぶ。"""
    win.better = Better(win)
    return win.better


def open_result(win):
    b = getattr(win, "better", None)
    if b is not None:
        return b.open_result()
    from . import result_guide_ui as U
    return U.open_result_guide(win)
