"""Android v0.7.8 runtime compatibility layer.

Does not alter strategy thresholds/state machines. It wires the latest extracted
GMO service into the same Kiwoom 0B/0D stream before the live-order adapter
patches the GMO paper-fill transition.
"""
from pathlib import Path
import sqlite3
import hashlib, hmac, json, uuid, time
import threading
import asyncio
import math
import re
from collections import Counter
from urllib.parse import urlencode
import android_live_bridge as _core
from android_sqlite_reader import db_rows
from android_live_position_guard_v049 import (
    refuse_paper_sell_if_live_open, verified_gmo_live_quantity, UnsafePaperExit)

# Bundled original EXE bridge lacks this read-only history helper.
# Add the accessor without modifying strategy or order state machines.
if not hasattr(_core, '_db_rows'):
    _core._db_rows = db_rows
import d_live as _stock_feed_core

# The original execution proxy allows PAPER fills whenever LIVE is disabled.
# A restored broker position must not be paper-closed after an MSERVER lease
# loss. Inspect only existing broker FILLED/uncertain evidence, never invent
# broker holdings, and block before the original SQLite execution is inserted.
_ORIGINAL_PROXY_BEFORE = _core.LiveConnectionProxy._before

def _guard_live_position_from_paper_exit(self, sql, params=()):
    text=" ".join(str(sql).split())
    match=re.search(
        r"INSERT(?:\s+OR\s+IGNORE|\s+OR\s+REPLACE)?\s+INTO\s+executions\s*\((.*?)\)",
        text,re.IGNORECASE)
    if match and isinstance(params,(list,tuple)):
        columns=[x.strip().strip('"\u0060[]').lower() for x in match.group(1).split(",")]
        if len(columns)<=len(params):
            record=dict(zip(columns,params))
            if str(record.get("side") or "").upper()=="SELL":
                symbol=str(record.get("market") or record.get("code") or "")
                market="coin" if symbol.startswith("KRW-") else "stock"
                if not self._rt.live_enabled(market):
                    try:
                        refuse_paper_sell_if_live_open(self._rt.root,market,symbol)
                    except UnsafePaperExit as unsafe:
                        self._rt._err("paper_exit_of_live_holding_blocked",unsafe)
                        raise
    return _ORIGINAL_PROXY_BEFORE(self,sql,params)

_core.LiveConnectionProxy._before = _guard_live_position_from_paper_exit

# Preserve the original EXE state machines. Only observe the transport progress
# which the Android bridge previously discarded, and surface fatal startup errors.
_ORIGINAL_STOCK_PROGRESS = _core.Runtime._stock_progress
_ORIGINAL_RUNTIME_SNAPSHOT = _core.Runtime.snapshot
_ORIGINAL_STOCK_RUN = _stock_feed_core.LivePaperRunner.run

def _phone_stock_progress(self, item):
    if isinstance(item, dict) and item.get('stage'):
        self._phone_stock_progress = {
            'stage': str(item.get('stage') or '')[:110],
            'detail': str(item.get('detail') or '')[:210],
            'state': str(item.get('state') or '')[:25],
            'done': int(item.get('done') or 0),
            'total': int(item.get('total') or 0),
            'updated_at_ms': int(time.time() * 1000),
        }
    return _ORIGINAL_STOCK_PROGRESS(self, item)

async def _phone_guard_stock_run(self):
    # A fatal worker exception must not leave a service that appears RUNNING
    # while its single Kiwoom socket has stopped forever. Reconcile pending
    # orders through the ORIGINAL startup on every retry; never fake a fill.
    attempt=0
    while not self.stop_event.is_set():
        try:
            self._progress('주식 원장 사전점검',0,1,
                'PC 승계 원장 및 키움 시세 엔진 준비',state='running')
            return await _ORIGINAL_STOCK_RUN(self)
        except Exception as exc:
            attempt+=1
            msg=type(exc).__name__+': '+str(exc)[:200]
            try:
                self._confirmed_ws_session=False
                self._health('연결 끊김 - 재연결 대기',msg)
                self._progress('키움 실시간 엔진 복구 대기',0,0,
                    msg+' · 원본 원장 재검증 후 재시작',state='waiting')
                from at_close_bridge import set_broker_transport
                set_broker_transport('disconnected',msg[:120])
            except Exception:
                self.status='키움 실시간 엔진 복구 대기'
            try:
                self.store.issue('ANDROID_KIWOOM_FEED_FATAL',
                    '키움 실시간 엔진 예외 종료 · 오류 누적 및 원장 재검증 재시작',
                    key='android-kiwoom-fatal:'+str(int(time.time()))+':'+str(attempt),
                    details={'type':type(exc).__name__,'message':str(exc)[:180],
                             'attempt':attempt})
            except Exception:pass
            await asyncio.sleep(min(60,2**min(attempt,6)))
    return None

def _phone_runtime_snapshot(self):
    raw = _ORIGINAL_RUNTIME_SNAPSHOT(self)
    try:
        snapshot = json.loads(raw) if isinstance(raw, str) else dict(raw)
        runner = getattr(self, 'stock_runner', None)
        status = str(getattr(runner, 'status', '전략엔진 준비')) if runner is not None else '전략엔진 준비'
        stock = snapshot.get('stock') or {}
        # Never classify a disconnected stock feed as live solely from a status substring.
        stock['status'] = status
        snapshot['stock'] = stock
        last = dict(getattr(self, '_phone_stock_progress', {}) or {})
        # The original Runtime._stock_progress only reports generic phases,
        # not per-stock completions. 0.0.16's phase/checklist denominator could
        # oscillate, and 0.0.17's interim stage-based guess never counted 459.
        # Count uniquely proven native recover_frame successes instead.
        from stock_pending_recovery_v017 import loading_snapshot as _stock_loading
        native_load=_stock_loading(runner)
        try:
            from at_close_bridge import broker_transport_snapshot
            transport = broker_transport_snapshot()
        except Exception:
            transport = {}
        # REG/tick was confirmed on this socket AND the broker transport is still alive.
        # The runner may temporarily change its status to NXT-universe refresh during LIVE.
        stock['connected'] = bool(runner is not None and
            getattr(runner, '_confirmed_ws_session', False) and
            transport.get('state') == 'connected')
        try:
            preload = runner.preload_gate.snapshot() if runner is not None else {}
        except Exception:
            preload = {}
        snapshot['stock_feed'] = {
            'stage': last.get('stage') or status,
            'detail': last.get('detail') or (transport.get('reason') or ''),
            'preload_stock_done': native_load['done'],
            'preload_stock_total': native_load['total'],
            'preload_stock_counter_source': native_load['source'],
            'state': last.get('state') or 'waiting',
            'updated_at_ms': last.get('updated_at_ms') or 0,
            'subscribed': len(getattr(runner, 'subscriptions', []) or []) if runner else 0,
            'last_frame_ms': int(float(getattr(runner, '_watch_feed_last_epoch', 0) or 0) * 1000) if runner else 0,
            'transport': transport,
            'preload': preload,
            # Distinguish authenticated socket, received trade, and ordered
            # strategy processing. Snapshot is read-only and never orders.
            'ws_session_confirmed': bool(getattr(runner,'_confirmed_ws_session',False)) if runner else False,
            'receive_queue_depth': getattr(runner,'_feed_message_queue',None).qsize()
                if runner is not None and getattr(runner,'_feed_message_queue',None) is not None else 0,
            'receive_queue_high_water': int(getattr(runner,'_feed_high_water',0) or 0) if runner else 0,
            'receive_packets_processed': int(getattr(runner,'_feed_processed',0) or 0) if runner else 0,
            'receive_frames_discarded': int(getattr(runner,'_feed_discarded',0) or 0) if runner else 0,
            'receive_queue_overflows': int(getattr(runner,'_feed_overflows',0) or 0) if runner else 0,
            'receive_max_lag_ms': round(float(getattr(runner,'_feed_lag_max_ms',0) or 0),1)
                if runner else 0.0,
            'last_trade_age_sec': round(max(0.0,time.time()-float(getattr(runner,
                '_watch_feed_last_epoch',0) or 0)),1) if runner and getattr(runner,
                '_watch_feed_last_epoch',0) else None,
        }
        # v081 startup readiness is read-only telemetry from BOTH existing
        # exchange sessions. No second login/socket and no synthetic ticks.
        now_mono=time.monotonic()
        cr=getattr(self,'coin_runner',None)
        def preload(runner):
            try:return runner.preload_gate.snapshot() if runner else {}
            except (AttributeError,ValueError,TypeError,RuntimeError):return {}
        stock_gate=preload(runner)
        coin_gate=preload(cr)
        stock_keys=bool(getattr(getattr(self,'kiwoom',None),'key','') and
                        getattr(getattr(self,'kiwoom',None),'secret',''))
        stock_session=False
        try:
            from d_live import now_kst,calendar_status,load_closed_overrides,live_session
            current=now_kst()
            open_day,_=calendar_status(current,load_closed_overrides(runner.store.root)) if runner else (False,'')
            stock_session=bool(open_day and (live_session(current,'KRX') or
                                                   live_session(current,'NXT')))
        except (ImportError,AttributeError,ValueError,TypeError,OSError):
            # Unknown calendar is not proof of a working stock feed.
            stock_session=bool(stock_keys)
        quote_stamp=float(getattr(cr,'_last_ws_quote',0) or 0) if cr else 0.0
        frame_stamp=float(getattr(cr,'_ws_last_frame',0) or 0) if cr else 0.0
        coin_socket=bool(cr is not None and getattr(cr,'_ws_connected',False) and
                         frame_stamp>0 and 0<=now_mono-frame_stamp<=15.0)
        coin_trade=bool(coin_socket and quote_stamp>0 and
                        0<=now_mono-quote_stamp<=15.0)
        coin_node=snapshot.get('coin') or {}
        coin_node['rest_connected']=bool(coin_node.get('connected',False))
        # REST marks alone can never be reported as an authenticated live WS.
        coin_node['connected']=coin_trade
        snapshot['coin']=coin_node
        trend=getattr(self,'market_trend',None)
        # Read-only TOP40 comparison: requested vs submitted vs genuine frames.
        # This is diagnostic evidence, not a synthetic quote or execution gate.
        try:
            trend_codes=()
            if trend is not None:
                with trend.lock:
                    trend_codes=tuple(trend._candidates.get('coin',{}))
            if cr is not None and hasattr(cr,'_v084_ws_health'):
                snapshot['upbit_ws_top40_v084']=cr._v084_ws_health(trend_codes)
        except Exception as exc:
            snapshot['upbit_ws_top40_v084']={'diagnostic_error':type(exc).__name__}
        warm={}
        for m in ('stock','coin'):
            required=0
            loaded=0
            try:
                if trend:
                    with trend.lock:
                        required=len(trend._candidates.get(m,{}) or {})*4
                        loaded=sum(1 for k,rows in trend._history.items()
                                   if k[0]==m and len(rows)>=21)
            except (AttributeError,TypeError,ValueError,RuntimeError):
                pass
            warm[m]={'rsi20_ready':loaded,'candidate_frames':required}
        stock_ws=bool(stock.get('connected',False))
        stock_subs=len(getattr(runner,'subscriptions',[]) or []) if runner else 0
        coin_codes=len(getattr(cr,'tracked',[]) or []) if cr else 0
        coin_watching=bool(cr is not None and getattr(cr,'ws_thread',None) is not None and
                           cr.ws_thread.is_alive())
        snapshot['startup_preload_v081']={
            'updated_at_ms':int(time.time()*1000),
            'engine_initialized':bool(getattr(self,'started',False)),
            'stock':{'keys_present':stock_keys,'session_required':stock_session,
                     'ws_ready':stock_ws,'subscriptions':stock_subs,
                     'preload':stock_gate,
                     'state':str(stock.get('status') or '')[:160]},
            'coin':{'session_required':True,'ws_session':coin_socket,
                    'ws_ready':coin_trade,'ws_supervisor_running':coin_watching,
                    'subscriptions_requested':coin_codes,
                    'preload':coin_gate,
                    'state':str(coin_node.get('status') or '')[:160]},
            'rsi_history':warm,
            'both_feeds_available':bool(coin_trade and
                (not stock_session or not stock_keys or stock_ws))}
        # Native source-only startup audit. Observe the ORIGINAL ongoing
        # missing-candle recovery, venue-qualified 0B/0D and Upbit WS book.
        # Never start a second socket or convert last/REST marks to fills.
        try:
            from market_feed_health_v004 import snapshot as _v016_feed
            from preload_audit_v016 import audit as _v016_audit
            snapshot['market_feed_health_v004']=_v016_feed(self)
            snapshot['startup_preload_v081']['work_audit']=_v016_audit(self,snapshot)
        except Exception as preflight_error:
            self._err('preload_audit_v016',preflight_error)
            snapshot['startup_preload_v081']['work_audit']={
                'status':'AUDIT_FAILED','ready':False,'percent':0,
                'work_units_done':0,'work_units_total':0,
                'first_missing':type(preflight_error).__name__}
        # Publish tiny authentic held-stock 0B/official REST prices in the
        # 2.5-second heartbeat. The slow 6.5-17-second full SQLite view
        # cannot be the only route for prices or the trading executor.
        try:
            from stock_quote_truth_v066 import live_quote_snapshot
            snapshot['stock_quote_live_v086']=live_quote_snapshot(runner)
        except (AttributeError,ValueError,RuntimeError,TypeError) as quote_error:
            snapshot['stock_quote_live_v086']={'quotes':{},'revision':'',
                'error':type(quote_error).__name__}
        return json.dumps(snapshot, ensure_ascii=False, default=str)
    except Exception:
        return raw

_core.Runtime._stock_progress = _phone_stock_progress
_core.Runtime.snapshot = _phone_runtime_snapshot
_stock_feed_core.LivePaperRunner.run = _phone_guard_stock_run

# v081: The original coin supervisor previously waited up to 75 seconds for
# OHLCV/ledger preload before opening its *first* public Upbit websocket.
# Start the SAME single existing socket immediately after a real universe is
# confirmed, then keep REST/history preparation going in the original worker.
# Early WS frames prime real prices/books but cannot dispatch new entries
# before existing ledger/preload verification reports ready.
import d_upbit as _v081_upbit
_V081_COIN_UNIVERSE_ORIG=_v081_upbit.UpbitPaperRunner._universe
_V081_COIN_WS_EVENT_ORIG=_v081_upbit.UpbitPaperRunner._enqueue_ws_event

def _v081_coin_universe_then_single_ws(self,*args,**kwargs):
    result=_V081_COIN_UNIVERSE_ORIG(self,*args,**kwargs)
    if (getattr(self,'tracked',None) and not self.stop_event.is_set()
            and not self.preload_gate.snapshot().get('ready',False)):
        # _ensure_ws_workers() is the original idempotent worker supervisor;
        # no new socket implementation, no duplicate login, no private keys.
        try:self._ensure_ws_workers()
        except (AttributeError,RuntimeError,OSError,ValueError) as exc:
            try:self._safe_ws_issue('V081_PRELOAD_WS_START',
                                    type(exc).__name__+': '+str(exc)[:130])
            except (OSError,RuntimeError,AttributeError):pass
    return result

def _v081_coin_verified_ws_prime_before_orders(self,kind,*args,**kwargs):
    if (kind in ('book','mark') and
        not self.preload_gate.snapshot().get('ready',False)):
        # _ws() already stores genuine ticker in latest_prices and real book
        # in last_book. Ignore broker/paper-entry side effects during preload.
        # The original scan thread also waits until preload completes.
        return False
    return _V081_COIN_WS_EVENT_ORIG(self,kind,*args,**kwargs)

_v081_upbit.UpbitPaperRunner._universe=_v081_coin_universe_then_single_ws
_v081_upbit.UpbitPaperRunner._enqueue_ws_event=_v081_coin_verified_ws_prime_before_orders

# 0.2.3 (#137): keep only the NEWEST order-book frame per market instead of queueing every frame
# (a full queue dropped the newest frames and the worker processed minutes-old books).
from ws_book_coalesce_v0230 import install as _install_ws_book_coalesce_v0230
_install_ws_book_coalesce_v0230(_v081_upbit)

_ORIG_PATCH_GMO = _core.Runtime._patch_gmo
_ORIG_STOP = _core.Runtime.stop
_ORIG_STOCK_AT_LEDGER = _core.Runtime._stock_at_ledger
# 0.4.6 (#161): the screen refresh must not hold the trading ledger gate (22 of 60 holds >= 5 s were UI threads).
try:
    import ui_ledger_ro_v0460 as _ui_ro
    _ORIG_STOCK_AT_LEDGER = _ui_ro.stock_at_ledger_ro
except Exception:
    _ui_ro = None
_ORIG_HUB_RECORDS = _core.Runtime.hub_records
_FRAMES=("5m","30m","60m","day")




def _upbit_jwt_hs512(self, params=None):
    head=self._b64(b'{"alg":"HS512","typ":"JWT"}')
    payload={'access_key':self.access,'nonce':str(uuid.uuid4())}
    if params:
        q=urlencode([(k,str(v)) for k,v in params.items()])
        payload['query_hash']=hashlib.sha512(q.encode()).hexdigest()
        payload['query_hash_alg']='SHA512'
    body=self._b64(json.dumps(payload,separators=(',',':')).encode())
    sig=hmac.new(self.secret.encode(),(head+'.'+body).encode(),hashlib.sha512).digest()
    return head+'.'+body+'.'+self._b64(sig)



def _sell_net_stock(e, pos):
    try:
        if str(e.get('side') or '').upper()!='SELL':
            return None
        q=float(e.get('qty') or 0)
        eq=max(1.0,float(pos.get('entry_qty') or q or 1))
        entry=float(pos.get('entry_price') or e.get('price') or 0)
        entry_fee=float(pos.get('entry_fee') or 0)*(q/eq)
        return round((float(e.get('price') or 0)-entry)*q-entry_fee-float(e.get('fees') or 0)-float(e.get('tax') or 0))
    except Exception:
        return None



def _closed_position_win_rate(positions, executions):
    closed={int(p.get('id') or 0) for p in positions or [] if str(p.get('status') or '').upper()=='CLOSED' and int(p.get('id') or 0)>0}
    if not closed:return None
    pnl={pid:0.0 for pid in closed}
    seen=set()
    for e in executions or []:
        try:pid=int(e.get('position_id') or 0)
        except Exception:continue
        if pid not in closed or str(e.get('side') or '').upper()!='SELL':continue
        val=e.get('net_profit')
        if val is None:val=e.get('net_pnl')
        if val is None:val=e.get('net_realized')
        try:pnl[pid]+=float(val or 0);seen.add(pid)
        except Exception:pass
    evaluated=[pid for pid in closed if pid in seen]
    if not evaluated:return None
    return round(sum(1 for pid in evaluated if pnl.get(pid,0)>0)*100.0/len(evaluated),2)


