"""Synthetic native SQLite accounting tests; never touch private phone data."""
from __future__ import annotations
import datetime as dt
import sqlite3
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app/src/main/python"))
from paper_exit_replay_v019 import replay,GUARDS
from paper_exit_native_ledger_v019 import apply_inside_transaction,UnsafeRecovery
import new_legacy_1118_resting as resting

BUY="2026-11-19T09:00:00+09:00"
def source(owner,limit1=104,limit2=108,qty1=30,qty2=35,original=100,remaining=100):
    is_stock=owner.startswith("stock")
    return {"owner":owner,"epoch":"SYNTHETIC_TEST_EPOCH",
        "position_id":11,"mode":"PAPER","status":"OPEN",
        "venue":"KRX" if is_stock else "UPBIT",
        "original_buy_receipt":"1","buy_at":BUY,"buy_price":100,
        "original_qty":original,"remaining_qty":remaining,
        "broker_order_present":False,"original_full_exit_seen":False,
        "stages":[
            {"stage":1,"limit":limit1,"qty":qty1,"filled_qty":0,
             "original_qty":original,"policy_source":"ORIGINAL_NATIVE_BUY_PLAN",
             "created_at":BUY,"broker_order_id":""},
            *([] if owner=="stock/cw" else [{
             "stage":2,"limit":limit2,"qty":qty2,"filled_qty":0,
             "original_qty":original,"policy_source":"ORIGINAL_NATIVE_BUY_PLAN",
             "created_at":BUY,"broker_order_id":""}])
        ]}

def tape(p,prices):
    start=dt.datetime.fromisoformat(BUY)
    return [{"owner":p["owner"],"epoch":p["epoch"],
       "position_id":p["position_id"],"venue":p["venue"],
       "sequence":i+1,"ts":(start+dt.timedelta(seconds=20+i*30)).isoformat(),
       "original_feed_event_id":"testtick-"+str(i+1),
       "kind":"TRADE","price":px,"source":"AUTHENTICATED_EXCHANGE_TICK",
       "native_protections_checked":list(GUARDS[p["owner"]]),
       "native_stop_triggered":False,"limit_order_eligible":True,
       "original_stop_limit":85} for i,px in enumerate(prices)]

