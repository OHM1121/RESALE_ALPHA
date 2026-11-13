"""Stress-check WAL-safe phone export, manifest SHA and forbidden secrets."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

SOURCE=Path(__file__).parents[1]/"app/src/main/python/android_ledger_backup.py"
spec=importlib.util.spec_from_file_location("artitrade_backup_under_test",SOURCE)
backup=importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class PhoneLedgerExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="artitrade_phone_export_")
        base=Path(self.tmp.name)
        self.root=base/"phone"
        self.root.mkdir()
        self.export=base/"full_ledger.zip"
        stock=self.root/"stock"
        coin=self.root/"coin/base"
        stock.mkdir(parents=True)
        coin.mkdir(parents=True)
        self.db=stock/"ARTITRADE_D.sqlite3"
        self.writer=sqlite3.connect(self.db)
        self.writer.execute("PRAGMA journal_mode=WAL")
        self.writer.execute("PRAGMA wal_autocheckpoint=0")
        self.writer.execute("CREATE TABLE candidate_snapshots(id INTEGER PRIMARY KEY)")
        self.writer.executemany("INSERT INTO candidate_snapshots(id) VALUES(?)",
                                [(i,) for i in range(1,21)])
        self.writer.commit()
        with sqlite3.connect(coin/"upbit_paper.sqlite3") as c:
            c.execute("CREATE TABLE candidates(id INTEGER)")
            c.execute("INSERT INTO candidates VALUES(7)")
        (stock/"PROJECTGMO_V1.0_account_state.json").write_text(json.dumps(
            {"positions":{"005930":{"qty":2}},"fills":[{"code":"005930"}]},
            ensure_ascii=False),encoding="utf8")
        (stock/"research_log.jsonl").write_text('{"code":"005930"}\n')
        (stock/"api_token.json").write_text('{"secret":"do-not-export"}')
        (stock/"credential_cache.json").write_text('{"key":"do-not-export"}')
        (stock/"AT_CLOSE_PAPER.sqlite3.pre_pc_import_20261113").write_text(
            "should-not-be-copied")

    def tearDown(self):
        self.writer.close()
        self.tmp.cleanup()

    def test_export_live_wal_contains_all_committed_rows_and_no_secrets(self):
        for i in range(8):
            rows=20+i
            out=json.loads(backup.make_backup(str(self.root),str(self.export)))
            self.assertTrue(out["ok"])
            self.assertGreaterEqual(out["file_count"],4)
            self.assertTrue(backup.validate_backup(str(self.export))["ok"])
            with zipfile.ZipFile(self.export) as z:
                manifest=json.loads(z.read("manifest.json"))
                self.assertEqual(manifest["schema"],backup.SCHEMA)
                files=manifest["strategy_files"]
                self.assertIn("stock/ARTITRADE_D.sqlite3",files)
                self.assertIn("coin/base/upbit_paper.sqlite3",files)
                self.assertIn("stock/PROJECTGMO_V1.0_account_state.json",files)
                self.assertIn("stock/research_log.jsonl",files)
                self.assertNotIn("stock/api_token.json",files)
                self.assertNotIn("stock/credential_cache.json",files)
                self.assertEqual(files["stock/ARTITRADE_D.sqlite3"]["tables"]
                                 ["candidate_snapshots"],rows)
                scratch=Path(self.tmp.name)/"snapshot.sqlite3"
                scratch.write_bytes(z.read("stock/ARTITRADE_D.sqlite3"))
                with sqlite3.connect(scratch) as con:
                    actual=con.execute("SELECT COUNT(*) FROM candidate_snapshots").fetchone()[0]
                self.assertEqual(actual,rows)
                self.assertNotIn(b"do-not-export",self.export.read_bytes())
            self.writer.execute("INSERT INTO candidate_snapshots(id) VALUES(?)",
                                (rows+1,))
            self.writer.commit()
        self.assertTrue(self.db.exists())

    def test_missing_manifest_member_and_corrupted_member_fail_closed(self):
        backup.make_backup(str(self.root),str(self.export))
        bad=Path(self.tmp.name)/"damaged.zip"
        with zipfile.ZipFile(self.export) as original,zipfile.ZipFile(bad,"w") as forged:
            for name in original.namelist():
                blob=original.read(name)
                if name=="stock/research_log.jsonl":
                    blob+=b"unaccounted"
                forged.writestr(name,blob)
        with self.assertRaisesRegex(ValueError,"SHA mismatch"):
            backup.validate_backup(str(bad))
        with zipfile.ZipFile(bad,"a") as z:
            z.writestr("stock/undocumented.csv","x")
        with self.assertRaisesRegex(ValueError,"do not match"):
            backup.validate_backup(str(bad))

    def test_invalid_gmo_json_preserves_previous_complete_backup(self):
        before=json.loads(backup.make_backup(str(self.root),str(self.export)))
        old=self.export.read_bytes()
        (self.root/"stock/PROJECTGMO_V1.0_account_state.json").write_text("{truncated")
        with self.assertRaisesRegex(ValueError,"GMO account-state"):
            backup.make_backup(str(self.root),str(self.export))
        self.assertEqual(self.export.read_bytes(),old)
        self.assertEqual(backup._sha256(self.export),before["sha256"])
        self.assertFalse(Path(str(self.export)+".partial").exists())


if __name__=="__main__":unittest.main()