def _target_summary(rows, mode):
    hit=miss=pending=0
    for p in rows or []:
        status=str(p.get('status') or '').upper()
        reached=False
        if mode in ('stock_at','stock_ma'):
            # Price HIGH alone is virtual market observation, not an actual fill.
            reached=bool(p.get('first_sold'))
        elif mode=='coin':
            try:reached=float(p.get('first_sold_qty') or 0)>0
            except Exception:reached=False
        elif mode=='close':
            reached=bool(p.get('first_sold'))
        if reached: hit+=1
        elif status=='CLOSED' or (mode=='stock_at' and int(p.get('qty') or 0)<=0): miss+=1
        else: pending+=1
    evaluated=hit+miss
    eligible=hit+miss+pending
    # Confirmed target-hit share of every entered position. Old percentage
    # was hit/(hit+miss), which advertises 100% for 1 HIT and 9 PENDING.
    # Keep conditional decided rate separately for forensic comparisons.
    return {'evaluated':evaluated,'eligible':eligible,'hit':hit,'miss':miss,
            'pending':pending,
            'decided_rate_pct':round(hit*100.0/evaluated,2) if evaluated else None,
            'rate_pct':round(hit*100.0/eligible,2) if eligible else None}


def _research_scope(items):
    actual=[x for x in items if bool(x.get('bought'))]
    virtual=[x for x in items if not bool(x.get('bought'))]
    actual_done=[x for x in actual if x.get('outcome') in ('HIT','MISS')]
    virtual_entered=[x for x in virtual if bool(x.get('entry_observed'))]
    virtual_done=[x for x in virtual_entered if x.get('outcome') in ('HIT','MISS')]
    def summary(rows,all_rows):
        h=sum(1 for x in rows if x.get('outcome')=='HIT')
        m=len(rows)-h
        # Previous rate_pct was HIT/(HIT+MISS), so one HIT plus nine PENDING
        # displayed an unjustified 100%. Show verified-hit share of all entries
        # and separately expose conditional decided rate as an audit statistic.
        return {'evaluated':len(rows),'hit':h,'miss':m,
                'pending':max(0,len(all_rows)-len(rows)),
                'eligible':len(all_rows),
                'decided_rate_pct':round(h*100.0/len(rows),2) if rows else None,
                'rate_pct':round(h*100.0/len(all_rows),2) if all_rows else None}
    out={'signals':len(items),'bought':len(actual),'not_bought':len(virtual),
         'virtual_entry':len(virtual_entered)}
    out['actual_summary']=summary(actual_done,actual)
    out['virtual_summary']=summary(virtual_done,virtual_entered)
    return out


def _research_bands(items):
    out=[]
    for lo,hi,label in ((0,20,'0~20점'),(21,40,'21~40점'),(41,60,'41~60점'),(61,80,'61~80점'),(81,100,'81~100점')):
        part=[]
        for x in items:
            try:
                sc=float(x.get('score'))
                if lo<=sc<=hi: part.append(x)
            except Exception: pass
        q=_research_scope(part)
        a=q['actual_summary'];v=q['virtual_summary']
        out.append({'band':label,'signals':q['signals'],'bought':q['bought'],'not_bought':q['not_bought'],
                    'virtual_entry':q['virtual_entry'],
                    'actual_evaluated':a['evaluated'],'actual_hit':a['hit'],'actual_miss':a['miss'],
                    'actual_pending':a['pending'],'actual_rate_pct':a['rate_pct'],
                    'virtual_evaluated':v['evaluated'],'virtual_hit':v['hit'],'virtual_miss':v['miss'],
                    'virtual_pending':v['pending'],'virtual_rate_pct':v['rate_pct']})
    return out


def _research_record(items, score_items=None):
    q=_research_scope(items)
    a=q['actual_summary'];v=q['virtual_summary']
    return {'schema':'phone-v078-research','items':items,'score_items':score_items if score_items is not None else items,
            'signals':q['signals'],'bought':q['bought'],'not_bought':q['not_bought'],'virtual_entry':q['virtual_entry'],
            'actual_summary':a,'virtual_summary':v,'bands':_research_bands(items),
            'actual_rate_pct':a['rate_pct'],'virtual_rate_pct':v['rate_pct']}


def _research_first_target_fills(db):
    """Recorded FIRST-target SELL executions only; never use candle highs.

    The result is a per-position first observed actual account/PAPER fill.
    Missing columns or missing DB tables cannot create a fictitious sale.
    """
    fills={}
    try:
        # The original UI window used the latest 30,000 *all* fills, which
        # silently discarded older first-target SELL evidence and caused
        # '목표달성, 매도가 미기록' despite verified EXE fills existing.
        records=_core._db_rows(db, """SELECT * FROM executions
            WHERE UPPER(COALESCE(side,'')) IN ('SELL','S','매도')
              AND (UPPER(COALESCE(reason,'')) LIKE '%FIRST_TARGET%'
                   OR UPPER(COALESCE(reason,'')) LIKE '%FIRST_TP%'
                   OR UPPER(COALESCE(reason,'')) LIKE '%TP_FIRST%'
                   OR UPPER(COALESCE(reason,'')) LIKE '%TP1%'
                   OR reason LIKE '%1차 목표%' OR reason LIKE '%1차_목표%')
            ORDER BY id DESC""")
    except Exception:
        raise
    for row in reversed(records):
        side=str(row.get('side') or '').upper()
        if side not in ('SELL','S','매도'):
            continue
        reason=str(row.get('reason') or '').upper().replace('-','_')
        if not any(mark in reason for mark in
                   ('FIRST_TARGET','FIRST_TP','TP_FIRST','TP1','1차 목표','1차_목표')):
            continue
        try:
            position_id=int(row.get('position_id') or 0)
            price=float(row.get('price') or 0)
        except (ValueError,TypeError):
            continue
        at=row.get('ts') or row.get('at')
        if position_id>0 and price>0 and at:
            fills[position_id]={'sell_price':row.get('price'),
                'sell_at':at,'sell_evidence':'ORIGINAL_FIRST_TARGET_EXECUTION'}
    return fills


def _gmo_fill_evidence(candidate, research, buys, sells):
    """Use only PAPER fills linked to the D+1 entry; avoid later rebuys."""
    if not research.get('paper_bought'):
        return {}
    code=str(candidate.get('code') or '')
    entry=str(research.get('entry_date') or candidate.get('entry_date') or '')
    if len(entry)>=8 and entry[:8].isdigit():
        entry=entry[:4]+'-'+entry[4:6]+'-'+entry[6:8]
    entry=entry[:10]
    all_buys=sorted((x for x in buys if isinstance(x,dict) and
                     str(x.get('code') or '')==code and x.get('time') and x.get('qty')),
                    key=lambda x:str(x.get('time')))
    matched=[x for x in all_buys if str(x['time'])[:10]==entry]
    if not matched:
        return {}
    start=max(str(x['time']) for x in matched)
    next_buy=min((str(x['time']) for x in all_buys
                  if str(x['time'])>start),default=None)
    out={'buy_price':research.get('paper_buy_price'),'buy_at':start,
         'buy_evidence':'GMO_PAPER_BUY_FILL'}
    if not research.get('paper_first_target_filled'):
        return out
    attributed=[]
    for x in sells:
        if not isinstance(x,dict):
            continue
        when=str(x.get('time') or '')
        if str(x.get('code') or '')!=code or not when or when<start or (
                next_buy is not None and when>=next_buy):
            continue
        purpose=str(x.get('purpose') or '').upper()
        if not (purpose.startswith('TP') and ('FIRST' in purpose or 'SMALL_FULL' in purpose)):
            continue
        try:
            if float(x.get('price') or 0)<=0 or float(x.get('qty') or 0)<=0:
                continue
        except (ValueError,TypeError):
            continue
        attributed.append(x)
    if attributed:
        first=min(attributed,key=lambda x:str(x.get('time')))
        out.update({'sell_price':first['price'],'sell_at':first['time'],
                    'sell_evidence':'GMO_PAPER_FIRST_TARGET_FILL'})
    return out


def _stock_at_research(root):
    db=Path(root)/'stock'/'ARTITRADE_D.sqlite3'
    candidates=_core._db_rows(db, """SELECT signal_id,tf,code,name,signal_ts AS at,ref_ts,
        diverse_11_score AS score,limit_price,first_target FROM candidate_snapshots
        ORDER BY signal_id DESC LIMIT 3000""")
    positions=_core._db_rows(db,"SELECT * FROM positions")
    first_target_sells=_research_first_target_fills(db)
    from research_evidence_v036 import from_at_forward
    shadows={int(x.get('signal_id') or 0):x for x in _core._db_rows(db,
        "SELECT signal_id,state,entry_ts,entry_price,peak,realized_net_krw,sold_at FROM stock_shadow_trials")}
    shadow_target_sales={int(x.get('signal_id') or 0):x for x in _core._db_rows(db,
        "SELECT * FROM stock_shadow_fills WHERE reason='FIRST_TARGET_40PCT'")}
    target_fills=set(shadow_target_sales)
    actual={(str(x.get('tf')),str(x.get('code')),str(x.get('ref_ts'))):x for x in positions}
    decisions={int(x.get('signal_id') or 0):x for x in _core._db_rows(db,
        "SELECT signal_id,decision_at_evaluation,reason_at_evaluation,origin_10_rank,"
        "diverse_11_rank FROM candidate_cohort_decisions")}
    forward={int(r.get('signal_id') or 0):r for r in _core._db_rows(db,
        "SELECT signal_id,minute_count,unobserved_session_minutes,last_close,"
        "min_price,max_price,first_limit_touch_ts,first_target_after_touch_ts,"
        "observation_basis,last_ts FROM candidate_forward")}
    items=[]
    for x in candidates:
        key=(str(x.get('tf')),str(x.get('code')),str(x.get('ref_ts')))
        decision=decisions.get(int(x.get('signal_id') or 0),{})
        p=actual.get(key);sh=shadows.get(int(x.get('signal_id') or 0))
        trail=forward.get(int(x.get('signal_id') or 0),{})
        bought=p is not None;entry_observed=bought or bool(sh and sh.get('entry_ts'))
        outcome='PENDING';closed=False
        if bought:
            # The original EXE marks first_sold only after the sell execution.
            # A peak merely crossing the limit must stay in pending state.
            reached=bool(p.get('first_sold'))
            closed=str(p.get('status') or '').upper()=='CLOSED'
            outcome='HIT' if reached else 'MISS' if closed else 'PENDING'
        elif sh and sh.get('entry_ts'):
            closed=str(sh.get('state') or '').upper()=='CLOSED'
            outcome='HIT' if int(x.get('signal_id') or 0) in target_fills else 'MISS' if closed else 'PENDING'
        items.append({'at':x.get('at'),'tf':x.get('tf'),'strategy':x.get('tf'),'code':x.get('code'),
            'name':x.get('name'),'ref_ts':x.get('ref_ts'),'score':x.get('score'),
            'bought':bought,'entry_observed':entry_observed,'closed':closed,'outcome':outcome,
            'target_hit': True if outcome=='HIT' else False if outcome=='MISS' else None,
            'entry_status':'ACTUAL_FILLED' if bought else 'VIRTUAL_SHADOW' if entry_observed else 'UNSELECTED',
            'unselected':not bought,
             'unselected_reason':decision.get('reason_at_evaluation') if not bought else None,
             'limit_price':x.get('limit_price'),'first_target':x.get('first_target'),
             'observation_minutes':trail.get('minute_count',0),
             'missing_observation_minutes':trail.get('unobserved_session_minutes',0),
             'observed_last_price':trail.get('last_close'),
             # max_price is an aggregate with NO timestamp for the peak.
             # candidate_forward.last_ts is the LAST observed tick, not the
             # peak's timestamp. Never claim a 5,450 KRW max occurred that day.
             # Keep source values for audit only, not user-visible high prices.
             'observed_max_unverified':trail.get('max_price'),
             'observed_min_unverified':trail.get('min_price'),
             'observed_max_price':None,
             'observed_min_price':None,
             'observed_max_evidence':'AGGREGATE_NO_PEAK_TIMESTAMP',
             'forward_high_status':'UNVERIFIED_NO_SOURCE_BAR',
             'first_observed_limit_touch':trail.get('first_limit_touch_ts'),
             'first_observed_target_after_touch':trail.get('first_target_after_touch_ts'),
             'observation_provenance':trail.get('observation_basis'),
             'last_observed_at':trail.get('last_ts'),
             'observed_max_at':None,
             'observation_last_at_is_not_peak_at':True,
             'signal_id':x.get('signal_id'),
            'decision':decision.get('decision_at_evaluation'),
            'origin_rank':decision.get('origin_10_rank'),
            'diverse_rank':decision.get('diverse_11_rank'),
            'reason':decision.get('reason_at_evaluation') or '선정 근거 원본 별도 저장'})
        item=items[-1]
        item.update(from_at_forward(item))
        if bought:
            item.update({'buy_price':p.get('entry_price'),'buy_at':p.get('entry_ts'),
                         'buy_evidence':'ORIGINAL_POSITION'})
            if outcome=='HIT':
                item.update(first_target_sells.get(int(p.get('id') or 0),{}))
        elif sh and sh.get('entry_ts'):
            item.update({'virtual_buy_price':sh.get('entry_price'),
                         'virtual_buy_at':sh.get('entry_ts')})
            if outcome=='HIT':
                virtual=shadow_target_sales.get(int(x.get('signal_id') or 0),{})
                if virtual:
                    item.update({'virtual_sell_price':virtual.get('price'),
                                 'virtual_sell_at':virtual.get('minute_ts'),
                                 'virtual_sell_evidence':'STOCK_SHADOW_1M_FILL'})
    # Exact source stock code + actual (or shadow-observed) entry timestamp.
    # API queries are queued off the hub/trading thread and keep their own
    # stock/forward_high_v041.sqlite3 research cache.
    try:
        from stock_forward_high_v041 import queue as queue_forward_high, enrich
        queue_forward_high(root,items)
        enrich(root,items)
    except Exception:
        pass
    return _research_record(items,candidates)


def _stock_ma_research(root):
    from ma_epoch_v062 import stock_active
    db=stock_active(root)
    candidates=_core._db_rows(db,"SELECT id,tf,code,name,ref_ts,created_at AS at,score,status,target,features FROM candidates ORDER BY id DESC LIMIT 3000")
    positions=_core._db_rows(db,"SELECT * FROM positions")
    first_target_sells=_research_first_target_fills(db)
    pos={int(x.get('candidate_id') or 0):x for x in positions if x.get('candidate_id') is not None}
    items=[]
    for x in candidates:
        p=pos.get(int(x.get('id') or 0));bought=p is not None
        outcome='PENDING';closed=False
        if p:
            closed=str(p.get('status') or '').upper()=='CLOSED'
            reached=bool(p.get('first_sold'))
            outcome='HIT' if reached else 'MISS' if closed else 'PENDING'
        items.append({'at':x.get('at'),'tf':x.get('tf'),'strategy':'MA','code':x.get('code'),'name':x.get('name'),
            'ref_ts':x.get('ref_ts'),'score':x.get('score'),'bought':bought,'entry_observed':bought,
            'closed':closed,'outcome':outcome,'target_hit':True if outcome=='HIT' else False if outcome=='MISS' else None,
            'entry_status':'ACTUAL_FILLED' if bought else 'UNSELECTED',
             'unselected':not bought,'unselected_reason':x.get('status') if not bought else None,
            'candidate_id':x.get('id'),'decision':x.get('status'),
             'features':x.get('features'),
            'reason':x.get('status') or '판단 근거 별도 저장'})
        if bought:
            items[-1].update({'buy_price':p.get('entry'),
                'buy_at':p.get('entry_at'),'buy_evidence':'ORIGINAL_MA_POSITION'})
            if outcome=='HIT':
                items[-1].update(first_target_sells.get(int(p.get('id') or 0),{}))
    # The original EXE records *every* examined reference in 'research',
    # including rejected signals which never enter the candidates table.
    # Treat the independent research row as evidence, not as a paper fill.
    recorded={(str(x.get('tf') or ''),str(x.get('code') or ''),
               str(x.get('ref_ts') or '')) for x in candidates}
    decisions=_core._db_rows(db, "SELECT id,ts AS at,tf,code,ref_ts,verdict,detail "
        "FROM research ORDER BY id DESC LIMIT 3000")
    for d in decisions:
        key=(str(d.get('tf') or ''),str(d.get('code') or ''),
             str(d.get('ref_ts') or ''))
        if key in recorded:
            continue
        items.append({'at':d.get('at'),'tf':d.get('tf'),'strategy':'MA',
            'code':d.get('code'),'ref_ts':d.get('ref_ts'),
            'score':None,'bought':False,'entry_observed':False,'closed':False,
            'outcome':'PENDING','target_hit':None,'entry_status':'RESEARCH_UNSELECTED',
            'unselected':True,'unselected_reason':d.get('verdict'),
            'decision':d.get('verdict'),'reason':d.get('detail') or d.get('verdict'),
            'research_id':d.get('id'),'evidence_source':'EXE_MA_RESEARCH'})
    items.sort(key=lambda x:str(x.get('at') or ''),reverse=True)
    return _research_record(items[:3000],decisions)


