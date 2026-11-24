# -*- coding: utf-8 -*-
"""フルクロ→セミクロ変換（semi_convert）。値は写真 RTV-231,WF（受注伝票 50014861）の読み取り。"""
import os
import tempfile
import unittest
from pathlib import Path

from nd287_app import semi_convert as S, param_build, fanuc_param

# 写真のフル側（読み取り）。1260/1820/1013 は両方の表に同じ値で載っていた
FULL = {"1013": "'00000000", "1260": "'360.000", "1820": "2", "1815": "'00100010",
        "1821": "45000", "2000": "'***1***0", "2018": "'*******1", "2023": "8192",
        "2024": "69", "2084": "9", "2085": "25", "2209": "'*1******", "2020": "279"}
# 写真のセミ側（読み取り）
SEMI = {"1815": "00100000", "1821": "2000", "2000": "***0***0", "2024": "12500",
        "2084": "1", "2085": "500", "2209": "*0******"}


def norm(v):
    return str(v).lstrip("'")


class TestRule(unittest.TestCase):
    def test_photo_full_to_semi_matches_photo(self):
        r = S.convert(FULL, gear_header="'1/180")
        self.assertEqual(r["stop"], [])
        self.assertEqual(r["mode"], "フル")
        for n, v in SEMI.items():
            self.assertEqual(norm(r["values"][n]), v, n)
        self.assertNotIn("2018", r["values"])                        # 書かない
        self.assertEqual([s["num"] for s in r["skipped"]], [2018])
        self.assertEqual(sorted(c["num"] for c in r["changes"]),
                         [1815, 1821, 2000, 2024, 2084, 2085, 2209])
        self.assertEqual(r["values"]["2020"], "279")                  # 他は変えない
        self.assertIn("2000", r["basis"])
        self.assertIn("360000", r["basis"])

    def test_input_not_modified(self):
        before = dict(FULL)
        S.convert(FULL, gear_header="1/180")
        self.assertEqual(FULL, before)

    def test_2000_bit4_flagged_for_review(self):
        r = S.convert(FULL, gear_header="1/180")
        c = next(c for c in r["changes"] if c["num"] == 2000)
        self.assertTrue(c["review"])
        self.assertIn("要確認", c["note"])

    def test_bits_only_changed_bits(self):
        self.assertEqual(S.set_bit_masked("'***1***0", 4, False), "'***0***0")
        self.assertEqual(S.set_bit_masked("00100010", 1, False), "00100000")
        self.assertEqual(S.set_bit_masked("*1******", 6, False), "*0******")
        self.assertEqual(S.set_bit_masked("1", 0, False), None)       # 10進は触らない
        self.assertEqual(S.set_bit_masked("1*1", 0, False), "*****1*0")

    def test_2000_bit0_and_2023(self):
        v = dict(FULL, **{"2000": "'***1***1", "2023": "819"})
        r = S.convert(v, gear_header="1/180")
        self.assertEqual(norm(r["values"]["2000"]), "***0***0")
        self.assertEqual(r["values"]["2023"], "8192")

    def test_no_change_when_already_semi_values(self):
        v = dict(FULL, **{"2024": "12500", "2023": "8192"})
        r = S.convert(v, gear_header="1/180")
        self.assertNotIn(2024, [c["num"] for c in r["changes"]])

    def test_already_semi_stops(self):
        r = S.convert(dict(FULL, **{"1815": "'00100000"}), gear_header="1/180")
        self.assertEqual(r["mode"], "セミ")
        self.assertTrue(r["stop"])
        self.assertEqual(r["values"]["1821"], "45000")

    def test_1815_unreadable_stops(self):
        v = dict(FULL)
        del v["1815"]
        r = S.convert(v, gear_header="1/180")
        self.assertEqual(r["mode"], "")
        self.assertTrue(r["stop"])

    def test_mpi_numbers_dropped_and_listed(self):
        v = dict(FULL, **{"1874": "5", "1875": "6", "1876": "7", "2015": "'00000001", "2141": "3"})
        r = S.convert(v, gear_header="1/180")
        for n in ("1874", "1875", "1876", "2015", "2141", "2018"):
            self.assertNotIn(n, r["values"])
        self.assertEqual(sorted(s["num"] for s in r["skipped"]), [1874, 1875, 1876, 2015, 2018, 2141])

    def test_indivisible_ratio_not_converted(self):
        r = S.convert(dict(FULL, **{"2024": "12"}), gear_header="1/990")
        self.assertTrue(r["stop"])
        self.assertIn("割り切れ", r["stop"][0])
        self.assertEqual(r["values"]["1821"], "45000")                 # 元のまま

    def test_dd_not_converted(self):
        r = S.convert(FULL, gear_header="1/1")
        self.assertTrue(r["stop"])

    def test_unit_is_1013(self):
        # 設定単位 0.0001 度（#1）なら 10 倍
        r = S.convert(dict(FULL, **{"1013": "'00000010"}), gear_header="1/180")
        self.assertEqual(r["stop"], [])
        self.assertEqual(r["values"]["1821"], "20000")
        r = S.convert(dict(FULL, **{"1013": "'00000011"}), gear_header="1/180")
        self.assertTrue(r["stop"])
        v = {k: x for k, x in FULL.items() if k != "1013"}
        self.assertTrue(S.convert(v, gear_header="1/180")["stop"])

    def test_cmr_other_than_2_not_converted(self):
        for cmr in ("4", "1", "8"):   # 2 と 20 は実データで合った（許す）
            r = S.convert(dict(FULL, **{"1820": cmr}), gear_header="1/180")
            self.assertTrue(r["stop"], cmr)
            self.assertIn(f"CMR が 2・20 以外のため確かめていません（値: {cmr}）", r["stop"])
            self.assertEqual(r["values"]["1821"], "45000")

    def test_allowed_cmr_is_one_place(self):
        old = S.ALLOWED_CMR
        try:
            S.ALLOWED_CMR = (2, 4)
            r = S.convert(dict(FULL, **{"1820": "4"}), gear_header="1/180")
            self.assertEqual(r["stop"], [])
            self.assertEqual(r["values"]["1821"], "4000")             # 倍率2 → 1パルス 0.0005 度
        finally:
            S.ALLOWED_CMR = old

    def test_1260_not_360_not_converted(self):
        self.assertTrue(S.convert(dict(FULL, **{"1260": "'180.000"}), gear_header="1/180")["stop"])

    def test_gear_unknown_back_calc(self):
        r = S.convert(FULL)                                            # ヘッダ・DB なし → 69 → 1/180
        self.assertEqual(r["stop"], [])
        self.assertEqual(r["gear"], "1/180")
        self.assertIn("逆算", r["basis"])
        self.assertEqual(r["values"]["1821"], "2000")

    def test_back_calc_must_be_unique(self):
        self.assertEqual(S.back_calc_gear("69", S.Fraction(1, 1000)), S.Fraction(1, 180))
        self.assertIsNone(S.back_calc_gear("70", S.Fraction(1, 1000)))     # 177〜178 は割り切れない
        self.assertTrue(S.convert(dict(FULL, **{"2024": "70"}))["stop"])

    def test_gear_sources(self):
        r = S.convert(FULL, gear_db="1/180")
        self.assertEqual(r["stop"], [])
        self.assertIn("登録", r["basis"])
        r = S.convert(FULL, gear_header="1/180", gear_db="1/90")
        self.assertTrue(r["stop"])
        self.assertIn("食い違", r["stop"][0])

    def test_gear_disagrees_with_2024(self):
        r = S.convert(dict(FULL, **{"2024": "100"}), gear_header="1/180")
        self.assertTrue(r["stop"])

    def test_lookup_fills_missing_values(self):
        v = {"1815": "00100010", "2024": "69", "2084": "9", "2085": "25"}
        base = {"1013": "00000000", "1260": "360.0", "1820": "2", "1821": "45000",
                "2000": "00010000", "2023": "8192"}
        r = S.convert(v, gear_header="1/180", lookup=lambda n: base.get(str(n)))
        self.assertEqual(r["stop"], [])
        self.assertEqual(r["values"]["1821"], "2000")
        self.assertEqual(r["values"]["2000"], "00000000")

    def test_tuple_key_values_untouched(self):
        v = dict(FULL)
        v[("2024", "L2")] = "7"
        r = S.convert(v, gear_header="1/180")
        self.assertEqual(r["values"][("2024", "L2")], "7")


