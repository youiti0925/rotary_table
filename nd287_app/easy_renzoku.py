# -*- coding: utf-8 -*-
"""はじめてモードの「連続測定（フルクロ → セミクロ）」の画面側（EasyModeDialog に混ぜて使う）。

決まりごとは renzoku.py（Qt なし）。ここは、帯の画面・押したときの動きだけ。
機械に書くのは、今までの「機械に入れる」画面（人が確かめて押す）だけ。ここからは書かない。
流れ（1台ぶん）:
  測る予定 → 先にどちらか選ぶ → 〔機械に入れる → 電源・原点〕 → 測る → 保存
  → 次の工程の案内（切り替える） → … → 最後に、フルへもどすか選ぶ → 次の1台（載せ替え）
"""
from PySide6 import QtCore, QtWidgets

from . import easy_art, easy_flow
from . import renzoku as RZ
from .easy_flow import STEPS, INDEX

RZ_CTX_RESET = dict(rz_planned=False, rz_unit=1, rz_pos=0, rz_skip_write=False, rz_return_mode=False, rz_return_done=False,
                    rz_return_answered=False, rz_first=None, rz_first_user=False, rz_semi=False, rz_choice=None,
                    rz_machine=None, rz_machine_note="", rz_prev_model="")
SWITCH_TEXT = "書く → 電源の入れ直し → 原点 → プログラムを送る"


