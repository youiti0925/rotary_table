"""はじめてモードの「測る所から先」の画面を、アプリ自身に写させる（機械にはつながない・本番の設定と測定データは触らない）。

使い方（会社PCで、150% の本物の画面で）:  python tools\はじめてモードの測る画面を写す.py
結果は、このファイルの隣の「はじめてモード_写真」フォルダに出る（PNG と 結果.txt）。
表示器は、にせ物（練習用）。設定は一時フォルダ。写真（軸取り外し）は、リポジトリの隣の「写真」フォルダにあれば使う。
docs/はじめてモード_測る画面の作り直し_20261126.md
"""
import copy, os, shutil, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
if "--offscreen" in sys.argv:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6 import QtWidgets, QtCore
from nd287_app import settings as S, nd287
_tmp = Path(tempfile.mkdtemp())
S.settings_path = lambda: _tmp / "settings.json"                # 本番の設定を触らない
nd287.time.sleep = lambda s: None                               # にせ物の表示器の待ち時間を無くす
from nd287_app import gui as G, easy_mode, easy_flow
from nd287_app.settings import DEFAULTS

OUT = HERE / "はじめてモード_写真"
OUT.mkdir(exist_ok=True)
lines = []


def say(s):
    lines.append(str(s))
    print(s)


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
G.apply_font(app, 7)
G.apply_theme(app, "ネイビー・コーポレート")
scr = app.primaryScreen()
say(f"画面: 使える所 {scr.availableGeometry().width()}x{scr.availableGeometry().height()}　倍率 {scr.devicePixelRatio()}")

for cand in (ROOT / "写真" / "detach.jpg", HERE / "detach.jpg"):   # 軸取り外しの写真
    if cand.is_file():
        (_tmp / "写真").mkdir(exist_ok=True)
        shutil.copy(cand, _tmp / "写真" / "detach.jpg")
        say(f"写真: {cand}")
        break
else:
    say("写真: 軸取り外しの写真（detach.jpg）が無いので、絵で写します")


class Poller:
    def __init__(self):
        self.st = easy_flow.machine_state({"values": {"aut": 1, "run": 3, "emergency": 0, "alarm": 0}}, [], online=True)

    def state(self):
        return dict(self.st)

    def pause(self): pass
    def resume(self): pass
    def stop(self): pass


def pump(n=8):
    for _ in range(n):
        app.processEvents()
        QtCore.QThread.msleep(25)


def run(tag, model, mode, axes, steps, n):
    st = copy.deepcopy(DEFAULTS)
    st.update(focas_auto_find=False, save_root=str(_tmp / "save"), bs_save_root=str(_tmp / "bs"), webapp_sync_enabled=False, ui_font_pt=7)
    w = G.MainWindow(nd287.DummyDevice(), 10.0, 0.5, 5.0, 0.0, st)
    try:
        w.fit_before_show()
    except Exception:
        pass
    w.showMaximized()
    pump()
    w.e_model.setText(model)
    w.on_model_entered()
    if mode:
        w.mode_combo.setCurrentText(mode)
        w.on_model_entered()
    w.e_machine.setText("50014861")
    w.e_operator.setText("試験")
    w.e_temp.setText("20")
    d = easy_mode.EasyModeDialog(w, Poller())
    d.photo_base = str(_tmp)
    d.show()
    pump()

    def snap(name):
        pump()
        d._refresh()
        pump()
        f = OUT / f"{tag}_{name}.png"
        d.grab().save(str(f))
        g = d.frameGeometry()
        a = scr.availableGeometry()
        inside = a.contains(g)
        say(f"{f.name}: 窓 {d.width()}x{d.height()}　画面の中に{'収まっています' if inside else '収まっていません ★'}")

    def goto(sid):
        d.ctx["axes"] = list(axes)
        d.ctx["unit"] = "23"
        d.flow = "measure"
        d.history = [easy_flow.INDEX["home"]]
        d._enter(easy_flow.INDEX[sid])
        pump()

    for sid in steps:
        goto(sid)
        snap(sid)
    goto("measuring")
    if w.seq is None:
        w.start()
    for _ in range(n):
        w.take_manual()
    try:
        w.better.tick()
    except Exception:
        pass
    snap("measuring")
    v = d.v_measure
    say(f"　測っている画面: 点 {v.graph.points}　{v.lbl_count.text()}　{v.lbl_dev.text()}")
    while w.seq is not None and not w.seq.done():
        w.take_manual()
    pump()
    d._pending_next = False
    d._after_measuring()
    snap("summary")
    say(f"　結果の画面: {d.v_summary.lbl_verdict.text()}　表のます {len(d.v_summary.cells)}　赤 {sum(1 for _t, r in d.v_summary.cells if r)}")
    d._next()
    snap("save")
    w.saved = True
    try:
        d._timer.stop()
    except Exception:
        pass
    d.hide()
    w.hide()
    d.deleteLater()
    w.deleteLater()
    pump()


run("1分割", "RNA-250R", "", [("回転", "Z")], ["cond_check", "fields", "axis_on", "start1"], 23)
run("2再現性_登録なし", "ABC-999", "回転再現性", [("回転", "Z")], ["cond_check"], 30)
run("3分割と再現_2軸", "RNA-250R", "回転分割+再現", [("回転", "Z"), ("傾斜", "X")], ["axis_on"], 120)
(OUT / "結果.txt").write_text("\n".join(lines), encoding="utf-8")
say(f"出した所: {OUT}")
