"""Scope isolation, 500MB-equivalent hard limit and lossless multipart restore."""
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/"app/src/main/python"))
sys.path.insert(0,str(Path(__file__).parents[1]/"pc_tools"))
from android_research_bundle import make_bundle
from artitrade_research_restore import restore_bundle


class ResearchBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name)
        self.phone=self.base/"phone"
        self.phone.mkdir()
        for rel in ("stock/ARTITRADE_D.sqlite3","coin/base/upbit_paper.sqlite3",
                    "coin/ma/upbit_paper.sqlite3"):
            path=self.phone/rel
            path.parent.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(path) as c:
                c.execute("CREATE TABLE candidates(id INTEGER PRIMARY KEY,reason TEXT)")
                c.executemany("INSERT INTO candidates(reason) VALUES(?)",
                              [("UNSELECTED_"+str(i)+"_"+"x"*1000,) for i in range(25)])
        (self.phone/"stock"/"large_research.jsonl").write_bytes(b"R"*210000)
        self.output=self.base/"parts";self.output.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def generate(self,scope,limit=500000):
        payload=json.loads(make_bundle(str(self.phone),str(self.output),scope,limit))
        self.assertTrue(payload["ok"])
        return payload

    def test_stock_coin_all_include_only_their_source_dbs(self):
        for scope in ("stock","coin","all"):
            result=self.generate(scope)
            index=None
            for p in result["files"]:
                self.assertLessEqual(p["bytes"],500000)
                with zipfile.ZipFile(p["path"]) as z:
                    self.assertIsNone(z.testzip())
                    current=json.loads(z.read("analysis/bundle_index.json"))
                    if index is None:index=current
                    self.assertEqual(current,index)
            names=index["sources"]
            self.assertTrue(names)
            if scope=="stock":self.assertTrue(all(x.startswith("stock/") for x in names))
            if scope=="coin":self.assertTrue(all(x.startswith("coin/") for x in names))
            if scope=="all":
                self.assertTrue(any(x.startswith("stock/") for x in names))
                self.assertTrue(any(x.startswith("coin/") for x in names))

    def test_large_file_split_without_truncation_and_restore_sha(self):
        result=self.generate("stock",max_part_bytes:=60000)
        self.assertGreater(result["parts"],1)
        for file in result["files"]:
            self.assertLessEqual(file["bytes"],max_part_bytes)
        dest=self.base/"reassembled"
        out=restore_bundle([Path(f["path"]) for f in result["files"]],dest)
        self.assertTrue(out["ok"])
        self.assertEqual((dest/"stock/large_research.jsonl").read_bytes(),
                         (self.phone/"stock/large_research.jsonl").read_bytes())
        db=dest/"stock/ARTITRADE_D.sqlite3"
        with sqlite3.connect(db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM candidates").fetchone()[0],25)
        with self.assertRaises(FileExistsError):
            restore_bundle([Path(f["path"]) for f in result["files"]],dest)

    def test_missing_part_refuses_partial_restore(self):
        result=self.generate("stock",60000)
        with self.assertRaises(ValueError):
            restore_bundle([Path(f["path"]) for f in result["files"][:-1]],
                           self.base/"missing_restore")

if __name__=='__main__': unittest.main()
