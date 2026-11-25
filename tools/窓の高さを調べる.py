# -*- coding: utf-8 -*-
"""会社PC（150%表示）で、最大にしたメイン画面の高さが どの時点で 画面より大きくなるかを調べる。読むだけ・何も書かない。

使い方（リポジトリのフォルダで）:  python 窓の高さを調べる.py
結果は同じフォルダの「窓の高さ_結果.txt」に出る。機械・表示器にはつながない（ダミー）。
settings.json は作業フォルダに作られるので、リポジトリの外の空のフォルダで動かすか、終わったら消す。
"""
import copy
import os
import sys

sys.path.insert(0, os.getcwd())
from PySide6 import QtCore, QtWidgets
from nd287_app import gui as G
from nd287_app.nd287 import DummyDevice
from nd287_app.settings import DEFAULTS

app = QtWidgets.QApplication(sys.argv)
G.apply_font(app, 7)
G.apply_theme(app, "ネイビー・コーポレート")
st = copy.deepcopy(DEFAULTS)
st.update(focas_auto_find=False, webapp_sync_enabled=False, bs_save_root="",
          save_root=os.path.join(os.environ.get("TEMP", "."), "nd287_takasa_test"))
win = G.MainWindow(DummyDevice(), 10.0, 0.5, 5.0, 0.0, st)
lines = []


def snap(tag):
    scr = win.screen()
    g, f, a, s = win.geometry(), win.frameGeometry(), scr.availableGeometry(), scr.geometry()
    sb = win.statusBar()
    sb_bottom = sb.mapToGlobal(QtCore.QPoint(0, sb.height())).y()
    lines.append(
        f"{tag}: 最大={win.isMaximized()} 中身=({g.x()},{g.y()},{g.width()}x{g.height()}) "
        f"枠込み=({f.x()},{f.y()},{f.width()}x{f.height()}) 画面={s.width()}x{s.height()} "
        f"使える所=({a.x()},{a.y()},{a.width()}x{a.height()}) 倍率={scr.devicePixelRatio()} "
        f"最低(hint)={win.minimumSizeHint().width()}x{win.minimumSizeHint().height()} "
        f"最低(設定)={win.minimumSize().width()}x{win.minimumSize().height()} "
        f"下の帯の下端={sb_bottom} グラフ={win.plots_widget.height()} "
        f"グラフ最低={win.plots_widget.minimumHeight()} 測定条件={win.cond_scroll.height()}"
        f"/{win.cond_group.sizeHint().height()} 末尾の表={'開' if win.suffix_panel.isChecked() else '畳'}")


def finish():
    snap("5秒後")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "窓の高さ_結果.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    app.quit()


snap("出す前")
if hasattr(win, "fit_before_show"):
    win.fit_before_show()          # アプリ本体（gui.run）と同じ順番
    snap("出す前に詰めた後")
win.showMaximized()
snap("最大にした直後")
for ms, tag in ((0, "0秒後"), (100, "0.1秒後"), (400, "0.4秒後"), (1000, "1秒後"), (2500, "2.5秒後")):
    QtCore.QTimer.singleShot(ms, lambda t=tag: snap(t))
QtCore.QTimer.singleShot(5000, finish)
app.exec()
