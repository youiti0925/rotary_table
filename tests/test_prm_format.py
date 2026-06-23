# -*- coding: utf-8 -*-
import unittest

from nd287_app import prm_format as P

# 提供された実物 T50013078.prm の中身（cp932 実機ファイル相当。ここでは \n 表記）
SAMPLE_PRM = (
    ";   System Versio\n"
    "System Version=Ver.1.10\n"
    ";   Select PrintForm parameters\n"
    "PrintForm=1\n"
    ";   Model parameters\n"
    "Model=RTT-137,AA\n"
    ";   Seiban parameters\n"
    "Seiban=50013078\n"
    ";   Motor RPM parameters\n"
    "Motor RPM=3000\n"
    ";   Gear Rate parameters\n"
    "Gear Rate='1/36\n"
    ";   Axis parameters\n"
    "Axis=T\n"
    ";   Direction parameters\n"
    "Direction=CCW\n"
    ";   ZRN Direction parameters\n"
    "ZRN Direction=P\n"
    ";   Motor Number parameters\n"
    "Motor Number=A06B-2215-B000\n"
    ";   Motor Model parameters\n"
    "Motor Model=αiS4/5000-B\n"
    ";   Motor Detector parameters\n"
    "Motor Detector=αiA4000\n"
    ";   Separate Detector parameters\n"
    "Separate Detector=\n"
    ";   Separate Convert. parameters\n"
    "Separate Convert.=\n"
    ";   Servo Amp Model parameters\n"
    "Servo Amp Model=\n"
    ";   Date parameters\n"
    "DATE=\n"
    ";   Check parameters\n"
    "CHECK=\n"
    ";   Note1 parameters\n"
    "Note1=\n"
    ";   Note2 parameters\n"
    "Note2=\n"
    '"1001","---","","","*******0","直線軸移動単位\nINM"\n'
    '"1005","---","","","******10","ﾚﾌｧﾚﾝｽ点設定機能\nDLZ"\n'
    '"1006","---","","","**0*****","ZRN方向\nZRN DIRECTION"\n'
    '"1013","---","","","\'00000000","設定単位\nIS-C"\n'
    '"1260","---","","","\'360.000","一回転移動量\nIS-C"\n'
    '"1320","---","","","\'111.000","+ｽﾄｱｰﾄﾞｽﾄﾛｰｸﾁｪｯｸ1\n+STORED STROKE CHECK1"\n'
    '"1321","---","","","\'-31.000","-ｽﾄｱｰﾄﾞｽﾄﾛｰｸﾁｪｯｸ1\n-STORED STROKE CHECK1"\n'
    '"1420","---","","","\'30000.000","G0速度\nG0 SPEED"\n'
    '"1425","---","","","\'286.000","ZRN時FL速度\nFL SPEED AT ZRN"\n'
    '"1620","---","","","250","G0直線TA時間\nGO ACC/DEC TIME"\n'
    '"1800","---","","","***1****","切削早送り別ﾊﾞｯｸﾗｯｼ補正\n"\n'
    '"1815","---","","","\'00100000","別置検出器\nSEPARATE DETECTOR"\n'
    '"1820","---","","","2","ＣＭＲ\n"\n'
    '"1821","---","","","10000","ﾚﾌｧﾚﾝｽｶｳﾝﾀ\nREFERENCE COUNTER"\n'
    '"1825","---","","","3000","位置ﾙｰﾌﾟｹﾞｲﾝ\nPOS.LOOP GAIN"\n'
    '"1826","---","","","10","ｲﾝﾎﾟｼﾞｼｮﾝ幅\nINPOSITION WIDTH"\n'
    '"1828","---","","","25000","動位置偏差限界値\nG0 SERVO ERR.LIMIT"\n'
    '"1829","---","","","500","停止位置偏差限界値\nSTOP SERVO ERR.LIMIT"\n'
    '"1850","---","","","","ｸﾞﾘｯﾄﾞｼﾌﾄ量\nGRID SHIFT"\n'
    '"1851","---","","","","ﾊﾞｯｸﾗｯｼ量(N1852同一値)\nBACKLASH"\n'
    '"1852","---","","","","早送り時ﾊﾞｯｸﾗｯｼ量\nG0 BACKLASH"\n'
    '"2000","---","","","***0***0","ﾊﾟﾗﾒｰﾀ設定\nPARAMETER SETTING"\n'
    '"2017","---","","","1******0","非常停止距離短縮ﾀｲﾌﾟ1\n高速ﾙｰﾌﾟ比例項高速処理"\n'
    '"2020","---","","","265","ﾓｰﾀ型式\nMOTOR MODEL"\n'
    '"2021","---","","","0","負荷ｲﾅｰｼｬｰ\nLOAD INERTIA"\n'
    '"2022","---","","","-111","ﾓｰﾀ回転方向\nMOTOR DIRECTION"\n'
    '"2023","---","","","8192","ＰＵＬＣＯ\n"\n'
    '"2024","---","","","12500","ＰＰＬＳ\n"\n'
    '"2066","---","","","0","加減速ﾌｨｰﾄﾞﾊﾞｯｸｹﾞｲﾝ\nACCELERATION FEEDBACK"\n'
    '"2084","---","","","1","ｷﾞﾔ分子\nNUMERATOR RATIO"\n'
    '"2085","---","","","100","ｷﾞﾔ分母\nDENOMINATOR RATIO"\n'
    '"","","","","",""\n'
    '"2200","---","","","*******0","暴走検出\n"\n'
    '"2204","---","","","0*******","非常停止距離短縮ﾀｲﾌﾟ2\n"\n'
    '"2209","---","","","*0******","ﾎﾟｼﾞｼｮﾝｹﾞｲﾝ内部精度向上\n"\n'
)


