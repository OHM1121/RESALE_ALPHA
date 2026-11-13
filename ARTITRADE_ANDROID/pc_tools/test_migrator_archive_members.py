#!/usr/bin/env python3
"""Regression: PC ZIP must exclude SQLite -shm/-wal even when stage contains them."""
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import artitrade_ledger_migrator as migration


class TestArchiveMembers(unittest.TestCase):
    def test_transient_sqlite_sidecars_never_enter_pc_migration_zip(self):
        with tempfile.TemporaryDirectory(prefix="artitrade_zip_test_") as folder:
            base = Path(folder)
            source_files = {}
            names = (
                "stock/ARTITRADE_D.sqlite3",
                "stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3",
                "coin/base/upbit_paper.sqlite3",
                "coin/ma/upbit_paper.sqlite3",
            )
            for name in names:
                original = base / "pc" / name
                original.parent.mkdir(parents=True, exist_ok=True)
                original.write_bytes(b"fake-original-for-isolated-zip-test")
                source_files[name] = (original, True)

            def fake_sqlite_snapshot(source, target):
                target.write_bytes(b"self-contained-snapshot")
                (target.parent / (target.name + "-shm")).write_bytes(b"transient-shm")
                (target.parent / (target.name + "-wal")).write_bytes(b"transient-wal")

            with patch.object(migration, "source_map", return_value=source_files), \
                 patch.object(migration, "stock_root", return_value=base / "pc" / "stock"), \
                 patch.object(migration, "coin_root", return_value=base / "pc" / "coin"), \
                 patch.object(migration, "sqlite_snapshot", side_effect=fake_sqlite_snapshot), \
                 patch.object(migration, "sqlite_counts", return_value={"trades": 1}):
                archive, manifest = migration.build_archive()
                try:
                    with zipfile.ZipFile(archive, "r") as packed:
                        self.assertEqual(set(packed.namelist()), {"manifest.json", *names})
                    self.assertEqual(set(manifest["files"]), set(names))
                finally:
                    shutil.rmtree(archive.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