class TestThroughPrepare(unittest.TestCase):
    BASIC = ("%\n"
             "N01013Q1A1P00000000\n"
             "N01260Q1A1P360.0\nN01815Q1A1P00100010\nN01820Q1A1P2\nN01821Q1A1P45000\n"
             "N02000Q1A1P00010000\nN02018Q1A1P00000001\nN02023Q1A1P8192\nN02024Q1A1P69\n"
             "N02084Q1A1P9\nN02085Q1A1P25\nN02209Q1A1P01000000\n%\n")

    def test_converted_values_reach_output(self):
        raw = self.BASIC
        vals = {k: v for k, v in FULL.items() if k not in ("2020",)}
        r = S.convert(vals, raw=raw, axis=1, gear_header="1/180")
        self.assertEqual(r["stop"], [], r["stop"])
        plan = param_build.prepare(raw, axis_values={1: r["values"]}, axis=1, seiban="50014861",
                                   reference="")
        t = plan["newtext"]
        self.assertIn("N01815Q1A1P00100000", t)
        self.assertIn("N01821Q1A1P2000", t)
        self.assertIn("N02000Q1A1P00000000", t)
        self.assertIn("N02024Q1A1P12500", t)
        self.assertIn("N02084Q1A1P1", t)
        self.assertIn("N02085Q1A1P500", t)
        self.assertIn("N02209Q1A1P00000000", t)
        self.assertIn("N02018Q1A1P00000001", t)         # 書かない → BASIC のまま
        # DGPR(2000 #1) を後から 0 にしても #4 の変換を消さない（順番: 変換 → サーボのオプション）
        v2 = param_build.with_servo_options(raw, 1, r["values"], zero_motor=True)
        self.assertEqual(norm(v2["2000"])[-2], "0")

    def test_detect_with_basic_fallback(self):
        # 製品が 1815 を持たず BASIC がフルクロでも判定できる
        r = S.convert({"2024": "69"}, raw=self.BASIC, axis=1, gear_header="1/180")
        self.assertEqual(r["mode"], "フル")
        self.assertEqual(r["stop"], [])
        self.assertIn("1815", r["values"])


