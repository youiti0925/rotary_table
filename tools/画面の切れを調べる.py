"""画面の「切れ・はみ出し」を数字で出す（読むだけ。機械にはつながない・本番の設定は触らない）。

使い方（会社PCで、150% の本物の画面で）:  python tools\画面の切れを調べる.py
結果は、このファイルの隣の「画面の切れ_結果.txt」と「画面の切れ_写真」フォルダに出る。
各部品の「実際の大きさ」と「文字や中身に必要な大きさ」を比べて、足りないものを並べる。
"""
import copy, os, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
if "--offscreen" in sys.argv:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6 import QtWidgets, QtCore
from nd287_app import settings as S
_tmp = tempfile.mkdtemp()
S.settings_path = lambda: Path(_tmp) / "settings.json"         # 本番の設定を触らない
from nd287_app import gui as G, kind_conditions as K
from nd287_app.nd287 import DummyDevice
from nd287_app.settings import DEFAULTS

OUT = HERE / "画面の切れ_写真"
OUT.mkdir(exist_ok=True)
lines = []


def say(s):
    lines.append(s)
    print(s)


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
G.apply_font(app, 7)
G.apply_theme(app, "ネイビー・コーポレート")
x = Path(_tmp) / K.FILE_NAME
K.build_template(x, [{"型式": "TEST-1", "測定の種類": "セミ・モーター", "歯数": 60},
                     {"型式": "TEST-1", "測定の種類": "フル・スケール付き", "ホイール刻み": 5}])
st = copy.deepcopy(DEFAULTS)
st.update(focas_auto_find=False, save_root=_tmp, bs_save_root="", webapp_sync_enabled=False,
          kind_conditions_xlsx=str(x), last_kinds={}, ui_font_pt=7)

TYPES = (QtWidgets.QLabel, QtWidgets.QPushButton, QtWidgets.QCheckBox, QtWidgets.QRadioButton, QtWidgets.QToolButton,
         QtWidgets.QLineEdit, QtWidgets.QComboBox, QtWidgets.QAbstractSpinBox)


def name(w):
    t = ""
    for a in ("text", "currentText"):
        if hasattr(w, a):
            try:
                t = str(getattr(w, a)())
                break
            except Exception:
                pass
    if isinstance(w, QtWidgets.QAbstractSpinBox):
        t = w.text()
    return f"{type(w).__name__}[{w.objectName()}]「{t[:28]}」"


def audit(tag, root):
    app.processEvents()
    root.grab().save(str(OUT / f"{tag}.png"))
    n = 0
    for w in root.findChildren(QtWidgets.QWidget):
        if not w.isVisibleTo(root) or w.width() < 6:
            continue
        if isinstance(w, QtWidgets.QAbstractScrollArea):
            hb = w.horizontalScrollBar()
            if hb.isVisible() and hb.maximum() > 0 and not isinstance(w, (QtWidgets.QTableWidget, QtWidgets.QTextEdit)):
                say(f"  [{tag}] 横の動かし棒: {name(w)} {w.width()}x{w.height()} 余り{hb.maximum()}"); n += 1
        if isinstance(w, TYPES):
            if isinstance(w, QtWidgets.QLabel) and (w.wordWrap() or not w.text()):
                continue
            if isinstance(w, QtWidgets.QLineEdit):
                need = w.fontMetrics().horizontalAdvance(w.text()) + 14
                if need > w.width():
                    say(f"  [{tag}] 入力欄で文字が切れる: {name(w)} 実際{w.width()} 必要{need}"); n += 1
                continue
            sh = w.minimumSizeHint() if isinstance(w, QtWidgets.QAbstractSpinBox) else w.sizeHint()
            if w.width() + 2 < sh.width() or w.height() + 2 < sh.height():
                say(f"  [{tag}] 切れ: {name(w)} 実際{w.width()}x{w.height()} 必要{sh.width()}x{sh.height()}"); n += 1
    say(f"[{tag}] 窓 {root.width()}x{root.height()}  切れ・はみ出し {n} 件")


win = G.MainWindow(DummyDevice(), 10.0, 0.5, 5.0, 0.0, st)
win.flow_timer.stop()
win.show()
win.showMaximized()
for _ in range(40):
    app.processEvents()
    QtCore.QThread.msleep(25)
say(f"画面 {app.primaryScreen().size().width()}x{app.primaryScreen().size().height()} 倍率 {app.primaryScreen().devicePixelRatio()}"
    f" 使える所 {app.primaryScreen().availableGeometry().width()}x{app.primaryScreen().availableGeometry().height()}")
audit("main_00_empty", win)
win.e_model.setText("TEST-1"); win.e_machine.setText("123456"); win.on_model_entered()
audit("main_01_model", win)
try:
    win._kind_btns["セミ・モーター"].click()
    audit("main_02_kind", win)
except Exception as e:
    say(f"種類ボタンを押せない: {e!r}")
for i in range(win.mode_combo.count()):
    win.mode_combo.setCurrentIndex(i)
    audit(f"main_mode{i}_{win.mode_combo.currentText()}", win)
win.mode_combo.setCurrentIndex(0)
from nd287_app import easy_mode, easy_machine
for tag, fn in (("設定", lambda: G.SettingsDialog(win, win.settings)),
                ("はじめてモード", lambda: easy_mode.EasyModeDialog(win, easy_machine.MachinePoller(win.settings)))):
    try:
        d = fn()
        d.show()
        for _ in range(10):
            app.processEvents()
        audit("dlg_" + tag, d)
        d.close()
    except Exception as e:
        say(f"[{tag}] 開けない: {e!r}")
try:
    from nd287_app import kind_conditions_ui as KU
    d = KU.KindConditionDialog(win, win.settings, "TEST-1"); d.show(); audit("dlg_種類別の登録", d); d.close()
except Exception as e:
    say(f"[種類別の登録] 開けない: {e!r}")
(HERE / "画面の切れ_結果.txt").write_text("\n".join(lines), encoding="utf-8")
say(f"結果: {HERE / '画面の切れ_結果.txt'}")
