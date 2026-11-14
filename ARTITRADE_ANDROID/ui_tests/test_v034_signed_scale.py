"""v034 regression: both stock and coin retain the signed -100..+100 market scale."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
import market_research_v033 as m


def coin_data(sign):
    ticks=[{
        "market":f"KRW-COIN{i}",
        "signed_change_rate":(0.03 if sign>0 else -0.03),
        "acc_trade_price_24h":float(1000000+i)}
        for i in range(30)]
    def prices(asset):
        return [{"trade_price":1000*(1+sign*(0.015 if asset=="btc" else 0.025)*i)}
                for i in range(34,-1,-1)]
    return ticks,prices("btc"),prices("eth")


class SignedMarketScale(unittest.TestCase):
    def test_stock_exe_raw_score_not_remapped_to_0_100(self):
        strong=m.stock_score({"KOSPI":1.5,"KOSDAQ":2.0,"NASDAQ":1.8},{})
        weak=m.stock_score({"KOSPI":-1.5,"KOSDAQ":-2.0,"NASDAQ":-1.8},{})
        flat=m.stock_score({"KOSPI":0,"KOSDAQ":0},{})
        self.assertEqual(strong["score"],100)
        self.assertEqual(weak["score"],-100)
        self.assertEqual(flat["score"],0)
        self.assertEqual(strong["score"],strong["raw_score"])
        self.assertEqual(weak["score"],weak["raw_score"])

    def test_coin_uses_same_signed_range_with_signed_component_impacts(self):
        high=m.coin_score(*coin_data(1))
        low=m.coin_score(*coin_data(-1))
        self.assertGreater(high["score"],0)
        self.assertLess(low["score"],0)
        self.assertTrue(all(-100<=f["impact"]<=100 for f in high["factors"]))
        self.assertTrue(all(-100<=f["impact"]<=100 for f in low["factors"]))
        self.assertAlmostEqual(sum(f["impact"]*f["weight"] for f in high["factors"]),
            high["score"],places=1,msg="weighted signed subscore must equal market score")

    def test_negative_bands_preserve_positive_55_and_20_examples(self):
        for score,band in ((-100,"-100~-81"),(-80,"-80~-61"),
            (-50,"-60~-41"),(-20,"-20~-1"),(-0.1,"-20~-1"),
            (0,"0~19"),(20,"20~39"),(50,"40~59"),
            (55,"40~59"),(75,"60~79"),(100,"80~100")):
            self.assertEqual(m.market_band(score),band,msg=f"wrong band for {score}")
        for score in (-100.1,100.1,float("nan")):
            self.assertIsNone(m.market_band(score))

    def test_old_v033_score_rows_migrate_once_not_rewrite_holding_baselines(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"market_research_v033.sqlite3"
            with sqlite3.connect(path) as c:
                c.executescript("""
                    CREATE TABLE score_events(market TEXT,ts INTEGER,score REAL,
                        band TEXT,meta TEXT,PRIMARY KEY(market,ts));
                    CREATE TABLE cohort_segments(id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market TEXT,band TEXT,start_ts INTEGER,end_ts INTEGER);
                    CREATE TABLE cohort_positions(segment_id INTEGER,identity TEXT,
                        start_return REAL,latest_return REAL,last_ts INTEGER,
                        PRIMARY KEY(segment_id,identity));
                    INSERT INTO score_events VALUES('stock',10000,70,'61~70',
                        '{"raw_score":40,"status":"정상"}');
                    INSERT INTO score_events VALUES('coin',10001,10,'0~50',
                        '{"status":"관측 완료"}');
                    INSERT INTO cohort_segments(id,market,band,start_ts) VALUES
                        (1,'stock','61~70',10000),(2,'coin','0~50',10001);
                    INSERT INTO cohort_positions VALUES
                        (1,'stock/at/5m/7',-4.0,2.0,10020),
                        (2,'coin/general/5m/8',-3.0,-1.0,10020);
                    PRAGMA user_version=33;
                """)
            ledger=m.ScoreLedger(path)
            self.assertEqual(ledger.score_at("stock",10020),40)
            self.assertEqual(ledger.score_at("coin",10020),-80)
            self.assertEqual(ledger.snapshot("stock")["active_band"],"40~59")
            self.assertEqual(ledger.snapshot("coin")["active_band"],"-80~-61")
            self.assertEqual(ledger.snapshot("stock")["active_avg_change_pp"],6)
            self.assertEqual(ledger.snapshot("coin")["active_avg_change_pp"],2)
            ledger.close()
            second=m.ScoreLedger(path)
            self.assertEqual(second.score_at("coin",10020),-80)
            self.assertEqual(second.score_at("stock",10020),40)
            self.assertEqual(second.conn.execute("PRAGMA user_version").fetchone()[0],35)
            second.close()

    def test_fresh_scores_keep_minus_100_and_signed_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger=m.ScoreLedger(Path(folder)/"market.sqlite3")
            self.assertTrue(ledger.record_score("coin",-88,10000,{"coin/general/5m/1":-4},{}))
            self.assertEqual(ledger.snapshot("coin")["score"],-88)
            self.assertEqual(ledger.snapshot("coin")["active_band"],"-100~-81")
            ledger.close()


if __name__=="__main__":unittest.main()
