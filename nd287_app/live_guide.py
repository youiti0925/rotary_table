"""測っているあいだの実況と見張り・かかる時間を覚える（2026-11-26。Qt なし）。

- 実況: いまどの向きの何点目か・あと何分くらいか。
- 見張り: (a) いま入った値が、前の点から規格（単一誤差）より大きく動いた (b) いつもの間隔よりずっと長く、次の値が来ていない。
  どちらも**見えた事実だけ**を言う（原因は言わない。合否も決めない。測定は止めない）。
- 測り終わるたびに、型式・測り方・点数・かかった秒を「測定にかかった時間.csv」（PC側・マスタ外）に残し、
  次から「この型式は、前は約 N 分でした」と出す（登録するほど、見込みが当たるようになる）。
"""
from __future__ import annotations

import csv
import datetime as _dt
from pathlib import Path
from statistics import median

TIME_FILE = "測定にかかった時間.csv"
TIME_HEADER = ("日時", "型式", "測り方", "点数", "秒")
MIN_POINTS_FOR_PACE = 3          # 今回の進み方から「あと何分」を出し始める点数
STALL_FACTOR = 4                 # いつもの間隔の何倍あいたら知らせるか
STALL_MIN_SEC = 30

SERIES_NAME = {"wheel_cw": "ホイール・時計回り（CW）", "wheel_ccw": "ホイール・反時計回り（CCW）",
               "worm_cw": "ウォーム・時計回り（CW）", "worm_ccw": "ウォーム・反時計回り（CCW）",
               "cw": "時計回り（CW）から止める", "ccw": "反時計回り（CCW）から止める"}
# その向きで何を測っているか（測定条件の決まりそのまま。sequence.py の順序の説明と同じ）
SERIES_NOTE = {
    "wheel_cw": "テーブルを時計回りに少しずつ回して、止まった角度のずれを順に測っています。",
    "wheel_ccw": "今度は反対向きに回して、同じ所を測っています。向きで止まる位置がどれだけ変わるか（バックラッシ）が分かります。",
    "worm_cw": "ウォーム1回転ぶんの短い範囲を、細かい刻みで測っています。",
    "worm_ccw": "ウォーム1回転ぶんの短い範囲を、反対向きに測っています。",
    "cw": "同じ角度に何回も止めて、止まる位置のばらつき（再現性）を測っています。",
    "ccw": "同じ角度に何回も止めて、止まる位置のばらつき（再現性）を測っています。",
}


def minutes_text(sec):
    """秒 → 「約 N 分」。1分より短ければ「1 分より短い」。"""
    sec = float(sec)
    if sec < 50:
        return "1 分より短い"
    return f"約 {max(1, int(round(sec / 60.0)))} 分"


def remaining(elapsed, done, total, intervals=()):
    """今回の進み方から、残りの秒。まだ分からなければ None。
    点と点の間の秒（intervals）が3つあれば、その真ん中の値で数える（途中で長く止まっても、見込みが狂わない）。"""
    if done < MIN_POINTS_FOR_PACE or total <= 0:
        return None
    iv = [float(x) for x in intervals if x and x > 0]
    if len(iv) >= 3:
        return max(0.0, median(iv) * (total - done))
    if elapsed <= 0:
        return None
    return max(0.0, float(elapsed) / done * (total - done))


# ------------------------------------------------------------------ かかった時間の記録
def load_times(path):
    p = Path(path)
    if not p.exists():
        return []
    try:
        with open(p, newline="", encoding="utf-8-sig") as f:
            return [dict(r) for r in csv.DictReader(f)]
    except (OSError, csv.Error, UnicodeDecodeError):
        return []


