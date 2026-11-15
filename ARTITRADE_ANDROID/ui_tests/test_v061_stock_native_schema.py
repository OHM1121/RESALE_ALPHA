"""v061 integration on the real frozen KRX MarketStore and MA SQLite schemas."""
import datetime as dt
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo
BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE/'app/src/main/python'))
import ma_v061_stock_runtime as patched
from d_storage import DStore
import stock_ma_engine as stock


class NativeStockSQLite(unittest.TestCase):
    def test_150_real_sqlite_rows_new_downbear_without_record_volume(self):
        patched.install()
        with tempfile.TemporaryDirectory() as root:
            r=Path(root)/'stock'
            market=DStore(r)
            first=dt.datetime(2026,11,6,9,0,tzinfo=ZoneInfo('Asia/Seoul'))
            records=[]
            for i in range(145):
                start=first+dt.timedelta(minutes=30*i)
                closing=30000-i*10
                opening=closing+15
                records.append(('005930','KOSPI','30m',
                   start.strftime('%Y%m%d%H%M%S'),opening,opening+5,
                   closing-5,closing,100,'UNIT_TEST_FIXED'))
            with market.connect() as c:
                c.executemany('''INSERT INTO bars(
                    code,market,frame,ts,open,high,low,close,volume,source)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''',records)
            engine=stock.StockMAService(r,market)
            ref=records[-1][3]
            engine.scan_completed({'30m':ref},{'005930':('KOSPI','삼성전자')})
            with stock._connect(engine.db) as c:
                candidate=c.execute('SELECT * FROM candidates').fetchone()
                self.assertIsNotNone(candidate)
                self.assertEqual(candidate['ref_ts'],ref)
                self.assertEqual(candidate['limit_price'],records[-1][6])
                self.assertEqual(candidate['status'],'PENDING')
                import json
                facts=json.loads(candidate['features'])
                self.assertEqual(facts['ma_v061_kind'],'DOWN_BEAR')
                self.assertEqual(facts['ma_v061_signal']['ma_down'],5)
                self.assertEqual(facts['ma_v061_signal']['candle'],'BEAR')
                self.assertEqual(candidate['special'],0)


if __name__=='__main__':unittest.main()