class NativeOwnerWriters(unittest.TestCase):
    def coin(self):
        c=sqlite3.connect(":memory:",isolation_level=None)
        c.row_factory=sqlite3.Row
        c.executescript("""
          CREATE TABLE wallets(tf TEXT PRIMARY KEY,cash INTEGER,realized INTEGER,seed INTEGER);
          CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,market TEXT,name TEXT,
              entry_at TEXT,entry TEXT,qty0 TEXT,qty TEXT,entry_fee INTEGER,
              first_qty TEXT,first_sold_qty TEXT,mark TEXT,status TEXT);
          CREATE TABLE executions(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT UNIQUE,
              position_id INTEGER,tf TEXT,market TEXT,at TEXT,side TEXT,
              qty TEXT,price TEXT,fees INTEGER,tax INTEGER,net_pnl INTEGER,
              reason TEXT,cash_after INTEGER);
          INSERT INTO wallets VALUES('5m',100000,0,200000);
          INSERT INTO positions VALUES(11,'5m','KRW-AAA','synthetic', 
              '2026-11-19T09:00:00+09:00','100','100','100',5,'30','0','100','OPEN');
          INSERT INTO executions(event_key,position_id,tf,market,at,side,qty,
              price,fees,tax,net_pnl,reason,cash_after)
              VALUES('ORIGINAL_BUY',11,'5m','KRW-AAA','2026-11-19T09:00:00+09:00',
                  'BUY','100','100',5,0,0,'NATIVE_BUY',100000);
        """)
        resting.place(c,11,"coin","KRW-AAA","5m",BUY,100,104,108,30,35,True)
        return c

    def cw(self):
        c=sqlite3.connect(":memory:",isolation_level=None)
        c.row_factory=sqlite3.Row
        c.executescript("""
          CREATE TABLE wallets(strategy TEXT PRIMARY KEY,cash INTEGER,realized INTEGER,updated_at TEXT);
          CREATE TABLE positions(id INTEGER PRIMARY KEY,status TEXT,strategy TEXT,
             entry_ts TEXT,entry_price INTEGER,entry_qty INTEGER,qty INTEGER,
             entry_fee INTEGER,code TEXT,name TEXT,first_sold INTEGER,
             closed_ts TEXT,exit_reason TEXT,last_price INTEGER);
          CREATE TABLE executions(id INTEGER PRIMARY KEY AUTOINCREMENT,
             ts TEXT,strategy TEXT,position_id INTEGER,code TEXT,name TEXT,
             side TEXT,qty INTEGER,price INTEGER,fee INTEGER,tax INTEGER,
             cash_after INTEGER,reason TEXT,net_realized INTEGER);
          INSERT INTO wallets VALUES('CLOSE',100000,0,'2026-11-19T09:00:00+09:00');
          INSERT INTO positions VALUES(11,'OPEN','CLOSE','2026-11-19T09:00:00+09:00',
             100,100,100,15,'005930','synthetic',0,NULL,NULL,100);
          INSERT INTO executions(ts,strategy,position_id,code,name,side,qty,price,
             fee,tax,cash_after,reason) VALUES('2026-11-19T09:00:00+09:00',
             'CLOSE',11,'005930','synthetic','BUY',100,100,15,0,100000,'ORIGINAL_BUY');
        """)
        return c

    def test_coin_original_resting_cash_pnl_and_idempotence(self):
        c=self.coin()
        p=source("coin/general")
        events=tape(p,[106,110])
        proof=replay(p,events)
        self.assertEqual(proof["status"],"PAPER_ASSUMPTION_CANDIDATES_VERIFIED")
        obs={"testtick-1":106,"testtick-2":110}
        c.execute("BEGIN IMMEDIATE")
        got=apply_inside_transaction(c,p["owner"],proof,p["epoch"],obs)
        c.commit()
        self.assertEqual(got["receipt_count"],2)
        rows=c.execute("SELECT qty,price,fees,net_pnl,reason FROM executions WHERE side='SELL' ORDER BY id").fetchall()
        self.assertEqual([str(r["qty"]) for r in rows],["30","35"])
        self.assertEqual([str(r["price"]) for r in rows],["106","110"])
        self.assertTrue(all("PAPER_V019_" in r["reason"] for r in rows))
        self.assertEqual(c.execute("SELECT qty FROM positions WHERE id=11").fetchone()[0],"35")
        realized=c.execute("SELECT realized FROM wallets WHERE tf='5m'").fetchone()[0]
        self.assertEqual(realized,sum(r["net_pnl"] for r in rows))
        cash=c.execute("SELECT cash FROM wallets WHERE tf='5m'").fetchone()[0]
        self.assertEqual(cash,100000+sum(
            int(float(r["qty"])*float(r["price"]))-r["fees"] for r in rows))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM v019_paper_replay_applied").fetchone()[0],2)
        self.assertEqual([r[0] for r in c.execute(
            "SELECT status FROM nl1118_resting_sells ORDER BY stage")],
            ["PAPER_FILLED","PAPER_FILLED"])
        with self.assertRaises(UnsafeRecovery):
            c.execute("BEGIN IMMEDIATE")
            try:apply_inside_transaction(c,p["owner"],proof,p["epoch"],obs)
            finally:c.rollback()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM executions").fetchone()[0],3)
        c.close()

    def test_cw_exact_original_one_stage_full_exit(self):
        c=self.cw()
        p=source("stock/cw",limit1=101.2,qty1=100)
        e=tape(p,[104])
        proof=replay(p,e)
        self.assertEqual(proof["status"],"PAPER_ASSUMPTION_CANDIDATES_VERIFIED")
        c.execute("BEGIN IMMEDIATE")
        result=apply_inside_transaction(c,p["owner"],proof,p["epoch"],
                                         {"testtick-1":104})
        c.commit()
        self.assertEqual(result["receipt_count"],1)
        self.assertEqual(c.execute("SELECT qty FROM positions").fetchone()[0],0)
        self.assertEqual(c.execute("SELECT status FROM positions").fetchone()[0],"CLOSED")
        self.assertEqual(c.execute("SELECT first_sold FROM positions").fetchone()[0],1)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM executions WHERE side='SELL'").fetchone()[0],1)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM v019_paper_replay_applied").fetchone()[0],1)
        fee=c.execute("SELECT fee FROM executions WHERE side='SELL'").fetchone()[0]
        tax=c.execute("SELECT tax FROM executions WHERE side='SELL'").fetchone()[0]
        self.assertEqual(c.execute("SELECT cash FROM wallets").fetchone()[0],
                         100000+104*100-fee-tax)
        c.close()

    def test_wrong_source_qty_rolls_back_all_financial_fields(self):
        c=self.coin()
        p=source("coin/general")
        proof=replay(p,tape(p,[106,110]))
        orig=c.execute("SELECT cash,realized FROM wallets").fetchone()
        c.execute("BEGIN IMMEDIATE")
        with self.assertRaises(UnsafeRecovery):
            apply_inside_transaction(c,"coin/general",dict(proof,source_remaining_qty="99"),
                         p["epoch"],{"testtick-1":106,"testtick-2":110})
        c.rollback()
        self.assertEqual(tuple(c.execute("SELECT cash,realized FROM wallets").fetchone()),
                         tuple(orig))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM executions").fetchone()[0],1)
        c.close()

    def test_stop_before_tp_has_no_candidates_for_native_writer(self):
        p=source("coin/general")
        ev=tape(p,[95,106])
        ev[0]["native_stop_triggered"]=True
        ev[0]["price"]=80
        res=replay(p,ev)
        self.assertEqual(res["status"],"STOP_FIRST_NO_PROFIT_CATCHUP")
        c=self.coin()
        c.execute("BEGIN IMMEDIATE")
        with self.assertRaises(UnsafeRecovery):
            apply_inside_transaction(c,p["owner"],res,p["epoch"],{"testtick-2":106})
        c.rollback()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM executions").fetchone()[0],1)
        c.close()

if __name__=="__main__":unittest.main()
