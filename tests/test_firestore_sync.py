# -*- coding: utf-8 -*-
import base64
import unittest

from nd287_app.firestore_sync import (
    FirestoreSync,
    build_measurement_doc,
    from_firestore_fields,
    from_firestore_value,
    overall_judgement,
    to_firestore_fields,
    to_firestore_value,
)


class TestFirestoreEncoding(unittest.TestCase):
    def test_scalar_types(self):
        self.assertEqual(to_firestore_value("a"), {"stringValue": "a"})
        self.assertEqual(to_firestore_value(3), {"integerValue": "3"})
        self.assertEqual(to_firestore_value(2.5), {"doubleValue": 2.5})
        self.assertEqual(to_firestore_value(True), {"booleanValue": True})
        self.assertEqual(to_firestore_value(None), {"nullValue": None})

    def test_bool_is_not_int(self):
        # Pythonの bool は int のサブクラス。booleanValueが先に選ばれること
        self.assertIn("booleanValue", to_firestore_value(False))

    def test_bytes_become_base64_string(self):
        value = to_firestore_value(b"\x89PNG")
        self.assertEqual(value, {"stringValue": base64.b64encode(b"\x89PNG").decode()})

    def test_nested(self):
        fields = to_firestore_fields({
            "results": [{"item": "精度PP", "value": '8.0"'}],
            "meta": {"machine": "260976K"},
        })
        item = (fields["results"]["arrayValue"]["values"][0]
                ["mapValue"]["fields"]["item"])
        self.assertEqual(item, {"stringValue": "精度PP"})
        self.assertEqual(fields["meta"]["mapValue"]["fields"]["machine"],
                         {"stringValue": "260976K"})


class TestDecode(unittest.TestCase):
    def test_roundtrip(self):
        data = {"type": "prepare", "station": "PC-3", "n": 7, "ratio": 1.5,
                "flag": True, "none": None,
                "list": [1, "a"], "map": {"x": 2}}
        decoded = from_firestore_fields(to_firestore_fields(data))
        self.assertEqual(decoded, data)

    def test_decode_scalars(self):
        self.assertEqual(from_firestore_value({"integerValue": "5"}), 5)
        self.assertEqual(from_firestore_value({"stringValue": "x"}), "x")
        self.assertEqual(from_firestore_value({"booleanValue": False}), False)
        self.assertIsNone(from_firestore_value({"nullValue": None}))


class TestOverallJudgement(unittest.TestCase):
    def test_ng_wins(self):
        rows = [("a", "OK（規格…）"), ("b", "NG（規格…）")]
        self.assertEqual(overall_judgement(rows), "NG")

    def test_ok(self):
        rows = [("a", "OK（規格…）"), ("b", '8.0"')]
        self.assertEqual(overall_judgement(rows), "OK")

    def test_no_judgement(self):
        self.assertEqual(overall_judgement([("a", '8.0"')]), "判定なし")
        self.assertEqual(overall_judgement([]), "判定なし")


class TestBuildDoc(unittest.TestCase):
    def test_build(self):
        doc = build_measurement_doc(
            model="RW-250R", machine="260976K", operator="ODA",
            date="2026-06-13", temperature="26", mode="回転分割",
            results=[("ホイールCW 精度PP", '8.00"'),
                     ("ホイール バックラッシ 判定", 'NG（規格…）')],
            comment="テスト", plot_png=b"\x89PNG456",
            saved_files=["C:/data/260976K.csv"],
        )
        self.assertEqual(doc["source"], "nd287_app")
        self.assertEqual(doc["machine"], "260976K")
        self.assertEqual(doc["judgement"], "NG")
        self.assertEqual(doc["results"][0],
                         {"item": "ホイールCW 精度PP", "value": '8.00"'})
        self.assertEqual(doc["plotPng"], base64.b64encode(b"\x89PNG456").decode())
        self.assertEqual(doc["savedFiles"], ["C:/data/260976K.csv"])
        self.assertIn("savedAt", doc)
        # Firestoreエンコードが通ること
        to_firestore_fields(doc)

    def test_build_without_png(self):
        doc = build_measurement_doc(
            model="m", machine="x", operator="o", date="d",
            temperature="20", mode="回転分割", results=[])
        self.assertNotIn("plotPng", doc)
        self.assertNotIn("savedFiles", doc)
        self.assertEqual(doc["judgement"], "判定なし")


class TestSyncClient(unittest.TestCase):
    def test_unconfigured(self):
        sync = FirestoreSync("", "", "")
        self.assertFalse(sync.configured())
        ok, message = sync.push_document("x", {"a": 1})
        self.assertFalse(ok)

    def test_document_url(self):
        sync = FirestoreSync("KEY", "proj-1", "product-inspection-v1", "rotaryMeasurements")
        url = sync._document_url("260976K_20260613-103000")
        self.assertIn("projects/proj-1/databases/(default)/documents/"
                      "artifacts/product-inspection-v1/public/data/"
                      "rotaryMeasurements/260976K_20260613-103000", url)


if __name__ == "__main__":
    unittest.main()