class TestPrmRoundTrip(unittest.TestCase):
    def test_byte_identical_roundtrip(self):
        doc = P.parse_prm(SAMPLE_PRM)
        out = P.format_prm(doc, newline="\n")
        self.assertEqual(out, SAMPLE_PRM)

    def test_crlf_save_load(self):
        doc = P.parse_prm(SAMPLE_PRM)
        text = P.format_prm(doc)  # CRLF
        self.assertIn("\r\n", text)
        # CRLFで往復しても構造が保たれる
        doc2 = P.parse_prm(text)
        self.assertEqual(P.format_prm(doc2, newline="\n"), SAMPLE_PRM)


class TestPrmHeader(unittest.TestCase):
    def setUp(self):
        self.doc = P.parse_prm(SAMPLE_PRM)

    def test_header_values(self):
        self.assertEqual(P.header_get(self.doc, "Seiban"), "50013078")
        self.assertEqual(P.header_get(self.doc, "Axis"), "T")
        self.assertEqual(P.header_get(self.doc, "Model"), "RTT-137,AA")
        self.assertEqual(P.header_get(self.doc, "Gear Rate"), "'1/36")
        self.assertEqual(P.header_get(self.doc, "DATE"), "")

    def test_filename(self):
        self.assertEqual(P.default_filename(self.doc), "T50013078.prm")

    def test_header_set_changes_only_value(self):
        P.header_set(self.doc, "Seiban", "50099999")
        P.header_set(self.doc, "Model", "RTT-200,BB")
        out = P.format_prm(self.doc, newline="\n")
        self.assertIn("Seiban=50099999\n", out)
        self.assertIn("Model=RTT-200,BB\n", out)
        self.assertEqual(P.default_filename(self.doc), "T50099999.prm")
        # コメント行はそのまま
        self.assertIn(";   System Versio\n", out)


class TestPrmGenerate(unittest.TestCase):
    def test_generate_from_master(self):
        master = P.parse_prm(SAMPLE_PRM)
        doc, missing = P.generate(
            master,
            header_overrides={"Seiban": "50088888", "Model": "RTT-137,BB"},
            value_overrides={"1825": "2500", "1826": "8"})
        self.assertEqual(missing, [])
        self.assertEqual(P.default_filename(doc), "T50088888.prm")
        self.assertEqual(P.param_value(doc, "1825"), "2500")
        self.assertEqual(P.param_value(doc, "1826"), "8")
        # マスタは変更されない（deepcopy）
        self.assertEqual(P.param_value(master, "1825"), "3000")
        self.assertEqual(P.header_get(master, "Seiban"), "50013078")

    def test_generate_reports_missing(self):
        master = P.parse_prm(SAMPLE_PRM)
        _, missing = P.generate(master, value_overrides={"9999": "1"})
        self.assertEqual(missing, ["9999"])


class TestPrmCp932(unittest.TestCase):
    def test_cp932_save_load_roundtrip(self):
        import tempfile, os
        doc = P.parse_prm(SAMPLE_PRM)
        path = os.path.join(tempfile.mkdtemp(), "T50013078.prm")
        P.save_prm(path, doc)          # cp932 + CRLF
        with open(path, "rb") as f:
            raw = f.read()
        self.assertIn(b"\r\n", raw)    # CRLF
        # cp932 で読めて、構造が一致
        doc2 = P.load_prm(path)
        self.assertEqual(P.format_prm(doc2, newline="\n"), SAMPLE_PRM)


class TestPrmParams(unittest.TestCase):
    def setUp(self):
        self.doc = P.parse_prm(SAMPLE_PRM)

    def test_param_value(self):
        self.assertEqual(P.param_value(self.doc, "1825"), "3000")
        self.assertEqual(P.param_value(self.doc, "1820"), "2")
        self.assertEqual(P.param_value(self.doc, "1850"), "")  # 空値
        self.assertIsNone(P.param_value(self.doc, "9999"))     # 無い番号

    def test_set_param_value_byte_safe(self):
        self.assertTrue(P.set_param_value(self.doc, "1825", "2500"))
        self.assertEqual(P.param_value(self.doc, "1825"), "2500")
        out = P.format_prm(self.doc, newline="\n")
        self.assertIn('"1825","---","","","2500","位置ﾙｰﾌﾟｹﾞｲﾝ\nPOS.LOOP GAIN"\n', out)
        # 他の行は元のまま
        self.assertIn('"1826","---","","","10","ｲﾝﾎﾟｼﾞｼｮﾝ幅\nINPOSITION WIDTH"\n', out)

    def test_apply_values_reports_missing(self):
        missing = P.apply_values(self.doc, {"1825": "2500", "9999": "1"})
        self.assertEqual(missing, ["9999"])
        self.assertEqual(P.param_value(self.doc, "1825"), "2500")

    def test_iter_params_splits_desc(self):
        d = {n: (v, jp, en) for n, v, jp, en in P.iter_params(self.doc)}
        self.assertEqual(d["1825"], ("3000", "位置ﾙｰﾌﾟｹﾞｲﾝ", "POS.LOOP GAIN"))
        self.assertEqual(d["1820"], ("2", "ＣＭＲ", ""))  # 英名空
        self.assertNotIn("", d)  # 区切り空行は除外


if __name__ == "__main__":
    unittest.main()