class RenzokuMixin:
    # ------------------------------------------------------------ 状態
    def _rz_init(self):
        self.rz = None                 # いまの1台の進み具合（renzoku.Run）
        self.rz_last_machine = None    # 前の1台が終わったときの、機械の状態（順番の目安にだけ使う。飛ばす根拠にはしない）
        self.ctx.update(RZ_CTX_RESET)

    def _rz_plan(self):
        if self.flow != "measure":
            return None
        try:
            return self.host.easy_rz_plan()
        except Exception:
            return None

    def _rz_ctx(self, c):
        """_all_ctx から呼ぶ: 連続測定に関わる値を足す。"""
        plan = self._rz_plan()
        active = bool(plan) and (len(plan["stages"]) == 2 or bool(plan["problems"]))
        c["rz_active"] = active
        c["rz_problems"] = list(plan["problems"]) if (plan and active) else []
        c["rz_order"] = self.ctx.get("rz_first") if active else None
        c["rz_pos"] = self.rz.pos if self.rz is not None else 0
        c["rz_can_return"] = bool(self.rz is not None and self.rz.can_return())
        if active:
            c["kind_missing"] = False        # 種類は、予定から自動で入れる（ここで選ばせない）
        return c

    def _rz_band(self, sid):
        """画面の上の帯: つづけて測るあいだ、いま何番目の工程かを出す。"""
        lb = getattr(self, "lbl_rz", None)
        if lb is None:
            return
        rz = self.rz
        show = rz is not None and self.flow == "measure" and sid not in ("rz_plan", "home")
        if show:
            if self.ctx.get("rz_return_mode"):
                text = "つづけて測る　最後の仕上げ：フルクロにもどしています"
            else:
                cur = rz.pos + (1 if sid in ("finish", "rz_return") else 0)           # 保存が済んだ画面では、いまの工程も済んだ印
                parts = [("✓ " if i < cur else ("● " if i == cur else "○ ")) + s["label"] for i, s in enumerate(rz.stages)]
                text = "つづけて測る　" + "　→　".join(parts) + (f"　（{rz.unit}台目）" if rz.unit >= 2 else "")
            lb.setText(text)
            li = getattr(self, "lbl_rz_in", None)
            if li is not None:
                li.setText(text)
        lb.setVisible(bool(show))
        li = getattr(self, "lbl_rz_in", None)
        if li is not None:
            li.setVisible(bool(show and sid == "machine_in"))

    def _rz_stage_label(self, st):
        return RZ.STAGE_JA[st["stage"]] if st else ""

    # ------------------------------------------------------------ 「つづけて測る予定」の画面
    def _rz_enter_plan(self):
        """この画面に入ったとき: 機械がいまフルかセミかを読む（読むだけ）。読めたら、そのまま測れる方を先の既定にする。"""
        if self.ctx.get("rz_first_user"):
            return
        self._rz_read(first_time=True)

    def _rz_read(self, first_time=False):
        plan = self._rz_plan()
        if not plan or len(plan["stages"]) < 2:
            return
        machine, note = None, ""
        if self.st.get("online"):
            if self.poller is not None:
                self.poller.pause()
            try:
                r = self.host.easy_machine_loop()
            except Exception as e:
                r = {"ok": False, "stage": None, "error": str(e)}
            finally:
                if self.poller is not None:
                    self.poller.resume()
            machine = r.get("stage")
            note = "" if machine else ("機械の検出器の状態を読めませんでした" + (f"（{r.get('error')}）" if r.get("error") else ""))
        else:
            note = "機械が見えないので、検出器の状態は読んでいません"
        self.ctx["rz_machine"], self.ctx["rz_machine_note"] = machine, note
        if not self.ctx.get("rz_first_user"):
            self.ctx["rz_first"] = RZ.default_first(plan["stages"], machine or self.rz_last_machine)

    def _build_rz_plan(self):
        plan = self._rz_plan()
        if not plan:
            return
        stages = plan["stages"]
        if plan["problems"]:
            band = QtWidgets.QLabel("\n".join(plan["problems"]))
            band.setObjectName("kind_problem")
            band.setWordWrap(True)
            band.setStyleSheet("background:#FBE9E7; color:#B02A20; border:2px solid #D43A2F; border-radius:10px;"
                               " padding:8px 12px; font-size:14px; font-weight:700;")
            self.extra.addWidget(band)
        if len(stages) < 2:
            return
        head = QtWidgets.QLabel(RZ.plan_line(stages))
        head.setObjectName("rz_line")
        head.setWordWrap(True)
        head.setStyleSheet("color:#17212B; font-size:15px; font-weight:700;")
        self.extra.addWidget(head)
        q = QtWidgets.QLabel("どちらから測りますか？")
        q.setStyleSheet("color:#5A6876; font-size:13px;")
        self.extra.addWidget(q)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        self._rz_btns = {}
        from .easy_mode import ChoiceButton
        for st in stages:
            b = ChoiceButton(self._rz_stage_label(st) + "から", easy_art.choice_measure_kind(st["kind"]))
            b.setChecked(self.ctx.get("rz_first") == st["stage"])
            b.clicked.connect(lambda _c=False, k=st["stage"]: self._rz_pick_first(k))
            row.addWidget(b)
            self._rz_btns[st["stage"]] = b
        self.extra.addLayout(row)
        first = next((s for s in stages if s["stage"] == self.ctx.get("rz_first")), None)
        m = self.ctx.get("rz_machine")
        unit = self.ctx.get("rz_unit") or 1
        model = self.host.e_model.text().strip()
        if m:
            line = f"機械はいま「{RZ.STAGE_JA[m]}」の状態です。"
            col = "#17212B"
        else:
            line = (self.ctx.get("rz_machine_note") or "機械の状態は、まだ読んでいません。") + "　フルクロから測るのを、はじめの選びにしてあります。"
            col = "#8A6A00"
        lb = QtWidgets.QLabel(line)
        lb.setObjectName("rz_machine")
        lb.setWordWrap(True)
        lb.setStyleSheet(f"font-size:14px; font-weight:700; color:{col};")
        self.extra.addWidget(lb)
        if first is not None:
            rel = RZ.relation(m, first["stage"])
            same_model = bool(unit >= 2 and model and model == self.ctx.get("rz_prev_model"))
            if unit >= 2 and rel == "same" and same_model:
                msg, col = "このまま測れます。機械に書く・プログラムを送るは、飛ばします。", "#16864C"
            elif unit >= 2 and rel == "same":
                msg, col = "型式が前の1台と違うので、機械に入れる所から進みます。", "#8A6A00"
            elif unit >= 2 and rel == "switch":
                msg, col = f"{RZ.STAGE_JA[first['stage']]}に切り替えます（{SWITCH_TEXT}）。", "#8A6A00"
            elif unit >= 2:
                msg, col = "機械の状態を読めないので、飛ばさずに、機械に入れる所から進みます。", "#8A6A00"
            else:
                msg, col = "最初の1台は、機械に入れる所から進みます。", "#5A6876"
            n = QtWidgets.QLabel(msg)
            n.setObjectName("rz_note")
            n.setWordWrap(True)
            n.setStyleSheet(f"font-size:14px; font-weight:700; color:{col};")
            self.extra.addWidget(n)
        sfx = plan.get("suffix") or {}
        if sfx:
            t = QtWidgets.QLabel("保存の名前の末尾（社内の表）: " + "　".join(
                f"{RZ.STAGE_SHORT[s['stage']]}＝{sfx.get(s['stage']) or 'なし'}" for s in stages) + "　（工程ごとに別の名前で保存します）")
            t.setWordWrap(True)
            t.setStyleSheet("color:#5A6876; font-size:12px;")
            self.extra.addWidget(t)
        r = QtWidgets.QPushButton("機械の状態を読み直す（読むだけ）")
        r.setObjectName("sub")
        r.setCursor(QtCore.Qt.PointingHandCursor)
        r.clicked.connect(self._rz_reread)
        self.extra.addWidget(r)

    def _rz_pick_first(self, stage):
        self.ctx["rz_first"], self.ctx["rz_first_user"] = stage, True
        self._render()

    def _rz_reread(self):
        self.ctx["rz_first_user"] = False
        self._rz_read()
        self._render()

    def _rz_art_items(self, plan=None):
        if self.rz is not None:
            stages = list(self.rz.stages)
        else:
            plan = plan or self._rz_plan()
            stages = list(plan["stages"]) if plan else []
            first = self.ctx.get("rz_first")
            stages.sort(key=lambda s: 0 if s["stage"] == first else 1)
        return [(RZ.STAGE_JA[s["stage"]], CK_TEXT(s), s["kind"]) for s in stages]

    # ------------------------------------------------------------ 予定を決めて先へ
    def _rz_confirm(self):
        """「つづけて測ります」の「次へ」: 1台ぶんの進み具合を作り、最初の工程を測定の画面に入れる。"""
        plan = self._rz_plan()
        if not plan or len(plan["stages"]) < 2 or plan["problems"]:
            return
        c = self.ctx
        machine = c.get("rz_machine")
        first = c.get("rz_first") or RZ.default_first(plan["stages"], machine)
        unit = c.get("rz_unit") or 1
        self.rz = RZ.Run(plan, first, machine, unit)
        model = self.host.e_model.text().strip()
        skip = self.rz.skip_write(model, c.get("rz_prev_model"), machine)
        c.update(rz_planned=True, rz_pos=0, rz_skip_write=skip, handoff=False, skip_machine=False, cycled=False,
                 cycled_in_wizard=False, powered_on=False, prog="auto", params="auto")
        self._rz_apply_current()
        if skip:
            c["handoff"] = True
            self.history.append(self.i)
            self._enter(INDEX["fields"])
            return
        if unit >= 2:                                       # 載せ替えで切り替える: はじめの電源・非常停止の所から
            self.history.append(self.i)
            self._enter(self._rz_first_needed())
            return
        j = easy_flow.next_index(self.i, self.st, self._all_ctx(), self.flow)
        if j is not None:
            self.history.append(self.i)
            self._enter(j)

    def _rz_first_needed(self):
        c = self._all_ctx()
        for j in range(INDEX["power"], len(STEPS)):
            if self.flow in STEPS[j]["fl"] and easy_flow.needed(STEPS[j]["id"], self.st, c):
                return j
        return INDEX["machine_in"]

    def _rz_apply_current(self):
        """いまの工程の種類・何を測るかを、測定の画面に入れる。「機械に入れる」画面へ渡す選び（セミにする・分け方）も決める。"""
        st = self.rz.current
        self.ctx["rz_semi"] = st["stage"] == RZ.STAGE_SEMI
        self.ctx["rz_choice"] = st["ck"]
        try:
            self.host.easy_rz_apply(st)
        except Exception as e:
            self._notice = f"測り方を入れられませんでした: {e}"

    # ------------------------------------------------------------ 機械に入れ終えた・飛ばした
    def _rz_after_wizard(self, res):
        """「機械に入れる」が終わった: 実際に書いたのが、予定の状態か確かめる。違っても止めない（人の選び）が、はっきり出す。"""
        if self.rz is None:
            return
        want = RZ.STAGE_SEMI if (self.ctx.get("rz_return_mode") is False and self.ctx.get("rz_semi")) else RZ.STAGE_FULL
        got = RZ.STAGE_SEMI if res.get("semi_written") else (RZ.STAGE_FULL if (res.get("has_full_product") or want == RZ.STAGE_FULL) else want)
        self.rz.mark_machine(got)
        self.ctx["rz_machine"] = got
        if got != want:
            self._notice = (f"予定は{RZ.STAGE_JA[want]}ですが、{RZ.STAGE_JA[got]}の値で書きました。"
                            "このまま測ると、保存の名前の末尾とずれます。")

    def _rz_skip_machine(self):
        if self.rz is not None:
            target = RZ.STAGE_FULL if self.ctx.get("rz_return_mode") else self.rz.current["stage"]
            self.rz.mark_machine(target)

    # ------------------------------------------------------------ 1つ目が終わった → 次の工程
    def _rz_finish_lines(self):
        """finish の画面: (見出し, 文の一覧, 次へのボタンの文 or None)。"""
        rz = self.rz
        if self.ctx.get("rz_return_mode"):
            return "フルクロにもどしました", ["機械に、製品の値を書いてもどしました。", "書いたあとの読み戻しで、合っていることを確かめてください。"], None
        cur = rz.current
        if not rz.is_last:
            nx = rz.upcoming
            return (f"{cur['label']}が終わりました",
                    [f"つぎは {nx['label']}（{RZ.STAGE_JA[nx['stage']]}）。", f"機械を切り替えます（{SWITCH_TEXT}）。",
                     "保存した結果は、そのまま残ります。"], f"つぎは{RZ.STAGE_JA[nx['stage']]} ▶")
        lines = [f"{cur['label']}が終わりました。", "ぜんぶ測り終わりました。"]
        if self.ctx.get("rz_return_answered") and not self.ctx.get("rz_return_mode"):
            lines.append("フルクロには、もどしていません。機械はいま「" + RZ.STAGE_JA.get(rz.machine or "", "不明") + "」です。")
        return "連続測定が終わりました", lines, None

    def _rz_advance(self):
        rz = self.rz
        if rz is None or not rz.advance():
            return
        c = self.ctx
        c.update(handoff=False, skip_machine=False, cycled=False, cycled_in_wizard=False, powered_on=False, prog="auto",
                 params="auto", rz_pos=rz.pos, rz_skip_write=False, finish_seen=False)
        try:
            self.host.easy_rz_discard()
        except Exception:
            pass
        self._rz_apply_current()
        self.history.append(self.i)
        self._enter(self._rz_first_needed())

    def _rz_next_unit(self):
        """載せ替えて、同じ型式の次の1台。機械はいまの状態のまま（同じ状態なら、書く・送るを飛ばせる）。"""
        if self.rz is None:
            return
        self.rz_last_machine = self.rz.machine
        model = self.host.e_model.text().strip()
        unit = (self.ctx.get("rz_unit") or 1) + 1
        self.rz = None
        try:
            self.host.easy_new_unit()
        except Exception as e:
            self._notice = f"次の1台を始められませんでした: {e}"
        self.ctx.update(RZ_CTX_RESET)
        self.ctx.update(rz_unit=unit, rz_prev_model=model, handoff=False, skip_machine=False, cycled=False,
                        cycled_in_wizard=False, powered_on=False, prog="auto", params="auto", finish_seen=False)
        self.history.append(self.i)
        self._enter(INDEX["rz_plan"])

    # ------------------------------------------------------------ 最後に、フルへもどすか
    def _build_rz_return(self):
        b = self._act_button("フルクロにもどす（おすすめ）", self._rz_return_yes)
        self._extra_widgets["act"] = b
        r = QtWidgets.QPushButton("もどさない（このまま）")
        r.setObjectName("sub")
        r.setCursor(QtCore.Qt.PointingHandCursor)
        r.clicked.connect(self._rz_return_no)
        self.extra.addWidget(r)
        self._small_note("もどすときは、受注伝票番号の製品の値を機械に書きます。いまの機械の値は、書く前に控えます。")

    def _rz_return_no(self):
        self.ctx.update(rz_return_answered=True, rz_return_done=True)
        self._next()

    def _rz_return_yes(self):
        rz = self.rz
        if rz is None:
            return
        full = next((s for s in rz.stages if s["stage"] == RZ.STAGE_FULL), None)
        c = self.ctx
        c.update(rz_return_answered=True, rz_return_mode=True, rz_semi=False, rz_choice=full["ck"] if full else None,
                 handoff=False, skip_machine=False, cycled=False, cycled_in_wizard=False, powered_on=False, prog="auto",
                 params="auto", rz_skip_write=False)
        self.history.append(self.i)
        self._enter(self._rz_first_needed())


def CK_TEXT(st):
    return RZ.WHAT_JA.get(st["ck"], "")
