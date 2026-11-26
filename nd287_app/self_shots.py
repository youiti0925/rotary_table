# -*- coding: utf-8 -*-
"""アプリ自身に、自分の画面を写させる（入れたアプリそのものの見た目を確かめるため。2026-11-26）。

起動: `nd287_bunkatsu.exe --self-shots <出すフォルダ>`
- 練習モードの場所（アプリの隣の `練習`）で動く。本番の設定・マスタ・測定データは書かない。機械にはつながない。
- 表示器は、にせ物。通常の画面と、はじめてモードの「測り方を確かめる → 軸取り外し → 測っています → こういう結果でした → 保存」を
  PNG にして、`結果.txt` に窓の大きさと、画面の中に収まったかを書く。写し終わったら自分で閉じる。
会社PCは、外から画面を操作する許可が出ないので、この方法で確かめる。
"""
import sys
import traceback
from pathlib import Path


def run(out_dir, settings):
    from PySide6 import QtCore, QtWidgets
    from . import easy_flow, easy_mode, nd287
    from . import gui as G

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    lines = []

    def say(s):
        lines.append(str(s))
        (out / "結果.txt").write_text("\n".join(lines), encoding="utf-8")      # 途中で止まっても、そこまでが残る

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    G.install_japanese_standard_buttons(app)
    G.apply_font(app, settings.get("ui_font_pt", G.DEFAULT_FONT_PT))
    G.apply_theme(app, settings.get("ui_theme", G.DEFAULT_THEME))
    scr = app.primaryScreen()
    a = scr.availableGeometry()
    say(f"画面: 使える所 {a.width()}x{a.height()}　倍率 {scr.devicePixelRatio()}　字 {settings.get('ui_font_pt')}pt　色 {settings.get('ui_theme')}")
    say(f"動かしているもの: {sys.executable}")
    nd287.time.sleep = lambda s: None                               # にせ物の表示器の待ち時間を無くす

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

    def inside(wd):
        if wd.isMaximized():                # 最大にした窓は、枠が画面の外へ数ピクセル出るのがふつう（Windows）。中身の大きさで見る
            ok = wd.width() <= a.width() and wd.height() <= a.height()
        else:
            ok = a.contains(wd.frameGeometry())
        return "収まっています" if ok else "収まっていません ★"

    def one(tag, model, mode, axes, steps, n, main_shot=False):
        w = G.MainWindow(nd287.DummyDevice(), 10.0, 0.5, 5.0, 0.0, dict(settings))
        try:
            w.fit_before_show()
        except Exception:
            pass
        w.showMaximized()
        pump(12)
        w.e_model.setText(model)
        w.on_model_entered()
        if mode:
            w.mode_combo.setCurrentText(mode)
            w.on_model_entered()
        w.e_machine.setText("50014861")
        w.e_operator.setText("試験")
        w.e_temp.setText("20")
        pump()
        if main_shot:
            w.grab().save(str(out / "0通常の画面_測る前.png"))
            say(f"0通常の画面_測る前.png: 窓 {w.width()}x{w.height()}　画面の中に{inside(w)}")
        d = easy_mode.EasyModeDialog(w, Poller())
        d.show()
        pump()

        def snap(name):
            pump()
            d._refresh()
            pump()
            f = out / f"{tag}_{name}.png"
            d.grab().save(str(f))
            say(f"{f.name}: 窓 {d.width()}x{d.height()}　画面の中に{inside(d)}")

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
        if main_shot:
            d.hide()
            pump()
            w.grab().save(str(out / "0通常の画面_測ったあと.png"))
            say(f"0通常の画面_測ったあと.png: 窓 {w.width()}x{w.height()}　画面の中に{inside(w)}")
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

    code = 0
    try:
        one("1分割", "RNA-250R", "", [("回転", "Z")], ["cond_check", "fields", "axis_on", "start1"], 23, main_shot=True)
        one("2再現性_登録なし", "ABC-999", "回転再現性", [("回転", "Z")], ["cond_check"], 30)
        one("3分割と再現_2軸", "RNA-250R", "回転分割+再現", [("回転", "Z"), ("傾斜", "X")], ["cond_check", "axis_on"], 120)
        say("おわり（最後まで写せました）")
    except Exception:
        say("★ 途中で止まりました:\n" + traceback.format_exc())
        code = 1
    return code
