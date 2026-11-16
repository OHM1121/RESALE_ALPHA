"""Offline regression tests for 250MB+ migrations. No real user files/network accessed."""
import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import artitrade_ledger_migrator as app


class LargeTransferTests(unittest.TestCase):
    def test_size_limit_extended_consistently(self):
        self.assertGreater(app.MAX_ARCHIVE, 250 * 1024 * 1024)
        self.assertEqual(app.MAX_ARCHIVE, 4 * 1024 ** 3)
        self.assertEqual(app.UPLOAD_CHUNK, 1_000_000)

    def test_sparse_270_megabyte_file_uploads_in_bounded_chunks(self):
        with tempfile.TemporaryDirectory() as dirname:
            source = Path(dirname) / "large.zip"
            test_size = 270 * 1024 * 1024 + 123
            with source.open("wb") as out:
                out.seek(test_size - 1)
                out.write(b"x")
            calls = {"begin": None, "count": 0, "commit": False}
            def fake_request(base, token, method, endpoint, payload=None, **kwargs):
                if endpoint.endswith("/begin"):
                    calls["begin"] = payload
                    return {"ok": True}
                if endpoint.endswith("/chunk"):
                    i = calls["count"]
                    self.assertEqual(payload["index"], i)
                    self.assertEqual(payload["session"], calls["begin"]["session"])
                    encoded = payload["data_b64"]
                    self.assertLessEqual(len(encoded), 1_400_000)
                    chunk = base64.b64decode(encoded)
                    self.assertLessEqual(len(chunk), app.UPLOAD_CHUNK)
                    self.assertEqual(hashlib.sha256(chunk).hexdigest(), payload["sha256"])
                    calls["count"] += 1
                    return {"ok": True}
                if endpoint.endswith("/commit"):
                    self.assertEqual(payload["session"], calls["begin"]["session"])
                    calls["commit"] = True
                    return {"ok": True}
                raise AssertionError(endpoint)
            with patch.object(app, "request", side_effect=fake_request):
                result = app.upload_archive("http://100.77.12.4:8765", "a"*64, source,
                                            {"archive_sha256":"b"*64,"files":{}}, lambda msg: None)
            self.assertTrue(result["ok"])
            self.assertTrue(calls["commit"])
            self.assertEqual(calls["begin"]["source"]["chunk_bytes"], 1_000_000)
            self.assertEqual(calls["begin"]["total_bytes"], test_size)
            self.assertEqual(calls["count"], (test_size + app.UPLOAD_CHUNK-1)//app.UPLOAD_CHUNK)

    def test_failed_db_snapshot_cleans_large_staging_tree(self):
        with tempfile.TemporaryDirectory() as dirname:
            parent = Path(dirname)
            source = parent / "tiny.sqlite3"
            source.write_bytes(b"not a database")
            work = parent / "artitrade_pc_ledger_failure_test"
            work.mkdir()
            source_map = {
                "stock/ARTITRADE_D.sqlite3": (source, True),
                "stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3": (source, True),
                "coin/base/upbit_paper.sqlite3": (source, True),
                "coin/ma/upbit_paper.sqlite3": (source, True),
            }
            with patch.object(app, "source_map", return_value=source_map), \
                 patch.object(app, "sqlite_snapshot", side_effect=OSError("test failure")), \
                 patch.object(app.tempfile, "mkdtemp", return_value=str(work)):
                with self.assertRaisesRegex(OSError, "test failure"):
                    app.build_archive()
            self.assertFalse(work.exists())


if __name__ == "__main__":
    unittest.main()