def _coin_research(root,which):
    from ma_epoch_v062 import coin_active
    db=coin_active(root) if which=='ma' else Path(root)/'coin'/which/'upbit_paper.sqlite3'
    candidates=_core._db_rows(db,"SELECT id,tf,market,name,ref_at AS ref_ts,created_at AS at,score,limit_price,target,reason FROM candidates ORDER BY id DESC LIMIT 3000")
    positions=_core._db_rows(db,"SELECT * FROM positions")
    first_target_sells=_research_first_target_fills(db)
    actual={(str(x.get('tf')),str(x.get('market')),str(x.get('ref_at'))):x for x in positions if str(x.get('status') or '')!='RESET_B_V1'}
    shadows={int(x.get('candidate_id') or 0):x for x in _core._db_rows(db,
        "SELECT candidate_id,state,entry_at,entry_price,peak,realized_net,sell_at FROM coin_shadow_trials")}
    decisions={}
    for decision in _core._db_rows(db,"SELECT candidate_id,decision,reason,competing_score,at FROM candidate_decisions ORDER BY rowid DESC"):
        decisions.setdefault(int(decision.get('candidate_id') or 0),decision)
    # The EXE's recorded UPBIT_BOOK_BID 1-minute shadow path is the
    # authoritative VIRTUAL high observation, not a virtual/real SELL.
    # Read only recently displayed candidate IDs and apply their own target.
    from research_evidence_v036 import attach_coin_virtual_goal
    book_observed=_core._db_rows(db,"""SELECT c.id AS candidate_id,
        MIN(b.last_at) AS observed_target_at,
        CAST(c.target AS REAL) AS observed_target_price
        FROM (SELECT id,target FROM candidates ORDER BY id DESC LIMIT 3000) AS c
        JOIN coin_shadow_trials t ON t.candidate_id=c.id
        JOIN coin_shadow_book_path b ON b.candidate_id=c.id
        WHERE b.source='UPBIT_BOOK_BID' AND t.entry_at<>''
        AND b.last_at>=t.entry_at
        AND CAST(b.bid_high AS REAL)>=CAST(c.target AS REAL)
        AND CAST(c.target AS REAL)>0
        GROUP BY c.id""")
    observed_by_id={
        int(x['candidate_id']):{
            'observed_target_price':x['observed_target_price'],
            'observed_target_at':x['observed_target_at'],
            'observed_target_evidence':'COIN_BOOK_BID_HIGH_NOT_FILL'}
        for x in book_observed if x.get('candidate_id') is not None}
    target_fills=set()
    shadow_target_sales={}
    for x in _core._db_rows(db,"SELECT * FROM coin_shadow_fills WHERE side='SELL'"):
        reason=str(x.get('reason') or '').upper()
        if 'FIRST_TARGET' in reason or '1차 목표' in reason or 'FIRST' in reason and 'TARGET' in reason:
            cid=int(x.get('candidate_id') or 0)
            target_fills.add(cid)
            shadow_target_sales.setdefault(cid,x)
    items=[]
    for x in candidates:
        key=(str(x.get('tf')),str(x.get('market')),str(x.get('ref_ts')))
        p=actual.get(key);sh=shadows.get(int(x.get('id') or 0))
        selection=decisions.get(int(x.get('id') or 0),{})
        bought=p is not None;entry_observed=bought or bool(sh and sh.get('entry_at'))
        outcome='PENDING';closed=False
        if p:
            closed=str(p.get('status') or '').upper()=='CLOSED'
            try: reached=float(p.get('first_sold_qty') or 0)>0
            except Exception: reached=False
            outcome='HIT' if reached else 'MISS' if closed else 'PENDING'
        elif sh and sh.get('entry_at'):
            closed=str(sh.get('state') or '').upper()=='CLOSED'
            outcome='HIT' if int(x.get('id') or 0) in target_fills else 'MISS' if closed else 'PENDING'
        items.append({'at':x.get('at'),'tf':x.get('tf'),'strategy':x.get('tf'),'market':x.get('market'),
            'name':x.get('name'),'ref_ts':x.get('ref_ts'),'score':x.get('score'),
            'bought':bought,'entry_observed':entry_observed,'closed':closed,'outcome':outcome,
            'target_hit':True if outcome=='HIT' else False if outcome=='MISS' else None,
            'entry_status':'ACTUAL_FILLED' if bought else 'VIRTUAL_SHADOW' if entry_observed else 'UNSELECTED',
            'unselected':not bought,
             'unselected_reason':(selection.get('reason') or x.get('reason')) if not bought else None,
             'decision':selection.get('decision'),
             'competing_score':selection.get('competing_score'),
             'decision_at':selection.get('at'),
             'limit_price':x.get('limit_price'),'target':x.get('target'),
             'reason':selection.get('reason') or x.get('reason')})
        item=items[-1]
        if bought:
            item.update({'buy_price':p.get('entry'),'buy_at':p.get('entry_at'),
                         'buy_evidence':'ORIGINAL_COIN_POSITION'})
            if outcome=='HIT':
                item.update(first_target_sells.get(int(p.get('id') or 0),{}))
        elif sh and sh.get('entry_at'):
            item.update({'virtual_buy_price':sh.get('entry_price'),
                         'virtual_buy_at':sh.get('entry_at')})
            attach_coin_virtual_goal(item,observed_by_id.get(int(x.get('id') or 0)))
            if outcome=='HIT':
                sell=shadow_target_sales.get(int(x.get('id') or 0),{})
                if sell:
                    item.update({'virtual_sell_price':sell.get('price'),
                                 'virtual_sell_at':sell.get('at'),
                                 'virtual_sell_evidence':'COIN_SHADOW_BOOK_FILL'})
    return _research_record(items,candidates)


def _position_facts(kind,positions,db_path):
    """Attach first profit target / nearest stop-loss to each holding (display only, fail-soft)."""
    try:
        import position_view_v0200 as view
        if kind=='ma':view.enrich_ma(positions,db_path)
        elif kind=='close':view.enrich_close(positions)
        else:view.enrich_reference(positions,db_path)
    except Exception as exc:
        # 0.3.6 (#151): never silent - the error history shows WHY a card lost its target / stop.
        try:
            rt=getattr(_core,'_RUNTIME',None)
            if rt is not None:rt._err('position_facts_%s_v0360'%kind,exc)
        except Exception:
            pass


def _close_full(self):
    db=Path(self.root)/'stock'/'close'/'AT_CLOSE_PAPER.sqlite3'
    wallets=_core._db_rows(db,"SELECT * FROM wallets")
    positions=_core._db_rows(db,"SELECT * FROM positions WHERE status='OPEN' ORDER BY id DESC LIMIT 300")
    all_positions=_core._db_rows(db,"SELECT * FROM positions ORDER BY id DESC LIMIT 5000")
    executions=_core._db_rows(db,"SELECT * FROM executions ORDER BY id DESC LIMIT 3000")
    # Original phone BUY fills contain the actual executed rule and time.
    from phone_buy_evidence_v035 import attach_buy_evidence,queue_phone_buy_evidence
    from phone_buy_evidence_v035 import read_open_signal_evidence
    original_signals=read_open_signal_evidence(db,'stock','close')
    buy_records=attach_buy_evidence(positions,executions,original_signals,
                                   market='stock',strategy='close')
    queue_phone_buy_evidence(self.root,buy_records)
    _position_facts('close',positions,db)
    day=_core.datetime.now(_core.timezone(_core.timedelta(hours=9))).date().isoformat()
    pending=_core._db_rows(db, """SELECT s.id AS signal_id,s.ts AS ref_ts,s.ts AS at,s.strategy,s.code,s.name,
        s.decision,s.rank_no,s.trading_value_krw,s.price,s.feature_json AS reason
        FROM signals s WHERE s.decision='BUY' AND substr(s.ts,1,10)=?
          AND (s.entry_result IS NULL OR s.entry_result NOT LIKE 'BOUGHT %')
        ORDER BY s.id DESC LIMIT 500""",(day,))
    pos_by_id={str(p.get('id')):p for p in all_positions}
    for e in executions:
        _attach_policy_symbol(e,pos_by_id.get(str(e.get('position_id'))))
        e['at']=e.get('ts');e['net_profit']=e.get('net_realized');e['strategy']='CLOSE'
        _attach_fill_cost(e,pos_by_id.get(str(e.get('position_id'))))
    realized=sum(float(x.get('realized') or 0) for x in wallets)
    unreal=sum((float(x.get('last_price') or 0)-float(x.get('entry_price') or 0))*float(x.get('qty') or 0) for x in positions)
    win_rate=_closed_position_win_rate(all_positions,executions)
    target=_target_summary(all_positions,'close')
    bought=len(all_positions);closed=sum(1 for x in all_positions if str(x.get('status') or '').upper()=='CLOSED')
    performance={'close':{'realized_won':round(realized),'holding_won':round(unreal),'total_trades':bought,
        'bought_count':bought,'open_count':len(positions),'closed_count':closed,'pending_count':len(pending),
        'actual_target_rate_pct':target['rate_pct'],'actual_target_evaluated':target['evaluated'],
        'actual_target_hit':target['hit'],'actual_target_miss':target['miss'],'actual_target_pending':target['pending']}}
    # Explain a genuine empty CLOSE screen without fabricating candidates.
    # Read-only AT shared feed coverage vs original CW scanner scope.
    close_diag={}
    close_runner=getattr(self,'close_runner',None)
    if close_runner is not None:
        try:
            close_diag=dict(close_runner.snapshot_state() or {})
            stock_runner=getattr(self,'stock_runner',None)
            subscribed={str(x).replace('_NX','')
                        for x in (getattr(stock_runner,'subscriptions',()) or ())}
            requested=set(close_runner.shared_pinned())
            missing=sorted(requested-subscribed)
            close_diag['requested_shared_codes']=len(requested)
            close_diag['subscribed_shared_codes']=len(requested&subscribed)
            close_diag['not_subscribed_count']=len(missing)
            close_diag['not_subscribed_sample']=missing[:12]
            close_diag['actual_stock_subscriptions']=len(subscribed)
            close_diag['research_rows_retained']=len(
                self.close_store.recent_candidates(250))
            close_diag['diagnostic_scope']='READ_ONLY_ORIGINAL_CW_NO_SYNTHETIC_SIGNALS'
        except Exception as exc:
            close_diag={'error':type(exc).__name__,'diagnostic_scope':'READ_ONLY'}
    return {'schema':'phone-v078','origin':'phone','accounts':wallets,'positions':positions,'pending':pending,'executions':executions,
        'performance_by_frame':performance,'actual_target':target,'realized_pnl':round(realized),'unrealized_pnl':round(unreal),
        'total_pnl':round(realized+unreal),'win_rate':win_rate,
        'positions_count':len(positions),'pending_count':len(pending),
        'cw_runtime_diagnostic_v087':close_diag}


def _close_research(root):
    db=Path(root)/'stock'/'close'/'AT_CLOSE_PAPER.sqlite3'
    signals=_core._db_rows(db,"SELECT id,ts AS at,strategy,code,name,decision,entry_result,feature_json,reason_json FROM signals ORDER BY id DESC LIMIT 3000")
    positions=_core._db_rows(db,"SELECT * FROM positions ORDER BY id DESC LIMIT 5000")
    by_signal={int(x.get('signal_id') or 0):x for x in positions if x.get('signal_id') is not None}
    first_target_sells=_research_first_target_fills(db)
    # SELECT * keeps pre-upgrade EXE followup files readable: mfe_pct and
    # mae_pct were added later, and their absence is not a missing ledger.
    followups=_core._db_rows(db,"SELECT * FROM research_followups ORDER BY id DESC LIMIT 16000")
    by_signal_followup={};by_candidate_followup={}
    for f in followups:
        sid=f.get('source_signal_id');cid=f.get('source_candidate_id')
        if sid is not None:by_signal_followup.setdefault(int(sid),f)
        if cid is not None:by_candidate_followup.setdefault(int(cid),f)
    items=[]
    for x in signals:
        p=by_signal.get(int(x.get('id') or 0));bought=p is not None
        follow=by_signal_followup.get(int(x.get('id') or 0),{})
        outcome='PENDING';closed=False
        if p:
            closed=str(p.get('status') or '').upper()=='CLOSED'
            outcome='HIT' if bool(p.get('first_sold')) else 'MISS' if closed else 'PENDING'
        items.append({'at':x.get('at'),'strategy':'CLOSE','code':x.get('code'),'name':x.get('name'),
            'score':None,'bought':bought,'entry_observed':bought,'closed':closed,'outcome':outcome,
            'target_hit':True if outcome=='HIT' else False if outcome=='MISS' else None,
            'entry_status':'ACTUAL_FILLED' if bought else 'UNSELECTED',
            'unselected':not bought,'unselected_reason':x.get('entry_result') if not bought else None,
             'decision':x.get('decision'),'followup_horizon':follow.get('horizon'),
             'followup_status':follow.get('status'),
             'observed_return_pct':follow.get('return_pct'),
             'observed_mfe_pct':follow.get('mfe_pct'),
             'observed_mae_pct':follow.get('mae_pct'),
             'observed_price':follow.get('observed_price'),
             'observed_at':follow.get('observed_ts'),
             'reason':x.get('reason_json') or x.get('entry_result') or x.get('decision')})
        if bought:
            items[-1].update({'buy_price':p.get('entry_price'),
                'buy_at':p.get('entry_ts'),'buy_evidence':'ORIGINAL_CLOSE_POSITION'})
            if outcome=='HIT':
                items[-1].update(first_target_sells.get(int(p.get('id') or 0),{}))
    score_rows=_core._db_rows(db,"SELECT id,ts AS at,strategy,code,name,decision,near_signal,feature_json,reason_json FROM research_candidates ORDER BY id DESC LIMIT 3000")
    # The EXE separately saves rejected and near-qualifying CLOSE candidates.
    # Their forward prices are research evidence, never simulated fills.
    sig_keys={(str(r.get('strategy')),str(r.get('code')),str(r.get('at'))) for r in signals}
    for x in score_rows:
        key=(str(x.get('strategy')),str(x.get('code')),str(x.get('at')))
        if key in sig_keys:continue
        follow=by_candidate_followup.get(int(x.get('id') or 0),{})
        items.append({'at':x.get('at'),'strategy':'CLOSE','code':x.get('code'),
            'name':x.get('name'),'score':None,'bought':False,
            'entry_observed':False,'outcome':'PENDING','target_hit':None,
            'entry_status':'RESEARCH_UNSELECTED','unselected':True,
            'unselected_reason':x.get('reason_json') or x.get('decision'),
            'near_signal':bool(x.get('near_signal')),
            'decision':x.get('decision'),
            'followup_horizon':follow.get('horizon'),
            'followup_status':follow.get('status'),
            'observed_return_pct':follow.get('return_pct'),
            'observed_mfe_pct':follow.get('mfe_pct'),
            'observed_mae_pct':follow.get('mae_pct'),
            'observed_price':follow.get('observed_price'),
            'observed_at':follow.get('observed_ts'),
            'reason':x.get('reason_json') or x.get('decision')})
    items.sort(key=lambda item:str(item.get('at') or ''),reverse=True)
    return _research_record(items[:3000],score_rows)


def _ui_read_ctx(store):
    """0.4.6 (#161): UI snapshot connection - read-only, no trading gate; falls back to the old gated read if unavailable."""
    if _ui_ro is not None:
        return _ui_ro.ui_read(store)
    import contextlib

    @contextlib.contextmanager
    def gated():
        with store.lock,store.connect() as c:
            yield c
    return gated()


def _stock_at_v19_full(self, base):
    if not self.stock_runner:
        return base
    store=self.stock_runner.store
    with _ui_read_ctx(store) as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if 'v19_accounts' not in tables or 'v19_orders' not in tables:
            return base
        wallets=self.stock_runner.engine.wallet_snapshot(c)
        positions=[dict(r) for r in c.execute("""SELECT p.* FROM positions p
            WHERE p.status='OPEN' AND p.qty>0 AND (
              EXISTS(SELECT 1 FROM v19_members m WHERE m.position_id=p.id) OR
              EXISTS(SELECT 1 FROM v19_legacy_members l WHERE l.position_id=p.id))
            ORDER BY p.id DESC""").fetchall()]
        pending=[dict(r) for r in c.execute("""SELECT o.*,s.name,s.ref_ts,s.ts
            FROM v19_orders o JOIN signals s ON s.id=o.signal_id
            WHERE o.status='PENDING' AND NOT EXISTS(
              SELECT 1 FROM positions p WHERE p.code=o.code AND p.status='OPEN' AND p.qty>0
              AND (EXISTS(SELECT 1 FROM v19_members m WHERE m.position_id=p.id)
                   OR EXISTS(SELECT 1 FROM v19_legacy_members l WHERE l.position_id=p.id)))
            ORDER BY o.score DESC,o.since_ts DESC""").fetchall()]
        posmap={int(r['id']):dict(r) for r in c.execute("""SELECT p.* FROM positions p WHERE
              EXISTS(SELECT 1 FROM v19_members m WHERE m.position_id=p.id) OR
              EXISTS(SELECT 1 FROM v19_legacy_members l WHERE l.position_id=p.id)""").fetchall()}
        all_positions=list(posmap.values())
        executions=[dict(r) for r in c.execute("""SELECT e.* FROM executions e WHERE
              EXISTS(SELECT 1 FROM v19_members m WHERE m.position_id=e.position_id) OR
              EXISTS(SELECT 1 FROM v19_legacy_members l WHERE l.position_id=e.position_id)
              ORDER BY e.id DESC LIMIT 10000""").fetchall()]
        for e in executions:
            _attach_policy_symbol(e,posmap.get(int(e.get('position_id') or 0),{}))
            e['at']=e.get('ts');e['strategy']=e.get('tf') or 'AT'
            net=_sell_net_stock(e,posmap.get(int(e.get('position_id') or 0),{}))
            if net is not None:e['net_profit']=net
            _attach_fill_cost(e,posmap.get(int(e.get('position_id') or 0),{}))
        performance={}
        accounts=[]
        total_real=0.0;total_hold=0.0
        for tf in _FRAMES:
            w=wallets.get(tf) or {}
            held=[p for p in positions if str(p.get('tf') or '')==tf]
            hold=sum((float(p.get('last_price') or 0)-float(p.get('entry_price') or 0))*float(p.get('qty') or 0) for p in held)
            realized=float(w.get('realized_pnl') or 0)
            bought=int(c.execute("""SELECT COUNT(*) FROM positions p WHERE p.tf=? AND (
                EXISTS(SELECT 1 FROM v19_members m WHERE m.position_id=p.id) OR
                EXISTS(SELECT 1 FROM v19_legacy_members l WHERE l.position_id=p.id))""",(tf,)).fetchone()[0])
            open_count=len(held)
            closed=int(c.execute("""SELECT COUNT(*) FROM positions p WHERE p.tf=? AND (p.status='CLOSED' OR p.qty<=0) AND (
                EXISTS(SELECT 1 FROM v19_members m WHERE m.position_id=p.id) OR
                EXISTS(SELECT 1 FROM v19_legacy_members l WHERE l.position_id=p.id))""",(tf,)).fetchone()[0])
            pending_count=sum(1 for p in pending if str(p.get('tf') or '')==tf)
            tgt=_target_summary([p for p in all_positions if str(p.get('tf') or '')==tf],'stock_at')
            performance[tf]={
                'realized_won':round(realized),'holding_won':round(hold),
                'total_trades':bought,'bought_count':bought,'open_count':open_count,
                'closed_count':closed,'pending_count':pending_count,
                'actual_target_rate_pct':tgt['rate_pct'],'actual_target_evaluated':tgt['evaluated'],
                'actual_target_hit':tgt['hit'],'actual_target_miss':tgt['miss'],'actual_target_pending':tgt['pending']
            }
            accounts.append({'tf':tf,'strategy':'at','cash':w.get('cash',0),
                'seed':w.get('seed',2_000_000),'realized_pnl':round(realized),
                'unrealized_pnl':round(hold)})
            total_real+=realized;total_hold+=hold
    base.update({'schema':'phone-v078','origin':'phone','accounts':accounts,'positions':positions,
        'pending':pending,'executions':executions,'performance_by_frame':performance,
        'actual_target':_target_summary(all_positions,'stock_at'),
        'realized_pnl':round(total_real),'unrealized_pnl':round(total_hold),
        'total_pnl':round(total_real+total_hold),'positions_count':len(positions),
        'pending_count':len(pending)})
    base['win_rate']=_closed_position_win_rate(all_positions,executions)
    return base


