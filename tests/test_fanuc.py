# -*- coding: utf-8 -*-
import re
import unittest

from nd287_app.fanuc import (
    FanucConfig,
    expected_signal_count,
    fmt_num,
    generate,
    repeat_body,
)
from nd287_app.sequence import (
    CombinedSequence,
    IndexingSequence,
    RepeatabilitySequence,
    rotary_blocks,
    tilt_blocks,
)


def expand_runtime_signals(text, cfg):
    """生成プログラムを擬似実行し、ランタイムで発火する完了信号の数を数える。

    M98 P… L… はサブプログラム本体 × L に展開して数える。
    """
    lines = [l.strip() for l in text.splitlines()]
    # サブプログラム本体（O{rep_sub} … M99）を抽出
    subs = {}
    cur_sub = None
    main = []
    for l in lines:
        m = re.match(r"O(\d+)", l)
        if m:
            num = int(m.group(1))
            if num == cfg.main_number:
                cur_sub = None
            else:
                cur_sub = num
                subs[num] = []
            continue
        if l in ("%", ""):
            continue
        if cur_sub is None:
            main.append(l)
        else:
            subs[cur_sub].append(l)

    def count_signals(body):
        total = 0
        for l in body:
            call = re.match(rf"M98 P(\d+) L(\d+)", l)
            if call:
                num, reps = int(call.group(1)), int(call.group(2))
                total += count_signals(subs.get(num, [])) * reps
            elif l.startswith(cfg.mcode + " ") or l == cfg.mcode + " ;" or l == cfg.mcode:
                total += 1
        return total

    return count_signals(main)


class TestFormatting(unittest.TestCase):
    def test_fmt_num(self):
        self.assertEqual(fmt_num(10), "10.")
        self.assertEqual(fmt_num(-10.0), "-10.")
        self.assertEqual(fmt_num(0.5), "0.5")
        self.assertEqual(fmt_num(0.3), "0.3")
        self.assertEqual(fmt_num(1.0), "1.")


class TestRepeatBody(unittest.TestCase):
    def test_matches_real_subprogram(self):
        # 現場の実物サブプロと同じ形（±10前振り、ドゥエル、M80×2、正味0）
        cfg = FanucConfig(axis="X", preswing=10.0, dwell_sec=1.0, mcode="M80")
        body = repeat_body(cfg)
        self.assertEqual(body, [
            "G91 G00 X-10.", "G04 X1.", "X10.", "G04 X1.", "M80",
            "G91 G00 X10.", "G04 X1.", "X-10.", "G04 X1.", "M80",
        ])

    def test_config_changes_values(self):
        cfg = FanucConfig(axis="A", preswing=5.0, dwell_sec=0.5, mcode="M52")
        body = repeat_body(cfg)
        self.assertIn("G91 G00 A-5.", body)
        self.assertIn("G04 X0.5", body)
        self.assertEqual(body.count("M52"), 2)


class TestGenerate(unittest.TestCase):
    def test_division_only_signal_count(self):
        cfg = FanucConfig()
        text = generate(cfg, rotary=True, wheel_pitch=10, wheel_start=0, wheel_end=360,
                        worm_pitch=0.5, worm_range=5, worm_start=0,
                        include_division=True, include_repeat=False)
        # ホイール37×2 + ウォーム11×2 = 96
        expected = expected_signal_count(include_division=True, n_wheel=37, n_worm=11,
                                         include_repeat=False, n_blocks=0, repeats=0)
        self.assertEqual(expected, 96)
        self.assertEqual(expand_runtime_signals(text, cfg), 96)

    def test_repeat_subprogram_count(self):
        cfg = FanucConfig(use_subprogram=True)
        blocks = rotary_blocks(4)
        text = generate(cfg, rotary=True, blocks=blocks, repeats=7,
                        include_division=False, include_repeat=True)
        self.assertIn("M98 P9001 L7", text)
        self.assertIn("O9001", text)
        # 4ブロック×7回×2(CW/CCW) = 56
        self.assertEqual(expand_runtime_signals(text, cfg), 56)

    def test_repeat_single_program_inlined(self):
        cfg = FanucConfig(use_subprogram=False)
        blocks = rotary_blocks(4)
        text = generate(cfg, rotary=True, blocks=blocks, repeats=7,
                        include_division=False, include_repeat=True)
        self.assertNotIn("M98", text)
        self.assertNotIn("O9001", text)
        self.assertEqual(expand_runtime_signals(text, cfg), 56)

    def test_program_structure(self):
        cfg = FanucConfig(main_number=100)
        text = generate(cfg, rotary=True, blocks=rotary_blocks(4), repeats=3)
        self.assertTrue(text.startswith("%"))
        self.assertTrue(text.rstrip().endswith("%"))
        self.assertIn("O0100", text)
        self.assertIn("M30 ;", text)
        self.assertIn("G91", text)

    def test_dwell_has_decimal(self):
        # G04 X1（小数点なし）でなく G04 X1.（秒指定）であること
        cfg = FanucConfig(dwell_sec=1.0)
        text = generate(cfg, rotary=True, blocks=[], repeats=0, include_repeat=False)
        self.assertIn("G04 X1.", text)
        self.assertNotIn("G04 X1 ", text)


