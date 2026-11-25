# -*- coding: utf-8 -*-
"""連続測定（フルクロ → セミクロ）の決まりごと（Qt なし。画面なしで試験できる）。

決めたこと（docs/連続測定_下書き_20261125.md・測定条件_3種類_決めたやり方_20261125.md）:
  - 予定は、型式ごとの「測る予定」（種類別 Excel の3つめのシート）。フルもセミも「しない以外」が入っているときだけ、連続測定。
  - 先にどちらを測るかは選べる。既定は、機械の今の状態（1815 の #1。1＝フル）で、そのまま測れる方。読めないときはフル先。
  - 1つ目が終わったら、次の工程の案内へ。切り替えるときだけ「書く → 電源の入れ直し・原点の決め直し → プログラムを送る」。
  - 2台目以降（載せ替え）は、機械の今の状態から始める。同じ状態（機械から読めたとき）なら、書く・送るを飛ばす。
  - セミの結果も、フルと同じく検査表・Web に出す。名前は 機番＋末尾（社内の表）で区別し、フルの結果を上書きしない。
  - 最後に、フルへ戻すか選ぶ。既定は戻す（戻さなくてもよい）。戻すときは、伝票番号の製品データから作った値を書く。
機械に書くのは、ここでは一切しない（書くのは「機械に入れる」画面。人が確かめて押す）。
"""
from . import kind_conditions as K

KIND_FULL = "フル・スケール付き"
STAGE_FULL, STAGE_SEMI = "full", "semi"
STAGE_JA = {STAGE_FULL: "フルクロ", STAGE_SEMI: "セミクロ"}
STAGE_SHORT = {STAGE_FULL: "フル", STAGE_SEMI: "セミ"}
WHAT_CK = {"分割だけ": "division", "再現性だけ": "repeat", "両方": "one"}     # easy_cond_mode の「何を測るか」
CK_JA = {"division": "分割", "repeat": "再現性", "one": "分割＋再現性"}
WHAT_JA = {"division": "分割だけ", "repeat": "再現性だけ", "one": "分割＋再現性"}


def semi_kind_of(text):
    """計画の「セミの種類」（モーター／駆動装置／セミ・モーター…）→ 種類名。分からなければ ""。"""
    t = str(text or "").strip()
    k = K.normalize_kind(t)
    if k in ("セミ・モーター", "セミ・駆動装置"):
        return k
    return K.normalize_kind("セミ・" + t) if t in ("モーター", "駆動装置") else ""


def _stage(stage, kind, what):
    ck = WHAT_CK[what]
    return {"stage": stage, "kind": kind, "what": what, "ck": ck,
            "label": f"{STAGE_SHORT[stage]}（{WHAT_JA[ck]}）"}


def make_plan(table, model):
    """型式の測る予定 → {"model","stages":[…],"continuous":bool,"problems":[…]}。予定が無ければ None（今までどおり1回測る）。
    stages は フル → セミ の順（先に測る方は Run で決める）。フルかセミの片方だけ・どちらも無いときは continuous=False。"""
    rec = K.find_plan(table or {}, str(model or "").strip()) if str(model or "").strip() else None
    if not rec:
        return None
    full, semi = str(rec.get("フルで測る") or "").strip(), str(rec.get("セミで測る") or "").strip()
    stages, problems = [], []
    if full in WHAT_CK:
        stages.append(_stage(STAGE_FULL, KIND_FULL, full))
    if semi in WHAT_CK:
        sk = semi_kind_of(rec.get("セミの種類"))
        if not sk:
            problems.append("測る予定の「セミの種類」（モーター／駆動装置）が決まっていません")
        else:
            stages.append(_stage(STAGE_SEMI, sk, semi))
    if not stages and not problems:
        return None
    return {"model": str(model).strip(), "stages": stages, "problems": problems,
            "continuous": len(stages) == 2 and not problems}


def plan_line(stages):
    """「測る予定: フル（分割＋再現性） → セミ（分割だけ）」"""
    return "測る予定: " + " → ".join(s["label"] for s in stages)


def stage_of_loop(text):
    """機械から読んだ 'フル'／'セミ' → full／semi。読めない（''）は None。"""
    return {"フル": STAGE_FULL, "セミ": STAGE_SEMI}.get(str(text or "").strip())


def default_first(stages, machine):
    """先に測る方。機械が今その状態の方（そのまま測れる）。読めない・どちらにも当たらないときはフル先。"""
    kinds = [s["stage"] for s in stages]
    if machine in kinds:
        return machine
    return STAGE_FULL if STAGE_FULL in kinds else kinds[0]


def relation(machine, target):
    """機械の今の状態と、これから測る方: same（そのまま測れる）／switch（切り替える）／unknown（読めない。切り替えの案内にする）。"""
    if machine not in (STAGE_FULL, STAGE_SEMI):
        return "unknown"
    return "same" if machine == target else "switch"


class Run:
    """1台ぶんの連続測定の進み具合。unit=何台目か、machine=機械が今フルかセミか（読めた／書いたとおり。不明は None）。"""

    def __init__(self, plan, first=None, machine=None, unit=1):
        self.plan = plan
        self.unit = int(unit)
        self.machine = machine
        first = first or default_first(plan["stages"], machine)
        self.stages = sorted(plan["stages"], key=lambda s: 0 if s["stage"] == first else 1)
        self.pos = 0
        self.return_choice = True              # 最後に聞く。既定は戻す
        self.returned = False

    @property
    def current(self):
        return self.stages[self.pos]

    @property
    def is_last(self):
        return self.pos >= len(self.stages) - 1

    @property
    def upcoming(self):
        return None if self.is_last else self.stages[self.pos + 1]

    def relation(self, target=None):
        return relation(self.machine, (target or self.current)["stage"])

    def mark_machine(self, stage):
        """機械をその状態にした（または、その状態だと読めた）。"""
        self.machine = stage

    def advance(self):
        if self.is_last:
            return False
        self.pos += 1
        return True

    def can_return(self):
        """最後の工程がセミで、フルの予定もあるとき、フルへ戻せる（戻すかは人が選ぶ）。"""
        return self.is_last and self.current["stage"] == STAGE_SEMI and any(s["stage"] == STAGE_FULL for s in self.stages)

    def next_unit(self):
        """載せ替えて、同じ型式の次の1台。機械の今の状態から始める（同じ状態ならそのまま測れる）。"""
        return Run(self.plan, None, self.machine, self.unit + 1)

    def skip_write(self, model, prev_model, machine_read):
        """書く・送るを飛ばしてよいか。載せ替え（2台目以降）で、同じ型式、機械から読んだ状態が測る方と同じとき、だけ。
        読めていない（machine_read が None）ときは飛ばさない（書いた記憶だけでは決めない）。"""
        return (self.unit >= 2 and bool(model) and model == prev_model
                and machine_read is not None and relation(machine_read, self.current["stage"]) == "same")


def save_name_conflict(names):
    """連続測定の各工程の保存名（拡張子なし）が同じになるか。同じなら、その名前のリスト（フルの結果をセミが上書きする）。"""
    seen, dup = set(), []
    for n in names:
        if n in seen and n not in dup:
            dup.append(n)
        seen.add(n)
    return dup