def _ma_pending_diagnostics(self,snap):
    """Explain MA paper pending without changing fill/quote/real-order requirements."""
    try:
        from datetime import datetime
        from d_storage import KST
        from d_v19_portfolio import live_session
        from stock_ma_engine import trading_sessions_elapsed, _connect
        now=datetime.now(KST)
        ma=self.stock_runner.ma
        # Expiry in the Windows EXE is inside on_tick after a valid 0D quote.
        # Without a quote, stale PENDING orders can remain indefinitely.
        # Apply ONLY the original session-based expiry, never create a fill.
        last=getattr(self,'_phone_ma_expiry_at',0.0)
        if now.weekday()<5 and live_session(now,'KRX') and time.monotonic()-last>=90:
            self._phone_ma_expiry_at=time.monotonic()
            max_sessions={'5m':1,'30m':3,'60m':3,'day':8}
            expired=0
            with ma.lock, _connect(ma.db) as conn:
                candidates=conn.execute("SELECT id,tf,ref_ts FROM candidates WHERE status='PENDING' LIMIT 500").fetchall()
                for c in candidates:
                    allowed=max_sessions.get(str(c['tf']))
                    if allowed is None:continue
                    if trading_sessions_elapsed(str(c['ref_ts']),now.strftime('%Y%m%d%H%M%S'),ma.market_store.root)>allowed:
                        conn.execute("UPDATE candidates SET status='EXPIRED' WHERE id=? AND status='PENDING'",(c['id'],))
                        expired+=1
            if expired:
                ma._refresh_watched()
                snap=ma.snapshot() or {}
        quote_book=getattr(getattr(self.stock_runner,'engine',None),'book',None)
        book=getattr(quote_book,'_items',{}) or {}
        feed_ok=bool(getattr(self.stock_runner,'_confirmed_ws_session',False))
        for row in snap.get('pending') or []:
            code=str(row.get('code') or '')
            limit=int(row.get('limit_won') or 0)
            quote=book.get((code,'KRX'))
            reason='KRX 정규장 외 · 다음 거래일 대기'
            if live_session(now,'KRX'):
                if not feed_ok:reason='키움 0B/0D 실시간 구독 미확인 · WAIT'
                elif quote is None:reason='0D 실시간 호가 미수신 · 임의 체결 금지'
                else:
                    ask,bid,ask_qty,seen_at=quote
                    try:age=(now-seen_at).total_seconds()
                    except Exception:age=999
                    if not 0<=age<=3:reason='호가 정보 3초 초과 · 신규 매수 차단'
                    elif int(ask or 0)<=0 or int(bid or 0)<=0:reason='매수·매도 호가 유효성 미확인'
                    elif int(ask)>limit:reason='최우선 매도호가가 기준봉 저가 지정가 초과'
                    elif int(ask_qty or 0)<1:reason='최우선 매도호가 수량 부족'
                    else:reason='지정가 도달 · 0B 실제 체결가와 잔량 동시 확인 대기'
            row['reason']=reason
            row['blocked']=True  # Pending is NOT a filled broker/virtual position.
            row['ma_pending_diagnostic']=True
    except Exception as e:
        self._err('stock_ma_pending_diagnostics',e)
    return snap

def _stock_ma_full(self):
    if not self.stock_runner:
        return {}
    snap=_ma_pending_diagnostics(self,self.stock_runner.ma.snapshot() or {})
    perf=snap.get('performance_by_frame') or {}
    from ma_epoch_v062 import stock_active
    db=stock_active(self.root)
    all_positions=_core._db_rows(db,"SELECT * FROM positions ORDER BY id DESC LIMIT 10000")
    full_exec=_core._db_rows(db,"SELECT * FROM executions ORDER BY id DESC LIMIT 10000")
    # The original MA snapshot omits positions.entry_at and candidate.created_at.
    # Restore the *recorded* timestamps, not the reference candle time.
    full_by_id={str(row.get('id')):row for row in all_positions}
    for item in snap.get('positions') or []:
        original=full_by_id.get(str(item.get('id'))) or {}
        for field in ('entry_at','entry_date','candidate_id','first_sold','recovery'):
            if item.get(field) in (None,'') and original.get(field) not in (None,''):
                item[field]=original[field]
        if item.get('entry_price') in (None,''):
            original_entry=_first_existing(item.get('entry'),original.get('entry'))
            if original_entry is not None:item['entry_price']=original_entry
    pending_by_id={str(row.get('id')):row for row in _core._db_rows(
        db,"SELECT id,created_at,ref_ts,features FROM candidates ORDER BY id DESC LIMIT 10000")}
    for item in snap.get('pending') or []:
        original=pending_by_id.get(str(item.get('signal_id') or item.get('id'))) or {}
        if original.get('created_at') and not item.get('created_at'):
            item['created_at']=original['created_at']
        # No 'reason' column exists in the frozen MA candidates schema;
        # keep the original Korean quote/expiry diagnostic rather than invent one.
    for x in full_exec:
        _attach_policy_symbol(x,full_by_id.get(str(x.get('position_id'))))
        x['at']=x.get('ts');x['strategy']='MA'
        _attach_fill_cost(x,full_by_id.get(str(x.get('position_id'))))
    realized=sum(float((perf.get(tf) or {}).get('realized_won') or 0) for tf in _FRAMES)
    holding=sum(float((perf.get(tf) or {}).get('holding_won') or 0) for tf in _FRAMES)
    accounts=[]
    wallets=snap.get('wallets') or {}
    for tf in _FRAMES:
        w=wallets.get(tf) or {}
        tgt=_target_summary([p for p in all_positions if str(p.get('tf') or '')==tf],'stock_ma')
        perf.setdefault(tf,{})
        perf[tf].update({'actual_target_rate_pct':tgt['rate_pct'],'actual_target_evaluated':tgt['evaluated'],
                         'actual_target_hit':tgt['hit'],'actual_target_miss':tgt['miss'],'actual_target_pending':tgt['pending']})
        accounts.append({'tf':tf,'strategy':'ma','cash':w.get('cash',0),'seed':w.get('seed',2_000_000),
                         'realized_pnl':(perf.get(tf) or {}).get('realized_won',0),
                         'unrealized_pnl':(perf.get(tf) or {}).get('holding_won',0),
                         'max_positions':w.get('max_positions',4)})
    from phone_buy_evidence_v035 import attach_buy_evidence,queue_phone_buy_evidence
    # MA fills carry position_id, while selected reference is linked by
    # candidate_id. No symbol-only guessing across wallets.
    from phone_buy_evidence_v035 import read_open_signal_evidence
    ma_candidates=read_open_signal_evidence(db,'stock','ma')
    ma_positions=snap.get('positions') or []
    buy_records=attach_buy_evidence(ma_positions,full_exec,ma_candidates,
                                    market='stock',strategy='ma')
    queue_phone_buy_evidence(self.root,buy_records)
    _position_facts('ma',ma_positions,db)
    out=dict(snap)
    out['executions']=full_exec
    out['performance_by_frame']=perf
    out.update({'schema':'phone-v078','origin':'phone','accounts':accounts,
                'realized_pnl':round(realized),'unrealized_pnl':round(holding),
                'total_pnl':round(realized+holding),'actual_target':_target_summary(all_positions,'stock_ma'),
                'positions_count':len(snap.get('positions') or []),
                'pending_count':len(snap.get('pending') or [])})
    out['win_rate']=_closed_position_win_rate(all_positions,full_exec)
    return out

def _attach_policy_symbol(execution,position=None):
    """Display source-original symbol on fills which stored only position_id.

    No matching entry? Leave it unlinked rather than inventing a symbol.
    """
    if not isinstance(position,dict):
        return execution
    for field,candidates in (('code',('code','market')),
                             ('name',('name',)),
                             ('tf',('tf',))):
        if execution.get(field) not in (None,''):
            continue
        for key in candidates:
            value=position.get(key)
            if value not in (None,''):
                execution[field]=value
                break
    return execution


def _attach_fill_cost(execution,position=None):
    """Show recorded EXE position cost beside its paper/real SELL fill.

    A candidate price, reference candle or observed high cannot be used as
    an executed purchase price. Only actual entry/position ledger values count.
    The original EXE net-profit field remains authoritative after fees/taxes.
    """
    if not isinstance(execution,dict):
        return execution
    position=position if isinstance(position,dict) else {}
    side=str(execution.get('side') or '').upper()
    try:
        qty=float(execution.get('qty') or 0)
        sell_price=float(execution.get('price') or 0)
    except (TypeError,ValueError,OverflowError):
        return execution
    if not math.isfinite(qty) or qty<=0:
        return execution
    if side=='BUY' and sell_price>0 and math.isfinite(sell_price):
        execution.setdefault('buy_price',sell_price)
        execution.setdefault('buy_amount',round(qty*sell_price,4))
        return execution
    if side!='SELL':
        return execution
    entry=_first_existing(position.get('entry_price'),position.get('entry'),
                          position.get('avg_buy_price'))
    if entry is None:
        return execution
    try:entry=float(entry)
    except (TypeError,ValueError,OverflowError):return execution
    if not math.isfinite(entry) or entry<=0:
        return execution
    cost=entry*qty
    if not math.isfinite(cost) or cost<=0:
        return execution
    execution['buy_price']=entry
    execution['buy_amount']=round(cost,4)
    execution['cost_basis_origin']='EXE_POSITION_FILL'
    pnl=next((execution.get(k) for k in ('net_profit','net_realized','net_pnl')
              if execution.get(k) is not None and execution.get(k)!=''),None)
    if pnl is not None:
        try:
            profit=float(pnl)
            if math.isfinite(profit):
                execution['realized_pct']=round(profit/cost*100.0,4)
        except (TypeError,ValueError,OverflowError):
            pass
    return execution


def _first_existing(*values):
    """Use actual stored/quoted values only; never invent prices or reference candles."""
    for value in values:
        if value is not None and value != '' and str(value).lower() not in ('none','null','nan','0','0.0'):
            return value
    return None


def _normalize_coin_holdings(positions, history):
    """Join compact frame snapshots with read-only DB rows without rewriting the ledger.

    The old UI adaptation assigned entry_price=x.get('entry'), overwriting a
    legitimate pre-existing entry_price with None when the snapshot schema differed.
    """
    by_id={str(row['id']):row for row in history if row.get('id') is not None}
    by_key={}
    for row in history:
        if str(row.get('status') or '').upper() not in ('OPEN','HOLD','HOLDING'):
            continue
        tf=str(row.get('tf') or '')
        symbol=str(_first_existing(row.get('symbol'),row.get('market'),row.get('code'),row.get('ticker')) or '')
        if symbol:
            by_key.setdefault((tf,symbol),[]).append(row)
    result=[]
    for raw in positions:
        if not isinstance(raw,dict):continue
        row=dict(raw)
        db=by_id.get(str(row.get('id'))) if row.get('id') is not None else None
        if db is None:
            tf=str(row.get('tf') or '')
            symbol=str(_first_existing(row.get('symbol'),row.get('market'),row.get('code'),row.get('ticker')) or '')
            matches=by_key.get((tf,symbol),[])
            if len(matches)==1:db=matches[0]
        db=db or {}
        ref=_first_existing(row.get('ref_ts'),row.get('ref_at'),row.get('reference_ts'),
            row.get('reference_at'),row.get('base_candle_time'),row.get('ref_time'),
            db.get('ref_ts'),db.get('ref_at'),db.get('reference_ts'),db.get('reference_at'))
        entry=_first_existing(row.get('entry_price'),row.get('entry'),row.get('avg_buy_price'),
            row.get('buy_price'),row.get('avg_entry'),db.get('entry_price'),
            db.get('entry'),db.get('avg_buy_price'),db.get('buy_price'))
        mark=_first_existing(row.get('last_price'),row.get('mark'),row.get('current_price'),
            row.get('market_price'),row.get('mark_price'),db.get('last_price'),
            db.get('mark'),db.get('current_price'))
        if ref is not None:row['ref_ts']=ref
        if entry is not None:row['entry_price']=entry
        if mark is not None:row['last_price']=mark
        # Preserve real fill timestamps and actual first-sale quantity for the
        # position screen. A reference candle is never a BUY execution.
        for field in ('entry_at','first_sold_qty','entry_qty','entry_reason','reason'):
            if row.get(field) in (None,'') and db.get(field) not in (None,''):
                row[field]=db[field]
        result.append(row)
    return result


def _coin_full(ledger):
    snap=ledger.snapshot() or {}
    positions=[];accounts=[];performance={}
    with ledger.lock,ledger.connect() as c:
        all_positions=[dict(x) for x in c.execute("SELECT * FROM positions WHERE status<>'RESET_B_V1' ORDER BY id DESC LIMIT 10000").fetchall()]
        pending=[dict(x) for x in c.execute("SELECT * FROM orders WHERE status='PENDING' ORDER BY score DESC,created_at DESC LIMIT 3000").fetchall()]
        executions=[dict(x) for x in c.execute("SELECT * FROM executions ORDER BY id DESC LIMIT 10000").fetchall()]
        for tf in _FRAMES:
            frame=snap.get(tf) or {}
            pos=frame.get('positions') or []
            positions.extend(pos)
            bought=int(c.execute("SELECT COUNT(*) FROM positions WHERE tf=? AND status IN ('OPEN','CLOSED')",(tf,)).fetchone()[0])
            open_count=int(c.execute("SELECT COUNT(*) FROM positions WHERE tf=? AND status='OPEN' AND qty!='0'",(tf,)).fetchone()[0])
            closed=int(c.execute("SELECT COUNT(*) FROM positions WHERE tf=? AND status='CLOSED'",(tf,)).fetchone()[0])
            pending_count=int(c.execute("SELECT COUNT(*) FROM orders WHERE tf=? AND status='PENDING'",(tf,)).fetchone()[0])
            realized=float(frame.get('realized') or 0);holding=float(frame.get('holding') or 0)
            tgt=_target_summary([p for p in all_positions if str(p.get('tf') or '')==tf],'coin')
            performance[tf]={'realized_won':round(realized),'holding_won':round(holding),
                'total_trades':bought,'bought_count':bought,'open_count':open_count,
                'closed_count':closed,'pending_count':pending_count,
                'actual_target_rate_pct':tgt['rate_pct'],'actual_target_evaluated':tgt['evaluated'],
                'actual_target_hit':tgt['hit'],'actual_target_miss':tgt['miss'],'actual_target_pending':tgt['pending']}
            accounts.append({'tf':tf,'cash':frame.get('cash',0),'seed':frame.get('seed',2_000_000),
                'realized_pnl':round(realized),'unrealized_pnl':round(holding),
                'max_positions':frame.get('max_positions',4)})
    positions=_normalize_coin_holdings(positions,all_positions)
    if not positions:
        # Startup may not have populated the frame snapshot yet. Show only
        # truly OPEN rows from imported SQLite, marked as historical holdings.
        fallback=[dict(x) for x in all_positions
                  if str(x.get('status') or '').upper()=='OPEN'
                  and str(x.get('qty') or '0') not in ('','0','0.0')]
        positions=_normalize_coin_holdings(fallback,all_positions)
        for x in positions:x['ledger_fallback']=True
    for x in pending:
        x['ref_ts']=_first_existing(x.get('ref_ts'),x.get('ref_at'),
            x.get('reference_ts'),x.get('reference_at'))
        x['strategy']=x.get('tf')
    all_by_id={str(x.get('id')):x for x in all_positions}
    for e in executions:
        _attach_policy_symbol(e,all_by_id.get(str(e.get('position_id'))))
        e['net_profit']=e.get('net_pnl');e['strategy']=e.get('tf')
        _attach_fill_cost(e,all_by_id.get(str(e.get('position_id'))))
    from phone_buy_evidence_v035 import attach_buy_evidence,queue_phone_buy_evidence
    account_variant=str(getattr(ledger,'account_variant','original') or 'original')
    source_variant='ma' if account_variant=='ma' else 'base'
    # candidates are immutable original strategy research rows, not imported
    # hypothetical stock prices. Requiring both reference ID and position BUY.
    from phone_buy_evidence_v035 import read_open_signal_evidence
    coin_candidates=read_open_signal_evidence(ledger.path,'coin',source_variant)
    buy_records=attach_buy_evidence(positions,executions,coin_candidates,
                                   market='coin',strategy=source_variant)
    queue_phone_buy_evidence(Path(ledger.path).parents[2],buy_records)
    _position_facts('ma' if source_variant=='ma' else 'reference',positions,ledger.path)
    realized=sum(float(x.get('realized_pnl') or 0) for x in accounts)
    holding=sum(float(x.get('unrealized_pnl') or 0) for x in accounts)
    win_rate=_closed_position_win_rate(all_positions,executions)
    return {'schema':'phone-v078','origin':'phone','accounts':accounts,'positions':positions,
            'pending':pending,'executions':executions,'performance_by_frame':performance,
            'actual_target':_target_summary(all_positions,'coin'),
            'realized_pnl':round(realized),'unrealized_pnl':round(holding),
            'total_pnl':round(realized+holding),'win_rate':win_rate,
            'positions_count':len(positions),'pending_count':len(pending)}