HEADER = (";   Model parameters\nModel={model}\n;   Gear\nGear Rate='{gear}\n;   Axis\nAxis=R\n"
          "Motor Number=M1\n")


def prm(model, gear, v1815):
    return (HEADER.format(model=model, gear=gear)
            + '"番号","軸","和名","英名","値","説明"\n'
              f'"1815","","","","\'{v1815}",""\n"1821","","","","2000",""\n"2000","","","","\'***0***0",""\n'
              '"2024","","","","12500",""\n')


def prm_vals(model, gear, mode, extra=None, motor="M1"):
    """フル/セミの組の試験用ファイル。受注ごとに違う値(1850)・型式ごとに同じ値を持つ。"""
    base = {"1013": "00000000", "1260": "360.000", "1820": "2", "1850": "0", "2020": "279", "2022": "111",
            "2023": "8192"}
    if mode == "フル":
        base.update({"1815": "00100010", "1821": "45000", "2000": "***1***0", "2018": "*******1",
                     "2024": "69", "2084": "9", "2085": "25", "2209": "*1******"})
    else:
        base.update({"1815": "00100000", "1821": "2000", "2000": "***0***0", "2024": "12500",
                     "2084": "1", "2085": "500", "2209": "*0******"})
    base.update(extra or {})
    head = (HEADER.format(model=model, gear=gear).replace("Motor Number=M1", f"Motor Number={motor}"))
    body = '"番号","軸","和名","英名","値","説明"\n'
    for k, v in base.items():
        q = "'" if _bitlike(v) else ""
        body += f'"{k}","","","","{q}{v}",""\n'
    return head + body


def _bitlike(v):
    return len(v) == 8 and set(v) <= set("01*")


