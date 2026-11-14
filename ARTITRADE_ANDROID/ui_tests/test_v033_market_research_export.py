"""Full program research ZIP must include the causal market-score sidecar."""
import json,sqlite3,tempfile,unittest,zipfile,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'app/src/main/python'))
from android_research_bundle import make_bundle

class MarketBundle(unittest.TestCase):
 def test_whole_program_export_contains_stored_market_score(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'phone';(root/'stock').mkdir(parents=True)
   with sqlite3.connect(root/'stock'/'ARTITRADE_D.sqlite3') as c:
    c.execute('CREATE TABLE positions(id INTEGER)')
   with sqlite3.connect(root/'market_research_v033.sqlite3') as c:
    c.execute('CREATE TABLE score_events(market TEXT,ts INTEGER,score REAL)')
    c.execute("INSERT INTO score_events VALUES('stock',100,55)")
   out=Path(d)/'out';out.mkdir()
   export=json.loads(make_bundle(str(root),str(out),'all',200000))
   self.assertTrue(export['ok'])
   with zipfile.ZipFile(export['files'][0]['path']) as z:
    index=json.loads(z.read('analysis/bundle_index.json'))
   self.assertIn('market_research_v033.sqlite3',index['sources'])
if __name__=='__main__':unittest.main()
