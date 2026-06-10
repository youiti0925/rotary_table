# -*- coding: utf-8 -*-
"""測定シーケンス管理

割出測定は時間ポーリングではなく「割り出して静止 → 1点取込」のトリガ方式。

分割測定（IndexingSequence）の順序:
  1. ホイール CW  … 開始角度→終了角度 を wheel_pitch 刻み
                     （回転分割は0°→360°の閉じ点込み。傾斜分割は例 -30°→+110°）
  2. ホイール CCW … 同じポイントを逆順
  3. ウォーム CW  … worm_start からウォーム1回転ぶん(worm_range)を worm_pitch 刻み
  4. ウォーム CCW … 同じポイントを逆順

再現性測定（RepeatabilitySequence）の順序:
  各ブロック（ポイント）で CW から設定回数 → CCW から設定回数 → 次のブロック。
  例: 0,90,180,270の4ブロック × 各7回 × CW/CCW。
"""

import numpy as np

from .nd287 import deg_to_dms

SERIES_KEYS = ("wheel_cw", "wheel_ccw", "worm_cw", "worm_ccw")

SERIES_LABELS = {
    "wheel_cw": "ホイール CW",
    "wheel_ccw": "ホイール CCW",
    "worm_cw": "ウォーム CW",
    "worm_ccw": "ウォーム CCW",
}


def block_points(start, end, pitch, include_end=True):
    """開始〜終了を刻みで割ったポイント列"""
    stop = end + 1e-9 if include_end else end - 1e-9
    return [float(a) for a in np.arange(start, stop, pitch)]


class IndexingSequence:
    def __init__(
        self,
        wheel_pitch,
        worm_pitch,
        worm_range,
        worm_start=0.0,
        wheel_start=0.0,
        wheel_end=360.0,
    ):
        wheel_pts = block_points(wheel_start, wheel_end, wheel_pitch)
        worm_pts = block_points(worm_start, worm_start + worm_range, worm_pitch)
        self.steps = []  # (系列キー, 指令角度, 方向 +1/-1)
        for a in wheel_pts:
            self.steps.append(("wheel_cw", float(a), +1))
        for a in wheel_pts[::-1]:
            self.steps.append(("wheel_ccw", float(a), -1))
        for a in worm_pts:
            self.steps.append(("worm_cw", float(a), +1))
        for a in worm_pts[::-1]:
            self.steps.append(("worm_ccw", float(a), -1))
        self.idx = 0
        self.data = {k: ([], []) for k in SERIES_KEYS}

    def current(self):
        """次に測るべき (系列キー, 指令角度, 方向)。完了なら None。"""
        return self.steps[self.idx] if self.idx < len(self.steps) else None

    def guide_text(self):
        cur = self.current()
        if cur is None:
            return None
        key, target, _ = cur
        return (
            f"[{SERIES_LABELS[key]}]  {deg_to_dms(target)} へ割り出して静止"
            f" → ND287のPRINT（または手動取込）   ({self.idx + 1}/{len(self)})"
        )

    def record(self, measured: float):
        key, target, _ = self.steps[self.idx]
        self.data[key][0].append(target)
        self.data[key][1].append(float(measured))
        self.idx += 1

    def undo(self):
        """直前の取込を1点取り消す。取り消せたら True。"""
        if self.idx == 0:
            return False
        self.idx -= 1
        key, _, _ = self.steps[self.idx]
        self.data[key][0].pop()
        self.data[key][1].pop()
        return True

    def done(self):
        return self.idx >= len(self.steps)

    def __len__(self):
        return len(self.steps)


# 後方互換の別名
Sequence = IndexingSequence


class RepeatabilitySequence:
    """再現性測定: 各ブロックに CW/CCW それぞれ設定回数だけ割り出して読む。

    data: {("cw"|"ccw", ブロック番号): [測定値, …]}
    """

    def __init__(self, points, repeats):
        self.points = [float(p) for p in points]
        self.repeats = int(repeats)
        self.steps = []  # (方向キー, ブロック番号, 回数番号, 指令角度, 方向)
        for i, p in enumerate(self.points):
            for r in range(self.repeats):
                self.steps.append(("cw", i, r, p, +1))
            for r in range(self.repeats):
                self.steps.append(("ccw", i, r, p, -1))
        self.idx = 0
        self.data = {}

    def current(self):
        """次に測るべき (方向キー, 指令角度, 方向)。完了なら None。"""
        if self.idx >= len(self.steps):
            return None
        dirn, _, _, target, sign = self.steps[self.idx]
        return (dirn, target, sign)

    def guide_text(self):
        if self.idx >= len(self.steps):
            return None
        dirn, block, rep, target, _ = self.steps[self.idx]
        label = "CW" if dirn == "cw" else "CCW"
        return (
            f"[ブロック{block + 1}/{len(self.points)} {label} {rep + 1}/{self.repeats}回目]"
            f"  {deg_to_dms(target)} へ割り出して静止"
            f" → ND287のPRINT（または手動取込）   ({self.idx + 1}/{len(self)})"
        )

    def record(self, measured: float):
        dirn, block, _, _, _ = self.steps[self.idx]
        self.data.setdefault((dirn, block), []).append(float(measured))
        self.idx += 1

    def undo(self):
        if self.idx == 0:
            return False
        self.idx -= 1
        dirn, block, _, _, _ = self.steps[self.idx]
        self.data[(dirn, block)].pop()
        if not self.data[(dirn, block)]:
            del self.data[(dirn, block)]
        return True

    def done(self):
        return self.idx >= len(self.steps)

    def __len__(self):
        return len(self.steps)
