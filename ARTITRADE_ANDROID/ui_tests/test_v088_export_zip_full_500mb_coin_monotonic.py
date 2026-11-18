"""v088: live COIN/ALL exports, compressed ZIP utilization, monotonic WAL pages.

All fixtures are temporary; real paper trade / SQLite journals are never opened.
"""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from android_ledger_backup import MIN_FREE,_db_snapshot
from android_research_bundle import make_bundle,ExportCancelled

ROOT=Path(__file__).resolve().parents[1]/"app/src/main"

class LargeCoinExport(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="artitrade_coin_export_v088_")
        self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name)
        self.root=base/"phone"
        self.root.mkdir()
        self.out=base/"zip_parts"
        self.out.mkdir()
        self.coin=self.root/"coin/base"
        self.coin.mkdir(parents=True)
        self.stock=self.root/"stock"
        self.stock.mkdir()
        self.db=self.coin/"upbit_paper.sqlite3"
        with sqlite3.connect(self.db) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE events(id INTEGER PRIMARY KEY,kind TEXT)")
            c.executemany("INSERT INTO events(kind) VALUES(?)",
                          [("COIN_ORIGINAL_EVENT",)]*250)
        with sqlite3.connect(self.stock/"ARTITRADE_D.sqlite3") as c:
            c.execute("CREATE TABLE executions(id INTEGER PRIMARY KEY,source TEXT)")
            c.execute("INSERT INTO executions(source) VALUES('STOCK_ORIGINAL')")
        with sqlite3.connect(self.root/"execution_decisions_v074.sqlite3") as c:
            c.execute("CREATE TABLE events(market TEXT,reason TEXT)")
            c.execute("INSERT INTO events VALUES('coin','ORIGINAL_GATE')")
        (self.coin/"runtime_errors.jsonl").write_bytes(
            (b'{"kind":"UPBIT_BOOK_STALE","original":true}\n')*12000)

    def export(self,scope,limit=500_000_000):
        result=json.loads(make_bundle(str(self.root),str(self.out),scope,
                                      limit,bundle_kind="errors"))
        self.assertTrue(result["ok"])
        for row in result["files"]:
            file=self.out/row["name"]
            self.assertTrue(file.is_file())
            self.assertEqual(file.stat().st_size,row["bytes"])
            self.assertLessEqual(file.stat().st_size,limit)
            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(),row["sha256"])
            with zipfile.ZipFile(file) as z:
                self.assertIsNone(z.testzip())
        return result

    def test_coin_and_all_keep_original_coin_and_shared_journal(self):
        # A WAL checkpoint may legitimately rewrite bytes of the base DB,
        # even though the export never changes any trading row or decision.
        with sqlite3.connect(self.db) as live:
            expected=live.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        for scope in ("coin","all"):
            with self.subTest(scope=scope):
                result=self.export(scope)
                self.assertEqual(result["parts"],1)
                with zipfile.ZipFile(self.out/result["files"][0]["name"]) as z:
                    index=json.loads(z.read("analysis/bundle_index.json"))
                    self.assertIn("coin/base/upbit_paper.sqlite3",index["sources"])
                    self.assertIn("execution_decisions_v074.sqlite3",index["sources"])
                    self.assertEqual("stock/ARTITRADE_D.sqlite3" in index["sources"],
                                     scope=="all")
        with sqlite3.connect(self.db) as live:
            self.assertEqual(expected,
                live.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def test_compressed_total_under_limit_stays_one_zip_not_three(self):
        # Raw input is 7x the allowed ZIP size. It is compressible, so all
        # original bytes belong in exactly ONE verified archive.
        tiny_cap=160_000
        extra=self.coin/"huge_log.jsonl"
        extra.write_bytes(b'{"severity":"INFO","message":"REPEATED_ORIGINAL"}\n'*
                          22000)
        self.assertGreater(extra.stat().st_size,tiny_cap*6)
        result=self.export("coin",tiny_cap)
        self.assertEqual(result["parts"],1)
        self.assertLess(result["files"][0]["bytes"],tiny_cap)
        with zipfile.ZipFile(self.out/result["files"][0]["name"]) as z:
            index=json.loads(z.read("analysis/bundle_index.json"))
            row=index["sources"]["coin/base/huge_log.jsonl"]
            restored=b"".join(z.read(part["entry"])
                              for part in sorted(row["fragments"],
                                                 key=lambda p:p["offset"]))
            self.assertEqual(restored,extra.read_bytes())

    def test_real_incompressible_bytes_still_split_and_verify(self):
        file=self.coin/"random_errors.jsonl"
        data=os.urandom(400_000)
        file.write_bytes(data)
        limit=160_000
        result=self.export("coin",limit)
        self.assertGreater(result["parts"],1)
        with zipfile.ZipFile(self.out/result["files"][0]["name"]) as z:
            index=json.loads(z.read("analysis/bundle_index.json"))
        record=index["sources"]["coin/base/random_errors.jsonl"]
        restored=[]
        for part in sorted(record["fragments"],key=lambda p:p["offset"]):
            archive=self.out/result["files"][part["part"]-1]["name"]
            with zipfile.ZipFile(archive) as z:
                restored.append(z.read(part["entry"]))
        self.assertEqual(b"".join(restored),data)

    def test_cancel_compressed_planning_preserves_source(self):
        flag=Path(self.tmp.name)/"cancel"
        flag.write_text("x")
        before=self.db.read_bytes()
        with self.assertRaisesRegex(ExportCancelled,"ARTITRADE_ERROR_EXPORT_CANCELLED"):
            make_bundle(str(self.root),str(self.out),"coin",160000,
                        bundle_kind="errors",cancel_path=str(flag))
        self.assertEqual(before,self.db.read_bytes())

class WalPageEvidence(unittest.TestCase):
    def test_coincident_wal_writes_do_not_restart_display_counter(self):
        with tempfile.TemporaryDirectory(prefix="artitrade_wal_pages_") as folder:
            base=Path(folder)
            db=base/"coin.sqlite3"
            dest=base/"staged.sqlite3"
            with sqlite3.connect(db) as c:
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("CREATE TABLE events(id INTEGER PRIMARY KEY,blob BLOB)")
                payload=os.urandom(3000)
                c.executemany("INSERT INTO events(blob) VALUES(?)",
                              ((payload,) for _ in range(11000)))
                c.commit()
            original=db.read_bytes()
            stop=threading.Event()
            def writer():
                with sqlite3.connect(db,timeout=3) as c:
                    while not stop.is_set():
                        c.execute("INSERT INTO events(blob) VALUES(?)",(b'writer',))
                        c.commit()
                        time.sleep(.002)
            runner=threading.Thread(target=writer,daemon=True)
            runner.start()
            reports=[]
            try:
                def on_progress(done,total,status):
                    reports.append((done,total,status))
                    if len(reports)%5==0:time.sleep(.001)
                results=_db_snapshot(db,dest,on_progress=on_progress)
            finally:
                stop.set()
                runner.join(timeout=3)
            self.assertGreaterEqual(results["events"],11000)
            self.assertGreater(len(reports),2)
            self.assertEqual(len({x[1] for x in reports}),1)
            self.assertEqual([x[0] for x in reports],sorted(x[0] for x in reports))
            total=reports[0][1]
            self.assertGreater(total,10)
            self.assertEqual(reports[-1],(total,total,101))
            self.assertFalse(any(d==total for d,t,status in reports[:-1]))
            with sqlite3.connect(dest) as c:
                self.assertEqual(c.execute("PRAGMA quick_check").fetchone()[0],"ok")
            self.assertEqual(db.read_bytes()[:64],original[:64])

    def test_fails_closed_without_making_original_mutable(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/"bad.sqlite3"
            source.write_bytes(b"not a SQLite database"*40)
            out=Path(folder)/"copy.sqlite3"
            with self.assertRaises((RuntimeError,sqlite3.DatabaseError)):
                _db_snapshot(source,out)
            self.assertEqual(source.read_bytes(),b"not a SQLite database"*40)

class AndroidExportWiring(unittest.TestCase):
    def test_picker_scope_survives_orientation(self):
        source=(ROOT/"java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text()
        self.assertIn('artitrade_picker_scope_v088',source)
        self.assertIn('putString("errors",scope).commit()',source)
        self.assertIn('scope=picker.getString("errors",null)',source)
        self.assertIn('putString("research",scope).commit()',source)

    def test_bundler_uses_compressed_planner_only_for_error_mode(self):
        code=(ROOT/"python/android_research_bundle.py").read_text()
        self.assertIn('if bundle_kind=="errors":',code)
        self.assertIn('_put_compressed_error_item(pieces,used,capacity,record,',code)
        self.assertNotIn('total*2+MIN_FREE',code)
        self.assertIn('total+MIN_FREE',code)

if __name__=="__main__":unittest.main()