def add_time(path, *, model, mode, points, seconds, now=None):
    """測り終えた1回ぶんを残す。型式が空・点数0・秒が0以下は残さない。残したら True。"""
    model = str(model or "").strip()
    if not model or int(points) <= 0 or float(seconds) <= 0:
        return False
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with open(p, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(TIME_HEADER)
        w.writerow([(now or _dt.datetime.now()).strftime("%Y-%m-%d %H:%M"), model, str(mode or ""),
                    int(points), int(round(float(seconds)))])
    return True


def usual_seconds(rows, model, mode, points):
    """同じ型式・同じ測り方・同じ点数の、これまでの (真ん中の秒, 件数)。無ければ (None, 0)。"""
    model = str(model or "").strip().upper()
    vals = []
    for r in rows or ():
        try:
            if (str(r.get("型式") or "").strip().upper() == model and str(r.get("測り方") or "") == str(mode or "")
                    and int(r.get("点数") or 0) == int(points)):
                vals.append(float(r.get("秒") or 0))
        except ValueError:
            continue
    vals = [v for v in vals if v > 0]
    return (float(median(vals)), len(vals)) if vals else (None, 0)


# ------------------------------------------------------------------ 見張り
def _dev_sec(target, measured):
    """指令角度と測った角度の差[秒]（360°のまたぎをならす）。"""
    return ((float(measured) - float(target) + 180.0) % 360.0 - 180.0) * 3600.0


def step_watch(targets, measured, limit):
    """いま入った点が、前の点から規格（単一誤差）より大きく動いたか。動いていれば知らせる文、無ければ ""。

    比べ方は表の赤い字と同じ（絶対値が規格を超えたら）。測定は止めない。"""
    if not limit or len(targets) < 2 or len(measured) < 2:
        return ""
    step = _dev_sec(targets[-1], measured[-1]) - _dev_sec(targets[-2], measured[-2])
    if abs(step) <= float(limit):
        return ""
    return (f"{float(targets[-2]):g}° から {float(targets[-1]):g}° で、ずれが {abs(step):.1f} 秒 変わりました"
            f"（単一誤差の規格は {float(limit):g} 秒）。このまま続けて大丈夫です。測り終わると、ここだけ測り直せます。")


def count_steps_over(targets, measured, limit):
    """ここまでで、1歩の変化が規格を超えた所の数。"""
    if not limit or len(targets) < 2:
        return 0
    d = [_dev_sec(t, m) for t, m in zip(targets, measured)]
    return sum(1 for a, b in zip(d, d[1:]) if abs(b - a) > float(limit))


def stall_watch(since_last, intervals):
    """次の値が、いつもよりずっと長く来ていないか。来ていなければ知らせる文、無ければ ""。
    intervals＝今回ここまでの、点と点の間の秒。5 個たまるまでは言わない。"""
    iv = [float(x) for x in intervals if x is not None and x > 0]
    if len(iv) < 5:
        return ""
    usual = median(iv)
    if since_last <= max(STALL_MIN_SEC, usual * STALL_FACTOR):
        return ""
    return (f"{int(since_last)} 秒、次の値が来ていません（ここまでは 約 {max(1, int(round(usual)))} 秒ごと）。"
            "機械が止まっていないか、機械の画面にアラームが出ていないか見てください。")


# ------------------------------------------------------------------ 実況
def narration(key, done, total, *, elapsed=0.0, usual=None, usual_n=0, intervals=()):
    """実況の行（上から 向き・何を測っているか・進み・あと何分）。"""
    name = SERIES_NAME.get(key, "")
    lines = dict(name=name, note=SERIES_NOTE.get(key, ""), count=f"{done} ／ {total} 点")
    rem = remaining(elapsed, done, total, intervals)
    if rem is not None:
        lines["remain"] = "あと " + minutes_text(rem)
    elif usual:
        lines["remain"] = "全部で " + minutes_text(usual) + " の見込み"
    else:
        lines["remain"] = "あと何分かは、3 点測ると出ます"
    lines["usual"] = (f"この型式は、前は {minutes_text(usual)} でした（{usual_n} 回の記録）" if usual else
                      "この型式の時間の記録は、まだありません（測り終わると残ります）")
    return lines