class TestPairs(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def w(self, name, text):
        Path(self.d, name).write_bytes(text.encode("cp932"))

    def find(self, **kw):
        args = dict(model="RTV-231,WF", gear="1/180", motor_no="M1", kind="回転")
        args.update(kw)
        return S.find_pair_rules(self.d, **args)

    def test_model_levels(self):
        self.assertEqual(S.model_level("RTV-231,WF", "RTV-231,WF"), 0)
        self.assertEqual(S.model_level("RTV-231,WF", "RTV-231WF"), 1)
        self.assertEqual(S.model_level("RTV-231,WF", "RTV-231"), 2)
        self.assertEqual(S.model_level("RTV-231,WF", "RTV-232"), None)

    def test_pair_rules_only_differing_and_not_individual(self):
        f = {"1815": "00100010", "1821": "45000", "2024": "69", "1850": "5", "2000": "***1***0",
             "2018": "*******1", "2020": "279", "1013": "00000000"}
        s_ = {"1815": "00100000", "1821": "2000", "2024": "12500", "1850": "9", "2000": "***0***0",
              "2020": "299", "1013": "00000000"}
        r = S.pair_rules(f, s_)
        self.assertIn(("bit", 1815, 1, "0"), r)
        self.assertIn(("val", 1821, "2000"), r)
        self.assertIn(("bit", 2000, 4, "0"), r)
        self.assertIn(("drop", 2018), r)
        self.assertFalse([x for x in r if x[1] in (1850, 2020, 1013)])      # 受注ごとに違う値は入れない

    def test_apsz_bit_ignored(self):
        r = S.pair_rules({"1815": "00100010"}, {"1815": "00000000"})        # #5(APZ) は据付けごと
        self.assertEqual(r, {("bit", 1815, 1, "0")})

    def test_vote_majority(self):
        a = {("val", 1821, "2000"), ("val", 2022, "5")}
        b = {("val", 1821, "2000")}
        c = {("val", 1821, "2000"), ("val", 2022, "7")}
        taken, left = S.vote_rules([a, b, c])
        self.assertEqual([r for r, _n in taken], [("val", 1821, "2000")])
        self.assertEqual(sorted(r for r, _n in left), [("val", 2022, "5"), ("val", 2022, "7")])

    def test_motor_number_hash_suffix_is_ignored(self):
        """実データ: 製品の Motor Number は 'A06B-2229-B000#0100' のように # 付きが多い（0組になっていた）。"""
        self.w("R50000001.prm", prm_vals("RTV-231,WF", "1/180", "フル", motor="A06B-2229-B000#0100"))
        self.w("R50000002.prm", prm_vals("RTV-231,WF", "1/180", "セミ", motor="A06B-2229-B000"))
        r = self.find(motor_no="A06B-2229-B000")
        self.assertEqual(r["n_pairs"], 1)
        self.assertEqual(S.motor_key("A06B-2229-B000#0100"), S.motor_key("a06b-2229-b000"))

    def test_index_is_saved_and_reused_without_reading_unchanged_files(self):
        self.w("R50000001.prm", prm_vals("RTV-231,WF", "1/180", "フル"))
        self.w("R50000002.prm", prm_vals("RTV-231,WF", "1/180", "セミ"))
        self.w("R50000003.prm", prm_vals("RTV-999", "1/180", "セミ"))
        idx = os.path.join(self.d, "索引.json")
        a = S.find_pair_rules(self.d, model="RTV-231,WF", gear="1/180", motor_no="M1", kind="回転", index_path=idx)
        self.assertEqual(a["n_pairs"], 1)
        self.assertTrue(os.path.isfile(idx))
        opened = []
        real = open

        def spy(path, *args, **kw):
            opened.append(str(path))
            return real(path, *args, **kw)
        with mock.patch("builtins.open", spy):
            h = S.header_index(self.d, ("R",), index_path=idx)
        self.assertEqual([x for x in opened if x.endswith(".prm")], [])       # 変わっていない → 先頭を読まない
        self.assertEqual(len(h), 3)
        self.w("R50000004.prm", prm_vals("RTV-231,WF", "1/180", "セミ"))      # 新しいファイルだけ読み直す
        opened.clear()
        with mock.patch("builtins.open", spy):
            h = S.header_index(self.d, ("R",), index_path=idx)
        self.assertEqual(len([x for x in opened if x.endswith(".prm")]), 1)
        self.assertEqual(len(h), 4)

    def test_index_build_can_be_cancelled(self):
        self.w("R50000001.prm", prm_vals("RTV-231,WF", "1/180", "フル"))
        self.assertIsNone(S.header_index(self.d, ("R",), cancel=lambda: True))

    def test_find_pairs_and_apply_only_pair_numbers(self):
        self.w("R50000001.prm", prm_vals("RTV-231,WF", "1/180", "フル", {"1850": "11"}))
        self.w("R50000002.prm", prm_vals("RTV-231,WF", "1/180", "セミ", {"1850": "99"}))
        self.w("R50000003.prm", prm_vals("RTV-999", "1/180", "セミ"))          # 別型式
        self.w("T50000004.prm", prm_vals("RTV-231,WF", "1/180", "セミ"))       # 傾斜
        self.w("R50000005.prm", prm_vals("RTV-231,WF", "1/90", "セミ"))        # 別減速比
        rules = self.find()
        self.assertEqual(rules["n_pairs"], 1)
        self.assertEqual(rules["level"], 0)
        # 利用者の製品（別の受注。受注ごとに違う値を持つ）
        mine = {"1013": "'00000000", "1260": "360.000", "1820": "2", "1815": "'00100010",
                "1821": "45000", "2000": "'***1***0", "2018": "'*******1", "2023": "8192",
                "2024": "69", "2084": "9", "2085": "25", "2209": "'*1******", "2020": "279",
                "1850": "-94", "2022": "111"}
        r = S.convert(mine, rules=rules, gear_header="1/180")
        self.assertEqual(r["source"], "組")
        self.assertIn("同じ型式の組 1組から", r["basis"])
        for n, v in SEMI.items():
            self.assertEqual(norm(r["values"][n]), v, n)
        self.assertEqual(r["values"]["1850"], "-94")                   # 受注ごとに違う値は触らない
        self.assertEqual(r["values"]["2022"], "111")
        self.assertNotIn("2018", r["values"])
        self.assertIn("一致", r["compare"])
        self.assertNotIn("違う", r["compare"])

    def test_pair_wins_over_formula_and_cmr_other_than_2(self):
        self.w("R50000001.prm", prm_vals("RTV-231,WF", "1/180", "フル", {"1820": "4"}))
        self.w("R50000002.prm", prm_vals("RTV-231,WF", "1/180", "セミ", {"1820": "4", "1821": "2222"}))
        rules = self.find()
        mine = {k: v for k, v in FULL.items()}
        mine["1820"] = "4"
        r = S.convert(mine, rules=rules, gear_header="1/180")
        self.assertEqual(r["source"], "組")                            # CMR が 2 以外でも、実物の根拠があれば使う
        self.assertEqual(r["values"]["1821"], "2222")
        self.assertIn("計算式は使えません", r["compare"])
        self.assertIn("CMR", r["compare"])
        # 組が無ければ式になり、CMR 2 以外は変換しない
        r2 = S.convert(mine, rules=None, gear_header="1/180")
        self.assertTrue(r2["stop"])

    def test_formula_disagreement_listed(self):
        self.w("R50000001.prm", prm_vals("RTV-231,WF", "1/180", "フル"))
        self.w("R50000002.prm", prm_vals("RTV-231,WF", "1/180", "セミ", {"1821": "2222"}))
        r = S.convert(FULL, rules=self.find(), gear_header="1/180")
        self.assertIn("計算と実物で違う: 1821", r["compare"])
        self.assertEqual(norm(r["values"]["1821"]), "2222")

    def test_outside_known_numbers_flagged(self):
        self.w("R50000001.prm", prm_vals("RTV-231,WF", "1/180", "フル", {"2200": "1"}))
        self.w("R50000002.prm", prm_vals("RTV-231,WF", "1/180", "セミ", {"2200": "7"}))
        r = S.convert(dict(FULL, **{"2200": "1"}), rules=self.find(), gear_header="1/180")
        c = next(c for c in r["changes"] if c["num"] == 2200)
        self.assertTrue(c["review"])
        self.assertEqual(r["values"]["2200"], "7")

    def test_minority_rule_not_applied_but_listed(self):
        self.w("R50000001.prm", prm_vals("RTV-231,WF", "1/180", "フル"))
        self.w("R50000002.prm", prm_vals("RTV-231,WF", "1/180", "セミ", {"2022": "5"}))
        self.w("R50000003.prm", prm_vals("RTV-231,WF", "1/180", "セミ", {"2022": "7"}))
        self.w("R50000004.prm", prm_vals("RTV-231,WF", "1/180", "セミ"))
        rules = self.find()
        self.assertEqual(rules["n_pairs"], 3)
        r = S.convert(FULL, rules=rules, gear_header="1/180")
        self.assertEqual(r["values"]["2022"] if "2022" in r["values"] else None, FULL.get("2022"))
        self.assertTrue([c for c in r["changes"] if c.get("unapplied") and c["num"] == 2022])

    def test_model_ladder_head_only(self):
        self.w("R50000001.prm", prm_vals("RTV-231", "1/180", "フル"))
        self.w("R50000002.prm", prm_vals("RTV-231", "1/180", "セミ"))
        rules = self.find()                                            # 型式 'RTV-231,WF' → カンマ前で見つかる
        self.assertEqual(rules["level"], 2)
        self.assertEqual(rules["n_pairs"], 1)

    def test_no_pair_means_formula(self):
        self.w("R50000002.prm", prm_vals("RTV-231,WF", "1/180", "セミ"))   # セミだけ（フルの実物が無い）
        rules = self.find()
        self.assertEqual(rules["n_pairs"], 0)
        self.assertEqual(rules["semi_ref"]["name"], "R50000002.prm")
        r = S.convert(FULL, rules=rules, gear_header="1/180")
        self.assertEqual(r["source"], "計算式")
        c = S.compare_reference(r, rules["semi_ref"]["values"])
        self.assertEqual(c["matched"], c["total"] - len(c["mismatch"]))

    def test_none_when_absent(self):
        self.assertIsNone(self.find())
        self.assertIsNone(S.find_pair_rules(self.d + "x", model="a"))
        self.w("R50000001.prm", prm_vals("RTV-231,WF", "1/180", "フル"))
        self.w("R50000002.prm", prm_vals("RTV-231,WF", "1/180", "セミ"))
        self.assertIsNone(self.find(cancel=lambda: True))
        self.assertIsNone(self.find(model="ZZZ-1"))

    def test_exclude_own_file(self):
        self.w("R50000001.prm", prm_vals("RTV-231,WF", "1/180", "フル"))
        self.w("R50000002.prm", prm_vals("RTV-231,WF", "1/180", "セミ"))
        r = self.find(exclude_path=str(Path(self.d, "R50000002.prm")))
        self.assertEqual(r["n_pairs"], 0)

    def test_compare_counts_and_mismatch(self):
        r = S.convert(FULL, gear_header="1/180")
        c = S.compare_reference(r, dict(SEMI))
        self.assertEqual((c["matched"], c["total"]), (8, 8))
        c = S.compare_reference(r, dict(SEMI, **{"2084": "2", "2018": "1"}))
        self.assertEqual(c["matched"], 6)
        self.assertEqual(sorted(n for n, _a, _b in c["mismatch"]), [2018, 2084])


# ---------------------------------------------------------------- 画面（かんたん作成の段①）
import copy
import time
import unittest.mock as mock
from test_wizard_machine_write import WizardBase, HERE as _H   # noqa: E402


def product_text(v1815="00100010", cmr="2", gear="1/180", model="RTV-231,WF", extra=None):
    vals = {"1013": "00000000", "1260": "360.000", "1820": cmr, "1815": v1815, "1821": "45000",
            "2000": "***1***0", "2018": "*******1", "2023": "8192", "2024": "69", "2084": "9",
            "2085": "25", "2209": "*1******", "2020": "279"}
    vals.update(extra or {})
    head = (f";   Model parameters\nModel={model}\n;   Seiban parameters\nSeiban=50014861\n"
            f"Gear Rate='{gear}\n;   Axis parameters\nAxis=R\nMotor Number=A06B-2229-B000\n"
            "Motor Model=αiF8/3000 HV-B\nSeparate Detector=αA1000S\nServo Amp Model=\n"
            '"番号","軸","和名","英名","値","説明"\n')
    return head + "".join(
        f'"{k}","A5","","","\'{v}",""\n' if set(v) <= set("01*") and len(v) == 8 else f'"{k}","A5","","","{v}",""\n'
        for k, v in vals.items())


class TestWizardSemi(WizardBase):
    def setUp(self):
        super().setUp()
        prod = Path(self.settings["param_product_dir"])
        for f in prod.glob("*"):
            f.unlink()
        (prod / "R50014861.prm").write_text(product_text(), encoding="cp932")
        self.prod = prod

    def open(self, w=None):
        w = w or self.wizard()
        w.e_seiban.setText("50014861")
        w.search()
        for f in w._files:
            f["chk"].setChecked(True)
        w.cmb_motor.setCurrentIndex(w.cmb_motor.findData("product"))
        w._update_candidates()
        return w

    def job(self, w):
        import nd287_app.gui as G
        with mock.patch.object(G.QtWidgets.QMessageBox, "question",
                               lambda *a, **k: G.QtWidgets.QMessageBox.Yes), \
                mock.patch.object(G.QtWidgets.QMessageBox, "warning", lambda *a, **k: None):
            return w._machine_job()

    def pick_ctrl(self, w, unit="23"):
        w.cmb_motor.setCurrentIndex(w.cmb_motor.findData("temp"))     # 容量が決まらない試験製品なので、号機を全部出す
        w._update_candidates()
        i = next(k for k, (c, _a) in enumerate(w._cand) if str(c.unit) == unit)
        w.cmb_ctrl.setCurrentIndex(w.cmb_ctrl.findData(i))

    def test_shown_only_for_full_and_default_is_product(self):
        w = self.open()
        self.assertTrue(w.cmb_semi.isVisibleTo(w))
        self.assertEqual(w.cmb_semi.currentData(), "")
        self.assertFalse(w.txt_semi.isVisibleTo(w))
        self.assertIn("製品のモーター", w.lbl_decided.text())
        self.assertNotIn("セミクロに変換", w.lbl_decided.text())

    def test_hidden_for_semi_product(self):
        (self.prod / "R50014861.prm").write_text(product_text(v1815="00100000"), encoding="cp932")
        w = self.open()
        self.assertFalse(w.cmb_semi.isVisibleTo(w))

    def test_choose_semi_shows_diff_list(self):
        w = self.open()
        w.cmb_semi.setCurrentIndex(1)
        self.assertTrue(w.txt_semi.isVisibleTo(w))
        t = w.txt_semi.toPlainText()
        self.assertIn("1821", t)
        self.assertIn("45000 → 2000", t)
        self.assertIn("2018", t)
        self.assertIn("書かない", t)
        self.assertIn("要確認", t)
        self.assertIn("根拠: 計算式", t)
        self.assertIn("セミクロに変換", w.lbl_decided.text())

    def test_selection_resets_when_product_changes(self):
        w = self.open()
        w.cmb_semi.setCurrentIndex(1)
        for f in w._files:
            f["chk"].setChecked(False)
        for f in w._files:
            f["chk"].setChecked(True)
        self.assertEqual(w.cmb_semi.currentData(), "")

    def test_cmr_not_2_cannot_choose(self):
        (self.prod / "R50014861.prm").write_text(product_text(cmr="4"), encoding="cp932")
        w = self.open()
        self.assertTrue(w.cmb_semi.isVisibleTo(w))
        self.assertFalse(w.cmb_semi.model().item(1).isEnabled())
        self.assertIn("CMR が 2・20 以外のため確かめていません（値: 4）", w.lbl_semi.text())
        w.cmb_semi.setCurrentIndex(1)                        # 無理に選んでも作れない
        w._update_candidates()
        self.assertEqual(w.cmb_semi.currentData(), "")

    def test_converted_value_reaches_machine_plan_and_band(self):
        import nd287_app.gui as G
        w = self.open()
        w.cmb_semi.setCurrentIndex(1)
        self.wait_search(w)
        self.pick_ctrl(w)
        job = self.job(w)
        (settings, plan_fn), kw = job
        self.assertIn("セミクロに変換して書きます", kw["semi_note"])
        raw = (HERE_FIX / "focas_param_31i.DAT").read_bytes().decode("cp932", "replace")
        plan = plan_fn(raw)
        t = plan["newtext"]
        ax = list(plan["axis_values"])[0]
        self.assertEqual(fanuc_param.value_on_axis(t, 1821, ax), "2000")
        for n, v in (("2024", "12500"), ("2084", "1"), ("2085", "500")):   # 試験用 BASIC に行が無い番号は、書く値で見る
            self.assertEqual(plan["axis_values"][ax][n], v)
        self.assertEqual(fanuc_param.value_on_axis(t, 1815, ax)[-2], "0")

    def test_product_as_is_has_no_band_and_keeps_full_values(self):
        w = self.open()
        self.pick_ctrl(w)
        (settings, plan_fn), kw = self.job(w)
        self.assertEqual(kw["semi_note"], "")
        raw = (HERE_FIX / "focas_param_31i.DAT").read_bytes().decode("cp932", "replace")
        plan = plan_fn(raw)
        ax = list(plan["axis_values"])[0]
        self.assertEqual(fanuc_param.value_on_axis(plan["newtext"], 1821, ax), "45000")

    def test_file_name_has_semi(self):
        import nd287_app.gui as G
        w = self.open()
        w.cmb_semi.setCurrentIndex(1)
        self.wait_search(w)
        self.pick_ctrl(w)
        out = Path(self.settings["param_product_dir"]).parent / "out"
        out.mkdir(exist_ok=True)
        w.e_out.setText(str(out))
        seen = []

        class Prev:
            def __init__(s, parent, rows, subtitle="", **kw):
                seen.append(subtitle)

            def exec(s):
                return 0
        with mock.patch.object(G, "ParamPreviewDialog", Prev), \
                mock.patch.object(G.QtWidgets.QMessageBox, "warning", lambda *a, **k: None):
            w.create()
        self.assertTrue(seen, "プレビューまで進まなかった")
        self.assertIn("_SEMI.", seen[0])
        self.assertIn("SEMI", seen[0])

    def wait_search(self, w):
        for _ in range(200):
            self.app.processEvents()
            if not w._semi_pending:
                break
            time.sleep(0.02)
        self.app.processEvents()

    def test_pair_in_product_folder_is_first_basis(self):
        (self.prod / "R50000008.prm").write_text(product_text(extra={"1850": "5"}), encoding="cp932")
        (self.prod / "R50000009.prm").write_text(
            product_text(v1815="00100000", extra={"1821": "2000", "2000": "***0***0", "2024": "12500",
                                                  "2084": "1", "2085": "500", "2209": "*0******",
                                                  "2018": "", "1850": "9"}), encoding="cp932")
        w = self.open()
        w.cmb_semi.setCurrentIndex(1)
        self.wait_search(w)
        t = w.txt_semi.toPlainText()
        self.assertIn("同じ型式の組 1組から", t)
        self.assertIn("45000 → 2000", t)
        self.assertNotIn("1850", t)                          # 受注ごとに違う値は組の差に入らない

    def test_no_pair_uses_formula_and_says_nothing_extra(self):
        w = self.open()
        w.cmb_semi.setCurrentIndex(1)
        self.wait_search(w)
        self.assertIn("計算式", w.txt_semi.toPlainText())
        self.assertEqual(w.lbl_semi_ref.text(), "")

    def test_gather_waits_while_searching(self):
        w = self.open()
        w.cmb_semi.setCurrentIndex(1)
        self.wait_search(w)
        self.pick_ctrl(w)
        w._semi_pending = True
        warned = []
        import nd287_app.gui as G
        with mock.patch.object(G.QtWidgets.QMessageBox, "question",
                               lambda *a, **k: G.QtWidgets.QMessageBox.Yes), \
                mock.patch.object(G.QtWidgets.QMessageBox, "warning", lambda *a, **k: warned.append(a[2])):
            self.assertIsNone(w._machine_job())
        self.assertTrue(any("探している途中" in x for x in warned))


HERE_FIX = Path(_H) / "fixtures"
