"""Read-only authenticated/live quote and independent public-score provenance.

A public REST mark is NOT a websocket trade or a broker executable 0B/0D
book. This report never changes strategies, orders, wallets, credentials,
or the underlying SQLite database. All failures are visible, never masked.
"""
from __future__ import annotations
import datetime as dt
import math
import time
from pathlib import Path


def _age(now,stamp,scale=1.):
    try:
        s=float(stamp)/scale
        if not math.isfinite(s) or s<=0:return None
        return round(max(0.,now-s),2)
    except (ValueError,TypeError,OverflowError):return None


def _age_datetime(now,stamp):
    try:
        if isinstance(stamp,dt.datetime):
            parsed=stamp
        else:parsed=dt.datetime.fromisoformat(str(stamp))
        if parsed.tzinfo is None:return None
        return _age(now,parsed.timestamp())
    except (ValueError,TypeError,OverflowError):return None


def snapshot(runtime, now=None,clock=None):
    now=time.time() if now is None else float(now)
    mono=time.monotonic() if clock is None else float(clock)
    r={"schema":"ARTITRADE_MARKET_SOURCE_HEALTH_V004",
       "asof":int(now),"prices_are_never_fabricated":True,
       "live_orders_enabled_by_this_module":False}
    stock=getattr(runtime,'stock_runner',None)
    info={"api_object_present":bool(getattr(stock,'api',None)),
          "ws_registered":bool(getattr(stock,'_confirmed_ws_session',False)),
          "fresh_0b_count":0,"fresh_0d_count":0,
          "quote_status":"UNVERIFIED_NO_NEW_0B"}
    if stock is not None:
        lock=getattr(stock,'_v066_quote_lock',None)
        if lock is not None:
            try:
                with lock:
                    prints=list(getattr(stock,'_v066_quote_by_venue',{}).values())
                info['fresh_0b_count']=sum(1 for q in prints
                    if q.get('is_fresh') and
                    (age:=_age(now,q.get('exchange_ms'),1000)) is not None and age<=3)
                info['last_0b_exchange_age_sec']=min(
                    (v for q in prints for v in [_age(now,q.get('exchange_ms'),1000)]
                    if v is not None),default=None)
            except Exception as exc:info['audit_error']=type(exc).__name__
        book=getattr(getattr(getattr(stock,'engine',None),'book',None),'_bid_depth',{})
        try:
            depths=list(book.values())
            info['fresh_0d_count']=sum(1 for v in depths if len(v)>2
                and (age:=_age_datetime(now,v[2])) is not None and age<=3)
        except Exception as exc:info['book_error']=type(exc).__name__
    if info['ws_registered'] and (info['fresh_0b_count'] or info['fresh_0d_count']):
        info['quote_status']='RECEIVING_VERIFIED_EXCHANGE_BOOK_OR_TRADE'
    elif info['ws_registered']:
        info['quote_status']='WS_AUTHENTICATED_NO_CURRENT_QUOTE_EVIDENCE'
    r['stock']=info
    coin=getattr(runtime,'coin_runner',None)
    info={"api_object_present":bool(getattr(coin,'client',None)),
          "ws_connected":bool(getattr(coin,'_ws_connected',False)),
          "last_ws_quote_age_sec":None,"rest_mark_age_sec":None,
          "requested_ticker_count":0,"active_ticker_count":0,
          "requested_book_count":0,"active_book_count":0,
          "quote_status":"NO_WS_QUOTE"}
    if coin is not None:
        with getattr(coin,'_v083_lock',_NullLock()):
            requested=set(getattr(coin,'_v083_codes',()) or ())
            active=set(getattr(coin,'_v083_active_codes',()) or ())
            requested_books=set(getattr(coin,'_v083_bids',()) or ())
            active_books=set(getattr(coin,'_v083_active_bids',()) or ())
        info.update(requested_ticker_count=len(requested),active_ticker_count=len(active),
                    requested_book_count=len(requested_books),active_book_count=len(active_books),
                    last_ws_quote_age_sec=_age(mono,getattr(coin,'_last_ws_quote',0)),
                    rest_mark_age_sec=_age(mono,getattr(coin,'last_live',0)),
                    last_ws_error=str(getattr(coin,'_ws_last_reason',''))[:140])
        if (info['ws_connected'] and info['last_ws_quote_age_sec'] is not None
            and info['last_ws_quote_age_sec']<=10 and (not requested or active)):
            info['quote_status']='WS_CURRENT'
        elif info['rest_mark_age_sec'] is not None and info['rest_mark_age_sec']<=16:
            info['quote_status']='REST_MARK_ONLY_NOT_TRADE_PROOF'
        else:info['quote_status']='WS_STALE_OR_DISCONNECTED'
    r['coin']=info
    try:
        from market_research_v033 import _WORKERS,_WORKERS_LOCK
        key=str(Path(runtime.root).resolve())
        with _WORKERS_LOCK:worker=_WORKERS.get(key)
        if worker is None:
            r['score']={'stock':{'status':'WORKER_ABSENT'},'coin':{'status':'WORKER_ABSENT'}}
        else:
            snapshots=worker.snapshot()
            r['score']={m:{
                'score':v.get('score') if not v.get('stale',True) else None,
                'stale':bool(v.get('stale',True)),
                'asof':v.get('asof'),
                'coverage':v.get('coverage'),
                'status':str(v.get('status') or ''),
                'feed_error':str(v.get('poll_error') or ''),
                'factor_count':len(v.get('factors') or []),
                'source':str(v.get('source') or '')
            } for m in ('stock','coin') for v in [snapshots.get(m) or {}]}
    except Exception as exc:
        r['score']={'error':type(exc).__name__}
    return r


class _NullLock:
    def __enter__(self):return self
    def __exit__(self,*args):return False