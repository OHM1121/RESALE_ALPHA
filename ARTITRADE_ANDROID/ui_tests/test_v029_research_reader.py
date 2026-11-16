"""Regression: five EXE research records must read original SQLite without changing it."""
import sqlite3
import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "app" / "src" / "main" / "python"))
from android_sqlite_reader import db_rows


class OriginalReadOnlySqlite(unittest.TestCase):
    def test_all_original_rows_are_visible_with_alias_and_filters(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'paper.sqlite3'
            with sqlite3.connect(db) as con:
                con.execute('CREATE TABLE candidates(id INTEGER PRIMARY KEY, tf TEXT, code TEXT, score REAL)')
                con.executemany('INSERT INTO candidates VALUES(?,?,?,?)', [
                    (1, '5m', '005930', 61.5), (2, '30m', '000660', 47.0)])
            rows = db_rows(db, 'SELECT id,code,score FROM candidates WHERE tf=? ORDER BY id', ('5m',))
            self.assertEqual(rows, [{'id':1,'code':'005930','score':61.5}])
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM candidates').fetchone()[0], 2)

    def test_missing_db_or_optional_table_does_not_create_or_block_other_strategy(self):
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d)/'missing.sqlite3'
            self.assertEqual(db_rows(missing, 'SELECT * FROM candidates'), [])
            self.assertFalse(missing.exists())
            db = Path(d)/'partial.sqlite3'
            with sqlite3.connect(db) as con:
                con.execute('CREATE TABLE research(id INTEGER, verdict TEXT)')
                con.execute("INSERT INTO research VALUES(1,'QUALIFIED')")
            self.assertEqual(db_rows(db, 'SELECT * FROM candidates'), [])
            self.assertEqual(db_rows(db, 'SELECT * FROM research')[0]['verdict'], 'QUALIFIED')

    def test_write_query_rejected_and_missing_column_reported(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'legacy.sqlite3'
            with sqlite3.connect(db) as con:
                con.execute('CREATE TABLE research(id INTEGER, verdict TEXT)')
                con.execute("INSERT INTO research VALUES(1,'QUALIFIED')")
            with self.assertRaises(ValueError):
                db_rows(db, "DELETE FROM research")
            with self.assertRaises(sqlite3.OperationalError):
                db_rows(db, "SELECT mfe_pct FROM research")
            self.assertEqual(db_rows(db, 'SELECT * FROM research')[0]['id'], 1)

if __name__=='__main__': unittest.main()
