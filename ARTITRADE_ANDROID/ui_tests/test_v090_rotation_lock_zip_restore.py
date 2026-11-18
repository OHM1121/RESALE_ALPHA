"""v090 device rotation-lock / crash avoidance and analyzer-ready ZIP exports.

Only temporary synthetic ledgers are accessed. Original phone SQLite and
trading thresholds are neither imported nor modified.
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from android_research_bundle import make_bundle

SRC=Path(__file__).resolve().parents[1]/"app/src/main"
ACTIVITY=(SRC/"java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text()
SERVICE=(SRC/"java/com/artitrade/mobile/UnifiedTradingService.java").read_text()
MANIFEST=(SRC/"AndroidManifest.xml").read_text()

class RotationLockAndNoActivityRelaunch(unittest.TestCase):
    def test_full_user_follows_device_lock_but_allows_reverse_landscape(self):
        doc=ET.fromstring(MANIFEST)
        activity=doc.find("application/activity")
        ns="{http://schemas.android.com/apk/res/android}"
        self.assertEqual(activity.attrib[ns+"screenOrientation"],"fullUser")
        self.assertNotIn('android:screenOrientation="fullSensor"',MANIFEST)
        changes=set(activity.attrib[ns+"configChanges"].split("|"))
        self.assertTrue({"orientation","screenSize","smallestScreenSize",
                         "screenLayout","density"}.issubset(changes))
        self.assertIn('void onConfigurationChanged(android.content.res.Configuration config)',ACTIVITY)

    def test_rotation_never_resets_preload_worker_or_export_or_service(self):
        start=ACTIVITY.index("void onConfigurationChanged(")
        end=ACTIVITY.index("@Override protected void onSaveInstanceState",start)
        body=ACTIVITY[start:end]
        self.assertIn("if(!preloadScreenReady)",body)
        self.assertIn("startupProgress.setLayoutParams(lp)",body)
        self.assertIn("fullExportRunning.get()",body)
        self.assertIn("root.requestLayout()",body)
        self.assertIn("showShell()",body)
        self.assertNotIn("startRuntimeLater()",body)
        self.assertNotIn("onDestroy()",body)
        self.assertNotIn("finishPreload(",body)
        self.assertIn("main.removeCallbacksAndMessages(null)",ACTIVITY)
        self.assertIn('new Thread(this::monitorLoop',SERVICE)

class PortableErrorZip(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory(prefix="v090_zip_portability_")
        self.addCleanup(d.cleanup)
        self.root=Path(d.name)/"phone"
        self.out=Path(d.name)/"zip"
        self.root.mkdir()
        self.out.mkdir()
        folder=self.root/"coin"
        folder.mkdir()
        self.db=folder/"upbit_paper.sqlite3"
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE executions(id INTEGER PRIMARY KEY, note TEXT, blob BLOB)")
            c.execute("INSERT INTO executions(note,blob) VALUES(?,zeroblob(800000))",
                      ("ORIGINAL_PAPER_FILL",))
        with sqlite3.connect(self.db) as live:
            self.expected=live.execute(
                "SELECT id,note,length(blob) FROM executions").fetchall()

    def bundle(self,scope="coin",cap=160_000):
        info=json.loads(make_bundle(str(self.root),str(self.out),scope,cap,
                                    bundle_kind="errors"))
        self.assertTrue(info["ok"])
        for part in info["files"]:
            f=self.out/part["name"]
            self.assertLessEqual(f.stat().st_size,cap)
            with zipfile.ZipFile(f) as z:
                self.assertIsNone(z.testzip())
                self.assertIn("analysis/restore_parts.py",z.namelist())
                self.assertIn("analysis/bundle_index.json",z.namelist())
        return info

    def test_one_zip_contains_normal_sqlite_not_opaque_fragments(self):
        info=self.bundle()
        self.assertEqual(info["parts"],1)
        zipfile_path=self.out/info["files"][0]["name"]
        with zipfile.ZipFile(zipfile_path) as z:
            self.assertIn("coin/upbit_paper.sqlite3",z.namelist())
            index=json.loads(z.read("analysis/bundle_index.json"))
            entry=z.read("coin/upbit_paper.sqlite3")
            self.assertEqual(hashlib.sha256(entry).hexdigest(),
                index["sources"]["coin/upbit_paper.sqlite3"]["sha256"])
            fragments=index["sources"]["coin/upbit_paper.sqlite3"]["fragments"]
            self.assertEqual(len(fragments),1)
            self.assertEqual(fragments[0]["entry"],"coin/upbit_paper.sqlite3")
            extracted=self.out/"extracted_snapshot.sqlite3"
            extracted.write_bytes(entry)
        with sqlite3.connect(extracted) as restored:
            self.assertEqual(
                restored.execute("SELECT id,note,length(blob) FROM executions").fetchall(),
                self.expected)

    def test_multi_part_zip_reassembles_and_checks_every_source_sha(self):
        evidence=self.root/"coin"/"runtime_errors.jsonl"
        evidence.write_bytes(os.urandom(430_000))
        expected=hashlib.sha256(evidence.read_bytes()).hexdigest()
        info=self.bundle(cap=160_000)
        self.assertGreater(info["parts"],1)
        first=self.out/info["files"][0]["name"]
        with zipfile.ZipFile(first) as z:
            script=z.read("analysis/restore_parts.py")
        tool=self.out/"restore_parts.py"
        tool.write_bytes(script)
        reconstructed=self.out/"restored"
        result=subprocess.run([sys.executable,str(tool),str(first),
                               str(reconstructed)],capture_output=True,
                              text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        with zipfile.ZipFile(first) as z:
            index=json.loads(z.read("analysis/bundle_index.json"))
        self.assertEqual(hashlib.sha256(
            (reconstructed/"coin/upbit_paper.sqlite3").read_bytes()).hexdigest(),
            index["sources"]["coin/upbit_paper.sqlite3"]["sha256"])
        self.assertEqual(hashlib.sha256(
            (reconstructed/"coin/runtime_errors.jsonl").read_bytes()).hexdigest(),
            expected)
        with sqlite3.connect(reconstructed/"coin/upbit_paper.sqlite3") as c:
            self.assertEqual(c.execute(
                "SELECT note FROM executions WHERE id=1").fetchone()[0],
                "ORIGINAL_PAPER_FILL")
        with sqlite3.connect(self.db) as original:
            self.assertEqual(original.execute(
                "SELECT id,note,length(blob) FROM executions").fetchall(),
                self.expected)

    def test_reassembler_rejects_wrong_or_missing_parts(self):
        evidence=self.root/"coin"/"runtime_errors.jsonl"
        evidence.write_bytes(os.urandom(430_000))
        info=self.bundle(cap=160_000)
        first=self.out/info["files"][0]["name"]
        with zipfile.ZipFile(first) as z:
            script=z.read("analysis/restore_parts.py")
        tool=self.out/"restore_parts.py"
        tool.write_bytes(script)
        last=self.out/info["files"][-1]["name"]
        last.unlink()
        result=subprocess.run([sys.executable,str(tool),str(first),
                               str(self.out/"bad_restore")],capture_output=True,
                              text=True,timeout=30)
        self.assertNotEqual(result.returncode,0)

if __name__=="__main__":
    unittest.main()
