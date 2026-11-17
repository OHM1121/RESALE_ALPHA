"""v083: native MA coin manual SELL must pass both original strategy guard wrappers.

This test evaluates the real nested wrappers from production source. It tests
the exact original event-key / reason pairs, not a reimplemented policy.
"""
import ast
import sqlite3
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'app/src/main/python'


def wrapped(source_file, method, delegate):
    tree=ast.parse((ROOT/source_file).read_text(encoding='utf-8'))
    outer=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='install')
    func=next(x for x in ast.walk(outer) if isinstance(x,ast.FunctionDef) and x.name==method)
    ns={'original_sell':delegate,'previous_sell':delegate,'_table':lambda c:None}
    exec(compile(ast.Module(body=[func],type_ignores=[]),source_file,'exec'),ns)
    return ns[method]


class Db:
    def __init__(self,kind):
        self.kind=kind
        self.calls=[]
    def execute(self,sql,args):
        self.calls.append((sql,args))
        if 'ma_v061_positions' in sql:
            return Row({'kind':self.kind if self.kind=='TURNPOINT' else None})
        if 'ma_v067_positions' in sql:
            return Row({'one':1 if self.kind=='REVERSAL_UP' else None})
        raise AssertionError(sql)


class Row:
    def __init__(self,data):self.data=data
    def fetchone(self):
        if self.data.get('kind') is None and self.data.get('one') is None:return None
        return self.data


class CoinManualGuard(unittest.TestCase):
    def setUp(self):
        self.recorded=[]
        def original(ledger,c,p,desired,price,at,reason,event_key=None):
            self.recorded.append((p['id'],str(desired),str(price),reason,event_key))
            return True
        self.v061=wrapped('ma_v061_coin_runtime.py','protected_sell',original)
        self.v067=wrapped('ma_v067_coin_runtime.py','scoped_sell',self.v061)
        self.db=Db('REVERSAL_UP')
        self.position={'id':724}
        self.reason='사용자 가상 강제매도 100%'
        self.key='USER:742614c5-81f1-4ff4-b3f2-3fef63f6e1ca'

    def sell(self,reason=None,key=None):
        return self.v067(object(),self.db,self.position,'3.71','94.90','now',
                         self.reason if reason is None else reason,
                         event_key=self.key if key is None else key)

    def test_v067_manual_reason_and_uuid_reach_original_native_sell(self):
        self.assertTrue(self.sell())
        self.assertEqual(self.recorded[0][-1],self.key)

    def test_v061_turnpoint_manual_reason_and_uuid_reach_original_native_sell(self):
        self.db=Db('TURNPOINT')
        # V061 is independently installed for historical TURNPOINT holdings.
        self.assertTrue(self.v061(object(),self.db,self.position,'3.71','94.90','now',
                                  self.reason,event_key=self.key))
        self.assertEqual(len(self.recorded),1)

    def test_non_manual_reasons_cannot_bypass_v067_strategy_gate(self):
        self.assertFalse(self.sell(reason='UNRELATED_EXIT'))
        self.assertFalse(self.sell(key='NOT_USER:fake'))
        self.assertFalse(self.sell(reason='MA_V061_WRONG_STRATEGY'))
        self.assertEqual(self.recorded,[])

    def test_non_manual_reasons_cannot_bypass_v061_turnpoint_gate(self):
        self.db=Db('TURNPOINT')
        self.assertFalse(self.v061(object(),self.db,self.position,1,95,'now','SR1_TARGET',
                                    event_key=self.key))
        self.assertFalse(self.v061(object(),self.db,self.position,1,95,'now',self.reason,
                                    event_key='WRONG'))
        self.assertEqual(self.recorded,[])

    def test_existing_auto_exit_prefixes_preserved(self):
        self.assertTrue(self.sell(reason='MA_V065_UP_FIRST_10PCT',key='MA_V067:1'))
        self.assertTrue(self.sell(reason='MA_V067_SECOND_FOLLOWUP_BELOW_ENTRY',key='MA_V067:2'))
        self.db=Db('TURNPOINT')
        self.assertTrue(self.v061(object(),self.db,self.position,1,95,'now',
                                  'MA_V061_FIRST_PROFIT',event_key='MA_V061:3'))
        self.assertEqual(len(self.recorded),3)


if __name__=='__main__':unittest.main()
