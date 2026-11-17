"""v082: interrupted all-market error bundles release staging without altering journals."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import android_research_bundle as exporter


class ErrorExportCancel(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)/"runtime"
        self.out=Path(self.tmp.name)/"output"
        self.root.mkdir();self.out.mkdir()
        for market in ("stock","coin"):
            folder=self.root/market/"errors"
            folder.mkdir(parents=True)
            (folder/"record.jsonl").write_bytes(
                (json.dumps({"market":market,"error":"REAL_SOURCE_UNCHANGED"})+"\n").encode()*8000)
            db=folder/"runtime.sqlite3"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE runtime_errors(id INTEGER PRIMARY KEY, reason TEXT)")
                conn.executemany("INSERT INTO runtime_errors(reason) VALUES(?)",
                                 [("TEST_DIAGNOSTIC",)]*110)
        self.marker=Path(self.tmp.name)/"cancel.flag"
        self.before=self.digests()

    def tearDown(self):
        self.tmp.cleanup()

    def digests(self):
        return {str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob("*") if p.is_file()}

    def call(self):
        return exporter.make_bundle(str(self.root),str(self.out),"all",
                                    160_000,None,"errors",
                                    '{"runtime_faults":[]}',str(self.marker))

    def test_cancel_before_begin_does_not_create_partial_zip(self):
        self.marker.write_text("STOP")
        with self.assertRaisesRegex(exporter.ExportCancelled,
                                    "ARTITRADE_ERROR_EXPORT_CANCELLED"):
            self.call()
        self.assertEqual(list(self.out.glob("*.zip")),[])
        self.assertEqual(self.before,self.digests())

    def test_cancel_during_first_zip_stream_removes_partial_output(self):
        original=exporter._copy_piece
        called=[]
        def interrupt(source,offset,size,out,check_cancel=None):
            called.append(size)
            self.marker.write_text("STOP")
            return original(source,offset,size,out,check_cancel)
        with patch.object(exporter,"_copy_piece",side_effect=interrupt):
            with self.assertRaisesRegex(exporter.ExportCancelled,
                                        "ARTITRADE_ERROR_EXPORT_CANCELLED"):
                self.call()
        self.assertTrue(called)
        self.assertEqual(list(self.out.rglob("*.zip")),[])
        self.assertEqual(self.before,self.digests())

    def test_reopen_after_cancel_produces_verifiable_all_market_bundle(self):
        self.marker.write_text("STOP")
        with self.assertRaises(exporter.ExportCancelled):self.call()
        self.marker.unlink()
        result=json.loads(self.call())
        self.assertTrue(result["ok"])
        self.assertGreater(result["parts"],0)
        for entry in result["files"]:
            with zipfile.ZipFile(self.out/entry["name"]) as src:
                self.assertIsNone(src.testzip())
                index=json.loads(src.read("analysis/bundle_index.json"))
                self.assertIn("stock/errors/runtime.sqlite3",index["sources"])
                self.assertIn("coin/errors/runtime.sqlite3",index["sources"])
        self.assertEqual(self.before,self.digests())

    def test_android_ui_has_cancel_control_and_volatile_cross_screen_owner(self):
        text=(Path(__file__).resolve().parents[1]/
            "app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text()
        for needle in ("진행 중인 오류 추출 취소","cancelScopedErrorExport",
                       "static volatile Thread scopedErrorWorker",
                       "static final java.util.concurrent.atomic.AtomicBoolean fullExportRunning",
                       "checkScopedErrorCancel()","artitrade_scoped_error_export_v082.cancel"):
            self.assertIn(needle,text)


if __name__=="__main__":
    unittest.main()
