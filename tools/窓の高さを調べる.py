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
        f"/{win.cond_group.sizeHint().height()} 末尾の表={'開' if win.suffix_panel.isChecked() else '畳'} "
        f"上限(設定)={win.maximumWidth()}x{win.maximumHeight()} {win32(win) if win.isVisible() else ''}")
    # 幅（左の列・測定条件・操作の帯・右の列。「右で切れる」を調べる）
    cs, bs, rs = win.cond_scroll, win.bar_scroll, win.right_scroll
    fnt = win.font()
    lines.append(
        f"  幅: 窓の中身={g.width()} 左の列(グラフ)={win.plots_widget.width()} "
        f"測定条件の見える幅={cs.viewport().width()} 必要={win.cond_group.sizeHint().width()} 最低={win.cond_group.minimumSizeHint().width()} "
        f"横バー={'出' if cs.horizontalScrollBar().isVisible() else '無'}(最大{cs.horizontalScrollBar().maximum()}) / "
        f"操作の帯の見える幅={bs.viewport().width()} 必要={bs.widget().sizeHint().width()} "
        f"横バー={'出' if bs.horizontalScrollBar().isVisible() else '無'}(最大{bs.horizontalScrollBar().maximum()}) / "
        f"右の列={rs.width()} / 字={fnt.family()} {fnt.pointSizeF():g}pt 画面の点の数の倍率={scr.devicePixelRatio()} 論理DPI={scr.logicalDotsPerInch():g}")


def win32(w):
    """Windows 自身が言う窓の大きさ（画面の点の数。Qt の数字と別に、本当の大きさを見る）。"""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        hwnd = int(w.winId())
        r, c, wa, pt = wintypes.RECT(), wintypes.RECT(), wintypes.RECT(), wintypes.POINT(0, 0)
        u.GetWindowRect(hwnd, ctypes.byref(r))
        u.GetClientRect(hwnd, ctypes.byref(c))
        u.ClientToScreen(hwnd, ctypes.byref(pt))
        u.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)       # SPI_GETWORKAREA
        return (f"Win窓=({r.left},{r.top})-({r.right},{r.bottom}) Win中身の左上=({pt.x},{pt.y}) "
                f"Win中身={c.right}x{c.bottom} Win中身の下端={pt.y + c.bottom} "
                f"Win使える所=({wa.left},{wa.top})-({wa.right},{wa.bottom}) Win最大={bool(u.IsZoomed(hwnd))}")
    except Exception as e:
        return f"Win=読めない({e})"


class Watch(QtCore.QObject):
    """窓の大きさ・状態が変わるたびに1行残す（いつ・何が窓を大きくしたかを見る）。"""

    def eventFilter(self, obj, ev):
        if obj is win and ev.type() in (QtCore.QEvent.Resize, QtCore.QEvent.WindowStateChange, QtCore.QEvent.Move):
            g = win.geometry()
            lines.append(f"  [{ev.type().name}] 中身=({g.x()},{g.y()},{g.width()}x{g.height()}) 最大={win.isMaximized()} "
                         f"最低(hint)={win.minimumSizeHint().height()} 最低(設定)={win.minimumHeight()} "
                         f"上限(設定)={win.maximumWidth()}x{win.maximumHeight()}")
        return False


def plain():
    """比べる用: 何も入っていない窓を最大にしたときの大きさ（アプリのせいか、PC・Qt のせいかを分ける）。"""
    p = QtWidgets.QMainWindow()
    p.setWindowTitle("比べる用の空の窓")
    p.statusBar().showMessage("下の帯")
    p.showMaximized()
    for _ in range(40):
        app.processEvents()
        QtCore.QThread.msleep(20)
    g, f = p.geometry(), p.frameGeometry()
    sb = p.statusBar()
    lines.append(f"空の窓: 最大={p.isMaximized()} 中身=({g.x()},{g.y()},{g.width()}x{g.height()}) "
                 f"枠込み=({f.x()},{f.y()},{f.width()}x{f.height()}) "
                 f"下の帯の下端={sb.mapToGlobal(QtCore.QPoint(0, sb.height())).y()} {win32(p)}")
    p.close()
    p.deleteLater()
    app.processEvents()


def finish():
    snap("5秒後")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "窓の高さ_結果.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    app.quit()


plain()
watch = Watch()
win.installEventFilter(watch)
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
