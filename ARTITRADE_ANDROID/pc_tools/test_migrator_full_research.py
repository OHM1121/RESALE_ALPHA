import importlib.util
import json
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

MODULE=Path(__file__).with_name("artitrade_ledger_migrator.py")
spec=importlib.util.spec_from_file_location("artitrade_migrator_under_test",MODULE)
app=importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

class CompleteResearchImportTest(unittest.TestCase):
    def test_extra_research_and_gmo_state_are_in_same_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            stock=root/"stock";coin=root/"coin"
            stock.mkdir();coin.mkdir()
            # Four required source databases, plus GMO originals and free-form research.
            for rel in ("ARTITRADE_D.sqlite3","STOCK_MA_ENGINE_V1/ma_paper.sqlite3",
                        "AT_CLOSE_PAPER.sqlite3","PROJECTGMO_V1.0_research.sqlite3"):
                path=stock/rel;path.parent.mkdir(parents=True,exist_ok=True)
                import sqlite3
                with closing(sqlite3.connect(path)) as db:
                    db.execute("CREATE TABLE candidate_events(id INTEGER)")
            for rel in ("upbit_paper.sqlite3","MA_ENGINE_V1/upbit_paper.sqlite3"):
                path=coin/rel;path.parent.mkdir(parents=True,exist_ok=True)
                import sqlite3
                with closing(sqlite3.connect(path)) as db:db.execute("CREATE TABLE candidate_events(id INTEGER)")
            (stock/"PROJECTGMO_V1.0_account_state.json").write_text(
                '{"positions":{},"fills":[],"orders":{}}')
            (stock/"research_history.jsonl").write_text('{"signal_id":123}\n')
            (coin/"MA_ENGINE_V1"/"research_score.csv").write_text("tf,score\n5m,80\n")
            (stock/"credentials.dpapi.json").write_text('{"secret":"private"}')
            (coin/"api_token.json").write_text('{"token":"secret"}')
            (stock/"ARTITRADE_D.sqlite3-wal").write_bytes(b"transient")
            old_stock,old_coin=app.stock_root,app.coin_root
            try:
                app.stock_root=lambda:stock
                app.coin_root=lambda:coin
                names=app.source_map()
                self.assertIn("stock/research_history.jsonl",names)
                self.assertIn("coin/ma/research_score.csv",names)
                self.assertIn("stock/PROJECTGMO_V1.0_account_state.json",names)
                self.assertNotIn("stock/credentials.dpapi.json",names)
                self.assertNotIn("coin/base/api_token.json",names)
                self.assertNotIn("stock/ARTITRADE_D.sqlite3-wal",names)
                archive,manifest=app.build_archive()
                try:
                    with zipfile.ZipFile(archive) as z:
                        contents=set(z.namelist())
                        self.assertIn("stock/research_history.jsonl",contents)
                        self.assertIn("stock/PROJECTGMO_V1.0_account_state.json",contents)
                        self.assertIn("stock/PROJECTGMO_V1.0_research.sqlite3",contents)
                        self.assertNotIn("stock/credentials.dpapi.json",contents)
                    self.assertEqual(manifest["files"]["stock/research_history.jsonl"]["size"],(stock/"research_history.jsonl").stat().st_size)
                finally:
                    import shutil
                    shutil.rmtree(archive.parent,ignore_errors=True)
            finally:
                app.stock_root,app.coin_root=old_stock,old_coin

if __name__=="__main__":unittest.main()
