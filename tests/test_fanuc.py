# -*- coding: utf-8 -*-
import re
import unittest

from nd287_app.fanuc import (
    DEFAULT_EOB,
    EOB_CRLF,
    FanucConfig,
    expected_signal_count,
    fmt_num,
    generate,
    nc_bytes,
    repeat_body,
    sanitize_comment,
    validate,
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
            call = re.match(r"M98\s*P(\d+)\s*L(\d+)", l)
            if call:
                num, reps = int(call.group(1)), int(call.group(2))
                total += count_signals(subs.get(num, [])) * reps
            elif l == cfg.mcode:
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

    def test_swing_and_measure_dwell_separate(self):
        # 振り後＝振りドゥエル(小)、測定点＝測定ドゥエル の2系統が別々に効く
        cfg = FanucConfig(axis="X", preswing=10.0, swing_dwell_sec=1.0, dwell_sec=3.0)
        self.assertEqual(repeat_body(cfg), [
            "G91 G00 X-10.", "G04 X1.", "X10.", "G04 X3.", "M80",
            "G91 G00 X10.", "G04 X1.", "X-10.", "G04 X3.", "M80",
        ])


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
        self.assertIn("M98P1000L7", text)
        self.assertIn("O1000", text)
        # 4ブロック×7回×2(CW/CCW) = 56
        self.assertEqual(expand_runtime_signals(text, cfg), 56)

    def test_repeat_single_program_inlined(self):
        cfg = FanucConfig(use_subprogram=False)
        blocks = rotary_blocks(4)
        text = generate(cfg, rotary=True, blocks=blocks, repeats=7,
                        include_division=False, include_repeat=True)
        self.assertNotIn("M98", text)
        self.assertNotIn("O1000", text)
        self.assertEqual(expand_runtime_signals(text, cfg), 56)

    def test_program_structure(self):
        cfg = FanucConfig(main_number=100)
        text = generate(cfg, rotary=True, blocks=rotary_blocks(4), repeats=3)
        self.assertTrue(text.startswith("%"))
        self.assertTrue(text.rstrip().endswith("%"))
        self.assertIn("O0100", text)
        self.assertIn("M30", text)
        self.assertIn("G91", text)

    def test_dwell_has_decimal(self):
        # G04 X1（小数点なし）でなく G04 X1.（秒指定）であること
        cfg = FanucConfig(dwell_sec=1.0)
        text = generate(cfg, rotary=True, blocks=[], repeats=0, include_repeat=False)
        self.assertIn("G04X1.", text)
        self.assertFalse(re.search(r"G04X1(?!\.)", text))


class TestClampDivision(unittest.TestCase):
    """クランプ分割: 各測定点で クランプ→ドゥエル→完了信号→アンクランプ→ドゥエル"""

    def test_repeat_body_clamps_each_read(self):
        cfg = FanucConfig(axis="X", preswing=10.0, swing_dwell_sec=1.0, dwell_sec=3.0,
                          mcode="M80", clamp_enabled=True,
                          clamp_mcode="M10", unclamp_mcode="M11",
                          clamp_dwell_sec=2.0, unclamp_dwell_sec=0.5)
        # 振り後の振りドゥエル(1.)は残り、測定点はクランプ列に置き換わる（測定ドゥエル3.は出ない）
        self.assertEqual(repeat_body(cfg), [
            "G91 G00 X-10.", "G04 X1.", "X10.",
            "M10", "G04 X2.", "M80", "M11", "G04 X0.5",
            "G91 G00 X10.", "G04 X1.", "X-10.",
            "M10", "G04 X2.", "M80", "M11", "G04 X0.5",
        ])

    def test_clamp_order_in_division(self):
        # 1測定点の中で クランプ→完了信号→アンクランプ の順であること
        cfg = FanucConfig(clamp_enabled=True, clamp_mcode="M10",
                          unclamp_mcode="M11", mcode="M80")
        text = generate(cfg, rotary=True, wheel_pitch=90, wheel_start=0, wheel_end=360,
                        worm_pitch=1.0, worm_range=2.0, include_repeat=False)
        seq = [l.strip() for l in text.splitlines()]
        i_clamp = seq.index("M10")
        self.assertTrue(seq[i_clamp + 1].startswith("G04"))        # クランプ後ドゥエル
        self.assertEqual(seq[i_clamp + 2], "M80")             # 完了信号
        self.assertEqual(seq[i_clamp + 3], "M11")             # アンクランプ
        self.assertTrue(seq[i_clamp + 4].startswith("G04"))        # アンクランプ後ドゥエル

    def test_clamp_does_not_change_signal_count(self):
        # クランプを入れても完了信号(取込点数)は不変
        blocks = rotary_blocks(4)
        kwargs = dict(rotary=True, wheel_pitch=90, wheel_start=0, wheel_end=360,
                      worm_pitch=1.0, worm_range=2.0, blocks=blocks, repeats=3)
        off = FanucConfig(clamp_enabled=False)
        on = FanucConfig(clamp_enabled=True)
        n_off = expand_runtime_signals(generate(off, **kwargs), off)
        n_on = expand_runtime_signals(generate(on, **kwargs), on)
        self.assertEqual(n_off, n_on)

    def test_clamp_mcodes_configurable(self):
        cfg = FanucConfig(clamp_enabled=True, clamp_mcode="M21", unclamp_mcode="M22",
                          clamp_dwell_sec=1.5, unclamp_dwell_sec=0.3)
        text = generate(cfg, rotary=True, wheel_pitch=90, wheel_start=0, wheel_end=360,
                        worm_pitch=1.0, worm_range=2.0, include_repeat=False)
        self.assertIn("M21", text)
        self.assertIn("M22", text)
        self.assertIn("G04X1.5", text)
        self.assertIn("G04X0.3", text)

    def test_clamp_off_is_unchanged(self):
        # クランプOFFは従来どおり（測定ドゥエル→完了信号）
        cfg = FanucConfig(clamp_enabled=False, dwell_sec=2.0, mcode="M80")
        text = generate(cfg, rotary=True, wheel_pitch=90, wheel_start=0, wheel_end=360,
                        worm_pitch=1.0, worm_range=2.0, include_repeat=False)
        self.assertNotIn("M10", text)
        self.assertNotIn("M11", text)


class TestCounterReset(unittest.TestCase):
    def test_reset_block_at_program_top(self):
        # 分割プログラムの先頭にカウンターリセット（+p,-p,-p,+p,M00）が入る
        cfg = FanucConfig(counter_reset=True, preswing=10.0, reset_swing=10.0)
        text = generate(cfg, rotary=True, wheel_pitch=90, wheel_start=0, wheel_end=360,
                        worm_pitch=1.0, worm_range=2.0, include_repeat=False)
        lines = [l.strip() for l in text.splitlines()]
        i = lines.index("G91G00Z10.")
        self.assertEqual(lines[i:i + 5],
                         ["G91G00Z10.", "Z-10.", "Z-10.", "Z10.", "M00"])
        # M00 は測定点（M80）に数えない＝信号数は不変
        self.assertEqual(expand_runtime_signals(text, cfg), 2 * 5 + 2 * 3)

    def test_reset_swing_independent_from_preswing(self):
        # リセット振り量(15°)と測定の前振り量(10°)を別々に設定できる
        cfg = FanucConfig(counter_reset=True, preswing=10.0, reset_swing=15.0)
        text = generate(cfg, rotary=True, wheel_pitch=90, wheel_start=0, wheel_end=360,
                        worm_pitch=1.0, worm_range=2.0, include_repeat=False)
        lines = [l.strip() for l in text.splitlines()]
        i = lines.index("G91G00Z15.")  # リセットは15°
        self.assertEqual(lines[i:i + 5],
                         ["G91G00Z15.", "Z-15.", "Z-15.", "Z15.", "M00"])
        # 測定の前振りは10°のまま（ホイールCW先頭）
        self.assertIn("G00Z-10.", lines)
        self.assertNotIn("G00Z-15.", lines[i + 5:])  # 以降に15°前振りは出ない

    def test_reset_disabled(self):
        cfg = FanucConfig(counter_reset=False)
        text = generate(cfg, rotary=True, wheel_pitch=90, wheel_start=0, wheel_end=360,
                        worm_pitch=1.0, worm_range=2.0, include_repeat=False)
        self.assertNotIn("M00", text)

    def test_repeat_only_has_no_reset(self):
        # 再現のみ（分割なし）はリセットを入れない
        cfg = FanucConfig(counter_reset=True)
        text = generate(cfg, rotary=True, blocks=[0, 90, 180, 270], repeats=3,
                        include_division=False, include_repeat=True)
        self.assertNotIn("M00", text)


class TestTiltPositioning(unittest.TestCase):
    """傾斜合体: リセット@0 → 測定開始角へ移動 → … → 0へ戻る、の流れ"""

    def test_tilt_goto_sequence(self):
        cfg = FanucConfig(counter_reset=True, use_subprogram=True)
        blocks = tilt_blocks(-30, 120, 3)  # [-30, 45, 120]
        text = generate(cfg, rotary=False, wheel_pitch=30,
                        wheel_start=-30, wheel_end=120,
                        worm_pitch=1.0, worm_range=2.0, worm_start=0.0,
                        blocks=blocks, repeats=2)
        lines = [l.strip() for l in text.splitlines()]
        # リセット(0) → Z-30(測定開始へ) → … → 途中で Z30(0へ=ウォーム) → Z-30(再現へ)
        i = lines.index("G91G00Z10.")
        self.assertEqual(lines[i:i + 5],
                         ["G91G00Z10.", "Z-10.", "Z-10.", "Z10.", "M00"])
        # ウォーム前に 0° へ戻す G00Z30. がある（CCWが-30で終わるため）
        self.assertIn("G00Z30.", lines)
        # 再現開始で -30° へ G00Z-30.
        self.assertIn("G00Z-30.", lines)
        # 最後に 0° へ戻す（120から -120）
        self.assertIn("G00Z-120.", lines)
        # 信号数 = ホイール6×2 + ウォーム3×2 + 再現3ブロック×2回×2 = 30
        self.assertEqual(expand_runtime_signals(text, cfg), 6 * 2 + 3 * 2 + 3 * 2 * 2)


class TestMachineReadableFormat(unittest.TestCase):
    """制御装置が読み込める形になっているか（実機で読めなかった件の再発防止）

    実機が自分で出力したプログラム（O1000）と同じ形にそろえる:
    ・";" は1つも書かない（画面の ";" は EOB＝改行の表示で、文字としては不正）
    ・ISOコードに無い文字（小文字・日本語・"?"）を書かない
    ・コメントのカッコを入れ子にしない（最初の ")" でコメントが終わるため）
    ・ブロックの区切りは実機と同じ LF CR CR
    """

    def _text(self):
        return generate(FanucConfig(), rotary=True, title="261942 傾斜分割+再現",
                        wheel_pitch=90, wheel_start=0, wheel_end=360,
                        worm_pitch=1.0, worm_range=2.0,
                        blocks=rotary_blocks(4), repeats=3)

    def test_no_semicolon_anywhere(self):
        self.assertNotIn(";", self._text())

    def test_only_iso_characters(self):
        legal = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,-+*/=%()\n")
        bad = sorted({c for c in self._text() if c not in legal})
        self.assertEqual(bad, [], f"ISOコードに無い文字が出た: {bad}")

    def test_japanese_title_does_not_leak(self):
        # 日本語タイトルは "?" にせず取り除く（"?" もISOコードに無い）
        text = self._text()
        self.assertNotIn("?", text)
        self.assertIn("O0100(261942", text)

    def test_comment_parens_not_nested(self):
        for line in self._text().splitlines():
            _, sep, rest = line.partition("(")
            if sep:
                self.assertNotIn("(", rest.rstrip(")"), f"入れ子コメント: {line}")

    def test_generated_program_passes_validate(self):
        self.assertEqual(validate(self._text(), FanucConfig()), [])

    def test_default_axis_matches_machine(self):
        # 現在の実機の割出軸は Z。X は G04 X…（ドゥエル）と同じ文字で紛らわしい
        self.assertEqual(FanucConfig().axis, "Z")

    def test_default_sub_number_avoids_protected_range(self):
        # O8000〜O9999 は保護領域（3202 NE8/NE9）で転送が弾かれることがある
        self.assertLess(FanucConfig().rep_sub_number, 8000)

    def test_nc_bytes_uses_machine_eob(self):
        data = nc_bytes("%\nO0100\nM30\n%")
        self.assertEqual(data, b"%\n\r\rO0100\n\r\rM30\n\r\r%\n\r\r")
        self.assertEqual(DEFAULT_EOB, "\n\r\r")

    def test_nc_bytes_eob_selectable(self):
        self.assertEqual(nc_bytes("%\nM30\n%", EOB_CRLF), b"%\r\nM30\r\n%\r\n")

    def test_nc_bytes_drops_hand_typed_semicolon(self):
        # 手編集で ";" を書いてしまっても、EOBとして扱って落とす
        self.assertEqual(nc_bytes("G00A10. ;\nM30 ;", EOB_CRLF), b"G00A10.\r\nM30\r\n")

    def test_nc_bytes_is_pure_ascii(self):
        data = nc_bytes("(日本語コメント)\nM30")
        self.assertTrue(all(b < 128 for b in data))
        self.assertIn(b"M30", data)


class TestValidate(unittest.TestCase):
    def _has(self, problems, word):
        return any(word in p for p in problems)

    def test_flags_semicolon(self):
        self.assertTrue(self._has(validate("%\nO1\nM30 ;\n%"), '";"'))

    def test_flags_lowercase_and_question_mark(self):
        self.assertTrue(self._has(validate("%\nO1\n(set 0)\nM30\n%"), "使えない文字"))
        self.assertTrue(self._has(validate("%\nO1\n(????)\nM30\n%"), "使えない文字"))

    def test_flags_nested_comment(self):
        self.assertTrue(self._has(validate("%\nO1\n(A (B) C)\nM30\n%"), "入れ子"))

    def test_flags_protected_o_number(self):
        self.assertTrue(self._has(validate("%\nO9001\nM30\n%"), "保護プログラム"))

    def test_flags_axis_x(self):
        # X も正規の軸名なので警告しない（うちの軸は X/Y/Z/A/B/C で、
        # 現場の実物サブプロも割出軸 X と G04 X… を同じプログラムで使っている）
        self.assertFalse(self._has(validate("%\nO1\nM30\n%", FanucConfig(axis="X")),
                                   "割出軸が X"))

    def test_flags_missing_percent(self):
        self.assertTrue(self._has(validate("O1\nM30"), "%"))

    def test_clean_program_has_no_problems(self):
        self.assertEqual(validate("%\nO0100(TEST)\nG91G00Z10.\nM30\n%",
                                  FanucConfig()), [])


class TestSanitizeComment(unittest.TestCase):
    def test_uppercases(self):
        self.assertEqual(sanitize_comment("set 0 here"), "SET 0 HERE")

    def test_drops_japanese(self):
        self.assertEqual(sanitize_comment("261942 傾斜分割"), "261942")

    def test_flattens_nested_parens(self):
        self.assertEqual(sanitize_comment("A (B) C"), "A B C")

    def test_truncates(self):
        self.assertLessEqual(len(sanitize_comment("A" * 200)), 40)

    def test_empty(self):
        self.assertEqual(sanitize_comment(None), "")
        self.assertEqual(sanitize_comment("あ"), "")


class TestRepeatBlockStartEnd(unittest.TestCase):
    def test_start_end_count(self):
        # 開始0・終了270・4箇所 → 0,90,180,270（両端含む等間隔）
        self.assertEqual(tilt_blocks(0, 270, 4), [0.0, 90.0, 180.0, 270.0])
        self.assertEqual(tilt_blocks(-30, 120, 3), [-30.0, 45.0, 120.0])


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


class TestMidFilePercentInProgram(unittest.TestCase):
    """途中の % は読取終わり。そこから先が読み込まれない。

    パラメータ側の実データ（F35BASIC）で実際に起きていたので、
    プログラム側でも同じ検出をする。手編集で % を足すと起こり得る。
    """

    def test_detected(self):
        probs = validate("%\nO0100\nG91G00Z10.\n%\nM30\n%")
        self.assertTrue(any("途中に %" in p for p in probs), probs)

    def test_normal_program_is_clean(self):
        self.assertEqual(validate("%\nO0100\nG91G00Z10.\nM30\n%"), [])

    def test_generated_program_has_none(self):
        cfg = FanucConfig()
        text = generate(cfg, rotary=True, wheel_pitch=90, wheel_start=0,
                        wheel_end=360, worm_pitch=1.0, worm_range=2.0,
                        include_repeat=False)
        self.assertFalse(any("途中に %" in p for p in validate(text, cfg)))
