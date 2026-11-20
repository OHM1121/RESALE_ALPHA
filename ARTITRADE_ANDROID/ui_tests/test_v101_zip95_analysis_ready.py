"""v101 (limit lowered to 29 MB per part, owner rule 2026-11-20): transfer-safe export and independently readable JSON.

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
        self.assertEqual(DEFAULT_PART_BYTES,29_000_000)
        self.assertIn("GPT_SAFE_ZIP_PART_BYTES=29_000_000L",JAVA)
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

    def test_default_parts_are_each_at_most_29_million_bytes(self):
        """Owner rule 2026-11-20: the analysis tool accepts <30 MB per file, so a
        large history is split into as many parts as needed (~70 MB -> >=3)."""
        for i in range(3):
            (self.coin/("big%d_errors.jsonl"%i)).write_bytes(os.urandom(23_000_000))
        result=json.loads(make_bundle(str(self.root),str(self.out),"coin",
                                      bundle_kind="errors"))
        self.assertEqual(DEFAULT_PART_BYTES,29_000_000)
        self.assertGreaterEqual(result["parts"],3)
        sizes=[(self.out/name["name"]).stat().st_size
               for name in json.loads((self.out/result["analysis_file"]["name"])
                                      .read_text("utf8"))["zip_parts"]]
        self.assertEqual(len(sizes),result["parts"])
        for size in sizes:
            self.assertLessEqual(size,29_000_000)
        self.assertLessEqual(result["analysis_file"]["bytes"],29_000_000)

    # --- 0.1.1: whole-ledger backup and AI deep export are split too ----------
    def _big_ledger(self):
        for i in range(3):
            (self.coin/("big%d_errors.jsonl"%i)).write_bytes(os.urandom(23_000_000))
        (self.root/"ANDROID_OPERATION_V074.json").write_text('{"mode":"PAPER"}',"utf8")

    def test_whole_ledger_backup_is_split_below_30mb_and_restores_exactly(self):
        """Owner rule 2026-11-20: the menu 'full ledger backup' must never write one ZIP
        of 30 MB or more. ~70 MB of incompressible data -> at least 3 parts."""
        self._big_ledger()
        result=json.loads(make_bundle(str(self.root),str(self.out),"all",
                                      bundle_kind="ledger"))
        self.assertGreaterEqual(result["parts"],3)
        self.assertTrue(all(f["bytes"]<=29_000_000 for f in result["files"]))
        self.assertTrue(all("_ALL_LEDGER_BACKUP_" in f["name"] for f in result["files"]))
        self.assertLess(result["analysis_file"]["bytes"],29_000_000)
        # every original (including the root operation file) is restorable bit-exact
        import importlib.util
        first=self.out/result["files"][0]["name"]
        with zipfile.ZipFile(first) as z:
            restorer=self.base/"restore_parts.py"
            restorer.write_bytes(z.read("analysis/restore_parts.py"))
        spec=importlib.util.spec_from_file_location("restore_parts_under_test",restorer)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        dest=self.base/"restored"
        mod.recover(str(first),str(dest))
        for rel in ("coin/base/big0_errors.jsonl","coin/base/runtime_errors.jsonl",
                    "ANDROID_OPERATION_V074.json"):
            src=self.root/rel
            self.assertEqual(hashlib.sha256(src.read_bytes()).hexdigest(),
                             hashlib.sha256((dest/rel).read_bytes()).hexdigest(),rel)
        with sqlite3.connect(dest/"coin/base/upbit_paper.sqlite3") as c:
            self.assertEqual(c.execute("SELECT reason FROM executions").fetchone()[0],
                             "KNOWN_ORIGINAL_TEST_BUY")

    def test_ai_deep_export_keeps_reports_and_is_split(self):
        self._big_ledger()
        reports=json.dumps({"analysis/AI_FULL_REPORT_V046.json":'{"ok":true}'})
        result=json.loads(make_bundle(str(self.root),str(self.out),"all",
                                      bundle_kind="ai",reports_json=reports))
        self.assertGreaterEqual(result["parts"],3)
        self.assertTrue(all(f["bytes"]<=29_000_000 for f in result["files"]))
        self.assertTrue(all("_ALL_AI_DEEP_" in f["name"] for f in result["files"]))
        with zipfile.ZipFile(self.out/result["files"][0]["name"]) as z:
            index=json.loads(z.read("analysis/bundle_index.json"))
        self.assertIn("analysis/AI_FULL_REPORT_V046.json",index["reports"])
        self.assertIn("analysis/source_table_catalog.json",index["reports"])

    def test_bad_ai_report_path_is_rejected_not_exported(self):
        with self.assertRaises(ValueError):
            make_bundle(str(self.root),str(self.out),"all",bundle_kind="ai",
                        reports_json=json.dumps({"../evil.json":"{}"}))
        self.assertEqual([x for x in self.out.iterdir() if x.suffix==".zip"],[])

    def test_android_backup_and_ai_export_never_write_a_single_zip(self):
        """Boundary contract in the Java caller: the three menu paths use the split
        bundle and a folder picker; the old single-ZIP writers are gone."""
        self.assertNotIn("ARTITRADE_FULL_LEDGER_",JAVA)
        self.assertNotIn("ARTITRADE_AI_DEEP_\"+market",JAVA)
        self.assertNotIn('callAttr("make_backup"',JAVA)
        self.assertNotIn("exportFullLedgerToDocument",JAVA)
        self.assertNotIn("beginPreparedLedgerExport",JAVA)
        self.assertIn('exportBundleToFolder(data.getData(),"all","ledger",null,null)',JAVA)
        self.assertIn('exportBundleToFolder(data.getData(),"all","ai",report,scope)',JAVA)
        a=JAVA.index("private void requestFullLedgerBackup()")
        self.assertIn("ACTION_OPEN_DOCUMENT_TREE",JAVA[a:a+1800])
        self.assertNotIn("ACTION_CREATE_DOCUMENT",JAVA[a:a+1800])
        b=JAVA.index("private void requestAiEvidenceExport()")
        self.assertIn("ACTION_OPEN_DOCUMENT_TREE",JAVA[b:b+2200])
        self.assertNotIn("ACTION_CREATE_DOCUMENT",JAVA[b:b+2200])
        self.assertIn("callAttr(\"make_bundle\"",JAVA)
        self.assertIn("progressFile.getAbsolutePath(),kind,null,null,metadata",JAVA)
        self.assertIn("(RESEARCH|LEDGER_BACKUP|AI_DEEP)",JAVA)


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
