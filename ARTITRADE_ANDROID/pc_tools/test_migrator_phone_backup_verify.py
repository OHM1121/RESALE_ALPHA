"""PC EXE can verify the Android's complete read-only SQLite-backed export."""
import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

PC=Path(__file__).with_name("artitrade_ledger_migrator.py")
spec=importlib.util.spec_from_file_location("pc_backup_verify_app",PC)
app=importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class PhoneBackupVerificationTests(unittest.TestCase):
    def make_file(self,archive,change=False):
        name="stock/ARTITRADE_D.sqlite3"
        content=b"PHONE_LEDGER_TEST"
        meta={"schema":"ARTITRADE_PHONE_LEDGER_BACKUP_V1",
              "strategy_files":{
                  name:{"sha256":hashlib.sha256(content).hexdigest(),
                        "size":len(content),"kind":"sqlite"}
              }}
        with zipfile.ZipFile(archive,"w") as z:
            z.writestr("manifest.json",json.dumps(meta))
            z.writestr(name,content+(b"!" if change else b""))
        return name

    def test_full_backup_sha_roundtrip_and_corruption(self):
        with tempfile.TemporaryDirectory() as root:
            zip_path=Path(root)/"phone.zip"
            self.make_file(zip_path)
            result=app.verify_phone_backup(zip_path)
            self.assertTrue(result["ok"])
            self.assertEqual(result["file_count"],1)
            self.assertEqual(result["sqlite_count"],1)
            self.assertEqual(result["sha256"],app.sha256_file(zip_path))
            self.make_file(zip_path,change=True)
            with self.assertRaisesRegex(ValueError,"SHA-256"):
                app.verify_phone_backup(zip_path)

    def test_extra_member_and_path_traversal_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            zip_path=Path(root)/"phone.zip"
            self.make_file(zip_path)
            with zipfile.ZipFile(zip_path,"a") as z:
                z.writestr("stock/undocumented.csv",b"x")
            with self.assertRaisesRegex(ValueError,"파일목록"):
                app.verify_phone_backup(zip_path)
            name="../outside.db";blob=b"x"
            meta={"schema":"ARTITRADE_PHONE_LEDGER_BACKUP_V1",
                  "strategy_files":{name:{"sha256":hashlib.sha256(blob).hexdigest(),
                                          "size":len(blob),"kind":"sqlite"}}}
            with zipfile.ZipFile(zip_path,"w") as z:
                z.writestr("manifest.json",json.dumps(meta))
                z.writestr(name,blob)
            with self.assertRaisesRegex(ValueError,"안전하지 않은"):
                app.verify_phone_backup(zip_path)


if __name__=="__main__":unittest.main()