class TestConsistencyWithSequence(unittest.TestCase):
    """生成プログラムの信号数 == アプリの合体シーケンスのステップ数（最重要）"""

    def _combined(self, wheel_pitch, worm_pitch, worm_range, blocks, repeats,
                  wheel_start=0.0, wheel_end=360.0):
        division = IndexingSequence(wheel_pitch, worm_pitch, worm_range,
                                    wheel_start=wheel_start, wheel_end=wheel_end)
        repeat = RepeatabilitySequence(blocks, repeats, interleave=True)
        return CombinedSequence(division, repeat)

    def test_rotary_combined_matches(self):
        cfg = FanucConfig(use_subprogram=True)
        blocks = rotary_blocks(4)
        combined = self._combined(10, 0.5, 5, blocks, 7)
        text = generate(cfg, rotary=True, wheel_pitch=10, wheel_start=0, wheel_end=360,
                        worm_pitch=0.5, worm_range=5, blocks=blocks, repeats=7)
        self.assertEqual(expand_runtime_signals(text, cfg), len(combined))

    def test_tilt_combined_matches(self):
        cfg = FanucConfig(use_subprogram=False)
        blocks = tilt_blocks(-30, 110, 4)
        combined = self._combined(10, 0.5, 5, blocks, 5,
                                  wheel_start=-30, wheel_end=110)
        text = generate(cfg, rotary=False, wheel_pitch=10,
                        wheel_start=-30, wheel_end=110,
                        worm_pitch=0.5, worm_range=5, blocks=blocks, repeats=5)
        self.assertEqual(expand_runtime_signals(text, cfg), len(combined))


class TestCombinedSequence(unittest.TestCase):
    def test_routing_and_data(self):
        division = IndexingSequence(90, 1.0, 2.0)  # ホイール5×2 + ウォーム3×2 = 16
        repeat = RepeatabilitySequence(rotary_blocks(4), 3, interleave=True)  # 4×3×2=24
        combined = CombinedSequence(division, repeat)
        self.assertEqual(len(combined), 16 + 24)
        # 全ステップを指令値で記録
        order = []
        while not combined.done():
            cur = combined.current()
            order.append(cur)
            combined.record(cur[1])
        self.assertTrue(combined.done())
        # 分割データは IndexingSequence、再現データは RepeatabilitySequence に入る
        self.assertEqual(len(combined.data["wheel_cw"][0]), 5)
        self.assertEqual(len(combined.rep_data[("cw", 0)]), 3)
        # 前半16が分割、後半が再現
        self.assertEqual(combined.division.idx, 16)
        self.assertEqual(combined.repeat.idx, 24)

    def test_interleave_order(self):
        repeat = RepeatabilitySequence([0.0], 2, interleave=True)
        dirs = [s[0] for s in repeat.steps]
        self.assertEqual(dirs, ["cw", "ccw", "cw", "ccw"])
        repeat2 = RepeatabilitySequence([0.0], 2, interleave=False)
        dirs2 = [s[0] for s in repeat2.steps]
        self.assertEqual(dirs2, ["cw", "cw", "ccw", "ccw"])

    def test_undo_crosses_boundary(self):
        division = IndexingSequence(180, 1.0, 1.0)  # ホイール3×2+ウォーム2×2=10
        repeat = RepeatabilitySequence([0.0], 1, interleave=True)  # 2
        combined = CombinedSequence(division, repeat)
        while not combined.division.done():
            combined.record(0.0)
        combined.record(0.0)  # 再現1点目
        self.assertEqual(combined.repeat.idx, 1)
        self.assertTrue(combined.undo())  # 再現を戻す
        self.assertEqual(combined.repeat.idx, 0)
        self.assertTrue(combined.undo())  # 分割の最後を戻す
        self.assertEqual(combined.division.idx, len(division) - 1)


if __name__ == "__main__":
    unittest.main()
