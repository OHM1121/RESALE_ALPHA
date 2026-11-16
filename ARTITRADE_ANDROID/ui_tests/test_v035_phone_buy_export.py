"""The full research archive must retain immutable phone BUY provenance too."""
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/"app/src/main/python"))
from android_research_bundle import make_bundle

class PhoneBuyEvidenceExport(unittest.TestCase):
    def test_whole_program_zip_contains_original_mobile_buy_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"phone"
            (root/"stock").mkdir(parents=True)
            with sqlite3.connect(root/"stock"/"ARTITRADE_D.sqlite3") as c:
                c.execute("CREATE TABLE positions(id INTEGER)")
            with sqlite3.connect(root/"phone_buy_evidence_v035.sqlite3") as c:
                c.execute("CREATE TABLE buy_evidence(position_id TEXT,buy_reason TEXT)")
                c.execute("INSERT INTO buy_evidence VALUES('7','EXACT_PHONE_FILL')")
            output=Path(tmp)/"export"
            output.mkdir()
            result=json.loads(make_bundle(str(root),str(output),"all",250000))
            index=json.loads(zipfile.ZipFile(result["files"][0]["path"]).read(
                "analysis/bundle_index.json"))
            self.assertIn("phone_buy_evidence_v035.sqlite3",index["sources"])
if __name__=="__main__":unittest.main()
