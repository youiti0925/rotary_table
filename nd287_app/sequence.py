# -*- coding: utf-8 -*-
"""測定シーケンス管理

割出測定は時間ポーリングではなく「割り出して静止 → 1点取込」のトリガ方式。
順序:
  1. ホイール CW  … 0°→360° を wheel_pitch 刻みで一周
  2. ホイール CCW … 同じポイントを逆順に一周
  3. ウォーム CW  … worm_start からウォーム1回転ぶん(worm_range)を worm_pitch 刻み
  4. ウォーム CCW … 同じポイントを逆順
"""

import numpy as np

SERIES_KEYS = ("wheel_cw", "wheel_ccw", "worm_cw", "worm_ccw")

SERIES_LABELS = {
    "wheel_cw": "ホイール CW",
    "wheel_ccw": "ホイール CCW",
    "worm_cw": "ウォーム CW",
    "worm_ccw": "ウォーム CCW",
}


class Sequence:
    def __init__(self, wheel_pitch, worm_pitch, worm_range, worm_start=0.0):
        wheel_pts = np.arange(0.0, 360.0, wheel_pitch)
        worm_pts = worm_start + np.arange(0.0, worm_range + 1e-9, worm_pitch)
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
