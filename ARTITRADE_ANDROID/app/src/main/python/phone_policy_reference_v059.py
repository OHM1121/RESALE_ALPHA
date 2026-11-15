"""v059 read-only, position-ID-bound reference chronology for phone UI.

Never confuse reference occurrence with BUY fill time. The prior MA candidate,
original coin reference, GMO D-day and CLOSE actual buy are independent facts.
All writes in this module are to the returned JSON snapshot, NOT source SQLite.
"""
from __future__ import annotations
from contextlib import closing
import datetime as dt
import json
import sqlite3
from pathlib import Path

_INSTALLED=False


def _rows(db,ids):
    path=Path(db)
    if not path.is_file() or not ids:return {},{}
    unique=sorted({str(x) for x in ids if x is not None and str(x).lstrip('-').isdigit()})
    if not unique:return {},{}
    position_rows={}
    candidate_ids=set()
    candidates={}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',
                                    uri=True,timeout=3)) as conn:
            conn.row_factory=sqlite3.Row
            existing={r[0] for r in conn.execute(
                'SELECT name FROM sqlite_master WHERE type=\'table\'')}
            if 'positions' not in existing:return {},{}
            for index in range(0,len(unique),450):
                group=unique[index:index+450]
                q=','.join('?' for _ in group)
                for rec in conn.execute('SELECT * FROM positions WHERE id IN ('+q+')',group):
                    row=dict(rec)
                    position_rows[str(row['id'])]=row
                    if row.get('candidate_id') is not None:
                        candidate_ids.add(str(row['candidate_id']))
            if 'candidates' in existing and candidate_ids:
                keys=sorted(candidate_ids)
                for index in range(0,len(keys),450):
                    group=keys[index:index+450]
                    q=','.join('?' for _ in group)
                    for rec in conn.execute('SELECT * FROM candidates WHERE id IN ('+q+')',group):
                        row=dict(rec)
                        candidates[str(row['id'])]=row
    except (OSError,sqlite3.Error,ValueError,TypeError):
        return {},{}
    return position_rows,candidates


def _first(*values):
    for value in values:
        if value is None:continue
        item=str(value).strip()
        if item and item.lower() not in ('none','null','nan'):return value
    return None


def _day(timestamp):
    raw=str(timestamp or '')
    if len(raw)>=10 and raw[4]=='-' and raw[7]=='-':
        return raw[:10]
    if len(raw)>=8 and raw[:8].isdigit():
        return raw[:4]+'-'+raw[4:6]+'-'+raw[6:8]
    return None


def _enrich(ledger,db,kind):
    if not isinstance(ledger,dict):return
    positions=ledger.get('positions') or []
    executions=ledger.get('executions') or []
    ids=[x.get('id') for x in positions if isinstance(x,dict)]
    ids.extend(x.get('position_id') for x in executions if isinstance(x,dict))
    source,candidates=_rows(db,ids)
    for records in (positions,executions):
        for row in records:
            if not isinstance(row,dict):continue
            pid=str(row.get('id') if records is positions else row.get('position_id'))
            historical=source.get(pid) or {}
            original=candidates.get(str(historical.get('candidate_id'))) or {}
            if kind in ('at','stock_ma','coin','coin_ma'):
                ref=_first(
                    historical.get('ref_ts'),historical.get('ref_at'),
                    original.get('ref_ts'),original.get('ref_at'),
                    row.get('ref_ts'),row.get('ref_at'))
                if ref is not None:
                    row['reference_candle_at']=ref
            buy=_first(historical.get('entry_at'),historical.get('entry_ts'),
                       historical.get('buy_at'),row.get('entry_at'))
            if buy is not None and row.get('source_buy_at') in ('',None):
                row['source_buy_at']=buy
            if kind=='close':
                recorded=_first(historical.get('entry_date'),buy,
                                row.get('entry_date'))
                if recorded is not None:
                    row['close_buy_date']=_day(recorded)
            if kind=='stock_ma' and original:
                # Exact candidate_id join, not coincidence by ticker/name.
                row['reference_candidate_id']=historical.get('candidate_id')
                if original.get('features'):
                    try:
                        feature=json.loads(original['features'])
                        if isinstance(feature,dict):
                            refvol=(feature.get('reference_volume_v059') or
                                    (feature.get('reference_ohlcv') or {}).get('volume'))
                            if refvol is not None:row['reference_volume']=refvol
                    except (TypeError,ValueError):
                        pass


def enrich_packet(runtime,packet):
    result=packet if isinstance(packet,dict) else {}
    root=Path(runtime.root)
    stock=result.get('stock') or {}
    at=(stock.get('at') or {}).get('ledger') or {}
    if isinstance(at,dict):
        _enrich(at,root/'stock'/'ARTITRADE_D.sqlite3','at')
        ma=at.get('ma_subledger') or at.get('ma')
        _enrich(ma,root/'stock'/'STOCK_MA_ENGINE_V1'/'ma_paper.sqlite3','stock_ma')
        gmo=at.get('gmo') or {}
        if isinstance(gmo,dict):
            # GMO snapshot stores the true D event date by exact
            # (code,entry_date), and the source has no generalized
            # stock position_id; keep dates only if the original supplies.
            for rec in (gmo.get('positions') or [])+(gmo.get('executions') or []):
                if not isinstance(rec,dict):continue
                event=_first(rec.get('gmo_event_date'),rec.get('event_date'))
                if event is not None:rec['gmo_d_day']=_day(event)
                entry=_first(rec.get('entry_date'),rec.get('entry_at'),
                             rec.get('buy_at'))
                if entry is not None:rec['gmo_buy_date']=_day(entry)
    cw=(stock.get('cw') or {}).get('ledger')
    _enrich(cw,root/'stock'/'close'/'AT_CLOSE_PAPER.sqlite3','close')
    coins=result.get('coin') or {}
    runner=getattr(runtime,'coin_runner',None)
    for key,label in (('general','coin'),('ma','coin_ma')):
        wallet=(getattr(runner,'ledger',None) if key=='general'
                else getattr(runner,'ma_ledger',None))
        db=getattr(wallet,'path',None)
        if db is not None:
            _enrich((coins.get(key) or {}).get('ledger'),db,label)
    return result


def install(core):
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True
    original=core.Runtime.hub_records
    def chronologized(self):
        raw=original(self)
        try:
            packet=json.loads(raw) if isinstance(raw,str) else raw
            if isinstance(packet,dict):
                enrich_packet(self,packet)
                return json.dumps(packet,ensure_ascii=False,default=str)
        except (OSError,sqlite3.Error,TypeError,ValueError,AttributeError):
            pass
        return raw
    core.Runtime.hub_records=chronologized
