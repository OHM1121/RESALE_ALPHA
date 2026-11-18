"""v101: ChatGPT-transfer-safe 95 MB export and independently readable JSON.

Temporary SQLite and JSONL fixtures only. Never modify user trading ledgers.
"""
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from android_research_bundle import make_bundle,DEFAULT_PART_BYTES

SRC=Path(__file__).resolve().parents[1]/"app/src/main"
JAVA=(SRC/"java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text("utf8")


class SafePortableExport(unittest.TestCase):
    def setUp(self):
        obj=tempfile.TemporaryDirectory(prefix="v101_analysis_ready_")
        self.addCleanup(obj.cleanup)
        self.base=Path(obj.name)
        self.root=self.base/"phone"
        self.out=self.base/"out"
        self.coin=self.root/"coin/base"
        self.coin.mkdir(parents=True)
        self.out.mkdir()
        self.db=self.coin/"upbit_paper.sqlite3"
        with sqlite3.connect(self.db) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE executions(id INTEGER PRIMARY KEY, reason TEXT)")
            c.execute("INSERT INTO executions(reason) VALUES('KNOWN_ORIGINAL_TEST_BUY')")
        self.random=os.urandom(350_000)
        (self.coin/"runtime_errors.jsonl").write_bytes(self.random)

    def test_android_routes_no_longer_call_500mb_single_zip(self):
        self.assertEqual(DEFAULT_PART_BYTES,95_000_000)
        self.assertIn("GPT_SAFE_ZIP_PART_BYTES=95_000_000L",JAVA)
        self.assertEqual(JAVA.count("scope,GPT_SAFE_ZIP_PART_BYTES,"),2)
        self.assertEqual(JAVA.count("source.length()>GPT_SAFE_ZIP_PART_BYTES"),2)
        self.assertNotIn("scope,500_000_000,",JAVA)
        self.assertIn('requestScopedErrorExport("coin".equals(market)?"coin":"stock")',JAVA)
        self.assertIn('requestCombinedResearch("coin".equals(market)?"coin":"stock")',JAVA)
        self.assertEqual(JAVA.count("copyAnalysisSidecarToFolder(folder,parent,info,newDocuments"),2)

    def test_multi_zip_and_small_json_restores_original_exactly(self):
        result=json.loads(make_bundle(str(self.root),str(self.out),"coin",
             max_part_bytes=120_000,bundle_kind="errors",
             native_errors_json=json.dumps(
                 {"runtime_faults":[{"stage":"PAPER_SELL_HOLD_WAIT",
                                    "message":"TEST_MANUAL_PENDING"}]})))
        self.assertGreater(result["parts"],1)
        side=result["analysis_file"]
        sidepath=self.out/side["name"]
        self.assertTrue(sidepath.exists())
        self.assertLess(side["bytes"],2_000_000)
        self.assertEqual(sidepath.stat().st_size,side["bytes"])
        self.assertEqual(hashlib.sha256(sidepath.read_bytes()).hexdigest(),
                         side["sha256"])
        overview=json.loads(sidepath.read_text("utf8"))
        self.assertEqual(overview["schema"],"ARTITRADE_ANALYSIS_READY_V101")
        self.assertEqual(overview["zip_max_part_bytes"],120_000)
        self.assertEqual(overview["zip_part_count"],result["parts"])
        self.assertEqual(overview["native_error_summary"]["runtime_faults"][0]["stage"],
                         "PAPER_SELL_HOLD_WAIT")
        self.assertEqual(len(overview["zip_parts"]),result["parts"])
        self.assertTrue(overview["all_original_data_retained_in_zip"])
        for p in overview["zip_parts"]:
            archive=self.out/p["name"]
            self.assertTrue(archive.is_file())
            self.assertLess(archive.stat().st_size,120_000)
            self.assertEqual(archive.stat().st_size,p["bytes"])
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),
                             p["sha256"])
            with zipfile.ZipFile(archive) as z:
                self.assertIsNone(z.testzip())
                self.assertIn("analysis/bundle_index.json",z.namelist())
        with zipfile.ZipFile(self.out/overview["zip_parts"][0]["name"]) as first:
            index=json.loads(first.read("analysis/bundle_index.json"))
        rec=index["sources"]["coin/base/runtime_errors.jsonl"]
        assembled=b"".join(
            zipfile.ZipFile(
                self.out/overview["zip_parts"][frag["part"]-1]["name"]).read(frag["entry"])
            for frag in sorted(rec["fragments"],key=lambda x:x["offset"])
        )
        self.assertEqual(assembled,self.random)
        self.assertEqual(hashlib.sha256(assembled).hexdigest(),rec["sha256"])
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT reason FROM executions").fetchone()[0],
                             "KNOWN_ORIGINAL_TEST_BUY")

    def test_research_scope_also_has_standalone_overview(self):
        result=json.loads(make_bundle(str(self.root),str(self.out),"coin",
                                     max_part_bytes=120_000,
                                     bundle_kind="research"))
        overview=json.loads((self.out/result["analysis_file"]["name"]).read_text("utf8"))
        self.assertEqual(overview["bundle_kind"],"research")
        self.assertIsNone(overview["native_error_summary"])
        self.assertTrue(any(s["path"]=="coin/base/upbit_paper.sqlite3"
                            for s in overview["sources"]))


if __name__=="__main__":
    unittest.main()
