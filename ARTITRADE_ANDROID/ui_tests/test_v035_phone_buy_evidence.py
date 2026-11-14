"""Phone BUY reasoning: exact execution->position evidence for stock and coin."""
import json
import sqlite3
import tempfile
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/"app/src/main/python"))
from phone_buy_evidence_v035 import attach_buy_evidence, store_buy_evidence

class PhoneBuyEvidence(unittest.TestCase):
    def test_coin_base_and_ma_evidence_is_from_matching_phone_buy_fill(self):
        coinbase=[{"id":7,"tf":"5m","market":"KRW-BTC",
            "ref_at":"2026-11-11T09:00","entry_at":"2026-11-11T09:11"}]
        coinma=[{"id":7,"tf":"5m","market":"KRW-ETH",
            "ref_at":"2026-11-11T10:00","entry_at":"2026-11-11T10:11"}]
        for positions,market,strategy,reason in [
            (coinbase,"KRW-BTC","base","Upbit 공개 호가 기반 하단 지정가 가상체결"),
            (coinma,"KRW-ETH","ma","MA 이동평균 기준봉 매수")]:
            executions=[{"id":99,"position_id":7,"tf":"5m","market":market,
                "side":"BUY","at":"2026-11-11T09:11","reason":reason}]
            candidates=[{"id":11,"tf":"5m","market":market,
                "ref_at":positions[0]["ref_at"],"reason":"MA_NORMAL_REFERENCE"}]
            events=attach_buy_evidence(positions,executions,candidates,market="coin",strategy=strategy)
            self.assertEqual(len(events),1)
            self.assertEqual(positions[0]["buy_reason"],reason)
            self.assertEqual(positions[0]["buy_signal_reason"],"MA_NORMAL_REFERENCE")
            self.assertEqual(events[0]["source"],"ORIGINAL_BUY_EXECUTION")
            self.assertEqual(events[0]["buy_at"],"2026-11-11T09:11")

    def test_stock_at_uses_exact_position_id_and_signal_id(self):
        pos=[{"id":33,"tf":"60m","code":"278650","market":"KOSDAQ",
              "name":"HLB바이오스텝","entry_ts":"20261103105232","ref_ts":"20261103090000"}]
        exe=[{"id":70,"position_id":33,"side":"BUY","tf":"60m",
              "code":"278650","ts":"20261103105232",
              "reason":"AT DIVERSE 1.1 순수 점수 | 호가 기반 가상체결"}]
        signals=[{"id":900,"tf":"60m","code":"278650","ref_ts":"20261103090000",
                  "reason":"기준봉 거래량 신호","score":68.1}]
        events=attach_buy_evidence(pos,exe,signals,market="stock",strategy="at",
                                   linked_ids={"33":"900"})
        self.assertEqual(pos[0]["buy_reason"],exe[0]["reason"])
        self.assertEqual(pos[0]["buy_signal_reason"],"기준봉 거래량 신호")
        self.assertEqual(events[0]["position_id"],"33")

    def test_ma_close_are_independent_and_no_symbol_only_guessing(self):
        ma=[{"id":5,"tf":"30m","code":"005930","candidate_id":81}]
        close=[{"id":5,"code":"000660","signal_id":91}]
        execution=[{"position_id":5,"tf":"30m","code":"005930","side":"BUY",
                    "ts":"20261103111000","reason":"MA_BUY"}]
        attach_buy_evidence(ma,execution,[{"id":81,"code":"005930","tf":"30m",
                      "reason":"MA_NORMAL_REFERENCE"}],market="stock",strategy="ma")
        attach_buy_evidence(close,execution,[],market="stock",strategy="close")
        self.assertEqual(ma[0]["buy_reason"],"MA_BUY")
        self.assertFalse(close[0].get("buy_reason"),"unrelated symbol must not get reason")

    def test_missing_buy_fill_must_not_fabricate_signal_as_actual_buy(self):
        positions=[{"id":1,"code":"005930","tf":"5m","ref_ts":"20261103100000"}]
        events=attach_buy_evidence(positions,[],[{"id":1,"tf":"5m","code":"005930",
          "ref_ts":"20261103100000","reason":"BUY_SIGNAL"}],market="stock",strategy="at")
        self.assertEqual(events,[])
        self.assertNotIn("buy_reason",positions[0])

    def test_first_buy_fill_wins_over_later_topup_and_cross_position(self):
        p=[{"id":1,"market":"KRW-BTC","tf":"60m","ref_at":"20261103100000"}]
        exe=[{"id":3,"position_id":1,"market":"KRW-BTC","tf":"60m","side":"BUY",
              "at":"20261104090000","reason":"LATER_TOPUP"},
             {"id":2,"position_id":2,"market":"KRW-BTC","tf":"60m","side":"BUY",
              "at":"20261102100000","reason":"DIFFERENT_POSITION"},
             {"id":1,"position_id":1,"market":"KRW-BTC","tf":"60m","side":"BUY",
              "at":"20261103110000","reason":"INITIAL_BUY"}]
        attach_buy_evidence(p,exe,[],market="coin",strategy="base")
        self.assertEqual(p[0]["buy_reason"],"INITIAL_BUY")

    def test_phone_audit_is_idempotent_and_original_ledger_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            db=root/"coin"/"base"/"upbit_paper.sqlite3"
            db.parent.mkdir(parents=True)
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE positions(id INT, market TEXT)")
                con.execute("INSERT INTO positions VALUES(1,'KRW-BTC')")
            event={"position_id":"1","market":"coin","strategy":"base","code":"KRW-BTC",
                "tf":"5m","buy_at":"20261103110000","buy_reason":"SOURCE_EXACT",
                "buy_signal_reason":"QUALIFIED","source":"ORIGINAL_BUY_EXECUTION"}
            self.assertEqual(store_buy_evidence(root,[event]),1)
            self.assertEqual(store_buy_evidence(root,[event]),0)
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute("SELECT * FROM positions").fetchone(),(1,"KRW-BTC"))
            with sqlite3.connect(root/"phone_buy_evidence_v035.sqlite3") as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM buy_evidence").fetchone()[0],1)

if __name__=="__main__":unittest.main()
