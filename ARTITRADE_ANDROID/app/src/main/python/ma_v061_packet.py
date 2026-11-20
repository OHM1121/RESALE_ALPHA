"""Read-only state annotation for the existing MA holdings/policy UI.

Do not combine Original BASE, MA, Trend or GMO ledgers, and never invent an
entry timestamp. Position-ID joins distinguish repeat buys of same symbol.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

def _read_states(db):
    path=Path(db)
    if not path.is_file():return {}
    try:
        con=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
        con.row_factory=sqlite3.Row
        try:
            return {str(row['position_id']):dict(row) for row in
                con.execute("SELECT * FROM ma_v061_positions")}
        finally:con.close()
    except (sqlite3.Error,OSError,ValueError):
        return {}

def _annotate(ledger,db):
    if not isinstance(ledger,dict):return
    states=_read_states(db)
    if not states:return
    for bucket in ('positions','executions'):
        rows=ledger.get(bucket) or []
        for row in rows:
            if not isinstance(row,dict):continue
            pid=str(row.get('position_id') if bucket=='executions' else row.get('id'))
            original=states.get(pid)
            if not original:continue
            try:
                state=json.loads(original['state_json'])
            except (KeyError,ValueError,TypeError):state={}
            kind=original.get('kind')
            row['ma_v061_kind']=kind
            row['ma_v061_kind_label']=('MA 전환점' if kind=='TURNPOINT' else
                                      'MA 3개 이상 하락 음봉')
            row['ma_v061_turnpoint_at']=original.get('ref_at') or original.get('ref_ts')
            row['ma_v061_stage']=int(state.get('stage') or 0)
            row['ma_v061_first_target_touched']=int(state.get('stage') or 0)>=1
            row['ma_v061_first_anchor']=state.get('first_anchor') or ''
            row['ma_v061_first_sold']=state.get('first_sold') or '0'
            row['ma_v061_second_sold']=state.get('second_sold') or '0'
            row['ma_v061_peak2']=state.get('peak2') or ''
            if kind=='TURNPOINT':
                from ma_v061_rules import FIRST_TARGET_PCT
                row['ma_v061_first_target_pct']=str(FIRST_TARGET_PCT.get(
                    str(row.get('tf')),''))
            row['ma_v061_stage_label']=(
                '전환점 1차 목표 대기' if kind=='TURNPOINT' and int(state.get('stage') or 0)==0 else
                '전환점 2차 목표 추적 / 매수가 방어' if kind=='TURNPOINT' and int(state.get('stage') or 0)==1 else
                '전환점 3차 고점 -4% 추적 / 매수가 방어' if kind=='TURNPOINT' else
                '하락 음봉 / 기존 MA 분할매도 및 신규 강제손절')

def enrich(runtime,payload):
    root=Path(runtime.root)
    stock=(payload.get('stock') or {}).get('at') or {}
    stock_ledger=stock.get('ledger') or {}
    ma=stock_ledger.get('ma_subledger') or stock_ledger.get('ma')
    _annotate(ma,__import__('ma_epoch_v062').stock_active(root))
    coin=(payload.get('coin') or {}).get('ma') or {}
    _annotate(coin.get('ledger'),__import__('ma_epoch_v062').coin_active(root))
    return payload

def install(core):
    old=core.Runtime.hub_records
    def scoped(self,for_ui=False):
        raw=old(self,for_ui=for_ui)
        try:
            payload=json.loads(raw) if isinstance(raw,str) else raw
            if isinstance(payload,dict):
                enrich(self,payload)
                return json.dumps(payload,ensure_ascii=False,default=str)
        except (OSError,ValueError,TypeError,sqlite3.Error):
            pass
        return raw
    core.Runtime.hub_records=scoped