def _research_source_audit(db,primary,evidence):
    """Read-only original SQLite count audit, independent of the 3000-row UI window.

    A missing DB or table is never reported as 'zero researched candidates'.
    All evidence tables remain stored in the original imported database.
    """
    db=Path(db)
    if not db.is_file():
        return {'source_total':-1,'source_status':'DB_MISSING','evidence_counts':{}}
    try:
        with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True,timeout=4) as conn:
            tables={r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if primary not in tables:
                return {'source_total':-1,'source_status':'TABLE_MISSING',
                        'evidence_counts':{}}
            total=int(conn.execute('SELECT COUNT(*) FROM "'+primary+'"').fetchone()[0])
            counts={}
            for name in evidence:
                if name in tables:
                    counts[name]=int(conn.execute('SELECT COUNT(*) FROM "'+name+'"').fetchone()[0])
        return {'source_total':total,'source_status':'OK','evidence_counts':counts}
    except (sqlite3.Error,OSError,ValueError) as exc:
        return {'source_total':-1,'source_status':'READ_ERROR',
                'source_error':type(exc).__name__,'evidence_counts':{}}


def _v085_preserve_research(native, upstream):
    """Preserve independently persisted strategy decisions attached to the core.

    Legacy hub builds attached V074 MA decision evidence and then discarded it
    by replacing the entire research object. This is a read-only merge: neither
    simulated balances nor confirmed fills are modified or synthesized.
    """
    if not isinstance(native,dict):native={}
    if not isinstance(upstream,dict):return native
    merged=dict(native)
    def identity(row):
        if not isinstance(row,dict):return ('invalid',str(row))
        eid=row.get('id') or row.get('event_key')
        if eid:return ('event',str(eid))
        return ('case',str(row.get('strategy') or ''),str(row.get('tf') or row.get('frame') or ''),
                str(row.get('code') or row.get('symbol') or ''),
                str(row.get('at') or row.get('ts') or row.get('created_at') or ''),
                str(row.get('ref_ts') or row.get('ref_at') or ''),
                str(row.get('reason') or row.get('decision') or ''))
    preserved=0
    for bucket in ('items','score_items'):
        fresh=list(native.get(bucket) or [])
        existing={identity(row) for row in fresh}
        for row in upstream.get(bucket) or []:
            if not isinstance(row,dict):continue
            key=identity(row)
            if key in existing:continue
            fresh.append(row.copy())
            existing.add(key)
            preserved+=1
        if fresh:
            fresh.sort(key=lambda row:str(row.get('at') or row.get('ts') or ''),reverse=True)
        merged[bucket]=fresh
    merged['loaded_items']=len(merged.get('items') or [])
    merged['upstream_decisions_preserved_v085']=preserved
    # Preserve diagnostic error state even when upstream evidence is displayed.
    return merged


def _research_history(self, out):
    """Read every strategy independently: one legacy table failure must not mask other research.

    No write to PC/phone SQLite. Results are scoped to the originating strategy
    so the UI can distinguish a genuinely empty history from a failed load.
    """
    roots={
        'AT':('stock/ARTITRADE_D.sqlite3','candidate_snapshots',
              ('candidate_events','candidate_forward','candidate_milestones',
               'candidate_cohort_decisions','candidate_research_health',
               'stock_shadow_trials','stock_shadow_fills','signals','positions','executions')),
        'MA':('stock/MA_EPOCH_V062/STOCK_MA_ENGINE_V1/ma_paper.sqlite3','candidates',
              ('positions','executions','research')),
        'CLOSE':('stock/close/AT_CLOSE_PAPER.sqlite3','signals',
                 ('research_candidates','research_followups','research_bars',
                   'research_scan_skips','research_api_events','positions','executions')),
        'COIN_BASE':('coin/base/upbit_paper.sqlite3','candidates',
                     ('candidate_decisions','coin_scan_decisions','coin_shadow_trials',
                      'coin_shadow_fills','coin_shadow_book_path','coin_trade_followup','coin_trade_price_path','positions','executions')),
        'COIN_MA':('coin/ma_epoch_v062/upbit_paper.sqlite3','candidates',
                   ('candidate_decisions','coin_scan_decisions','coin_shadow_trials',
                    'coin_shadow_fills','coin_shadow_book_path','coin_trade_followup','coin_trade_price_path','positions','executions')),
    }
    def read(source, loader):
        rel,primary,evidence=roots[source]
        audit=_research_source_audit(Path(self.root)/rel,primary,evidence)
        try:
            result=loader()
            if not isinstance(result,dict):
                raise TypeError('research result is not a dict')
            result.update(audit)
            result['loaded_items']=len(result.get('items') or [])
            result['partial_window']=(
                audit['source_total']>result['loaded_items'] if audit['source_total']>=0 else False)
            status=audit['source_status']
            if status=='OK' and audit['source_total']>0 and result['loaded_items']==0:
                status='QUERY_RETURNED_ZERO'
                result['load_error']='PC_SOURCE_ROWS_PRESENT_BUT_QUERY_EMPTY: '+source
                self._err('research_history_query_empty_'+source,
                          RuntimeError(result['load_error']))
            result['load_status']=status
            result['origin']='IMPORTED_PC_AND_PHONE'
            return result
        except Exception as exc:
            self._err('research_history_v078_'+source,exc)
            result={'schema':'phone-v078-research','origin':'IMPORTED_PC_AND_PHONE',
                    'load_status':'ERROR','load_error':source,'loaded_items':0,
                    'items':[],'score_items':[],'bands':[]}
            result.update(audit)
            return result
    stock=out.setdefault('stock',{}).setdefault('at',{})
    prior_stock=stock.get('research') or {}
    original_ma=prior_stock.get('ma') if isinstance(prior_stock,dict) else None
    stock['research']=_v085_preserve_research(
        read('AT',lambda:_stock_at_research(self.root)),prior_stock)
    stock['research']['ma']=_v085_preserve_research(
        read('MA',lambda:_stock_ma_research(self.root)),original_ma)
    out.setdefault('stock',{}).setdefault('cw',{})['research']=read(
        'CLOSE',lambda:_close_research(self.root))
    out.setdefault('coin',{}).setdefault('general',{})['research']=read(
        'COIN_BASE',lambda:_coin_research(self.root,'base'))
    coin_ma=out.setdefault('coin',{}).setdefault('ma',{})
    coin_ma['research']=_v085_preserve_research(
        read('COIN_MA',lambda:_coin_research(self.root,'ma')),
        coin_ma.get('research'))
    return out

def _v086_enrich_gmo_executions(rows):
    """Source-only cash/cost evidence for BUY and partial SELL fills.

    Original GMO engine.fill() records net_profit AFTER cost basis, sell fee
    and stock sell tax. Therefore a SELL lot's exact allocated acquisition
    cost equals sell gross minus SELL fee/tax minus the recorded net P&L.
    No candle/high, unfilled order, virtual fill or guessed basis is used.
    """
    if not isinstance(rows,list):return rows
    for fill in rows:
        if not isinstance(fill,dict):continue
        try:
            qty=float(fill.get('qty'))
            price=float(fill.get('price'))
        except (TypeError,ValueError,OverflowError):continue
        if not (math.isfinite(qty) and qty>0 and math.isfinite(price) and price>0):
            continue
        side=str(fill.get('side') or '').upper()
        if side=='BUY':
            fill['buy_amount']=round(price*qty,2)
            fill['buy_price']=price
            fill['buy_qty']=qty
            fill['gmo_buy_amount_basis']='ORIGINAL_BUY_FILL_PRICE_X_QTY'
        elif side=='SELL':
            fill['sell_qty']=qty
            try:
                net=float(fill['net_profit'])
                fees=float(fill['fees'])
                tax=float(fill['tax'])
            except (KeyError,ValueError,TypeError,OverflowError):
                # Missing fee/tax or net evidence must not become a fake ROI.
                continue
            if not all(math.isfinite(x) and x>=0 for x in (fees,tax)):
                continue
            if not math.isfinite(net):continue
            allocated_cost=price*qty-fees-tax-net
            if allocated_cost<=0 or not math.isfinite(allocated_cost):
                continue
            fill['buy_amount']=round(allocated_cost,2)
            fill['gmo_allocated_buy_cost']=round(allocated_cost,2)
            # If the original linked BUY fill supplied an exact purchase
            # price, preserve it. Otherwise show effective per-unit cost
            # WITH buy fees rather than pretend a historical fill price.
            if fill.get('buy_price') in (None,''):
                fill['buy_price']=round(allocated_cost/qty,6)
                fill['gmo_buy_unit_basis']='ALLOCATED_COST_INCLUDES_BUY_FEE'
            fill['realized_pct']=round(net/allocated_cost*100,5)
            fill['gmo_realized_pct_basis']='ORIGINAL_NET_PROFIT_OVER_ALLOCATED_COST'
    return rows


def _gmo_original_record(root):
    """Read the unmodified PC GMO SQLite + full JSON state without an engine restart.

    This is the same PC archive as the other strategies, not a second import.
    There is no synthetic execution or target result: missing evidence stays pending.
    """
    root = Path(root) / 'stock'
    state_file = root / 'PROJECTGMO_V1.0_account_state.json'
    db = root / 'PROJECTGMO_V1.0_research.sqlite3'
    state_present=state_file.is_file()
    db_present=db.is_file()
    if not (state_present or db_present):
        return None
    # Missing account JSON must not hide candidates; missing SQLite must not
    # hide stored, verified PC PAPER holdings, fills and realized cash.
    state=json.loads(state_file.read_text(encoding='utf-8')) if state_present else {}
    candidates=[];candles=[];full_count=0
    if db_present:
        with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True,timeout=4) as conn:
            conn.row_factory=sqlite3.Row
            tables={r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if 'gmo_candidates' not in tables:
                raise ValueError('GMO candidate source table missing')
            candidates=[dict(r) for r in conn.execute(
                "SELECT * FROM gmo_candidates ORDER BY event_date DESC,code LIMIT 3000")]
            full_count=int(conn.execute("SELECT COUNT(*) FROM gmo_candidates").fetchone()[0])
            if 'gmo_daily_ohlc' in tables:
                candles=[dict(r) for r in conn.execute(
                    "SELECT code,event_date,trade_date,day_index,high,final "
                    "FROM gmo_daily_ohlc WHERE day_index BETWEEN 1 AND 15")]
    buys = state.get('buy_fills') or []
    sells = state.get('fills') or []
    positions = state.get('positions') or {}
    orders = state.get('orders') or {}
    signals = state.get('signals') or {}
    if not isinstance(positions, dict) or not isinstance(orders, dict):
        raise ValueError("GMO account state positions/orders schema invalid")
    names = {str(k): str(v.get('name') or k) for k,v in signals.items() if isinstance(v,dict)}
    # Original buy_fills.time is authoritative: match current position's
    # code+entry_date, not a later rebuy's same ticker and not event D-day.
    from gmo_entry_time_v036 import entry_fill
    active = []
    for code,p in positions.items():
        if not isinstance(p,dict): continue
        qty = int(p.get('qty') or 0)
        if qty <= 0: continue
        original_buy=entry_fill(dict(p,code=code),buys) or {}
        active.append({'tf':'gmo','strategy':'gmo','code':code,'gmo':True,
            'name':p.get('name') or names.get(code,code),'qty':qty,
            'entry_price':p.get('entry_price'),'last_price':None,
            'gmo_cost_basis':p.get('cost_basis'),
            'entry_at':original_buy.get('entry_at'),
            'buy_at':original_buy.get('entry_at'),
            'buy_reason':original_buy.get('buy_reason'),
            'buy_reason_source':original_buy.get('buy_reason_source'),
            'gmo_score':p.get('gmo_score'),'status':'OPEN',
            'first_sold':bool(p.get('first_filled_qty')),
            'reason':p.get('stage'),'gmo_event_date':p.get('entry_date')})
    pending = []
    for value in orders.values():
        if not isinstance(value,dict):continue
        if str(value.get('side','')).upper()!='BUY' or str(value.get('state','')).upper() in ('FILLED','CANCELED'):continue
        code = str(value.get('code') or '')
        pending.append({'tf':'gmo','strategy':'gmo','code':code,
            'name':names.get(code,code),'qty':max(0,int(value.get('requested') or 0)-int(value.get('filled') or 0)),
            'limit_price':value.get('limit'),'at':value.get('time'),
            'reason':value.get('purpose'),'status':value.get('state')})
    executions = []
    for kind,rows in (('SELL',sells),('BUY',buys)):
        for f in rows:
            if not isinstance(f,dict):continue
            code=str(f.get('code') or '')
            executions.append({'tf':'gmo','strategy':'gmo','code':code,
                'name':names.get(code,code),'side':kind,'ts':f.get('time'),
                'qty':f.get('qty'),'price':f.get('price'),
                'net_profit':f.get('net_profit') if kind=='SELL' else None,
                'fees':f.get('fees',f.get('fee')),'tax':f.get('tax'),
                'reason':f.get('purpose')})

    # The GMO original JSON carries buy and sell fills separately. Link only
    # traceable per-symbol purchase prices; never equate an unfilled order
    # or a research opening price with a realized cost basis.
    buy_prices={}
    for fill in buys:
        if not isinstance(fill,dict):continue
        code=str(fill.get('code') or '')
        if code and fill.get('price') is not None:
            try:buy_prices.setdefault(code,set()).add(float(fill['price']))
            except (TypeError,ValueError):pass
    for e in executions:
        code=str(e.get('code') or '')
        if str(e.get('side') or '').upper()=='SELL' and len(buy_prices.get(code,set()))==1:
            unique_price=next(iter(buy_prices[code]))
            _attach_fill_cost(e,{'entry_price':unique_price})
        elif str(e.get('side') or '').upper()=='BUY':
            _attach_fill_cost(e)
    _v086_enrich_gmo_executions(executions)
    executions.sort(key=lambda x:str(x.get('ts') or ''),reverse=True)
    # Use exactly the EXE's original 15-session evidence and paper-fill
    # attribution. A candle HIGH is not a broker/paper SELL execution.
    from d_gmo_score_research_paper import research_rows
    def canonical_date(value):
        raw=str(value or '').strip()
        if len(raw)>=8 and raw[:8].isdigit() and (len(raw)==8 or raw[8] in 'T '):
            return raw[:4]+'-'+raw[4:6]+'-'+raw[6:8]
        return raw[:10]
    normalized=[dict(c,entry_date=canonical_date(c.get('entry_date')))
                for c in candidates]
    original_rows=research_rows(normalized,candles,buys,sells)
    items=[]
    for c,r in zip(candidates,original_rows):
        bought=bool(r.get('paper_bought'))
        if bought:
            # Only a confirmed first-target paper SELL fill proves success.
            outcome=('HIT' if bool(r.get('paper_first_target_filled')) else
                     'MISS' if r.get('paper_position_status')=='EXIT_SOLD' else
                     'PENDING')
        else:
            virtual_status=r.get('research_5pct_status')
            outcome=('HIT' if virtual_status=='TOUCHED_HIGH_NOT_FILL' else
                     'MISS' if virtual_status=='NOT_TOUCHED_15_FINAL_DAYS' else
                     'PENDING')
        opener=float(c.get('opening_price') or 0)
        items.append({
            'tf':'gmo','strategy':'gmo','code':c.get('code'),
            'name':c.get('name'),'at':c.get('saved_at'),
            'event_date':c.get('event_date'),'entry_date':c.get('entry_date'),
            'score':r.get('gmo_original_score'),'score_state':r.get('observation_quality'),
            'bought':bought,'entry_observed':bought or opener>0,
             'unselected':not bought,
             'unselected_reason':c.get('status') if not bought else None,
            'outcome':outcome,
            'target_hit':True if outcome=='HIT' else False if outcome=='MISS' else None,
            'reason':('실제 PAPER 매도 체결 기준' if bought else
                      '가상 D+1 공식시가 대비 5% · D+1~D+15 관측')+
                     ' · '+str(r.get('research_5pct_status') or '기록 미완료'),
            'entry_status':('ACTUAL_FILLED' if bought else
                           'VIRTUAL_OPEN_OBSERVED' if opener>0 else 'VIRTUAL_NOT_ENTERED'),
            'first_target_filled':r.get('paper_first_target_filled'),
            'paper_realized_net_won':r.get('paper_realized_net_won'),
            'sell_reason':r.get('paper_last_exit_purpose') or None,
            'last_exit_at':r.get('paper_last_exit_at') or None,
            'paper_position_status':r.get('paper_position_status'),
            'official_d1_open':r.get('official_d1_open'),
            'score_base':r.get('score_base'),
            'score_personal':r.get('score_personal'),
            'score_volume':r.get('score_volume'),
            'score_decline':r.get('score_decline'),
            'highest_observed_high':r.get('highest_observed_high'),
            'horizon_complete':r.get('horizon_complete'),
            'research_5pct_status':r.get('research_5pct_status'),
            'research_first_hit_day':r.get('first_5pct_research_hit_day')
        })
        item=items[-1]
        if bought:
            # EXE PAPER first-target SELL must be attributed to this D+1 buy.
            item.update(_gmo_fill_evidence(
                dict(c,entry_date=canonical_date(c.get('entry_date'))),r,buys,sells))
        elif opener>0:
            item.update({'virtual_buy_price':r.get('official_d1_open'),
                         'virtual_buy_at':canonical_date(c.get('entry_date')),
                         'virtual_buy_evidence':'GMO_D1_OFFICIAL_OPEN_OBSERVATION'})
        # Reconstruct first D+N 5% HIGH observation day from the source daily
        # OHLC; this is a price observation, never a SELL execution.
        try:
            hit_day=int(r.get('first_5pct_research_hit_day') or 0)
        except (ValueError,TypeError):
            hit_day=0
        if hit_day>0 and r.get('research_5pct_status')=='TOUCHED_HIGH_NOT_FILL':
            for d in candles:
                if (str(d.get('code') or '')==str(c.get('code') or '') and
                    canonical_date(d.get('event_date'))==canonical_date(c.get('event_date')) and
                    int(d.get('day_index') or 0)==hit_day and d.get('trade_date') and
                    float(d.get('high') or 0)>0):
                    item.update({'observed_target_price':d.get('high'),
                                 'observed_target_at':d.get('trade_date'),
                                 'observed_target_evidence':'GMO_D_PLUS_HIGH_NOT_FILL'})
                    break
    if not state_present:
        # Without PAPER account JSON, entry, exit and profit cannot be proven.
        for item in items:
            item.update({'bought':False,'entry_observed':False,'outcome':'PENDING',
                         'target_hit':None,'entry_status':'ACCOUNT_STATE_MISSING',
                         'reason':'PC 매매상태 JSON 없음 · 성과 판정 보류'})
    research = _research_record(items,items)
    audit = _research_source_audit(db,'gmo_candidates',
        ('gmo_flow','gmo_daily_ohlc','gmo_minute_ohlc','gmo_scan',
         'gmo_open','gmo_issues','gmo_policy_restorations'))
    research.update(audit)
    research.update({'load_status':(audit['source_status'] if state_present else
                        'ACCOUNT_STATE_MISSING'), 'loaded_items':len(items),
                      'partial_window':full_count>len(items),
                      'origin':'IMPORTED_PC_GMO_ORIGINAL',
                      'account_state_present':state_present,
                      'research_db_present':db_present})
    wallet={'tf':'gmo','strategy':'gmo','seed':(state.get('cfg') or {}).get('seed'),
            'cash':state.get('cash'),'realized_pnl':state.get('realized'),
            'unrealized_pnl':None}
    ledger={'schema':'phone-v078','origin':'IMPORTED_PC_GMO_ORIGINAL',
            'accounts':[wallet],'positions':active,'pending':pending,
            'executions':executions,'realized_pnl':state.get('realized'),
            'unrealized_pnl':None,'positions_count':len(active),
            'pending_count':len(pending),'candidate_count':full_count,
            'load_diagnostic':{'stage':('PC_ORIGINAL_LOADED' if state_present and db_present
                else 'PC_ORIGINAL_PARTIAL'), 'files_present':[
                x.name for x in (state_file,db) if x.is_file()],
                'files_expected':2}}
    return ledger,research


def _capture_pc_research_evidence_async(self):
    """Observe every source SQLite change off the UI/broker thread.

    The EXE strategy DB remains the truth. This audit logs row versions without
    changing any entry/exit rule or modifying an original research table.
    """
    now=time.monotonic()
    if getattr(self,'_phone_evidence_capture_busy',False):
        return
    if now-float(getattr(self,'_phone_evidence_last_started',0) or 0)<90.0:
        return
    self._phone_evidence_capture_busy=True
    self._phone_evidence_last_started=now
    def work():
        try:
            from android_research_audit import capture
            result=capture(self.root)
            if result.get('source_errors'):
                try:
                    self._err('research_evidence_capture_partial',
                        RuntimeError(str(result['source_errors'])[:500]))
                except Exception:
                    pass
        except Exception as error:
            try:self._err('research_evidence_capture',error)
            except Exception:pass
        finally:
            self._phone_evidence_capture_busy=False
    threading.Thread(target=work,daemon=True,
        name='artitrade-research-evidence-audit').start()


def _gmo_fill_identity(row):
    return tuple(str(row.get(k) if row.get(k) is not None else '')
                 for k in ('side','code','ts','qty','price','reason'))


def _merge_gmo_pc_evidence(live,original):
    """Read-only UI backfill of verified PAPER data; never mutate trading state."""
    if not isinstance(live,dict) or not live:
        return original
    if not isinstance(original,dict) or not original:
        return live
    merged=dict(live)
    phone_exec=list(live.get('executions') or [])
    pc_exec=list(original.get('executions') or [])
    source_counts=Counter(_gmo_fill_identity(x) for x in pc_exec)
    phone_counts=Counter(_gmo_fill_identity(x) for x in phone_exec)
    phone_in_pc=all(n<=source_counts.get(k,0) for k,n in phone_counts.items())
    remaining=Counter(phone_counts)
    combined=list(phone_exec)
    for record in pc_exec:
        key=_gmo_fill_identity(record)
        if remaining[key]>0:
            remaining[key]-=1
        else:
            combined.append(dict(record,origin='IMPORTED_PC_GMO_ORIGINAL'))
    combined.sort(key=lambda x:str(x.get('ts') or ''),reverse=True)
    merged['executions']=combined
    phone_pos=list(live.get('positions') or [])
    pc_pos=list(original.get('positions') or [])
    combined_pos=list(phone_pos)
    known_codes={str(x.get('code') or '') for x in combined_pos}
    # New phone-only fills could have closed PC holdings: don't resurrect them.
    if phone_in_pc:
        for position in pc_pos:
            code=str(position.get('code') or '')
            if code and code not in known_codes and float(position.get('qty') or 0)>0:
                combined_pos.append(dict(position,origin='IMPORTED_PC_GMO_ORIGINAL'))
                known_codes.add(code)
    merged['positions']=combined_pos
    merged['positions_count']=len(combined_pos)
    merged['pending_count']=len(live.get('pending') or [])
    pc_realized=original.get('realized_pnl')
    pc_sells=sum(str(x.get('side') or '').upper()=='SELL' for x in pc_exec)
    phone_sells=sum(str(x.get('side') or '').upper()=='SELL' for x in phone_exec)
    if phone_in_pc and pc_sells>phone_sells and pc_realized is not None:
        merged['realized_pnl']=pc_realized
        accounts=list(live.get('accounts') or [])
        merged['accounts']=([dict(x,realized_pnl=pc_realized) for x in accounts]
                            if accounts else list(original.get('accounts') or []))
        unreal=merged.get('unrealized_pnl')
        merged['total_pnl']=(float(pc_realized)+float(unreal)
                              if unreal is not None else None)
    confirmed=[float(x.get('net_profit')) for x in combined
               if str(x.get('side') or '').upper()=='SELL'
               and x.get('net_profit') is not None]
    if combined:
        merged['win_rate']=(100.0*sum(x>0 for x in confirmed)/len(confirmed)
                            if confirmed else None)
    merged['load_diagnostic']={
        'stage':'PC_HISTORICAL_EVIDENCE_MERGED_READONLY',
        'phone_fills':len(phone_exec),'pc_fills':len(pc_exec),
        'merged_fills':len(combined),'phone_fill_subset_of_pc':phone_in_pc,
        'phone_positions':len(phone_pos),'pc_positions':len(pc_pos),
        'merged_positions':len(combined_pos)}
    merged['origin']='PHONE_WITH_IMPORTED_PC_HISTORY'
    return merged



def _ui_view_only_v104(tree, array_cap=128, string_cap=1024):
    """A screen-only view; NEVER used for trading or as the source DB backup.

    Original SQLite/WAL and research/log history remain full and immutable.
    Cap Python objects BEFORE serializing for the Java 256MB heap.
    """
    omitted={}
    def copy(node, path=(), depth=0):
        if depth>24:
            return None
        if isinstance(node,dict):
            return {key:copy(value,path+(str(key),),depth+1)
                    for key,value in node.items()}
        if isinstance(node,list):
            label=path[-1] if path else ''
            limit=(512 if label=='positions' else
                   300 if label in ('executions','pending','wallets','accounts')
                   else array_cap)
            visible=node[:limit]
            if len(node)>limit and label=='executions':
                from ui_exec_window_v0210 import linked_fills
                visible=visible+linked_fills(visible,node[limit:])
            if len(node)>len(visible):
                omitted['/'.join(path)]=len(node)-len(visible)
            return [copy(item,path+(str(i),),depth+1)
                    for i,item in enumerate(visible)]
        if isinstance(node,str) and len(node)>string_cap:
            # A hidden view string is NOT a stored strategy/execution value.
            omitted['/'.join(path)]=len(node)-string_cap
            return node[:string_cap]+' [UI_VIEW_TRUNCATED_ORIGINAL_IN_SQLITE]'
        return node
    view=copy(tree)
    view['ui_snapshot_v104']={
        'source':'READ_ONLY_DISPLAY_WINDOW_NOT_EXPORT',
        'omitted_count_by_path':omitted,
        'full_original_records_in_sqlite_unchanged':True}
    return view


def _serialize_hub_v104(payload,for_ui=False):
    if not for_ui:
        return json.dumps(payload,ensure_ascii=False,default=str)
    view=_ui_view_only_v104(payload)
    serialized=json.dumps(view,ensure_ascii=False,default=str,separators=(',',':'))
    if len(serialized)>5_000_000:
        view=_ui_view_only_v104(payload,array_cap=32,string_cap=256)
        serialized=json.dumps(view,ensure_ascii=False,default=str,separators=(',',':'))
    if len(serialized)>5_000_000:
        # Fail this read-only UI refresh closed, never create fake executions.
        raise ValueError('UI_VIEW_MEMORY_BUDGET_EXCEEDED_ORIGINAL_SQLITE_PRESERVED')
    return serialized


def hub_records_ui_v104():
    runtime=getattr(_core,'_RUNTIME',None)
    if runtime is None or not getattr(runtime,'started',False):
        return '{}'
    return runtime.hub_records(for_ui=True)


def score_snapshot_v005():
    """Fast read-only score lane for Android market screen, no heavy ledger.

    Market-score SQL event timestamps and all position/execution SQL remain
    authoritative. This is an independent display snapshot, not a trade signal
    or an alternative market-score database.
    """
    rt=getattr(_core,'_RUNTIME',None)
    if rt is None or not getattr(rt,'started',False):
        return json.dumps({'schema':'ARTITRADE_MARKET_SCORE_PLANE_V005',
            'generated_at_ms':int(time.time()*1000),
            'stock':{'score':None,'stale':True,'status':'시장점수 엔진 시작 대기'},
            'coin':{'score':None,'stale':True,'status':'시장점수 엔진 시작 대기'}},
            ensure_ascii=False)
    try:
        from market_research_v033 import compact_snapshot
        payload=compact_snapshot(rt.root)
        return json.dumps(payload,ensure_ascii=False,separators=(',',':'))
    except Exception as exc:
        return json.dumps({'schema':'ARTITRADE_MARKET_SCORE_PLANE_V005',
            'generated_at_ms':int(time.time()*1000),
            'stock':{'score':None,'stale':True,
                     'status':'시장점수 수집기 오류: '+type(exc).__name__},
            'coin':{'score':None,'stale':True,
                    'status':'시장점수 수집기 오류: '+type(exc).__name__}},
            ensure_ascii=False)


def _hub_records_v078(self,for_ui=False):
    # Start/revive genuine market-score collection independently of the
    # potentially heavy original wallet snapshot. A newly reset PAPER ledger
    # has zero score rows; never require old PC cache to bootstrap scoring.
    try:
        from market_research_v033 import ensure_worker
        ensure_worker(self.root)
    except Exception as score_boot_error:
        self._err('market_score_worker_start_v1118',score_boot_error)
    _capture_pc_research_evidence_async(self)
    raw=_ORIG_HUB_RECORDS(self)
    try:
        out=json.loads(raw) if isinstance(raw,str) else raw
    except Exception:
        # Never bypass the bounded display serializer on malformed source.
        # The full original remains available to the non-UI hub_records path.
        return '{}' if for_ui else raw
    # Read-only forensic summaries use a separate asynchronous reader;
    # the original GMO/CW strategy, broker and wallet databases are NOT modified.
    # Existing full error ZIP already contains the original source SQLite.
    try:
        from entry_failure_audit_v075 import snapshot_for_root
        out['entry_failure_audit']=snapshot_for_root(self.root)
    except Exception as audit_exc:
        self._err('entry_failure_audit_v075',audit_exc)
    try:
        stock_at=out.setdefault('stock',{}).setdefault('at',{})
        base=stock_at.setdefault('ledger',{})
        try:
            stock_at['ledger']=_stock_at_v19_full(self,base)
        except Exception as exc:
            self._err('stock_at_history_v078',exc)
        try:
            ma=_stock_ma_full(self)
            stock_at['ledger']['ma_subledger']=ma
            stock_at['ledger']['ma']=ma
        except Exception as exc:
            self._err('stock_ma_history_v078',exc)
        try:
            out.setdefault('stock',{}).setdefault('cw',{})['ledger']=_close_full(self)
        except Exception as exc:
            self._err('stock_close_history_v078',exc)
        if self.coin_runner:
            try:
                out.setdefault('coin',{}).setdefault('general',{})['ledger']=_coin_full(self.coin_runner.ledger)
            except Exception as exc:
                self._err('coin_general_history_v078',exc)
            try:
                if getattr(self.coin_runner,'ma_ledger',None) is not None:
                    out.setdefault('coin',{}).setdefault('ma',{})['ledger']=_coin_full(self.coin_runner.ma_ledger)
            except Exception as exc:
                self._err('coin_ma_history_v078',exc)
        # The dedicated Research screen is removed from v0.0.1. Its full
        # historical SQLite joins/legacy score reconciliation must not run
        # again on every 6.5s UI snapshot; full original ledger/AI export
        # remains unchanged when a full (non-UI) snapshot is requested.
        if not for_ui:
            try:
                _research_history(self,out)
            except Exception as exc:
                self._err('research_history_all_v078',exc)
        gmo=getattr(self.stock_runner,'gmo',None) if self.stock_runner else None
        if gmo is not None:
            snap=gmo.snapshot() or {}
            try:
                from gmo_entry_time_v036 import enrich_positions
                phone_state={
                    'positions':{
                        str(code):{'entry_date':getattr(position,'entry_date','')}
                        for code,position in gmo.engine.positions.items()},
                    'buy_fills':list(gmo.engine.buy_fills)}
                enrich_positions(snap.get('positions') or [],phone_state)
            except Exception as stamp_error:
                self._err('gmo_original_buy_fill_timestamp',stamp_error)
            try:
                from phone_buy_evidence_v035 import (
                    attach_gmo_buy_evidence,queue_phone_buy_evidence)
                entry_dates={str(code):str(getattr(p,'entry_date',''))
                    for code,p in getattr(gmo.engine,'positions',{}).items()}
                captured=attach_gmo_buy_evidence(snap.get('positions') or [],
                    snap.get('executions') or [],entry_dates)
                queue_phone_buy_evidence(self.root,captured)
            except Exception as evidence_exc:
                self._err('gmo_phone_buy_evidence',evidence_exc)
            # Preserve the exact original position cost basis and sell fees
            # for a consistent net liquidation estimate. This does not
            # modify GMO engine holdings or turn REST marks into live fills.
            try:
                cfg=gmo.engine.cfg
                for pos in (snap.get('positions') or []):
                    original=gmo.engine.positions.get(str(pos.get('code') or ''))
                    if original is None:continue
                    pos['gmo']=True
                    pos['strategy']='gmo'
                    pos['gmo_cost_basis']=float(original.cost_basis)
                    pos['gmo_sell_fee_rate']=float(cfg.sell_fee)
                    pos['gmo_sell_tax_rate']=float(cfg.sell_tax)
                    # 0.3.6 (#151): the card showed "1차 수익목표 미기록". The strategy's first profit target is
                    # a FRACTION (cfg / position 0.04-0.05); show the price it means (display only).
                    try:
                        fraction=float(getattr(original,'first_profit_target',None) or cfg.first_profit)
                        entry_price=float(getattr(original,'entry_price',0) or 0)
                        if entry_price>0 and 0<fraction<1:
                            pos['first_profit_target']=int(math.ceil(entry_price*(1+fraction)))
                            pos['first_profit_target_pct']=round(fraction*100,2)
                    except Exception:
                        pass
            except Exception as cost_exc:
                self._err('gmo_original_cost_basis_v086',cost_exc)
            try:
                from gmo_exec_link_v0410 import link as _link_gmo_fills_v0410
                _link_gmo_fills_v0410(snap.get('executions') or [],gmo.engine)
            except Exception as link_exc:
                self._err('gmo_fill_position_link_v0410',link_exc)
            _v086_enrich_gmo_executions(snap.get('executions') or [])
            wallet=snap.get('wallet') or {}
            sells=[x for x in (snap.get('executions') or []) if str(x.get('side') or '').upper()=='SELL']
            wins=sum(1 for x in sells if float(x.get('net_profit') or 0)>0)
            ledger={
                'schema':'phone-v078','origin':'phone',
                'accounts':[{'tf':'gmo','strategy':'gmo','seed':wallet.get('seed',2_000_000),
                             'cash':wallet.get('cash',0),'realized_pnl':wallet.get('realized',0),
                             'unrealized_pnl':wallet.get('holding',0)}],
                'positions':snap.get('positions') or [],
                'pending':snap.get('pending') or [],
                'executions':snap.get('executions') or [],
                'realized_pnl':wallet.get('realized',0),
                'unrealized_pnl':wallet.get('holding',0),
                'total_pnl':float(wallet.get('realized') or 0)+float(wallet.get('holding') or 0),
                'win_rate':(wins*100.0/len(sells) if sells else None),
                'positions_count':len(snap.get('positions') or []),
                'pending_count':len(snap.get('pending') or []),
                'candidate_count':snap.get('candidate_count',0),
                'actual_target':{
                    'evaluated':int((gmo.research_dashboard().get('bought') or {}).get('evaluated') or 0),
                    'hit':int((gmo.research_dashboard().get('bought') or {}).get('touched') or 0),
                    'miss':int((gmo.research_dashboard().get('bought') or {}).get('failed') or 0),
                    'pending':int((gmo.research_dashboard().get('bought') or {}).get('pending') or 0),
                    'rate_pct':(gmo.research_dashboard().get('bought') or {}).get('rate_pct')
                },
                'restorations':snap.get('restorations') or [],
                'last_scan':snap.get('last_scan'),
                'last_error':snap.get('last_error')
            }
            research=gmo.research_dashboard() or {}
            items=[]
            for row in research.get('candidate_rows') or []:
                status=str(row.get('status') or '')
                items.append({
                    'at':row.get('event_date'),'event_date':row.get('event_date'),
                    'gmo_event_date':row.get('event_date'),'strategy':'gmo',
                    'code':row.get('code'),'name':row.get('name'),
                    'score':row.get('gmo_score_at_signal'),'bought':bool(row.get('bought')),
                    'target_hit': True if status=='TOUCHED' else False if status=='NOT_TOUCHED' else None,
                    'decision':status,
                    'reason':'1차 목표 '+str(row.get('first_exit_target_pct') if row.get('first_exit_target_pct') is not None else '—')+'% · '+status
                })
            stock=out.setdefault('stock',{}).setdefault('at',{})
            stock.setdefault('ledger',{})['gmo']=ledger
            gr=_research_record(items,items)
            gmo_audit=_research_source_audit(Path(self.root)/'stock'/
                'PROJECTGMO_V1.0_research.sqlite3','gmo_candidates',
                ('gmo_events','gmo_fills','gmo_position_history'))
            gr.update(gmo_audit)
            gr['load_status']='OK' if gmo_audit['source_status']=='OK' else gmo_audit['source_status']
            gr['loaded_items']=len(items)
            gr['partial_window']=gmo_audit['source_total']>len(items) if gmo_audit['source_total']>=0 else False
            gr['actual_summary']={
                'evaluated':int((research.get('bought') or {}).get('evaluated') or 0),
                'hit':int((research.get('bought') or {}).get('touched') or 0),
                'miss':int((research.get('bought') or {}).get('failed') or 0),
                'pending':int((research.get('bought') or {}).get('pending') or 0),
                'rate_pct':(research.get('bought') or {}).get('rate_pct')
            }
            gr['actual_rate_pct']=gr['actual_summary']['rate_pct']
            gr['insights']=research.get('insights') or []
            stock.setdefault('research',{})['gmo']=gr
            # Always prefer the original EXE's paper SELL-fill + 15-session
            # virtual-result research rules over a condensed dashboard's
            # 'high touched' status. Keep current live balances/positions.
            # GMO snapshot() and research_dashboard() already read the current
            # original wallet. Running a second full PC-era SQLite/JSON
            # reconciliation for the removed Research page would double the
            # work and risk reintroducing old rows into the fresh UI.
            if not for_ui:
                try:
                    originals=_gmo_original_record(self.root)
                    if originals is not None:
                        source_ledger,source_research=originals
                        if source_research.get('load_status')=='OK':
                            stock['research']['gmo']=source_research
                            stock['ledger']['gmo']['actual_target']=source_research.get(
                                'actual_summary') or {}
                            stock['ledger']['gmo']=_merge_gmo_pc_evidence(ledger,source_ledger)
                except Exception as original_error:
                    self._err('gmo_original_research_parity',original_error)
        else:
            # Fresh PAPER epoch has NO old PC GMO positions or prior scores.
            # An uninitialized native GMO engine is genuinely not ready;
            # querying old files for restoration here could revive stale
            # 2026-11-14 history or double-load a previously deleted wallet.
            stock=out.setdefault('stock',{}).setdefault('at',{})
            stock.setdefault('ledger',{})['gmo']={
                'load_diagnostic':{'stage':'ENGINE_NOT_READY',
                                   'message':'NATIVE_GMO_ENGINE_START_PENDING'}}
            stock.setdefault('research',{})['gmo']={
                'items':[],'load_status':'ENGINE_NOT_READY',
                'source':'FRESH_NATIVE_GMO_ONLY'}
    except Exception as exc:
        self._err('hub_records_v078',exc)
        # Never read an old imported PC GMO database to fill holes in a
        # failed current wallet snapshot; leave the error visible.
        try:
            # Passive, read-only worker. Network and SQLite work only on its
            # daemon thread; this hub snapshot/order thread only enqueues.
            from market_research_v033 import offer_records
            out['market_research']=offer_records(self.root,out)
        except Exception as score_error:
            self._err('market_research_v033',score_error)
    # Always publish the score worker snapshot on the NORMAL successful path.
    # v037 accidentally only called this inside exception/recovery branches;
    # Android read local_records.market_research and therefore saw no scores.
    try:
        from market_research_v033 import offer_records
        out['market_research']=offer_records(self.root,out)
    except Exception as score_error:
        self._err('market_research_v038',score_error)
        out['market_research']={
            'stock':{'score':None,'status':'시장점수 처리 오류'},
            'coin':{'score':None,'status':'시장점수 처리 오류'}}
    # Read-only source freshness diagnostics: one KRX 0B/0D status, Upbit
    # WS-vs-REST distinction and original public score factor failures.
    # No second market socket, no order, no persisted wallet mutation.
    try:
        from market_feed_health_v004 import snapshot as feed_health
        out['market_feed_health_v004']=feed_health(self)
    except Exception as feed_exc:
        self._err('market_feed_health_v004',feed_exc)
    return _serialize_hub_v104(out,for_ui)

def _stock_at_ledger_v19(self):
    out=_ORIG_STOCK_AT_LEDGER(self)
    if not self.stock_runner:
        return out
    try:
        store=self.stock_runner.store
        with _ui_read_ctx(store) as c:
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if 'v19_accounts' not in tables:
                return out
            try:
                wallets=self.stock_runner.engine.wallet_snapshot(c)
            except Exception:
                wallets={}
                try:
                    alloc={r['tf']:int(r['target_seed']) for r in c.execute('SELECT tf,target_seed FROM v19_seed_allocations')}
                except Exception:
                    alloc={}
                for r in c.execute('SELECT tf,cash,realized_pnl,seed FROM v19_accounts').fetchall():
                    wallets[str(r['tf'])]={
                        'tf':str(r['tf']),'cash':int(r['cash']),
                        'realized_pnl':int(r['realized_pnl']),
                        'seed':alloc.get(str(r['tf']),int(r['seed']))
                    }
        accounts=[]
        positions=out.get('positions') or []
        realized=0.0;holding=0.0
        for tf in ('5m','30m','60m','day'):
            w=wallets.get(tf) or {}
            held=sum(float(x.get('paper_pnl') or 0) for x in positions if str(x.get('tf') or '')==tf)
            rp=float(w.get('realized_pnl') or 0)
            accounts.append({
                'tf':tf,'strategy':'at','cash':w.get('cash',0),'seed':w.get('seed',2_000_000),
                'realized_pnl':rp,'unrealized_pnl':held
            })
            realized+=rp;holding+=held
        out['accounts']=accounts
        out['realized_pnl']=realized
        out['unrealized_pnl']=holding
        out['total_pnl']=realized+holding
        from phone_buy_evidence_v035 import (
            attach_buy_evidence,read_at_open_buy_fills,queue_phone_buy_evidence)
        from at_hold_diagnostic_v035 import diagnose_at_position
        from datetime import datetime, timezone, timedelta
        stock_db=Path(self.root)/'stock'/'ARTITRADE_D.sqlite3'
        from phone_buy_evidence_v035 import read_open_signal_evidence
        source_signals=read_open_signal_evidence(stock_db,'stock','at')
        membership=_core._db_rows(stock_db,"SELECT position_id,signal_id FROM v19_members")
        link_map={str(m['position_id']):str(m['signal_id']) for m in membership}
        # OPEN holdings' original buys must not fall out of a capped UI feed.
        # 0.3.6 (#151): a failing evidence read must not abort the whole AT ledger view (the card then fell
        # back to the vendor's target / "미확인" stop / no basis); log it and keep going.
        try:
            buy_records=attach_buy_evidence(positions,read_at_open_buy_fills(stock_db),
                            source_signals,market='stock',strategy='at',linked_ids=link_map)
            queue_phone_buy_evidence(self.root,buy_records)
        except Exception as evidence_exc:
            self._err('stock_at_buy_evidence_v0360',evidence_exc)
        _position_facts('reference',positions,stock_db)
        try:
            import position_view_v0200 as _view
            if _view.restore_facts('stock_at',positions):
                self._err('stock_at_view_facts_restored_v0360',
                          RuntimeError('last good target/stop/basis reused for an open AT holding'))
            _view.remember_facts('stock_at',positions)
        except Exception as facts_exc:
            self._err('stock_at_view_facts_v0360',facts_exc)
        # Android Chaquopy may not package IANA tzdata. KRX uses KST +09:00.
        now=datetime.now(timezone(timedelta(hours=9)))
        for position in positions:
            # Saved hold_days is authoritative; these diagnostics are not
            # scheduled sell actions and never imply a broker fill.
            if not isinstance(position,dict):continue
            detail=diagnose_at_position(position,now,root=store.root)
            position['hold_diagnostic']=detail
            position['hold_exit_diagnostic']=detail['exit_scenario']
    except Exception as exc:
        self._err('stock_at_v19_wallet_snapshot',exc)
    return out

def _patched_gmo(self):
    if not self.stock_runner:
        return _ORIG_PATCH_GMO(self)
    if not hasattr(self.stock_runner, 'gmo') or self.stock_runner.gmo is None:
        from d_gmo_runtime import GMOService
        gmo_root = Path(self.root) / 'stock'
        gmo = GMOService(gmo_root)
        self.stock_runner.gmo = gmo

        # v067 root cause: d_live.run already routes 0D to GMO and every
        # 0B to its separate fast/priority worker. The older Android patch
        # also ran GMO.on_live_tick AND on_book_message in the WebSocket
        # receiver itself. The SAME feed packet was processed twice,
        # including synchronous DB/strategy work while the socket needed
        # to service PING/PONG, causing 1011 keepalive ping timeouts.
        # Keep the single original socket and native workers authoritative.
        # No second per-packet GMO calls on the WS event loop.
        gmo.start(lambda: self.stock_runner.api,
                  lambda: dict(self.stock_runner.market_map))
    # The v071 live bridge replaces GMO paper_match with a broker-only
    # wrapper, which silently ignores all PAPER orders when live is OFF.
    # Preserve the original simulated matcher and the explicitly labelled
    # 0B PAPER proxy. Only dispatch to the broker wrapper while LIVE is ON.
    engine=self.stock_runner.gmo.engine
    if not hasattr(engine,"_android_original_paper_match_v050"):
        engine._android_original_paper_match_v050=engine.paper_match
        engine._android_original_print_proxy_v050=engine.paper_match_krx_print_proxy
    _ORIG_PATCH_GMO(self)
    original_paper=engine._android_original_paper_match_v050
    original_print=engine._android_original_print_proxy_v050
    # The legacy broker wrapper sent ALL NEW GMO orders on ANY KRX tick,
    # including unrelated tickers without fresh 0D depth. Preserve strategy
    # decisions, but require a matching fresh quote for each live order.
    live_intents=set()
    original_make_order=engine._order
    def mode_scoped_order(*args,**kwargs):
        result=original_make_order(*args,**kwargs)
        if self.live_enabled("stock"):
            live_intents.add(result.id)
        return result
    engine._order=mode_scoped_order

    def mode_scoped_match(quote):
        code=str(getattr(quote,"code","") or "")
        if self.live_enabled("stock"):
            try:
                lag=float(getattr(quote,"receive_delay_seconds",float("nan")))
                if (getattr(quote,"venue",None)!="KRX" or not code or
                    not math.isfinite(lag) or lag<0 or lag>3.0):
                    return
                for order in list(engine.orders.values()):
                    if (order.state!="NEW" or order.code!=code or
                            order.id not in live_intents):
                        continue
                    qty=int(order.requested)-int(order.filled)
                    if qty<=0:continue
                    if order.side=="BUY":
                        if (getattr(quote,"trading_day_index",None)!=1 or
                            not engine._d1_buy_window_ok(order,quote.at) or
                            quote.ask<=0 or quote.ask_size<qty or
                            quote.ask>order.limit):
                            continue
                        if verified_gmo_live_quantity(self.root,code)>0:
                            raise UnsafePaperExit("GMO_LIVE_DUPLICATE_BROKER_BUY_BLOCKED")
                    elif order.side=="SELL":
                        if quote.bid<=0 or quote.bid_size<qty or quote.bid<order.limit:
                            continue
                        if verified_gmo_live_quantity(self.root,code)<qty:
                            raise UnsafePaperExit("GMO_PAPER_POSITION_LIVE_SELL_BLOCKED")
                    else:
                        continue
                    key="GMO-"+_core._safe_key(
                        order.id+":"+order.time+":"+order.side+":"+order.code)
                    try:
                        engine.mark_submitted(order.id,quote.at)
                        fill=self.kiwoom.fok(key,order.side,order.code,qty,
                                             order.limit,order.purpose,"KRX")
                        engine.fill(order.id,int(fill["qty"]),
                                    float(fill["price"]),quote.at)
                        self.on_live_fill("stock",order.side,order.code,fill["qty"],
                                          order.limit,fill,order.purpose)
                    except _core.LiveOrderNoFill as nofill:
                        # The original core treated EVERY FOK_NOT_FULL as an
                        # authoritative zero-fill cancellation. Kiwoom may
                        # have real PARTIAL fills on the matching order ID.
                        # Preserve SUBMITTED and the broker journal, never
                        # fake a cancel acknowledgement or retry into a
                        # potentially existing physical broker position.
                        engine._log(order.code,"BROKER_RECONCILIATION_REQUIRED",
                            "order="+str(order.id)+" broker="+str(key)+
                            " reason="+type(nofill).__name__+
                            " state=SUBMITTED; no new BUY until order-ID reconciliation",
                            quote.at)
                    except Exception as error:
                        self._err("gmo_live_order",error)
                        engine._log(order.code,"LIVE_ORDER_HALTED",
                            type(error).__name__+":"+str(error)[:160],quote.at)
                        self._live["stock"]=False
                        raise
            except UnsafePaperExit as unsafe:
                self._err("gmo_broker_provenance_blocked",unsafe)
            return
        # GMO PAPER uses its independent simulated wallet. A broker fill
        # made by stock AT/MA/CLOSE is not a GMO position and must not
        # suppress GMO's virtual fills or its research evidence.
        # Only a LIVE GMO fill (or an unresolved GMO broker order) may
        # block this market's simulated matcher after a mode switch.
        try:
            if verified_gmo_live_quantity(self.root,code)>0:
                raise UnsafePaperExit("GMO_LIVE_POSITION_PAPER_MATCH_BLOCKED")
        except UnsafePaperExit as unsafe:
            self._err("gmo_paper_broker_conflict",unsafe)
            return
        return original_paper(quote)

    def mode_scoped_print(quote, volume, **kwargs):
        if self.live_enabled("stock"):return 0
        try:
            if verified_gmo_live_quantity(self.root,quote.code)>0:
                raise UnsafePaperExit("GMO_LIVE_POSITION_PAPER_PROXY_BLOCKED")
        except UnsafePaperExit as unsafe:
            self._err("gmo_paper_proxy_broker_conflict",unsafe)
            return 0
        return original_print(quote,volume,**kwargs)
    engine.paper_match=mode_scoped_match
    engine.paper_match_krx_print_proxy=mode_scoped_print
    # Independent, read-only UI quote observation on the existing Kiwoom 0B
    # callback. No orderbook, strategy, FOK or account path is changed.
    try:
        from stock_quote_truth_v066 import install_ui_quote_tap
        install_ui_quote_tap(self.stock_runner)
    except Exception as exc:
        self._err('stock_quote_view_only_observer_v066',exc)


def _patched_stop(self):
    try:
        gmo = getattr(self.stock_runner, 'gmo', None) if self.stock_runner else None
        if gmo is not None:
            gmo.stop()
    except Exception as exc:
        self._err('stop_gmo', exc)
    return _ORIG_STOP(self)


_core.UpbitBroker._jwt = _upbit_jwt_hs512
_core.Runtime._patch_gmo = _patched_gmo
_core.Runtime._stock_at_ledger = _stock_at_ledger_v19
_core.Runtime.hub_records = _hub_records_v078
_core.Runtime.stop = _patched_stop

# Verified kt00009 status watcher preserves real PARTIAL quantities and
# distinguishes missing inquiry evidence from an actual FOK no-fill.
from kiwoom_fill_reconcile_v082 import install as _install_kiwoom_fills_v082
_install_kiwoom_fills_v082()

# v0.7.59: integrity guard on *completed* reference candles. Reuses the
# current original Upbit/KRX scanner and account-specific source ledgers.
from reference_integrity_v059 import install as _install_reference_integrity_v059
_install_reference_integrity_v059()

# v0.7.61 MA is an independent strategy in its existing stock/coin wallets:
# no ordinary record-volume entries; only 3+ downward SMA bearish candles
# or a first completed MA120-down/short4-up turnpoint. Both get existing
# broker FOK safety gates. Existing bought positions retain their policy.
from ma_v061_coin_runtime import install as _install_ma_v061_coin
from ma_v061_stock_runtime import install as _install_ma_v061_stock
_install_ma_v061_coin()
_install_ma_v061_stock()
from ma_v061_fast_entry import install as _install_ma_v061_fast
_install_ma_v061_fast()

# v0.7.62: MA-only cold-epoch account. The old stock/coin MA SQLite is
# preserved read-only, and both markets begin new virtual cash/PnL at zero.
# No physical stock/crypto broker balance or external position is reset.
from ma_epoch_v062 import install as _install_ma_epoch_v062
_install_ma_epoch_v062()

# Feed the first eligible KRX 0B tick of a previously UNWATCHED stock
# through the SAME MA + verified 0D/FOK transaction path. Bulk scan alone
# cannot meet the new 5-second turnpoint deadline.
from ma_v062_stock_live_lane import install as _install_ma_v062_stock_lane
_install_ma_v062_stock_lane()

# Start MA AI evidence from the new epoch exclusively. Other market and
# strategy research scopes continue writing to their original repositories.
from ma_v062_ai_scope import install as _install_ma_v062_ai_scope
_install_ma_v062_ai_scope()

# v067 MA ONLY: both stock and coin receive brand new on-disk strategy
# epochs and AI roots. Never delete previous databases or real broker state.
from ma_epoch_v067 import install as _install_ma_epoch_v067
_install_ma_epoch_v067()
from ma_v067_stock_runtime import install as _install_ma_v067_stock
_install_ma_v067_stock()

# v0.7.43: market trend is an AT/MA entry mode, never an independent account.
from market_trend_runtime_v043 import install as _install_market_trend_v043
_install_market_trend_v043(_core)

# v093: one Kiwoom socket for all stock strategies. Keep existing order
# execution and market-trend holdings intact; only rank quote REG cohorts.
from stock_priority_runtime_v093 import install as _install_stock_priority_v093
_install_stock_priority_v093(_core)

# 0.0.20 (owner decision): stock MarketTrend uses the SAME symbol universe as the
# reference-bar (AT) strategy; the trading-value TOP50 list stays coin-only.
from stock_trend_universe_v020 import install as _install_stock_trend_universe_v020
_install_stock_trend_universe_v020()

# 0.0.20 (owner decision): AT orders whose one-share cost is above the per-symbol
# budget can never fill; they are excluded instead of staying "buy pending".
from stock_limit_exclusion_v020 import install as _install_stock_limit_exclusion_v020
_install_stock_limit_exclusion_v020()

# The coin price observer is installed on the already existing MarketTrend
# Upbit ticker memory. Both markets retain their original broker/FOK owner.
from ma_v067_coin_runtime import install as _install_ma_v067_coin
_install_ma_v067_coin()

# 0.0.18: low-latency held coin MA SELL on existing original Upbit WS books.
# No extra exchange connection; native original PAPER wallet and depth gate.
from ma_fast_held_exit_v018 import install as _install_ma_fast_held_exit_v018
_install_ma_fast_held_exit_v018()

# Keep Kiwoom socket receive/PONG independent from periodic universe REST
# and local DB refresh. Never add a second Kiwoom WebSocket login.
from kiwoom_ws_v067 import install as _install_kiwoom_ws_v067
_install_kiwoom_ws_v067()

# v0.7.53: authoritative POST-SELL tracker. This is research-only, listens
# to the existing verified stock/coin feeds and never changes order logic.
from post_exit_runtime_v053 import install as _install_post_exit_v053
_install_post_exit_v053(_core)

# v0.7.59: read-only per-position chronology after all ledger wrappers.
from phone_policy_reference_v059 import install as _install_policy_reference_v059
_install_policy_reference_v059(_core)

# v061 MA mode/turnpoint stages are read-only ledger annotations; never
# substitute reference times for actual broker/PAPER BUY execution times.
from ma_v061_packet import install as _install_ma_v061_packet
_install_ma_v061_packet(_core)

# v062 new MA strategy-only dashboard/research packet and read-only legacy.
from ma_v062_packet import install as _install_ma_v062_packet
_install_ma_v062_packet(_core)

# v066 display-only quote provenance is installed after all strategy packets.
# It cannot modify engine SQLite or the existing live/paper order pathways.
from stock_quote_truth_v066 import install as _install_stock_quote_truth_v066
_install_stock_quote_truth_v066(_core)

# Fresh PAPER-only successor: no v061/v062 historical MA positions
# survive the first mandatory epoch reset. The old historical-only SELL
# watcher MUST NOT patch the current MA feed/order methods anymore.
# Active MA v067 native BUY/SELL policy modules remain unchanged.

# v094: finish incomplete initial slot funding ONLY from verified first
# execution +2 true exchange ticks, using same original public-quote owner.
from coin_partial_entry_v094 import install as _install_coin_partial_v094
_install_coin_partial_v094()
from trend_partial_entry_v094 import install as _install_trend_partial_v094
_install_trend_partial_v094()

# GMO continues only original D+1 partially funded positions at +2 KRX ticks.
# Original quote freshness, user D+1 day and confirmed ledger remain mandatory.
from gmo_partial_entry_v094 import install as _install_gmo_partial_v094
_install_gmo_partial_v094()

# v0.8.0: preplan D+1 PAPER buys at 07:50 NXT / 08:50 KRX;
# only real same-venue 0B/0D may execute the three staged +0/+1/+2 prices.
# Original GMO SELL, DATE, funding and native ledgers remain authoritative.
from gmo_d1_preplan_v097 import install as _install_gmo_d1_preplan_v097
_install_gmo_d1_preplan_v097(_core)

# Research-only integrity for CLOSE 5m/15m/30m/EOD/1D+ horizon labels.
# Never substitutes for the original CLOSE BUY/SELL or native orderbook.
from close_followup_integrity_v008 import install as _install_close_followup_v008
_install_close_followup_v008()

# v0.8.1: stock+coin MarketTrend RSI35 watches prepare three 0/1/2
# *exchange tick* entry budgets. Original MT54 wallet, KRX 0D/Upbit
# verified books and preclose constraints still own fills. Neither
# preplanner may transmit a real broker BUY or create a fake execution.
from market_trend_preplan_v099 import install as _install_trend_preplan_v099
_install_trend_preplan_v099()

def ledger_audit(manifest_json):
    """Read actual phone SQLite files with the same sqlite3 used by strategy engines.

    Read-only, no schema changes, no order execution. Unlike the Android system
    SQLite helper, this can open the exact DB format used by the bundled EXE.
    """
    import re,sqlite3
    try:
        manifest=json.loads(manifest_json) if isinstance(manifest_json,str) else manifest_json
        root=Path(getattr(getattr(_core,'_RUNTIME',None),'root',''))
        if not root.is_dir() or not isinstance(manifest,dict):
            raise RuntimeError('phone runtime or PC manifest missing')
        files=manifest.get('files') or {}
        if not files:raise RuntimeError('PC archive has no files')
        total_src=0;total_phone=0;checked=0;results={};problems=[]
        for rel,meta in files.items():
            if not isinstance(meta,dict):continue
            installed_rel='stock/close/AT_CLOSE_PAPER.sqlite3' if rel=='stock/AT_CLOSE/AT_CLOSE_PAPER.sqlite3' else rel
            file=root/installed_rel
            if not file.is_file():
                problems.append('DB_MISSING:'+rel);continue
            counts=meta.get('counts') or {}
            file_result={'path':installed_rel,'source_rows':0,'phone_rows':0,'tables_checked':0}
            if rel.endswith('.sqlite3'):
                connection=None
                try:
                    connection=sqlite3.connect('file:'+file.resolve().as_posix()+'?mode=ro',uri=True,timeout=10)
                    connection.execute('PRAGMA query_only=ON')
                    actual_tables={x[0] for x in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
                    for table,want in counts.items():
                        if not re.fullmatch('[A-Za-z0-9_]+',str(table)) or not isinstance(want,int) or want<0:
                            continue
                        if table not in actual_tables:
                            problems.append('TABLE_MISSING:'+rel+'/'+str(table));continue
                        if not any(k in table.lower() for k in ('position','execution','order','candidate',
                                                              'signal','shadow','research','event','trade','fill',
                                                              'history','restoration')):
                            continue
                        got=int(connection.execute('SELECT COUNT(*) FROM "'+table+'"').fetchone()[0])
                        file_result['source_rows']+=want
                        file_result['phone_rows']+=got
                        file_result['tables_checked']+=1
                        if got<want:
                            problems.append('ROW_SHORTFALL:'+rel+'/'+table+' pc='+str(want)+' phone='+str(got))
                except Exception as exc:
                    problems.append('SQLITE_READ_ERROR:'+rel+':'+type(exc).__name__+':'+str(exc)[:120])
                finally:
                    if connection is not None:connection.close()
            else:
                file_result['source_rows']=sum(v for k,v in counts.items() if isinstance(v,int)
                    and any(t in k.lower() for t in ('position','order','fill','trade')))
                file_result['phone_rows']=file_result['source_rows']
            results[rel]=file_result
            total_src+=file_result['source_rows'];total_phone+=file_result['phone_rows']
            checked+=1
        return json.dumps({'ok':not problems,'files_checked':checked,'source_rows':total_src,
            'phone_rows':total_phone,'files':results,'message':'; '.join(problems[:6]),
            'error_count':len(problems)},ensure_ascii=False)
    except Exception as exc:
        return json.dumps({'ok':False,'message':type(exc).__name__+': '+str(exc)[:240],
            'files_checked':0},ensure_ascii=False)

# v086: permit exact 3LEG31 stock AT stage1+stage2 catch-up from one
# post-disconnect genuine KRX 0B, with native same-SQLite SELL evidence.
# No replay of stale OHLCV and no new real brokerage permission.
from stock_at_catchup_v086 import install as _install_stock_at_catchup_v086
_install_stock_at_catchup_v086()

# D19 AT portfolio uses its OWN independent BUY/SELL methods. The old
# PaperEngine-only 3LEG31 installer did not initialize D19's durable
# exit_policy/first_target or price its catch-up sales at observed 0B.
# Preserve all original member/legacy holdings and repair ONLY provably
# post-phone unsold D19 buys; then arm the same three-leg native engine.
from stock_at_policy_reconcile_v091 import install as _install_at_policy_v091
_install_at_policy_v091()

# v107: current signed score <0 forbids only NEW AT/coin-general
# reference-bar buys. Existing positions and SELL callbacks stay untouched.
from at_market_score_gate_v107 import install as _install_reference_score_gate_v107
_install_reference_score_gate_v107()

# v107: observe original qualifying BUY candidates/targets for all 8
# strategy wallets. This never makes an order or modifies any SELL policy.
from entry_plan_hooks_v107 import install as _install_entry_plan_hooks_v107
_install_entry_plan_hooks_v107(_core)

# One recovery plan shared by five stock and three coin strategy ledgers.
# It monitors genuine 0B/Upbit authenticated events, prioritizes held-stock
# paper decision queue traffic and records recovery evidence. It NEVER
# substitutes REST/current display prices for an original strategy SELL,
# creates an off-line historical fill or calls a second broker REST API.
from unified_recovery_v092 import install as _install_unified_recovery_v092
_install_unified_recovery_v092(_core)

# Final workbook (NEW LEGACY 11.18): overrides obsolete RSI first-close
# next-open entry, but leaves all original market quote/FOK wallet SELL safety.
from market_trend_1118_runtime import install as _install_nl1118_trend
_install_nl1118_trend()

# The 11.18 workbook is the last reference-bar execution policy overlay.
# Original verified quote/order owners remain intact.
from reference_1118_runtime import install as _install_nl1118_reference
_install_nl1118_reference()

# 0.0.12: This final MA-only rule disables the now-disallowed historical
# bearish/old next-open scanners. Live 10..2s upward MA entry remains on
# its independent source-proven worker; GMO and CLOSE/CW are excluded.
from nl1118_strict_entry_v012 import install as _install_nl1118_strict_entry
_install_nl1118_strict_entry()

# 0.0.13 root-cause of zero KRX D19 BUY receipts despite historical
# positive market-score rows: D19's DStore.root points at <phone>/stock,
# while market-score publisher is registered at <phone>. Normalize its
# lookup in at_market_score_gate_v107 and start the publisher BEFORE
# native stock queue/subscription/order engines run. Never use a UI
# snapshot or stale historical score as a replacement order signal.
_ORIGINAL_NL1118_SCORE_BOOTSTRAP_START = _core.Runtime.start
def _start_with_signed_score_publisher(self):
    from market_research_v033 import ensure_worker
    ensure_worker(self.root)
    return _ORIGINAL_NL1118_SCORE_BOOTSTRAP_START(self)
_core.Runtime.start = _start_with_signed_score_publisher


# Only Android knows whether the current active network is validated.
# This has no ability to make an order or authorize stale 0B/0D.
def set_network_v092(validated):
    rt=getattr(_core,"_RUNTIME",None)
    observer=getattr(rt,"_v092_recovery",None) if rt is not None else None
    if observer is not None:observer.set_android_network(bool(validated))
    return True

# 0.0.15 read-only global position identifiers. This installer wraps
# the FINAL strategy/UI hub and Runtime.start, so each original wallet remains
# the only owner of BUY/SELL receipts, cash and native position primary keys.
from position_uid_v015 import install as _install_position_uid_v015
_install_position_uid_v015(_core)

# v017: the original FIX14 completed-bar REST reader lost retry ownership on
# DNS faults and on wake-ups earlier than its own 55s throttle. Keep source
# validation + original eight ledgers, repair only scheduling and the stale
# global-checkpoint reblock of already officially VERIFIED pending rows.
from stock_pending_recovery_v017 import install as _install_stock_pending_recovery_v017
_install_stock_pending_recovery_v017()

# 0.2.6 (owner 2026-11-22): overnight Korea signal (EWY US session) for stock
# BUY timing, held-position pre-emptive stop exits and the supply-demand D+1
# ceiling. Installed LAST so it wraps the final native BUY functions.
from night_futures_gate_v0260 import install as _install_night_futures_v0260
_install_night_futures_v0260(_core)

# 0.2.6: stock MA / MarketTrend near-signal subscription coverage and
# completed-bar RSI20<=30 watch start (after the 11.18 Trend installer).
from stock_ma_trend_coverage_v0260 import install as _install_stock_coverage_v0260
_install_stock_coverage_v0260(_core)

# 0.2.7: close-betting realtime feed keeps only the newest row per
# symbol/type, so its 3 s book gate sees current data (issue #142).
from close_feed_coalesce_v0270 import install as _install_close_feed_v0270
_install_close_feed_v0270(_core)

# 0.2.9 (sheet 11.22): regime (UP/DOWN/SIDEWAYS) for MA/MarketTrend sizing and the
# reference-bar UP exception; reference-bar +20 % surge-window filter.
# 0.3.0: regime judged on each bought symbol on its own frame from bars already held
# (coin: the original scanner's closed bars are remembered; no extra API call).
from market_regime_v0290 import install as _install_market_regime_v0290
_install_market_regime_v0290()
from at_surge_filter_v0290 import install as _install_at_surge_v0290
_install_at_surge_v0290()

# 0.3.2 (#147): supply-demand D selection fixed to the D close ranking (no D+1 look-ahead);
# pending rows show the D+1 buy price (D close x (1 + EWY)), registration time and reason.
from gmo_d_universe_v0320 import install as _install_gmo_d_universe_v0320
_install_gmo_d_universe_v0320(_core)

# 0.3.6 (#151): a KRX 0D row with a zero KRX-specific quantity FID but a positive combined quantity is
# read from the combined FID (61/71) for the AT book and the supply-demand (GMO) book.
from krx_depth_fallback_v0360 import install as _install_krx_depth_fallback_v0360
_install_krx_depth_fallback_v0360()

# 0.4.0 (#155): measurement only - what the stock ledger gate holder does during a >= 5 s hold, scheduling lag and
# queue state at every Kiwoom disconnect. No behaviour change; a failure here never stops the app.
try:
    from stock_feed_diag_v0400 import install as _install_stock_feed_diag_v0400
    _install_stock_feed_diag_v0400()
except Exception as _diag_exc:
    try:
        _core.__dict__.setdefault('_v0400_diag_error',repr(_diag_exc)[:200])
    except Exception:
        pass

# 0.4.2 (#157): the market-wide daily statistics (INTEGRATED 1.0 / 1.1, research values) are no longer recomputed for every
# symbol and frame while the SQLite ledger gate is held (they were 93 % of the gate hold of the higher-frame repair).
try:
    from market_ctx_memo_v0420 import install as _install_market_ctx_memo_v0420
    _install_market_ctx_memo_v0420()
except Exception as _memo_exc:
    try:
        _core.__dict__.setdefault('_v0420_memo_error',repr(_memo_exc)[:200])
    except Exception:
        pass

# 0.4.4 (#159): combined KRX+NXT (통합) stock candles, stage 1 - READ-ONLY measurement in its own database file; no strategy,
# ledger or `bars` change. A failure here never stops the app.
try:
    from stock_all_venue_bars_v0440 import install as _install_all_venue_v0440
    _install_all_venue_v0440()
except Exception as _all_exc:
    try:
        _core.__dict__.setdefault('_v0440_all_venue_error',repr(_all_exc)[:200])
    except Exception:
        pass

# 0.4.5 (#160): the stock tick feed is dead for most of every session. Fast reconnect after a healthy socket drops, background
# analytics yield to the real-time path while it lags, one feed-health line per minute. Failure-tolerant; strategies untouched.
try:
    from stock_feed_resilience_v0450 import install as _install_feed_resilience_v0450
    _install_feed_resilience_v0450()
except Exception as _res_exc:
    try:
        _core.__dict__.setdefault('_v0450_resilience_error',repr(_res_exc)[:200])
    except Exception:
        pass

# 0.4.6 (#161): canonical 14-digit bar stamps are parsed directly (7x faster) inside the gated recovery loops.
try:
    import ui_ledger_ro_v0460 as _ui_ro_install
    _ui_ro_install.install_fast_parse()
except Exception as _fast_exc:
    try:
        _core.__dict__.setdefault('_v0460_fast_parse_error',repr(_fast_exc)[:200])
    except Exception:
        pass

# 0.5.0 (#165): paper trading follows the real-trading process: the whole displayed best ask / bid size is takeable (was 50 %).
try:
    import real_parity_v0500 as _real_parity_v0500
    _real_parity_v0500.install_book_depth()
except Exception as _parity_exc:
    try:
        _core.__dict__.setdefault('_v0500_parity_error',repr(_parity_exc)[:200])
    except Exception:
        pass

# 0.5.3 (#168, rule R16): the quiet hours (after the NXT close until before the next open) complete the day's missing candles so the
# open is not loaded with repairs. Failure-tolerant; never touches an order, a signal or the ledger.
try:
    import offhours_prework_v0530 as _offhours_v0530
    _offhours_v0530.install()
except Exception as _off_exc:
    try:
        _core.__dict__.setdefault('_v0530_offhours_error',repr(_off_exc)[:200])
    except Exception:
        pass

# 0.5.5 (#170): stock MarketTrend history is filled from the local bars before the open and the broker is asked again only for a really missing
# bar, at a bounded rate (was: every symbol x frame went to the broker at the open). Failure-tolerant; no order, signal or ledger write.
try:
    import market_trend_history_v0550 as _mt_history_v0550
    _mt_history_v0550.install()
except Exception as _mth_exc:
    try:
        _core.__dict__.setdefault('_v0550_mt_history_error',repr(_mth_exc)[:200])
    except Exception:
        pass

# 0.5.5 (#170): a held supply-demand position's exit no longer waits behind the busy tick consumer (watchdog on the newest fresh print) and a missed
# first-target touch leaves evidence. The 3 s executable-freshness gate is unchanged. Failure-tolerant.
try:
    import gmo_held_exit_v0550 as _gmo_held_exit_v0550
    _gmo_held_exit_v0550.install()
except Exception as _held_exc:
    try:
        _core.__dict__.setdefault('_v0550_gmo_held_exit_error',repr(_held_exc)[:200])
    except Exception:
        pass

# Re-export the stable Android bridge API.
start = _core.start
stop = _core.stop
snapshot = _core.snapshot
hub_records = _core.hub_records
hub_records_ui = hub_records_ui_v104
# This APK has ONLY real-quote simulated fills and shadow/PAPER research.
# Its original LIVE switch is a broker-order adapter, never the user-facing
# "실거래" simulated-wallet label. A future real-account APK is separate.
def set_live(market,enabled):
    if enabled:
        return json.dumps({'ok':False,'error':'SIMULATED_ACCOUNT_ONLY_NO_BROKER_ORDERS',
            'live_enabled':{'stock':False,'coin':False}},ensure_ascii=False)
    return _core.set_live(market,False)

def broker_sells_v008():
    """Explicit, read-only broker outstanding SELL evidence; not order execution."""
    from broker_sell_receipt_v008 import audit_stock_sells
    try:
        return json.dumps(audit_stock_sells(getattr(_core,"_RUNTIME",None)),
                          ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok":False,"orders":[],
                           "error":"READ_ONLY_BROKER_QUERY_"+type(exc).__name__},
                          ensure_ascii=False)


def manual_paper_sell(request):
    from android_manual_paper_sell_v085 import perform
    return perform(request)

# v089: UI-only fast price lane, in-memory genuine WS observations ONLY.
# Does not issue an HTTP request, subscribe to a second socket, read SQLite,
# modify a price/order decision or write any source/original journal.
def dump_threads_v0310():
    """0.3.1: read-only stack dump of every Python thread (no engine lock, no I/O); issue #146."""
    from engine_stall_dump_v0310 import dump_threads
    return dump_threads()


def price_snapshot_v089():
    from stock_quote_truth_v066 import live_quote_snapshot
    import datetime as _dt
    try:
        runtime=getattr(_core,'_RUNTIME',None)
        if runtime is None or not getattr(runtime,'started',False):
            return json.dumps({'ready':False,'stock':{'quotes':{}},
                               'coin':{'quotes':{}},'revision':''})
        sr=getattr(runtime,'stock_runner',None)
        if sr is not None and getattr(sr,'_v066_quote_lock',None) is None:
            # Hook the ONE original verified 0B observer as early as the
            # price publisher starts; do not wait for a 6.5s SQLite snapshot.
            try:
                from stock_quote_truth_v066 import install_ui_quote_tap
                install_ui_quote_tap(sr)
            except (AttributeError,TypeError,ValueError,RuntimeError):
                pass
        stock=live_quote_snapshot(sr)
        # Unlike a timestamp-only receipt, a disconnected WebSocket must
        # never show its last print as continuously streaming LIVE.
        try:
            from at_close_bridge import broker_transport_snapshot
            alive=bool(sr is not None and getattr(sr,'_confirmed_ws_session',False)
                       and broker_transport_snapshot().get('state')=='connected')
        except (ImportError,AttributeError,TypeError):
            alive=False
        if not alive:
            for q in (stock.get('quotes') or {}).values():
                if q.get('status')=='FRESH':
                    q['status']='LAST_0B'
                    q['expires_ms']=0
            stock['revision']=stock.get('revision','')+'|SOCKET_DISCONNECTED'
        # Pending/candidate UI can reuse only already-subscribed, truly
        # received 0B prints. No extra REG slots or research REST calls.
        if sr is not None and getattr(sr,'_v066_quote_lock',None) is not None:
            try:
                from stock_quote_truth_v066 import _choose_for_code,KST
                with sr._v066_quote_lock:
                    by_venue=dict(getattr(sr,'_v066_quote_by_venue',{}) or {})
                held=dict(stock.get('quotes') or {})
                codes=sorted({k[0] for k in by_venue
                              if isinstance(k,tuple) and len(k)==2})[:220]
                now_stock=_dt.datetime.now(KST)
                connected=alive
                for code in codes:
                    if code in held:continue
                    valid,last=_choose_for_code(by_venue,code,now_stock,connected)
                    price=valid or last
                    if price is None:continue
                    held[code]={
                        'price':price['price'],
                        'status':'FRESH' if valid else 'LAST_0B',
                        'source':'KIWOOM_REAL_0B' if valid else 'KIWOOM_LAST_0B',
                        'venue':price['venue'],
                        'exchange_at':price['exchange_at'],
                        'received_at':price['received_at'],
                        'expires_ms':price['expires_ms'] if valid else 0}
                stock['quotes']=held
                stock['revision']='|'.join(
                    str(k)+':'+str(v.get('price'))+':'+
                    str(v.get('status'))+':'+str(v.get('received_at',''))
                    for k,v in sorted(held.items()))
            except (AttributeError,TypeError,ValueError,RuntimeError):
                pass
        cr=getattr(runtime,'coin_runner',None)
        now_epoch_ms=int(time.time()*1000)
        now_mono=time.monotonic()
        coin_quotes={}
        if cr is not None:
            # A 0B-equivalent Upbit trade timestamp is stored at WS receipt
            # BEFORE the slower coin strategy callback is queued.
            # No REST-derived latest_prices (which may be stale) are used.
            try:
                seen=dict(getattr(cr,'_v086_last_trade_by_code',{}) or {})
            except RuntimeError:
                seen={}
            held=list(getattr(cr,'_held_codes',()) or ())
            tracked=list(getattr(cr,'tracked',()) or ())
            codes=list(dict.fromkeys(str(code) for code in held+tracked
                                     if str(code).startswith('KRW-')))[:280]
            socket=bool(getattr(cr,'_ws_connected',False) and
                        0<=now_mono-float(getattr(cr,'_ws_last_frame',0) or 0)<=15.0)
            for code in codes:
                point=seen.get(code)
                if not isinstance(point,(tuple,list)) or len(point)<2:continue
                try:
                    stamp=int(point[0])
                    price=float(point[1])
                    age_ms=now_epoch_ms-stamp
                except (ValueError,TypeError,OverflowError):continue
                if not (price>0 and -500<=age_ms<=3_600_000):continue
                fresh=socket and age_ms<=10_000
                coin_quotes[code]={
                    'price':price,
                    'status':'FRESH' if fresh else 'LAST_WS',
                    'source':'UPBIT_VERIFIED_WS_TICKER',
                    'exchange_ms':stamp,
                    'expires_ms':stamp+10_000 if fresh else 0,
                    'age_ms':max(0,age_ms)}
        coin={'quotes':coin_quotes,'ws_reception_verified_only':True}
        # Small stable signature. A status flip also changes the signature,
        # but an unchanged quote does not create redundant XML writes.
        token=json.dumps([stock.get('revision',''),
                          [(k,v['price'],v['status'],v['exchange_ms'])
                           for k,v in sorted(coin_quotes.items())]],
                          ensure_ascii=True,separators=(',',':'))
        return json.dumps({
            'ready':True,'generated_ms':now_epoch_ms,
            'stock':stock,'coin':coin,
            'revision':hashlib.sha256(token.encode('ascii')).hexdigest(),
            'ui_only':True,'fills_allowed':False},
            ensure_ascii=False,separators=(',',':'))
    except Exception as exc:
        return json.dumps({'ready':False,'stock':{'quotes':{}},
                           'coin':{'quotes':{}},
                           'error':type(exc).__name__})

# v045 explicit score-independent market trend entry selection.
from market_trend_runtime_v043 import set_manual_mode as set_trend_manual