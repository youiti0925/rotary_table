# -*- coding: utf-8 -*-
"""型式ごとの測定プログラム一括作成"""
import tempfile
import unittest
from pathlib import Path

from nd287_app import batch_build, fanuc

COND = dict(model="RTH-538", close="", interval_h=80000, interval_w=2400,
            n_h=1, n_w=1, div1=45, div2=10, order=[])
COND_SEMI = dict(COND, close="セミ")
COND_NO_PITCH = dict(model="TN-450", close="", interval_h=0, interval_w=0,
                     n_h=1, n_w=1, div1=0, div2=0, order=[])


class TestProgramForModel(unittest.TestCase):
    def test_pitch_comes_from_conditions(self):
        # 間隔H=80000（0.0001°単位）÷ 1/N_H=1 → 8°刻み
        text = batch_build.program_for_model(COND, fanuc.FanucConfig(axis="Z"))
        self.assertIn("G00Z8.", text)
        self.assertIn("O0100(RTH-538)", text)

    def test_close_goes_into_the_comment(self):
        text = batch_build.program_for_model(COND_SEMI, fanuc.FanucConfig())
        self.assertIn("RTH-538", text)

    def test_generated_program_is_machine_readable(self):
        cfg = fanuc.FanucConfig(axis="Z")
        text = batch_build.program_for_model(COND, cfg)
        self.assertEqual(fanuc.validate(text, cfg), [])
        self.assertNotIn(";", text)

    def test_missing_pitch_raises(self):
        with self.assertRaises(ValueError):
            batch_build.program_for_model(COND_NO_PITCH, fanuc.FanucConfig())


class TestSafeName(unittest.TestCase):
    def test_replaces_path_characters(self):
        self.assertEqual(batch_build.safe_name("AB/CD"), "AB_CD")
        self.assertEqual(batch_build.safe_name(r"A\B"), "A_B")

    def test_empty_falls_back(self):
        self.assertEqual(batch_build.safe_name(""), "MODEL")
        self.assertEqual(batch_build.safe_name("..."), "MODEL")


class TestBatch(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.out = Path(self.d.name)
        self.cfg = fanuc.FanucConfig(axis="Z")

    def tearDown(self):
        self.d.cleanup()

    def test_folder_layout(self):
        rep = batch_build.batch_programs([COND], self.cfg, self.out, layout="folder")
        self.assertEqual(rep[0][2], "作成")
        self.assertTrue((self.out / "RTH-538" / "RTH-538.NC").exists())

    def test_flat_layout_for_memory_card(self):
        rep = batch_build.batch_programs([COND], self.cfg, self.out, layout="flat")
        self.assertEqual(rep[0][2], "作成")
        self.assertTrue((self.out / "RTH-538.NC").exists())

    def test_same_model_different_close_do_not_collide(self):
        rep = batch_build.batch_programs([COND, COND_SEMI], self.cfg, self.out,
                                         layout="flat")
        self.assertEqual([r[2] for r in rep], ["作成", "作成"])
        names = sorted(p.name for p in self.out.iterdir())
        self.assertEqual(names, ["RTH-538.NC", "RTH-538_セミ.NC"])

    def test_existing_file_is_not_overwritten(self):
        batch_build.batch_programs([COND], self.cfg, self.out, layout="flat")
        before = (self.out / "RTH-538.NC").read_bytes()
        (self.out / "RTH-538.NC").write_bytes(b"KEEP")
        rep = batch_build.batch_programs([COND], self.cfg, self.out, layout="flat")
        self.assertEqual(rep[0][2], "既存のため飛ばした")
        self.assertEqual((self.out / "RTH-538.NC").read_bytes(), b"KEEP")
        self.assertTrue(before)

    def test_overwrite_option(self):
        batch_build.batch_programs([COND], self.cfg, self.out, layout="flat")
        (self.out / "RTH-538.NC").write_bytes(b"KEEP")
        rep = batch_build.batch_programs([COND], self.cfg, self.out, layout="flat",
                                         overwrite=True)
        self.assertEqual(rep[0][2], "作成")
        self.assertNotEqual((self.out / "RTH-538.NC").read_bytes(), b"KEEP")

    def test_unbuildable_model_is_reported_not_skipped(self):
        rep = batch_build.batch_programs([COND, COND_NO_PITCH], self.cfg, self.out)
        states = {r[0]: r[2] for r in rep}
        self.assertEqual(states["RTH-538"], "作成")
        self.assertEqual(states["TN-450"], "作れない")

    def test_written_bytes_are_machine_format(self):
        batch_build.batch_programs([COND], self.cfg, self.out, layout="flat")
        data = (self.out / "RTH-538.NC").read_bytes()
        self.assertIn(b"\n\r\r", data)
        self.assertNotIn(b";", data)

    def test_missing_out_dir_raises(self):
        with self.assertRaises(NotADirectoryError):
            batch_build.batch_programs([COND], self.cfg, self.out / "nope")

    def test_summarize(self):
        rep = batch_build.batch_programs([COND, COND_NO_PITCH], self.cfg, self.out)
        text = batch_build.summarize(rep)
        self.assertIn("作成 1件", text)
        self.assertIn("作れない 1件", text)


if __name__ == "__main__":
    unittest.main()
