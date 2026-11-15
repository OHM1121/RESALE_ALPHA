"""v059 reference-candle chronology and record-integrity guard.

No second broker socket and no order/balance/fill reconstruction. The existing
strategy still decides price geometry, MA slopes and exits. This overlay
prevents stale pending references after a later, *completed* turnover record.
Every override is installed before the shared EXE runner begins.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import threading
from decimal import Decimal

_INSTALLED=False
_STOCK_SCAN=threading.local()


def _coin_metric(history):
    """Verified Upbit KRW traded value, else the legacy quantity (explicitly)."""
    if history and all(x.get('turnover') is not None for x in history):
        return 'turnover'
    return 'volume'


def _coin_view(history):
    """Map KRW traded value into the existing MA/record-volume comparisons.

    Preserve the native quantity under volume_qty. No fabricated turnover
    and never reuse a present-day ticker for historical OHLCV.
    """
    if _coin_metric(history)!='turnover':return history
    return [dict(x,volume=x['turnover'],volume_qty=x['volume'])
            for x in history]


def _coin_record(history,frame):
    from coin_v24_policy import prior_window
    if not history:return None
    rows=_coin_view(history)
    window=prior_window(rows,frame)
    if window is None:return None
    now=Decimal(str(window['ref']['volume']))
    prior=Decimal(str(window['prior_max']))
    return {'new_record':now>prior,
            'current':now,'previous_max':prior,
            'metric':_coin_metric(history),
            'ref_start':rows[-1]['start']}


def _coin_invalidate(ledger,market,frame,new_ref,now,record):
    """Only unfilled orders with an older reference can be superseded."""
    if ledger is None:return 0
    changed=0
    with ledger.lock,ledger.connect() as conn:
        old=conn.execute("""SELECT id,ref_at FROM orders
            WHERE market=? AND tf=? AND status='PENDING'""",
            (market,frame)).fetchall()
        for order in old:
            if not str(order['ref_at'])<str(new_ref):continue
            conn.execute("UPDATE orders SET status='REPLACED' WHERE id=? AND status='PENDING'",
                         (order['id'],))
            cid=conn.execute("""SELECT id FROM candidates
                WHERE market=? AND tf=? AND ref_at=?""",
                (market,frame,order['ref_at'])).fetchone()
            if cid is not None:
                ledger._decision(conn,cid['id'],frame,market,now,'REPLACED',
                    '최신 완료봉 거래기록 갱신으로 이전 기준봉 무효 ('+
                    record['metric']+')')
                try:ledger._expire_waiting_shadow(conn,cid['id'],now,'REPLACED')
                except (AttributeError,sqlite3.Error):pass
            changed+=1
        if changed:
            conn.execute("""INSERT INTO issues(at,severity,code,message)
                VALUES(?,?,?,?)""",(now.isoformat(),'INFO','MA_NEW_REFERENCE_REPLACES_OLD',
                (frame+' '+market+' '+str(new_ref)+' '+record['metric']+
                 ' record='+str(record['current'])+
                 ' previous='+str(record['previous_max'])+
                 ' previous_pending='+str(changed))[:300]))
    return changed


def _coin_ma_scan(original,self,history,frame,code,tick,at,name):
    from d_upbit import SECONDS
    from coin_hold_policy import ORDER_LIFE_SECONDS
    if not history or frame not in SECONDS:
        return {'market':code,'status':'INSUFFICIENT','reason':'NO_COMPLETED_HISTORY'}
    now=at.astimezone(dt.timezone.utc)
    duration=dt.timedelta(seconds=SECONDS[frame])
    life=float(ORDER_LIFE_SECONDS[frame])
    # Latest strict record wins. If it fails another MA gate, *do not*
    # resurrect an older pre-record buy. Recovery is pending-only: no
    # retroactive FOK or artificial orderbook quote.
    for end in range(len(history),121,-1):
        subset=history[:end]
        ref=subset[-1]
        ref_end=ref['start']+duration
        if ref_end>now:continue
        if (now-ref_end).total_seconds()>life:break
        record=_coin_record(subset,frame)
        if record is None:
            return {'market':code,'status':'INSUFFICIENT',
                    'reason':'RECORD_WINDOW_INCOMPLETE',
                    'newest_ref_at':ref['start'].isoformat()}
        if not record['new_record']:continue
        ref_key=ref['start'].isoformat()
        # Both independent original and MA queues must stop referencing an
        # older verified turnover peak. Only PENDING gets replaced.
        _coin_invalidate(self.ledger,code,frame,ref_key,at,record)
        _coin_invalidate(self.ma_ledger,code,frame,ref_key,at,record)
        audit=original(self,subset,frame,code,tick,at,name)
        audit['record_metric']=record['metric']
        audit['record_current']=float(record['current'])
        audit['record_previous_max']=float(record['previous_max'])
        audit['record_completed_at']=ref_end.isoformat()
        audit['recovered_completed_candle']=end<len(history)
        audit['latest_verified_reference_at']=ref_key
        return audit
    return {'market':code,'status':'REJECT',
            'reason':'NO_NEW_RECORD_WITHIN_ACTIVE_LIFE',
            'newest_ref_at':history[-1]['start'].isoformat()}


def _stock_volume_record(bars,frame):
    from coin_v24_policy import window_start
    from d_storage import KST
    if not bars or len(bars)<122:return None
    newest=dict(bars[-1])
    try:
        at=dt.datetime.strptime(str(newest['ts'])[:12],'%Y%m%d%H%M').replace(tzinfo=KST)
        beginning=window_start(at,frame)
        old=dt.datetime.strptime(str(bars[0]['ts'])[:12],'%Y%m%d%H%M').replace(tzinfo=KST)
        if old>beginning:return None
        eligible=[float(r['volume']) for r in bars[:-1] if
            beginning<=dt.datetime.strptime(str(r['ts'])[:12],'%Y%m%d%H%M')
                        .replace(tzinfo=KST)<at]
        if not eligible:return None
        volume=float(newest['volume'])
        if not math.isfinite(volume) or any(not math.isfinite(x) for x in eligible):
            return None
        return {'new_record':volume>max(eligible),'volume':volume,
                'previous_max':max(eligible),'ref_ts':newest['ts'],
                'tf':frame,'code':newest['code']}
    except (KeyError,ValueError,TypeError,OverflowError):
        return None


def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True
    import d_upbit
    import coin_ma_engine
    import stock_ma_engine

    original_closed=d_upbit.point_in_time_bars
    def completed_with_turnover(items,frame,at=None):
        bars=original_closed(items,frame,at)
        source={}
        for row in items or []:
            try:
                moment=d_upbit.candle_start_utc(row)
                value=Decimal(str(row['candle_acc_trade_price']))
                if value>=0:source[moment]=value
            except (KeyError,TypeError,ValueError,AttributeError):
                continue
        for bar in bars:
            if bar['start'] in source:
                bar['turnover']=source[bar['start']]
        return bars
    d_upbit.point_in_time_bars=completed_with_turnover

    # Existing coin ORIGINAL ref_features use quantity, while KRW Upbit's
    # 236M / 240M visual amount refers to candle traded value. Use the
    # original policy on timestamp-verified turnover when it is complete,
    # preserving every other normal rule and transparent fallback.
    orig_coin_ref=d_upbit.ref_features
    def verified_coin_ref(history,frame,market,tick,at=None,**opts):
        candidate=orig_coin_ref(_coin_view(history),frame,market,tick,at,**opts)
        if candidate:
            candidate['features']['reference_record_metric_v059']=_coin_metric(history)
            if _coin_metric(history)=='turnover':
                candidate['features']['reference_turnover_krw']=float(history[-1]['turnover'])
                candidate['features']['reference_volume_qty']=float(history[-1]['volume'])
        return candidate
    d_upbit.ref_features=verified_coin_ref
    coin_ma_engine.ref_features=verified_coin_ref
    original_recover=d_upbit.recoverable_ref_features
    def verified_coin_recovery(history,frame,market,tick,now,**opts):
        candidate=original_recover(_coin_view(history),frame,market,tick,now,**opts)
        if candidate:
            candidate['features']['reference_record_metric_v059']=_coin_metric(history)
        return candidate
    d_upbit.recoverable_ref_features=verified_coin_recovery
    original_reject=d_upbit.reference_rejection_reason
    def verified_rejection(history,frame,tick,at=None):
        return original_reject(_coin_view(history),frame,tick,at)
    d_upbit.reference_rejection_reason=verified_rejection

    original_ma=coin_ma_engine.ma_reference_decision
    def verified_ma(history,frame,market,tick,at):
        evidence=_coin_record(history,frame)
        if evidence is None:
            return ({'status':'INSUFFICIENT','reason':'MA_RECORD_HISTORY_INCOMPLETE',
                     'selected_ref_at':None},None)
        if not evidence['new_record']:
            return ({'status':'REJECT','reason':'MA_NOT_LATEST_RECORD',
                     'selected_ref_at':None,'record_metric':evidence['metric'],
                     'record_current':str(evidence['current']),
                     'record_previous_max':str(evidence['previous_max'])},None)
        audit,candidate=original_ma(_coin_view(history),frame,market,tick,at)
        audit['record_metric_v059']=evidence['metric']
        audit['record_current_v059']=str(evidence['current'])
        audit['record_previous_max_v059']=str(evidence['previous_max'])
        if candidate:
            candidate['features']['reference_record_metric_v059']=evidence['metric']
            candidate['features']['reference_record_krw_v059']=(
                float(evidence['current']) if evidence['metric']=='turnover' else None)
            candidate['features']['reference_volume_qty_v059']=float(history[-1]['volume'])
        return audit,candidate
    coin_ma_engine.ma_reference_decision=verified_ma

    original_ma_scan=d_upbit.UpbitPaperRunner._ma_scan_decision
    def repaired_ma_scan(self,history,frame,code,tick,at,name):
        return _coin_ma_scan(original_ma_scan,self,history,frame,code,tick,at,name)
    d_upbit.UpbitPaperRunner._ma_scan_decision=repaired_ma_scan

    original_stock=stock_ma_engine.ma_reference_decision
    def verified_stock_ma(bars,frame,at):
        evidence=_stock_volume_record(bars,frame)
        if evidence is None:
            return {'decision':'INSUFFICIENT_HISTORY',
                    'reason':'MA_COMPLETE_VOLUME_WINDOW_UNAVAILABLE'},None
        if evidence['new_record']:
            if getattr(_STOCK_SCAN,'pending',None) is not None:
                _STOCK_SCAN.pending.append(evidence)
        else:
            return {'decision':'REJECT','reason':'MA_NOT_NEW_VOLUME_RECORD',
                    'reference':evidence['ref_ts'],
                    'volume':evidence['volume'],
                    'previous_max':evidence['previous_max']},None
        audit,candidate=original_stock(bars,frame,at)
        audit['reference_record_metric_v059']='exchange_volume'
        audit['reference_volume_v059']=evidence['volume']
        audit['previous_max_volume_v059']=evidence['previous_max']
        return audit,candidate
    stock_ma_engine.ma_reference_decision=verified_stock_ma

    original_scan=stock_ma_engine.StockMAService.scan_completed
    def repaired_stock_scan(self,due,market_map):
        _STOCK_SCAN.pending=[]
        try:
            return original_scan(self,due,market_map)
        finally:
            refs=getattr(_STOCK_SCAN,'pending',[])
            _STOCK_SCAN.pending=None
            if refs:
                with self.lock,stock_ma_engine._connect(self.db) as conn:
                    for e in refs:
                        conn.execute("""UPDATE candidates SET status='REPLACED'
                            WHERE tf=? AND code=? AND status='PENDING' AND ref_ts<?""",
                            (e['tf'],e['code'],e['ref_ts']))
                self._refresh_watched()
    stock_ma_engine.StockMAService.scan_completed=repaired_stock_scan
