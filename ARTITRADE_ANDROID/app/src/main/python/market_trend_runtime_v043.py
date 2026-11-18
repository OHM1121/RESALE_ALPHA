"""ARTITRADE v043 market-score switched RSI20 mode coordinator.

Reuses the original Kiwoom and Upbit realtime feeds; never creates a second
broker session. The original AT/MA exits continue on their original rows.
Only status TREND_OPEN is owned by this coordinator.
"""
from __future__ import annotations
import collections
import contextlib
import datetime as dt
import json
import math
import queue
import sqlite3
import threading
import time
from pathlib import Path

import market_trend_v043 as mt
from market_trend_wallet_v043 import TrendWallet, UnsafeFill
from market_trend_wallet_v054 import TrendWalletV054
from market_trend_watch_v054 import WatchBook
from execution_evidence_v074 import ForensicLedger

_CORE=None
_INSTALLED=False
_MA_GUARD=threading.local()
_STOCK_TICK_LOCK=threading.RLock()
# REST quote snapshots are marks only; do not turn an old trade into a fresh WS print.
_PRICE_OBSERVATION_CONTEXT=threading.local()
# A genuine Upbit last-trade tick remains a verified price during quiet seconds.
# Extend RSI observation only; executable Upbit book must still be <=3s fresh.
COIN_VERIFIED_TRADE_MAX_AGE_SEC=10.0


def _parent(market):
    return ("at","ma") if market=="stock" else ("general","ma")


def _when(value):
    return mt._as_kst(value)


def _end_clock(now,market,frame):
    now=_when(now)
    if frame not in mt.FRAMES:return None
    if market=="coin":
        width=mt.FRAME_SECS[frame]
        ts=(int(now.timestamp())//width+1)*width
        return dt.datetime.fromtimestamp(ts,mt.KST)
    if not mt._krx_day(now.date()):return None
    from krx_session_v041 import SPECIAL_SESSIONS, START, END
    opening,closing=SPECIAL_SESSIONS.get(now.date().isoformat(),(START,END))
    finish=now.replace(hour=closing//60,minute=closing%60,
                       second=0,microsecond=0)
    if frame=="day":return finish
    start=now.replace(hour=opening//60,minute=opening%60,
                      second=0,microsecond=0)
    elapsed=(now-start).total_seconds()
    if elapsed<0 or elapsed>=(closing-opening)*60:return None
    size=mt.FRAME_SECS[frame]
    step=int(elapsed//size)+1
    return min(start+dt.timedelta(seconds=step*size),finish)


def _bars_from_upbit(items,frame,now):
    from d_upbit import candle_start_utc
    seconds=mt.FRAME_SECS[frame]
    rows={}
    for row in items if isinstance(items,list) else []:
        try:
            stamp=candle_start_utc(row).astimezone(mt.KST)
            end=stamp+dt.timedelta(seconds=seconds)
            close=mt._finite(row.get("trade_price"))
            high=mt._finite(row.get("high_price"))
            low=mt._finite(row.get("low_price"))
            if close is None or high is None or low is None or high<close or close<low:
                continue
            if end>now:continue
            rows[stamp.isoformat()]={"start":stamp,"end":end,
                "close":close,"high":high,"low":low,"source":"UPBIT_CLOSED"}
        except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
            continue
    return sorted(rows.values(),key=lambda x:x["start"])[-200:]


def _stock_bar_end(ts,frame):
    from d_engine import bar_end
    s=str(ts)
    if len(s)<14:return None
    end=bar_end(s[:14],frame)
    return dt.datetime.strptime(end,"%Y%m%d%H%M%S").replace(tzinfo=mt.KST)


class _PriceMemory(dict):
    """Intercept the core's existing ticker dictionary without a second socket."""
    def __init__(self,initial,callback):
        super().__init__(initial);self._callback=callback
    def __setitem__(self,code,price):
        super().__setitem__(code,price)
        # REST refresh also assigns latest_prices, but that assignment is not
        # a new exchange trade. Keep UI/mark refresh without arming an entry.
        if getattr(_PRICE_OBSERVATION_CONTEXT,'rest_only',False):return
        try:self._callback(str(code),float(price))
        except (ValueError,TypeError):pass


class _NoBuyConnection:
    """Filter the original MA candidate SELECT only inside its tick worker."""
    def __init__(self,raw):self._raw=raw
    def __getattr__(self,attr):return getattr(self._raw,attr)
    def execute(self,sql,params=()):
        q=str(sql).strip().upper()
        if getattr(_MA_GUARD,"block",False) and "FROM CANDIDATES WHERE CODE=" in q and "STATUS='PENDING'" in q:
            return self._raw.execute("SELECT * FROM candidates WHERE 0=1")
        return self._raw.execute(sql,params)


class Coordinator:
    def __init__(self,rt):
        self.rt=rt
        self.journal=mt.TrendJournal(rt.root)
        self.manual_trend={market:self.journal.manual_trend(market)
                           for market in ("stock","coin")}
        self.wallet=TrendWallet(rt,self.journal)  # LEGACY MT43 exits only
        self.standalone_wallet=TrendWalletV054(rt,legacy_owned=self.wallet.native_owned,
             order_lock=self.wallet.order_lock)
        self.watch=WatchBook()  # RSI35 watch is not an order or shadow fill
        self._sealed_watch={}
        self.stop_event=threading.Event()
        self.lock=threading.RLock()
        self.threads=[]
        # Independent trend failures must not gate the native AT/MA engines.
        self.modes={"stock":"ORIGINAL","coin":"ORIGINAL"}
        self.scores={"stock":None,"coin":None}
        self.mode_details={"stock":"","coin":""}
        self._candidates={"stock":{},"coin":{}}
        self._last_rank={"stock":0.0,"coin":0.0}
        self._quotes={"stock":{},"coin":{}}
        self._history={}
        self._history_requests={}
        # One diagnostic per source/bar/window, not every 200ms precheck poll.
        self._v078_missing_trade_seen=set()
        self._last_closed={}
        self._prechecked={}
        self._v074_window_seen=set()
        self._ordered=set()
        self._last_signal_prune=0.0
        self._fair={"stock":0,"coin":0}
        self._positions={}
        self._virtual={}
        self._last_fetch_error={}
        self._watch_after={}
        self._position_snapshot_at={}
        self._entry_block_counts={market:collections.Counter() for market in ('stock','coin')}
        # Bounded in-memory, per-symbol decision evidence; NOT an order/virtual fill.
        self._recent_blocked_entries={market:collections.deque(maxlen=160)
                                      for market in ('stock','coin')}
        # In-memory performance counters only. No trading-ledger writes.
        self._performance={market:{
            "preclose_cycles":0,"preclose_no_due_cycles":0,
            "preclose_due_cycles":0,"preclose_symbols_examined":0,
            "stages":{name:{"calls":0,"total_ms":0.0,"max_ms":0.0}
                      for name in ("score","closed","preclose","holds","history")}
        } for market in ("stock","coin")}
        self._recover_positions()
        try:
            self._v074_evidence=ForensicLedger(rt.root)
        except (OSError,sqlite3.Error) as exc:
            # Audit availability must not crash the original trading runtime.
            self._v074_evidence=None
            self._last_fetch_error['stock']='AUDIT_INIT:'+type(exc).__name__
            self._last_fetch_error['coin']='AUDIT_INIT:'+type(exc).__name__
        self._v074_audit_queue=queue.Queue(maxsize=16000)
        self._v074_audit_unique=set()
        self._v074_audit_dropped=0
        self._v074_audit_thread=threading.Thread(
            target=self._v074_audit_worker,daemon=True,
            name="ARTITRADE_V074_NO_ORDER_AUDIT")
        self._v074_audit_thread.start()

    def _record_latency(self,market,stage,started):
        elapsed=max(0.0,(time.monotonic()-started)*1000.0)
        with self.lock:
            row=self._performance[market]["stages"][stage]
            row["calls"]+=1
            row["total_ms"]+=elapsed
            row["max_ms"]=max(row["max_ms"],elapsed)

    def _recover_positions(self):
        for record in self.journal.open_positions():
            # v054 stop metadata accidentally stored as parent position in an
            # earlier release. MT54 positions are canonical only in MT54 DB.
            if record["strategy"]=="trend":continue
            try:
                data=json.loads(record["payload"])
                p=mt.TrendPosition(record["market"],record["strategy"],record["frame"],
                    record["symbol"],data["entry_at"],data["entry"],data["original_qty"],data["bar_key"])
                p.__dict__.update(data)
                p.entry_at=mt._as_kst(data["entry_at"])
                p.deadline=mt._as_kst(data["deadline"])
                p.__dict__["position_id"]=int(record["position_id"])
                with self.wallet._connect(p.market,p.strategy) as c:
                    codecol="code" if p.market=="stock" else "market"
                    row=c.execute("SELECT qty FROM positions WHERE id=? AND "+codecol+"=? AND status='TREND_OPEN'",
                                  (p.position_id,p.symbol)).fetchone()
                if row is None:
                    self.journal.event("MT43:RECOVERY_DISAGREE:"+p.market+":"+str(p.position_id),
                        p.market,p.strategy,p.frame,p.symbol,"UNSAFE",
                        "POSITION_LEDGER_DISAGREES")
                    continue
                p.qty=float(row["qty"])
                self._positions[(p.market,p.strategy,p.frame,p.symbol)]=p
            except Exception as exc:
                self.journal.event("MT43:RESTORE_FAIL:"+str(record["position_id"]),
                    record["market"],record["strategy"],record["frame"],record["symbol"],
                    "UNSAFE",type(exc).__name__)

        # Shadow trades are research-only but must survive phone restart.
        for record in self.journal.virtual_open():
            try:
                key=tuple(json.loads(record["signal_key"]))
                data=json.loads(record["payload"])
                v=mt.TrendPosition(record["market"],record["strategy"],
                    record["frame"],record["symbol"],data["entry_at"],
                    data["entry"],data["original_qty"],data["bar_key"])
                v.__dict__.update(data)
                v.entry_at=mt._as_kst(data["entry_at"])
                v.deadline=mt._as_kst(data["deadline"])
                self._virtual[key]=v
            except (KeyError,TypeError,ValueError) as exc:
                self.journal.event("MT43:VRESTORE:"+str(record["signal_key"]),
                    record["market"],record["strategy"],record["frame"],
                    record["symbol"],"UNSAFE",type(exc).__name__)

        # MT54 restores exclusively from its own accounts, never from parent cash.
        for market in ('stock','coin'):
            try:
                for p in self.standalone_wallet.open_positions(market):
                    self._positions[(market,'trend',p.frame,p.symbol)]=p
            except (sqlite3.Error,ValueError,TypeError,OSError) as exc:
                self._last_fetch_error[market]='MT54_RESTORE:'+type(exc).__name__

    def mode(self,market):
        return self.modes.get(market,"PAUSED")

    def set_manual_trend(self,market,enabled):
        if market not in ("stock","coin") or type(enabled) is not bool:
            raise ValueError("invalid market/manual flag")
        with self.lock:
            # Database persistence first, then immediate runtime application.
            self.journal.set_manual_trend(market,enabled)
            self.manual_trend[market]=enabled
            self._update_score(market)
            return {"ok":True,"market":market,"manual_trend":enabled,
                    "mode":self.mode(market),"score":self.scores.get(market)}

    def active_symbol(self,market,code):
        return (any(p.market==market and p.symbol==code and p.qty>0
                    for p in tuple(self._positions.values())) or
                self.wallet.trend_owned(market,code) or
                self.standalone_wallet.owned(market,code))

    def _start_score_worker(self):
        from market_research_v033 import offer_records
        offer_records(self.rt.root,{})

    def _read_score(self,market):
        from market_research_v033 import _WORKERS,_WORKERS_LOCK
        with _WORKERS_LOCK:
            worker=_WORKERS.get(str(Path(self.rt.root).resolve()))
        if worker is None:return {"score":None,"stale":True,"status":"시장점수 준비 중"}
        return worker.snapshot().get(market) or {"score":None,"stale":True}

    def _update_score(self,market):
        result=self._read_score(market)
        with self.lock:
            score=result.get("score")
            # v054: AT/MA never switch to RSI20 based on score or legacy manual
            # override. Their original engines retain their own entry filters.
            mode="ORIGINAL"
            prior=self.modes.get(market,"PAUSED")
            self.scores[market]=score
            self.mode_details[market]=str(result.get("status") or "")
            if mode!=prior:
                # No automatic cancellation of original strategy pending orders.
                # Broker-unknown pending orders remain unresolved and preserved.
                with self.journal.lock,self.journal.connect() as c:
                    c.execute("INSERT INTO gate_events VALUES(?,?,?,?,?)",
                             (time.time(),market,prior,mode,self.mode_details[market]))
                self.modes[market]=mode

    def _cancel_native_pending(self,market):
        """Never claim broker cancellation just because a local row changed.

        The original live broker adapter submits FOK orders, but a timeout or
        unknown broker status can still be unresolved. Preserve every original
        order/pending row until broker-side finality is verified. A separate
        broker reconciliation must explicitly confirm cancellation or fill.
        """
        if self.rt.live_enabled(market):
            self._last_fetch_error[market]="PENDING_BROKER_RECONCILIATION_REQUIRED"
            self.journal.event("MT43:PENDING_REVIEW:"+market+":"+str(int(time.time())),
                market,"system","","","UNSAFE","BROKER_ORDER_STATUS_UNCONFIRMED")
            return
        # Paper-mode rows may be expired by their original engines. Do not
        # erase pending rows even in paper mode without the owner's checks.
        self._last_fetch_error[market]="PENDING_ORIGINAL_ENGINE_RECONCILIATION"

    def observe_stock_tick(self,tick):
        if len(tick)<8 or tick[7]!="KRX":return
        code,_,_,_,price,_,stamp,_=tick[:8]
        try:
            seen=dt.datetime.fromisoformat(str(stamp)).astimezone(mt.KST)
            if abs((dt.datetime.now(mt.KST)-seen).total_seconds())>3:return
            with self.lock:self._quotes["stock"][str(code)]=(float(price),time.monotonic())
        except (ValueError,TypeError,AttributeError):pass

    def observe_coin_tick(self,code,price):
        with self.lock:self._quotes["coin"][code]=(float(price),time.monotonic())

    def _quote(self,market,code):
        with self.lock:raw=self._quotes[market].get(code)
        max_age=COIN_VERIFIED_TRADE_MAX_AGE_SEC if market=='coin' else 3.0
        if raw is None or time.monotonic()-raw[1]>max_age:return None
        price=raw[0]
        if market=="stock":
            runner=self.rt.stock_runner
            if runner is None:return None
            with runner.engine.book._lock:
                book=runner.engine.book._items.get((code,"KRX"))
                bid_record=runner.engine.book._bid_depth.get((code,"KRX"))
            if not book:return None
            ask,bid,ask_size,seen=book
            if (dt.datetime.now(mt.KST)-seen).total_seconds()>3:return None
            if not (ask>=bid>0 and float(ask_size)>0):return None
            bid_size=0
            if bid_record and bid_record[0]==bid and abs((dt.datetime.now(mt.KST)-bid_record[2]).total_seconds())<=3:
                bid_size=int(bid_record[1])
            return {"trade":price,"ask":float(ask),"bid":float(bid),
                    "ask_size":float(ask_size),"bid_size":float(bid_size)}
        runner=self.rt.coin_runner
        if runner is None:return None
        with runner._lock:
            book=runner.last_book.get(code)
        if not isinstance(book,dict):return None
        if abs(time.time()-float(book.get("timestamp",0))/1000)>3:return None
        bid=mt._finite(book.get("bid"));ask=mt._finite(book.get("ask"))
        size=mt._finite(book.get("ask_size"));bid_size=mt._finite(book.get("bid_size"))
        # Buying consumes ASK depth. Missing/zero BID depth must block a
        # subsequent SELL, not prevent a safely priced BUY signal.
        if any(x is None or x<=0 for x in (bid,ask,size)) or ask<bid:return None
        if bid_size is None or bid_size<0:bid_size=0.0
        return {"trade":price,"ask":ask,"bid":bid,"ask_size":size,"bid_size":bid_size}

    def _refresh_rank(self,market):
        if time.monotonic()-self._last_rank[market]<300:return
        try:
            if market=="stock":
                runner=self.rt.stock_runner
                ranks=(runner.api.ranks(per_market=50) if runner and runner.api else {})
                if len(ranks)<50:raise RuntimeError("KRX_TOP50_INCOMPLETE")
                picked=list(ranks.items())
            else:
                runner=self.rt.coin_runner
                rows=runner.client.tickers() if runner else []
                allowed=set(getattr(runner,"markets",{}))
                observed=[]
                for r in rows:
                    code=str(r.get("market") or "")
                    vol=mt._finite(r.get("acc_trade_price_24h"))
                    if (code.startswith("KRW-") and code not in ("KRW-USDT","KRW-USDC")
                        and (not allowed or code in allowed) and vol is not None):
                        observed.append((vol,code))
                if len(observed)<50:raise RuntimeError("UPBIT_TOP50_INCOMPLETE")
                observed.sort(reverse=True)
                picked=[(code,("CRYPTO",code)) for _,code in observed[:50]]
            with self.lock:
                self._candidates[market]=dict(picked)
                self._last_rank[market]=time.monotonic()
            # Coin ticker subscription is augmented dynamically by the tracked
            # property; do not permanently add every past candidate to base.
        except Exception as exc:
            self._last_fetch_error[market]="RANK:"+type(exc).__name__
            self._record_entry_block(market,'__ENGINE__','all',
                dt.datetime.now(mt.KST).date().isoformat(),
                'OPERATIONAL_RANK_'+type(exc).__name__)
            if time.monotonic()-self._last_rank[market]>600:
                with self.lock:self._candidates[market]={}

    @staticmethod
    def _native_coin_history(frame,code,now):
        """Reuse actual native MA officially verified bars; never fill gaps."""
        try:
            import ma_v061_coin_runtime as ma
            with ma._HISTORY_LOCK:
                captured=ma._HISTORY.get((frame,code))
            if not captured or len(captured[1])<21:return []
            # A fresh copy of the full MA-verified history can eliminate a
            # redundant Upbit REST fetch. A stale native cache cannot be used
            # for the executable RSI20 decision.
            end_expected=_end_clock(now,'coin',frame)-dt.timedelta(
                seconds=mt.FRAME_SECS[frame])
            stamp=mt._as_kst(captured[0])
            if stamp!=end_expected:return []
            bars={}
            for bar in captured[1]:
                start=mt._as_kst(bar['start'])
                finish=start+dt.timedelta(seconds=mt.FRAME_SECS[frame])
                close=mt._finite(bar.get('close'))
                high=mt._finite(bar.get('high'))
                low=mt._finite(bar.get('low'))
                if (finish>now or close is None or high is None or
                    low is None or low<=0 or high<close or close<low):
                    continue
                bars[start.isoformat()]={'start':start,'end':finish,
                    'close':close,'high':high,'low':low,
                    'source':'NATIVE_UPBIT_VERIFIED_COMPLETED'}
            result=sorted(bars.values(),key=lambda x:x['end'])[-125:]
            if len(result)<21 or result[-1]['end']!=end_expected:return []
            return result
        except (ImportError,AttributeError,KeyError,TypeError,ValueError,
                OverflowError):
            return []

    def _raw_history(self,market,code,frame):
        now=dt.datetime.now(mt.KST)
        if market=="coin":
            native=self._native_coin_history(frame,code,now)
            if len(native)>=21:return native
            client=self.rt.coin_runner.minute_client
            data=client.candles(code,frame,count=200)
            return _bars_from_upbit(data,frame,now)
        runner=self.rt.stock_runner
        if runner is None or runner.api is None:return []
        with runner.store.connect() as c:
            rows=c.execute("""SELECT ts,close,high,low FROM bars
                 WHERE code=? AND frame=? ORDER BY ts DESC LIMIT 90""",
                 (code,frame)).fetchall()
        converted={}
        for row in rows:
            try:
                stamp=str(row["ts"])
                end=_stock_bar_end(stamp,frame)
                if end>now:continue
                converted[stamp]={"start":end-dt.timedelta(seconds=mt.FRAME_SECS[frame]),
                    "end":end,"close":float(row["close"]),"high":float(row["high"]),
                    "low":float(row["low"]),"source":"LOCAL_KRX_VERIFIED"}
            except (ValueError,TypeError):continue
        if len(converted)>=25:
            # Local KRX bars can be numerically sufficient yet stale after a feed gap.
            # Retry official historical data during the regular session rather than
            # permanently returning an old 25+ bar cache and starving RSI20.
            from krx_session_v041 import is_regular_open
            newest=max(x['end'] for x in converted.values())
            if (not is_regular_open() or
                    (now-newest).total_seconds()<=3*mt.FRAME_SECS[frame]):
                return sorted(converted.values(),key=lambda x:x['end'])[-80:]
        source=runner.api.history_bars(code,frame)
        for row in source:
            try:
                end=_stock_bar_end(row[2],frame)
                if end>now:continue
                converted[str(row[2])]={"start":end-dt.timedelta(seconds=mt.FRAME_SECS[frame]),
                    "end":end,"close":float(row[6]),"high":float(row[4]),
                    "low":float(row[5]),"source":"KIWOOM_VERIFIED"}
            except (ValueError,TypeError,KeyError):continue
        return sorted(converted.values(),key=lambda x:x["end"])[-80:]

    def _prefetch(self,market):
        while not self.stop_event.is_set():
            with self.lock:
                universe=list(self._candidates[market])
                held=[(p.symbol,p.frame) for p in self._positions.values() if p.market==market]
            pairs=list(dict.fromkeys(held+[(code,tf) for code in universe
                                                for tf in mt.FRAMES]))
            # Populate missing RSI histories before refreshing existing complete
            # windows. Held positions retain highest repair priority.
            held_set=set(held)
            with self.lock:
                missing={p for p in pairs if len(self._history.get((market,p[0],p[1]),()))<21}
            pairs.sort(key=lambda p:(p not in held_set,p not in missing,mt.FRAMES.index(p[1]),p[0]))
            if not pairs:
                self.stop_event.wait(1)
                continue
            for code,frame in pairs:
                if self.stop_event.is_set():return
                key=(market,code,frame)
                with self.lock:
                    complete=len(self._history.get(key,()))>=21
                retry_after=45 if complete else 12
                if time.monotonic()-self._history_requests.get(key,0)<retry_after:
                    continue
                self._history_requests[key]=time.monotonic()
                started=time.monotonic()
                try:
                    bars=self._raw_history(market,code,frame)
                    if len(bars)>=21:
                        with self.lock:self._history[key]=bars
                    else:
                        self._last_fetch_error[market]="HISTORY_SHORT:"+str(len(bars))
                        self._record_entry_block(market,code,frame,
                            dt.datetime.now(mt.KST).date().isoformat(),
                            'OPERATIONAL_HISTORY_SHORT_'+str(len(bars)))
                except Exception as exc:
                    self._last_fetch_error[market]="HISTORY:"+type(exc).__name__
                    self._record_entry_block(market,code,frame,
                        dt.datetime.now(mt.KST).date().isoformat(),
                        'OPERATIONAL_HISTORY_'+type(exc).__name__)
                finally:
                    self._record_latency(market,"history",started)
                self.stop_event.wait(.20 if market=="coin" else .35)
            self.stop_event.wait(2)

    def _closed_series(self,market,code,frame,bar_end):
        with self.lock:barrows=list(self._history.get((market,code,frame),[]))
        if len(barrows)<21:return []
        # A missing immediately preceding completed bar invalidates entry RSI.
        closed=[x for x in barrows if x["end"]<=bar_end-dt.timedelta(seconds=1)]
        if len(closed)<21:return []
        if closed[-1]["end"]!=bar_end-dt.timedelta(seconds=mt.FRAME_SECS[frame]):
            # Stock final partial 60m candle, and official KRX daily bars have
            # session-specific chronology: exact last bar endpoint is mandatory
            # only for crypto; for stock the historical broker stamp is checked.
            if market=="coin":return []
            if (bar_end-closed[-1]["end"]).total_seconds()>3*mt.FRAME_SECS[frame]:
                return []
        return closed

    def _process_closed(self,market):
        """Advance latches only on new bars and replay held-position RSI safely.

        The original chart may have 80 old bars; processing them as fresh
        signals on every preload can exhaust SQLite and misassign buy H/L.
        """
        now=dt.datetime.now(mt.KST)
        with self.lock:
            histories=[(key,list(rows)) for key,rows in self._history.items()
                       if key[0]==market]
        for key,rows in histories:
            _,code,frame=key
            if len(rows)<21:continue
            # Candidate history can remain cached for minutes. Avoid 400
            # unnecessary file transactions per scheduling cycle.
            if (self._last_closed.get(key)==max(x["end"] for x in rows)
                and not any(p.market==market and p.symbol==code and
                            p.frame==frame and (not p.__dict__.get("bar_finalized")
                                or p.last_closed_bar!=rows[-1]["end"].isoformat())
                            for p in self._positions.values())
                and not any(v.market==market and v.symbol==code and v.frame==frame
                            and (not v.__dict__.get("bar_finalized")
                                or v.last_closed_bar!=rows[-1]["end"].isoformat())
                            for v in self._virtual.values())):
                continue
            rows=sorted(rows,key=lambda x:x["end"])
            last=self._last_closed.get(key)
            new_rows=rows[-1:] if last is None else [
                row for row in rows if last<row["end"]<=now]
            for row in new_rows:
                if row["end"]>now:continue
                stamp=row["end"].isoformat()
                v=mt.rsi20([x["close"] for x in rows if x["end"]<=row["end"]])
                if v is None:continue
                latch=self.journal.latch(market,code,frame)
                latch.completed(stamp,v)
                self.journal.save_latch(market,code,frame,latch)
                self._last_closed[key]=row["end"]

            held=[p for p in tuple(self._positions.values())
                  if p.market==market and p.symbol==code and
                     p.frame==frame and p.qty>0]
            for p in held:
                changed=False
                if not p.__dict__.get("bar_finalized"):
                    entrybar=next((row for row in rows
                                   if row["end"].isoformat()==p.bar_key),None)
                    if entrybar is not None and entrybar["end"]<=now:
                        rsi=mt.rsi20([x["close"] for x in rows
                                      if x["end"]<=entrybar["end"]])
                        p.close_entry_bar(p.bar_key,entrybar["high"],
                                          entrybar["low"],rsi)
                        p.__dict__["bar_finalized"]=True
                        p.__dict__["rsi20_closed"]=rsi
                        changed=True
                if p.__dict__.get("bar_finalized"):
                    for row in rows:
                        stamp=row["end"].isoformat()
                        if row["end"]>now or row["end"]<=p.entry_at:continue
                        if p.last_closed_bar is not None and stamp<=p.last_closed_bar:
                            continue
                        rsi=mt.rsi20([x["close"] for x in rows
                                      if x["end"]<=row["end"]])
                        if rsi is not None:
                            p.close_next_bar(stamp,rsi)
                            p.__dict__["rsi20_closed"]=rsi
                            changed=True
                if changed:
                    if p.strategy=='trend':self.standalone_wallet.save_position(p)
                    else:self.journal.save_position(p,p.position_id)
            for virtual_key,v in tuple(self._virtual.items()):
                if v.market!=market or v.symbol!=code or v.frame!=frame:
                    continue
                virtual_changed=False
                if not v.__dict__.get("bar_finalized"):
                    entrybar=next((row for row in rows
                                   if row["end"].isoformat()==v.bar_key),None)
                    if entrybar is not None and entrybar["end"]<=now:
                        rsi=mt.rsi20([x["close"] for x in rows
                                      if x["end"]<=entrybar["end"]])
                        v.close_entry_bar(v.bar_key,entrybar["high"],entrybar["low"],rsi)
                        v.__dict__["bar_finalized"]=True
                        virtual_changed=True
                if v.__dict__.get("bar_finalized"):
                    for row in rows:
                        stamp=row["end"].isoformat()
                        if row["end"]>now or row["end"]<=v.entry_at:continue
                        if v.last_closed_bar is not None and stamp<=v.last_closed_bar:
                            continue
                        rsi=mt.rsi20([x["close"] for x in rows
                                      if x["end"]<=row["end"]])
                        if rsi is not None:
                            v.close_next_bar(stamp,rsi)
                            virtual_changed=True
                if virtual_changed:
                    self.journal.save_virtual(json.dumps(virtual_key,ensure_ascii=False),v)

    def _prune_evaluation_cache(self,now):
        """Bound pre-close signal evidence without discarding current bars.

        One checked RSI per (market,symbol,frame,bar) can accumulate many
        thousands of keys per day. Keys with an end >2 hours in the past
        cannot lead to any new order and are safe to discard from memory.
        Persisted latches and execution event keys remain unchanged.
        """
        checked=getattr(self,'_prechecked',None)
        ordered=getattr(self,'_ordered',None)
        if checked is None or ordered is None:return
        if len(checked)+len(ordered)<3000:return
        mono=time.monotonic()
        last=getattr(self,'_last_signal_prune',0.0)
        if last and mono-last<300:return
        self._last_signal_prune=mono
        cutoff=(_when(now)-dt.timedelta(hours=2)).isoformat()
        with self.lock:
            for key in tuple(self._prechecked):
                if key[3]<cutoff:self._prechecked.pop(key,None)
            for key in tuple(self._ordered):
                if key[3]<cutoff:self._ordered.discard(key)

    @staticmethod
    def _evaluation_window_passed(now,finish,frame,market):
        """Only expire the watch after the whole verified 12..7s opportunity."""
        target=mt._entry_reference_close(now,finish,frame,market)
        return mt._as_kst(now)>target-dt.timedelta(seconds=mt.TREND_BUY_END_SEC)

    @staticmethod
    def _decision_clock():
        # Dependency seam for historical deterministic unit tests only.
        # Production must use the real present-time clock before every order.
        return dt.datetime.now(mt.KST)

    def _v074_audit_worker(self):
        """Persist no-order observations without delaying WS, price or order threads."""
        while not self.stop_event.is_set() or not self._v074_audit_queue.empty():
            try:
                row=self._v074_audit_queue.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                if self._v074_evidence is None:
                    raise OSError('FORENSIC_LEDGER_UNAVAILABLE')
                self._v074_evidence.write(row)
            except (sqlite3.Error,ValueError,TypeError,OSError) as exc:
                self._last_fetch_error[row['market']]='AUDIT_WRITE:'+type(exc).__name__
            finally:
                self._v074_audit_queue.task_done()

    def _record_entry_block(self,market,code,frame,barid,reason,rsi=None,trade_age_sec=None,strategy='trend',ma_details=None):
        """Do not call a rejected RSI observation a real BUY or virtual fill."""
        # Some broker-independent unit harnesses allocate Coordinator via
        # __new__; this must not turn diagnostics into a strategy exception.
        if not hasattr(self,'_v074_audit_unique'):self._v074_audit_unique=set()
        if not hasattr(self,'_v074_audit_queue'):self._v074_audit_queue=queue.Queue(maxsize=256)
        if not hasattr(self,'_v074_audit_dropped'):self._v074_audit_dropped=0
        try:
            value=mt._finite(rsi)
            age=mt._finite(trade_age_sec)
            row={'market':str(market),'code':str(code),'frame':str(frame),
                 'bar_end':str(barid),'reason':str(reason),
                 'rsi20_live':value,'strategy':str(strategy),
                 'last_trade_age_sec':round(age,3) if age is not None else None,
                 'logged_at':dt.datetime.now(mt.KST).isoformat(),
                 'audit_epoch':time.time(),
                 'ma_details':ma_details if strategy=='ma' and isinstance(ma_details,dict) else {}}
            key=(row['strategy'],row['market'],row['code'],row['frame'],
                 row['bar_end'],row['reason'])
            with self.lock:
                if key in self._v074_audit_unique:
                    return
                self._v074_audit_unique.add(key)
                if len(self._v074_audit_unique)>24000:
                    self._v074_audit_unique=set(list(self._v074_audit_unique)[-8000:])
                self._recent_blocked_entries[market].append(row)
            try:
                self._v074_audit_queue.put_nowait(row)
            except queue.Full:
                self._v074_audit_dropped+=1
                self._last_fetch_error[market]='AUDIT_QUEUE_OVERFLOW'
        except (KeyError,ValueError,TypeError,AttributeError):
            self._v074_audit_dropped+=1

    def record_ma_decision(self,market,code,frame,bar_end,reason,details=None):
        """Stock AND coin MA observation, including non-order reasons.

        A pre-5-second MA watch is never a broker/PAPER candidate by itself.
        No SQLite is opened on the actual live decision callback.
        """
        self._record_entry_block(market,code,frame,bar_end,reason,
                                 strategy='ma',ma_details=details)

    def _preclose_candidates(self,market,now):
        """v107 closed-candle first-under-30 rule; only next-open [0,10)s.

        Retains the original TrendWalletV054 executable quote/book, fee,
        available-quantity, position/slot and PAPER fill authority.
        No forming RSI can authorize a trade.
        """
        from market_trend_closed_entry_v107 import (
            first_closed_cross, first_ten_seconds, verified_closed_rows)
        if market=="stock":
            from krx_session_v041 import is_regular_open, SPECIAL_SESSIONS, START
            if not is_regular_open(now):return
        else:
            native=getattr(self.rt,'coin_runner',None)
            gate=getattr(native,'preload_gate',None)
            if gate is not None and not gate.snapshot().get('ready',False):
                return
        scheduled=[]
        for frame in mt.FRAMES:
            finish=_end_clock(now,market,frame)
            if finish is None:continue
            if market=="stock" and frame=="day":
                opening=SPECIAL_SESSIONS.get(now.date().isoformat(),
                                             (START,None))[0]
                begins=now.replace(hour=opening//60,minute=opening%60,
                                   second=0,microsecond=0)
            else:
                begins=finish-dt.timedelta(seconds=mt.FRAME_SECS[frame])
            if first_ten_seconds(now,begins):
                scheduled.append((frame,finish,begins))
        if not scheduled:return
        with self.lock:
            candidates=dict(self._candidates[market])
            histories={k:list(v) for k,v in self._history.items()
                       if k[0]==market and k[2] in {x[0] for x in scheduled}}
        # These are confirmed close observations. A candidate is not an
        # order until the original wallet verifies real market liquidity.
        qualified=collections.defaultdict(list)
        for code in candidates:
            for frame,finish,begins in scheduled:
                rows=verified_closed_rows(
                    histories.get((market,code,frame),()),begins,market,frame)
                if not rows:
                    continue
                signal=first_closed_cross(
                    [x["close"] for x in rows],mt.rsi20)
                if signal is None:continue
                closed_at=rows[-1]["end"]
                key=(market,code,frame,closed_at.isoformat())
                if key in self._ordered:continue
                qualified[code].append((frame,finish,begins,closed_at,signal))
        for code,options in qualified.items():
            chosen=mt.choose_frame([x[0] for x in options])
            candidate=next((x for x in options if x[0]==chosen),None)
            if candidate is None:continue
            frame,finish,begins,closed_at,signal=candidate
            at=self._decision_clock()
            if not first_ten_seconds(at,begins):
                self._record_entry_block(market,code,frame,closed_at.isoformat(),
                    'CLOSED_RSI_NEXT_OPEN_10S_EXPIRED',signal["rsi20_closed"])
                continue
            key=(market,code,frame,closed_at.isoformat())
            if key in self._ordered:continue
            if (self.journal.stop_reentry_blocked(market,code,at) or
                self.standalone_wallet.stop_reentry_blocked(market,code,at)):
                self._record_entry_block(market,code,frame,closed_at.isoformat(),
                    'STOP_REENTRY_LOCK',signal["rsi20_closed"])
                self._ordered.add(key)
                continue
            verified=self._quote(market,code)
            if verified is None:
                self._record_entry_block(market,code,frame,closed_at.isoformat(),
                    'CLOSED_RSI_FRESH_BOOK_OR_TRADE_MISSING',
                    signal["rsi20_closed"])
                continue  # retry real quote within this same ten-second gate
            self._ordered.add(key)
            # The position's bar_key is the CURRENT entry candle END; the
            # confirmed signal belongs to the immediately prior candle.
            barid=finish.isoformat()
            name=str(candidates[code][1]) if isinstance(
                candidates[code],(tuple,list)) else code
            disposition="NO_FILL"
            result=None
            unsafe=False
            try:
                p,disposition=self.standalone_wallet.buy(
                    market,frame,code,name,verified["ask"],
                    verified["ask_size"],at,barid,signal["rsi20_closed"])
                if p is not None:
                    self._positions[(market,"trend",frame,code)]=p
                    result=p
            except Exception as exc:
                disposition="UNSAFE:"+type(exc).__name__
                unsafe=True
                self._last_fetch_error[market]=disposition
            if str(disposition).startswith(("BROKER:","UNSAFE:")):
                unsafe=True
            if result is None:
                self._entry_block_counts[market][str(disposition)[:80]]+=1
                self._record_entry_block(market,code,frame,closed_at.isoformat(),
                    str(disposition)[:80],signal["rsi20_closed"])
            mode=(("ACTUAL" if result.__dict__.get("execution_mode")=="LIVE"
                   else "PAPER") if result else
                  ("UNSAFE" if unsafe else "VIRTUAL"))
            self.journal.event(
                "MT107:CLOSED:"+market+":"+code+":"+frame+":"+
                closed_at.isoformat(),market,"trend",frame,code,mode,
                "MARKET_TREND_RSI20_CONFIRMED_CLOSE_FIRST10_"+str(disposition),
                price=verified["ask"],
                rsi20_closed=signal["rsi20_closed"],
                payload={"signal_closed_at":closed_at.isoformat(),
                         "entry_bar_end":barid,
                         "rsi20_basis":"COMPLETED_CANDLE_CLOSE",
                         "rsi20_last10":signal["last_10_rsi20"],
                         "buy_window":"NEXT_BAR_OPEN_0_TO_10_SECONDS",
                         "verified_book":True,
                         "execution_mode":mode})
            if result is None and not unsafe:
                try:
                    v=mt.TrendPosition(
                        market,"trend",frame,code,at,verified["ask"],1,barid)
                    vkey=(market,"trend",frame,code,barid)
                    self._virtual[vkey]=v
                    self.journal.save_virtual(
                        json.dumps(vkey,ensure_ascii=False),v)
                except (ValueError,TypeError):
                    pass

    def _process_holds(self,market,now):
        for key,p in list(self._positions.items()):
            if p.market!=market or p.qty<=0:continue
            q=self._quote(market,p.symbol)
            if q is None:continue
            finish=_end_clock(now,market,p.frame)
            p.__dict__["bar3_preclose"]=bool(finish and mt.sell_bar3_due(now,finish))
            result=p.action(q["bid"],now,
                        bar_finished=bool(p.__dict__.get("bar_finalized")),
                        bar3_preclose=bool(p.__dict__.get("bar3_preclose")))
            if result:
                try:
                    owner=self.standalone_wallet if p.strategy=='trend' else self.wallet
                    sold,status=owner.sell(p,q["bid"],now,result[0],bid_size=q["bid_size"])
                    if not sold and (status in ("LIVE_POSITION_BROKER_DISABLED",
                            "PAPER_POSITION_LIVE_MODE","POSITION_FILL_MODE_UNVERIFIED")
                            or str(status).startswith(('BROKER:', 'UNSAFE:'))):
                        self._last_fetch_error[market]="SELL:"+status
                    if sold:
                        if result[0]=="MT_STOP_9PCT":
                            p.__dict__["stop_fill_at"]=now.isoformat()
                            if p.strategy=='trend':
                                self.standalone_wallet.save_position(p)
                                self.journal.record_stop_fill(p.market,p.symbol,now)
                            else:self.journal.save_position(p,p.position_id)
                        if p.qty<=0:self._positions.pop(key,None)
                except Exception as exc:
                    self._last_fetch_error[market]="SELL:"+type(exc).__name__
            else:
                # No need to open/write/close SQLite every 450ms while idle.
                # Retain periodic mark/peak checkpoints for restart safety.
                now_mono=time.monotonic()
                last=self._position_snapshot_at.get(key,0)
                if now_mono-last>=10.0:
                    if p.strategy=='trend':self.standalone_wallet.save_position(p)
                    else:self.journal.save_position(p,p.position_id)
                    self._position_snapshot_at[key]=now_mono
        for key,v in list(self._virtual.items()):
            if v.market!=market:continue
            q=self._quote(market,v.symbol)
            if q is None:continue
            finish=_end_clock(now,market,v.frame)
            if not v.__dict__.get("bar_finalized"):
                with self.lock:rows=self._history.get((market,v.symbol,v.frame),[])
                for row in rows:
                    if row["end"]>v.entry_at:
                        rsi=mt.rsi20([x["close"] for x in rows if x["end"]<=row["end"]])
                        v.close_entry_bar(v.bar_key,row["high"],row["low"],rsi)
                        v.__dict__["bar_finalized"]=True;break
            action=v.action(q["bid"],now,bool(v.__dict__.get("bar_finalized")),
                bool(finish and mt.sell_bar3_due(now,finish)))
            if not action:continue
            amount=min(v.qty,v.qty*action[1])
            v.apply_sell(action[0],amount,q["bid"])
            self.journal.save_virtual(json.dumps(key,ensure_ascii=False),v)
            self.journal.event("MT43:VEXIT:"+":".join(str(x) for x in key)+":"+action[0]+":"+str(int(now.timestamp())),
               v.market,v.strategy,v.frame,v.symbol,"VIRTUAL",action[0],price=q["bid"],
               qty=amount,payload={"hypothetical_entry":v.entry,
                                   "hypothetical_pct":100*(q["bid"]/v.entry-1)})
            if v.qty<=0:self._virtual.pop(key,None)

    def _loop(self,market):
        while not self.stop_event.is_set():
            try:
                started=time.monotonic()
                try:self._update_score(market)
                finally:self._record_latency(market,"score",started)
                started=time.monotonic()
                try:self._process_closed(market)
                finally:self._record_latency(market,"closed",started)
                # Entry evaluation has a dedicated short-latency worker.
                # Slow score/candle/holding IO must not consume its 1s window.
                started=time.monotonic()
                try:self._process_holds(market,dt.datetime.now(mt.KST))
                finally:self._record_latency(market,"holds",started)
            except Exception as exc:
                self._last_fetch_error[market]="COORD:"+type(exc).__name__
                self._record_entry_block(market,'__ENGINE__','all',
                    dt.datetime.now(mt.KST).date().isoformat(),
                    'OPERATIONAL_COORD_'+type(exc).__name__)
                # A trend-only failure must not pause native AT/MA entries.
            self.stop_event.wait(.45)

    def _preclose_loop(self,market):
        """Run RSI passive -22..12.5s and executable -12..7s independently.

        No stale 0B print or 0D book can authorize an order. A narrow stale
        interval must not erase the opportunity while genuine quotes recover.
        """
        while not self.stop_event.is_set():
            started=time.monotonic()
            try:
                self._preclose_candidates(market,dt.datetime.now(mt.KST))
            except Exception as exc:
                self._last_fetch_error[market]='PRECLOSE:'+type(exc).__name__
                self._record_entry_block(market,'__ENGINE__','all',
                    dt.datetime.now(mt.KST).date().isoformat(),
                    'OPERATIONAL_PRECLOSE_'+type(exc).__name__)
            finally:
                self._record_latency(market,'preclose',started)
            self.stop_event.wait(.20)

    def _watch_loop(self,market):
        """Record RSI<=40 internally; expose only RSI<=35 WATCH_PENDING.

        This research-only loop never submits a broker order or virtual fill.
        """
        stale_episodes=set()
        missing_history_reported=set()
        while not self.stop_event.is_set():
            # KRX does not produce genuine 0B prints outside regular hours.
            # Do not report a permanent feed outage at nights or weekends.
            if market=='stock':
                from krx_session_v041 import is_regular_open
                if not is_regular_open():
                    stale_episodes.clear()
                    self.stop_event.wait(5)
                    continue
            try:
                now=dt.datetime.now(mt.KST)
                since=time.monotonic()
                with self.lock:
                    candidates=list(self._candidates[market])
                    quotes=dict(self._quotes[market])
                for code in candidates:
                    raw=quotes.get(code)
                    max_age=COIN_VERIFIED_TRADE_MAX_AGE_SEC if market=='coin' else 3.0
                    if raw is None or since-raw[1]>max_age:
                        if code not in stale_episodes:
                            self._entry_block_counts[market]['WATCH_TICK_STALE']+=1
                            stale_episodes.add(code)
                        # A new BUY may not use this price. However, deleting
                        # a PREVIOUSLY verified RSI watch every 5s made quiet
                        # stocks invisible even though WatchBook has a 45s
                        # explicit expiry. Keep that proven old watch labelled
                        # by its original observation time until TTL expires.
                        # Actual -12s BUY still requires a NEW fresh trade/book.
                        continue
                    stale_episodes.discard(code)
                    price=raw[0]
                    for frame in mt.FRAMES:
                        finish=_end_clock(now,market,frame)
                        if finish is None:
                            self.watch.discard(market,code,frame)
                            continue
                        barid=finish.isoformat()
                        if self._sealed_watch.get((market,frame))==barid:
                            self.watch.discard(market,code,frame)
                            continue
                        rows=self._closed_series(market,code,frame,finish)
                        if not rows:
                            identity=(code,frame,barid)
                            if identity not in missing_history_reported:
                                missing_history_reported.add(identity)
                                self._entry_block_counts[market]['WATCH_HISTORY_MISSING']+=1
                                self._record_entry_block(market,code,frame,barid,
                                    'OPERATIONAL_WATCH_HISTORY_MISSING')
                            self.watch.discard(market,code,frame)
                            continue
                        rsi=mt.rsi20([x['close'] for x in rows],price)
                        if rsi is None:
                            self._entry_block_counts[market]['WATCH_RSI_UNAVAILABLE']+=1
                            self.watch.discard(market,code,frame)
                            continue
                        if rsi<=40:
                            self.journal.record_watch(market,code,frame,
                                price,rsi,barid,after_trigger=True)
                        if rsi<=30:
                            self._record_entry_block(market,code,frame,barid,
                                'RSI30_OBSERVED_NOT_AN_ORDER',rsi)
                        armed=self.journal.latch(market,code,frame).armed if rsi<=35 else False
                        self.watch.observe(market,code,frame,barid,rsi,armed,
                            observed_at=time.time(),price=price)
            except Exception as exc:
                self._last_fetch_error[market]='WATCH:'+type(exc).__name__
                self._record_entry_block(market,'__ENGINE__','all',
                    dt.datetime.now(mt.KST).date().isoformat(),
                    'OPERATIONAL_WATCH_'+type(exc).__name__)
            # Bounded observation-identity memory; no durable records deleted.
            if len(missing_history_reported)>12000:
                missing_history_reported.clear()
            self.stop_event.wait(5)

    def _ma_no_result_watchdog(self,market):
        """Record when an MA gate passed without a captured quote or decision.

        Only covers the existing verified Trend top-ranked watch scope.
        Independent audit worker, never touches an executable order, REST
        source, account position or broker connection.
        """
        sealed=set()
        while not self.stop_event.is_set():
            try:
                now=dt.datetime.now(mt.KST)
                if market=='stock':
                    # A missed stock MA entry only exists during genuine KRX
                    # trading. Never label after-hours / weekends as failures.
                    from d_v19_portfolio import live_session
                    from krx_session_v041 import is_regular_open
                    if not is_regular_open(now) or not live_session(now,'KRX'):
                        self.stop_event.wait(.8)
                        continue
                    import ma_v067_stock_runtime as native
                    due=[(frame,finish) for frame in mt.FRAMES
                         for remain,finish in [native._close_seconds(now,frame)]
                         if 1.0<=remain<=1.8]
                else:
                    import ma_v067_coin_runtime as native
                    due=[(frame,finish) for frame in mt.FRAMES
                         for remain,finish in [native._bar_close(now,frame)]
                         if 1.0<=remain<=1.8]
                if due:
                    with self.lock:
                        scope=tuple(self._candidates.get(market,{}))[:50]
                        quotes=dict(self._quotes.get(market,{}))
                        observed_ma={(key[2],key[3],key[4])
                                     for key in self._v074_audit_unique
                                     if key[0]=='ma' and key[1]==market
                                     and not str(key[5]).startswith('MA_PREWATCH_')}
                    for frame,finish in due:
                        bar=finish.isoformat()
                        guard=(market,frame,bar)
                        if guard in sealed:continue
                        sealed.add(guard)
                        if not scope:
                            self.record_ma_decision(market,'__ENGINE__',frame,bar,
                               'OPERATIONAL_MA_VERIFIED_RANK_CANDIDATES_EMPTY',
                               {'audit_scope':'TOP_RANK','not_an_order':True})
                        for code in scope:
                            if (code,frame,bar) in observed_ma:continue
                            observed=quotes.get(code)
                            try:
                                age=time.monotonic()-observed[1] if observed else None
                            except (TypeError,ValueError,IndexError):age=None
                            fresh=(age is not None and -0.5<=age<=3.0)
                            reason=('MA_NO_FRESH_OBSERVED_TICK_IN_10_TO_2_SECOND_WINDOW'
                                    if not fresh else
                                    'MA_10_TO_2_SECOND_DECISION_OUTCOME_NOT_RECORDED')
                            self.record_ma_decision(market,code,frame,bar,reason,
                               {'audit_scope':'TOP_RANK_ONLY',
                                'watchdog_after_window':True,
                                'last_quote_age_sec':round(age,3) if age is not None else None,
                                'no_broker_order_or_fill_inferred':True})
                    if len(sealed)>2400:sealed=set(list(sealed)[-600:])
            except Exception as exc:
                self._last_fetch_error[market]='MA_WATCHDOG:'+type(exc).__name__
            self.stop_event.wait(.2)

    def _book_evidence_loop(self,market):
        """Read-only, separately scheduled broker-book research collection.

        Live bid/ask snapshots are never manufactured from candle prices.
        Failures remain diagnostic, not a new reason to pause AT/MA or place
        a market-trend order.
        """
        from ai_live_book_v058 import sample_runtime
        while not self.stop_event.is_set():
            try:
                sample_runtime(self.rt.root,self.rt,market)
            except Exception as exc:
                self._last_fetch_error[market]="BOOK_EVIDENCE:"+type(exc).__name__
            self.stop_event.wait(2)

    def _rank_loop(self,market):
        while not self.stop_event.is_set():
            try:
                self._refresh_rank(market)
            except Exception as exc:
                self._last_fetch_error[market]="RANK_WORKER:"+type(exc).__name__
            self.stop_event.wait(5)

    def start(self):
        self._start_score_worker()
        coin=self.rt.coin_runner
        if coin is not None:
            # The property was installed after the original coin runner was
            # constructed: migrate its existing live price dictionary.
            earlier=coin.__dict__.pop("latest_prices",None)
            if earlier is not None:
                coin.latest_prices=earlier
            elif not isinstance(coin.latest_prices,_PriceMemory):
                coin.latest_prices=coin.latest_prices
        if coin is not None:
            orig=coin._both_watched_codes
            def watched():
                base=set(orig())
                with self.lock:
                    base.update(self._candidates["coin"])
                    base.update(p.symbol for p in self._positions.values() if p.market=="coin")
                # Original REST orderbook helper slices this collection;
                # preserve the original list contract, never return a set.
                return sorted(base)
            coin._both_watched_codes=watched
        for market in ("stock","coin"):
            for target in (lambda m=market:self._rank_loop(m),
                           lambda m=market:self._prefetch(m),
                           lambda m=market:self._book_evidence_loop(m),
                           lambda m=market:self._preclose_loop(m),
                           lambda m=market:self._ma_no_result_watchdog(m),
                           lambda m=market:self._watch_loop(m),
                           lambda m=market:self._loop(m)):
                thread=threading.Thread(target=target,daemon=True,
                       name="artitrade-market-trend-v043-"+market)
                thread.start();self.threads.append(thread)

    def stop(self):
        self.stop_event.set()
        # Wait briefly for old trend loops before a new coordinator starts.
        # The service remains fail-closed while the runtime restarts.
        for worker in list(self.threads):
            if worker.is_alive() and worker is not threading.current_thread():
                worker.join(timeout=0.5)
        if self._v074_audit_thread.is_alive():
            self._v074_audit_thread.join(timeout=1.0)

    def snapshot(self,include_performance=False):
        """Keep high-frequency telemetry out of the large local ledger packet.

        Service runtime diagnostics opt in. The hub/Android trade history stays
        stable when only profiling counters change, enabling UI memoization.
        """
        with self.lock:
            result={}
            for market in ("stock","coin"):
                item={
                    "mode":self.mode(market),"score":self.scores[market],
                    "manual_trend":bool(self.manual_trend[market]),
                    "mode_source":"INDEPENDENT",
                    "entry_mode":"INDEPENDENT_TREND",
                    "trend_active":True,
                    "watch_pending":len(self.watch.snapshot(market)) if hasattr(self,"watch") else 0,
                    "trend_wallet_frames":({tf:self.standalone_wallet.allocation(market,tf)
                       for tf in mt.FRAMES} if hasattr(self,"standalone_wallet") else {}),
                    "status":self.mode_details[market],
                    "candidates":len(self._candidates[market]),
                    "candidate_symbols":sorted(self._candidates[market]),
                    "forensic_audit_dropped":self._v074_audit_dropped,
                    "error":self._last_fetch_error.get(market,""),
                    "entry_block_reasons":dict(self._entry_block_counts[market].most_common(18)),
                    "recent_entry_blocks":list(self._recent_blocked_entries[market])[-30:],
                    "verified_coin_trade_max_age_sec":COIN_VERIFIED_TRADE_MAX_AGE_SEC if market=="coin" else 3.0,
                    "rsi_basis":"WILDER_RSI_20_MAIN_ONLY",
                    "rsi_signal_ma9_used":False}
                if include_performance:
                    perf=self._performance[market]
                    stages={}
                    for name,raw in perf["stages"].items():
                        calls=raw["calls"]
                        stages[name]={
                            "calls":calls,
                            "mean_ms":round(raw["total_ms"]/calls,3) if calls else None,
                            "max_ms":round(raw["max_ms"],3) if calls else None}
                    item["performance"]={
                        "preclose_cycles":perf["preclose_cycles"],
                        "preclose_no_due_cycles":perf["preclose_no_due_cycles"],
                        "preclose_due_cycles":perf["preclose_due_cycles"],
                        "preclose_symbols_examined":perf["preclose_symbols_examined"],
                        "stages":stages}
                result[market]=item
            return result

    def attach_history(self,raw):
        """Tag actual/virtual trend records without touching source cash/ledgers.

        A broker/parent position and its trend state are the SAME holding,
        not two holdings. A virtual missed buy is a research item, never
        a broker PENDING order or a balance reservation.
        """
        try:out=json.loads(raw) if isinstance(raw,str) else dict(raw)
        except (ValueError,TypeError):return raw
        out["market_trend_status"]=self.snapshot()
        out["market_trend_watch"]={m:self.watch.snapshot(m) for m in ('stock','coin')}
        for m in ('stock','coin'):
            node=out.setdefault(m,{})
            ledger=self.standalone_wallet.snapshot(m)
            # WATCH_PENDING lives in the read-only RSI observer, not the
            # FOK broker wallet. Earlier UI populated from ledger.pending
            # only, so every qualified watch appeared empty despite live RSI.
            # These rows are explicitly NOT orders or broker reservations.
            watches=[]
            for observation in out['market_trend_watch'][m]:
                if observation.get('status')!='WATCH_PENDING':continue
                watches.append({**observation,'tf':observation.get('frame'),
                    'strategy':'trend','entry_mode':'MARKET_TREND_RSI_WATCH',
                    'status':'WATCH_PENDING',
                    'external_order_pending':False,
                    'ledger_is_virtual_only':False,
                    'reason':'RSI20 관측 · 실제 매수주문 없음'})
            ledger.setdefault('pending',[]).extend(watches)
            ledger['pending_count']=len(ledger.get('pending') or [])
            node['trend']={'ledger':ledger}
        trend_research={"stock":[],"coin":[]}
        audit_errors={"stock":[],"coin":[]}
        with self.lock:
            current=tuple(self._positions.values())
            virtual=tuple(self._virtual.items())
        def target_ledger(market,strategy):
            if strategy=='trend':
                return out.get(market,{}).get('trend',{}).get('ledger',{})
            if market=="stock":
                ledger=out.get("stock",{}).get("at",{}).get("ledger",{})
                return ledger.get("ma_subledger",{}) if strategy=="ma" else ledger
            return out.get("coin",{}).get(strategy,{}).get("ledger",{})
        for p in current:
            packet={"id":p.position_id,"strategy":p.strategy,"code":p.symbol,
              "market":p.symbol if p.market=="coin" else "KOSPI",
              "tf":p.frame,"qty":p.qty,"entry_qty":p.original_qty,
              "qty0":p.original_qty,"entry_price":p.entry,"entry":p.entry,
              "entry_at":p.entry_at.isoformat(),"last_price":p.last_price,
              "mark":p.last_price,"paper_pnl":(p.last_price-p.entry)*p.qty,
              "status":"TREND_OPEN","entry_mode":"MARKET_TREND",
              "rsi20_live":p.__dict__.get("rsi20_live"),
              "rsi20_closed":p.__dict__.get("rsi20_closed")}
            node=target_ledger(p.market,p.strategy)
            if not isinstance(node,dict):continue
            positions=node.setdefault("positions",[])
            found=False
            for x in positions if isinstance(positions,list) else ():
                if not isinstance(x,dict):continue
                if (str(x.get("id"))==str(p.position_id) and
                    str(x.get("tf") or x.get("frame"))==p.frame and
                    str(x.get("code") or x.get("market"))==p.symbol):
                    x["entry_mode"]="MARKET_TREND"
                    found=True;break
            if not found and isinstance(positions,list):positions.append(packet)
        for key,v in virtual:
            node=target_ledger(v.market,v.strategy)
            if not isinstance(node,dict):continue
            # Append to the SAME queue used by the parent view. A new
            # empty 'pending' list must not shadow existing pending_orders.
            pending=next((node[k] for k in
                 ("pending","pending_orders","orders","candidates")
                 if isinstance(node.get(k),list)),None)
            if pending is None:
                pending=[]
                node["pending"]=pending
            pending.append({
                "id":"MT43:VIRTUAL:"+json.dumps(key,ensure_ascii=False),
                "tf":v.frame,"code":v.symbol,
                "market":v.symbol if v.market=="coin" else "KRX",
                "strategy":v.strategy,"entry_mode":"MARKET_TREND",
                "entry_at":v.entry_at.isoformat(),"buy_price":v.entry,
                "status":"VIRTUAL_RESEARCH_OPEN",
                "virtual_entered":True,"entry_observed":True,
                "reason":"마켓트렌드 미매수 가상추적 (브로커 미주문)",
                "ledger_is_virtual_only":True})
        # Only RSI20 entry decisions belong in RSI20 research.
        # SELL and INTENT events cannot masquerade as a new BUY signal.
        try:
            if self._v074_evidence and hasattr(self._v074_evidence,'recent_for_strategy'):
                forensic=(self._v074_evidence.recent_for_strategy('ma',650)
                          +self._v074_evidence.recent_for_strategy('trend',350))
            else:
                forensic=self._v074_evidence.recent(750) if self._v074_evidence else []
        except (sqlite3.Error,OSError) as exc:
            forensic=[]
            self._last_fetch_error['stock']='AUDIT_READ:'+type(exc).__name__
        # Different physical SQLite files: research audit cannot monopolize
        # broker, TrendJournal latch, or one-second executable order windows.
        ma_watch={'stock':[],'coin':[]}
        ma_watch_keys={'stock':set(),'coin':set()}
        for x in forensic+self.journal.events_recent(350):
            identity=str(x["event_key"])
            if identity.startswith(("MT74:AUDIT:","MA74:AUDIT:")):
                m=str(x["market"])
                if m not in audit_errors:continue
                is_ma=identity.startswith("MA74:")
                reason=str(x["reason"])
                try:detail=json.loads(x["payload"] or "{}")
                except (ValueError,TypeError):detail={}
                seen=dt.datetime.fromtimestamp(float(x["at"]),mt.KST).isoformat()
                evidence={"id":identity,"tf":x["frame"],"code":x["symbol"],
                    "market":m,"at":seen,"ts":seen,
                    "strategy":"ma" if is_ma else "trend",
                    "entry_mode":"MA_COIN_AUDIT" if is_ma else "MARKET_TREND_AUDIT",
                    "rsi20_live":x["rsi20_live"],
                    "bought":False,"entry_observed":False,
                    "execution_mode":"CANDIDATE_ONLY" if x["mode"]=="CANDIDATE_ONLY"
                                      else "NO_ORDER","audit_only":True,
                    "verified_order_placed":False,
                    "outcome":"OBSERVED_ONLY" if reason.startswith("RSI30_OBSERVED")
                              else "CANDIDATE_RECORDED_UNVERIFIED_EXECUTION"
                                   if x["mode"]=="CANDIDATE_ONLY"
                              else "NO_ORDER_RECORDED",
                    "reason":reason,"bar_end":detail.get("bar_end"),
                    "last_trade_age_sec":detail.get("last_trade_age_sec"),
                    "ma_details":detail.get("ma_details") or {}}
                if is_ma:
                    # These observations are never pending orders.  Expose
                    # a distinct MA watch pane so it is possible to inspect
                    # failure reasons even when the original order queue is 0.
                    identity=(x["symbol"],x["frame"],
                              "PREWATCH" if reason.startswith("MA_PREWATCH_") else "DECISION")
                    if (identity not in ma_watch_keys[m] and
                            len(ma_watch[m])<90 and
                            not str(x["symbol"]).startswith('__')):
                        ma_watch_keys[m].add(identity)
                        ma_watch[m].append({
                            "code":x["symbol"],"tf":x["frame"],
                            "bar_end":detail.get("bar_end"),"at":seen,
                            "status":"MA_OBSERVATION_ONLY",
                            "reason":reason,"ma_details":detail.get("ma_details") or {},
                            "external_order_pending":False,"verified_order_placed":False,
                            "ma120_down_tolerance":"0.0050"})
                    if m=="coin":
                        ma_node=out.get("coin",{}).get("ma",{})
                        if isinstance(ma_node,dict):
                            ma_research=ma_node.setdefault("research",{})
                            if isinstance(ma_research,dict):
                                ma_research.setdefault("items",[]).append(evidence)
                    elif m=="stock":
                        at=out.get("stock",{}).get("at",{})
                        if isinstance(at,dict):
                            ma_research=at.setdefault("research",{}).setdefault("ma",{})
                            if isinstance(ma_research,dict):
                                ma_research.setdefault("items",[]).append(evidence)
                else:
                    trend_research[m].append(evidence)
                    trend_node=out.get(m,{}).get("trend",{})
                    if isinstance(trend_node,dict):
                        target=trend_node.setdefault("research",{})
                        if isinstance(target,dict):
                            target.setdefault("items",[]).append(evidence)
                if (is_ma or reason.startswith("OPERATIONAL_") or
                    reason in ("ONE_SECOND_DECISION_DEADLINE_MISSED",
                        "QUOTE_OR_BOOK_STALE_BEFORE_ORDER",
                        "FRESH_BOOK_MISSING_AT_SUBMIT",
                        "CLOSED_CANDLE_HISTORY_MISSING")):
                    audit_errors[m].append({
                        "at":seen,"stage":("MA_"+m.upper() if is_ma else "MARKET_TREND")+"_"+reason,
                        "code":x["symbol"],"frame":x["frame"],
                        "message":reason+" | "+str(x["symbol"])+" / "+str(x["frame"])+
                                  " / "+str(detail.get("bar_end") or ""),
                        "status":("RULE_NOT_MET" if is_ma and
                            reason.startswith(("UP_RULE_","BEAR_RULE_"))
                            else "OBSERVATION_ONLY" if is_ma and
                            "CANDIDATE_REGISTERED" in reason
                            else "NO_ORDER"),
                        "ma_details":detail.get("ma_details") or {},
                        "source":"V074_PERSISTED_AUDIT"})
                continue
            if not (identity.startswith("MT43:SIG:") or
                    identity.startswith("MT54:SIG:")):continue
            item={"id":x["event_key"],"tf":x["frame"],
                  "code":x["symbol"],"market":x["market"],
                  "at":x["at"],"ts":x["at"],"strategy":x["strategy"],
                  "entry_mode":"MARKET_TREND",
                  "rsi20_live":x["rsi20_live"],"rsi20_closed":x["rsi20_closed"],
                  "score":None,"bought":x["mode"] in ("ACTUAL","PAPER"),
                  "entry_observed":x["mode"]=="VIRTUAL",
                  "execution_mode":"UNKNOWN_FILLED_LEDGER" if x["mode"]=="ACTUAL"
                      else "PAPER" if x["mode"]=="PAPER" else
                      "UNVERIFIED_ORDER" if x["mode"]=="UNSAFE" else "SHADOW_UNFILLED",
                  "reason":x["reason"],"price":x["price"],
                  "outcome":"REAL_FILL" if x["mode"]=="ACTUAL" else
                      "PAPER_FILL" if x["mode"]=="PAPER" else
                      "UNVERIFIED_ORDER" if x["mode"]=="UNSAFE" else "VIRTUAL"}
            try:
                info=json.loads(x["payload"])
                item["score"]=info.get("score")
                # Historical ACTUAL signals were used for both paper and live.
                # Only newer rows with explicit execution_mode can prove LIVE.
                if x["mode"]=="ACTUAL" and info.get("execution_mode")=="ACTUAL":
                    item["execution_mode"]="LIVE"
                if item["execution_mode"]=="UNKNOWN_FILLED_LEDGER":
                    item["outcome"]="UNVERIFIED_FILL"
            except (ValueError,TypeError):pass
            trend_research[x["market"]].append(item)
            if x['strategy']=='trend':
                target=out.get(x['market'],{}).get('trend',{})
                if isinstance(target,dict):
                    target.setdefault('research',{}).setdefault('items',[]).append(item)
            elif x["market"]=="stock":
                target=out.get("stock",{}).get("at",{})
                if x["strategy"]=="ma":
                    target=target.get("ledger",{}).get("ma_subledger",{})
                    if isinstance(target,dict):target.setdefault("research",[]).append(item)
                elif isinstance(target,dict):
                    target.setdefault("research",{}).setdefault("items",[]).append(item)
            else:
                target=out.get("coin",{}).get(x["strategy"],{})
                if isinstance(target,dict):
                    target.setdefault("research",{}).setdefault("items",[]).append(item)
        out["market_trend_research"]=trend_research
        out["market_trend_audit_errors"]=audit_errors
        out["ma_watch_v076"]=ma_watch
        return json.dumps(out,ensure_ascii=False,default=str)

def set_manual_mode(market,enabled):
    """UI bridge; changes entry selection only, not an order or ledger fill."""
    if market not in ("stock","coin") or type(enabled) is not bool:
        return json.dumps({"ok":False,"error":"INVALID_MARKET_OR_FLAG"})
    rt=getattr(_CORE,"_RUNTIME",None) if _CORE else None
    coordinator=getattr(rt,"market_trend",None) if rt else None
    if coordinator is None:
        return json.dumps({"ok":False,"error":"MARKET_TREND_NOT_READY"})
    try:
        return json.dumps(coordinator.set_manual_trend(market,enabled),
                          ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok":False,"error":"MANUAL_TREND_WRITE_FAILED",
                           "detail":type(exc).__name__},ensure_ascii=False)


def install(core):
    """Install guarded legacy buy paths before starting shared runtime."""
    global _CORE,_INSTALLED
    if _INSTALLED:return
    _INSTALLED=True;_CORE=core
    import d_v19_portfolio,d_upbit,stock_ma_engine,d_live

    # Upbit WS ticker and official REST ticker both write latest_prices. The
    # former confirms a newly received trade; REST only republishes a mark.
    # Tag REST assignments thread-locally, retaining native public API logic
    # and avoiding fake fresh RSI observations (also used by MA v067).
    original_rest_quotes=d_upbit.UpbitPaperRunner._rest_quotes
    def mark_only_rest_quotes(self,*a,**kw):
        old=getattr(_PRICE_OBSERVATION_CONTEXT,'rest_only',False)
        _PRICE_OBSERVATION_CONTEXT.rest_only=True
        try:return original_rest_quotes(self,*a,**kw)
        finally:_PRICE_OBSERVATION_CONTEXT.rest_only=old
    d_upbit.UpbitPaperRunner._rest_quotes=mark_only_rest_quotes

    stock_buy=d_v19_portfolio.D19PaperEngine._buy_limit_depth
    def gated_stock_buy(self,c,row,*a,**kw):
        rt=getattr(core,"_RUNTIME",None)
        co=getattr(rt,"market_trend",None) if rt else None
        if co is None or co.mode("stock")!="ORIGINAL":
            return False,"MT43_MARKET_SCORE_ENTRY_PAUSE"
        if co.active_symbol("stock",str(row["code"])):
            return False,"MT43_TREND_HELD_SYMBOL"
        return stock_buy(self,c,row,*a,**kw)
    d_v19_portfolio.D19PaperEngine._buy_limit_depth=gated_stock_buy

    coin_buy=d_upbit.CoinLedger._buy
    def gated_coin_buy(self,c,order,*a,**kw):
        rt=getattr(core,"_RUNTIME",None)
        co=getattr(rt,"market_trend",None) if rt else None
        if co is None or co.mode("coin")!="ORIGINAL":
            return 0
        if co.active_symbol("coin",str(order["market"])):
            return 0
        return coin_buy(self,c,order,*a,**kw)
    d_upbit.CoinLedger._buy=gated_coin_buy

    ma_scan=stock_ma_engine.StockMAService.scan_completed
    def gated_ma_scan(self,*a,**kw):
        rt=getattr(core,"_RUNTIME",None)
        co=getattr(rt,"market_trend",None) if rt else None
        if co is None or co.mode("stock")!="ORIGINAL":return
        return ma_scan(self,*a,**kw)
    stock_ma_engine.StockMAService.scan_completed=gated_ma_scan

    ma_tick=stock_ma_engine.StockMAService.on_tick
    def gated_ma_tick(self,tick,*a,**kw):
        rt=getattr(core,"_RUNTIME",None)
        co=getattr(rt,"market_trend",None) if rt else None
        blocked=(co is None or co.mode("stock")!="ORIGINAL" or
                 co.active_symbol("stock",str(tick[0])) if tick else True)
        _MA_GUARD.block=blocked
        try:return ma_tick(self,tick,*a,**kw)
        finally:_MA_GUARD.block=False
    stock_ma_engine.StockMAService.on_tick=gated_ma_tick

    # Upbit replaces latest_prices during preload. A property preserves
    # the fresh ticker capture across each original dictionary replacement.
    if not isinstance(getattr(d_upbit.UpbitPaperRunner,"latest_prices",None),property):
        def mt_prices_get(obj):
            return obj.__dict__.get("_market_trend_prices",{})
        def mt_prices_set(obj,value):
            def observed(code,price):
                rt=getattr(core,"_RUNTIME",None)
                trend=getattr(rt,"market_trend",None) if rt else None
                if trend:trend.observe_coin_tick(code,price)
            obj.__dict__["_market_trend_prices"]=_PriceMemory(dict(value or {}),observed)
        d_upbit.UpbitPaperRunner.latest_prices=property(mt_prices_get,mt_prices_set)

    # Preserve original KRX rank priority, but pin any live trend holding
    # before low-priority ranked symbols so it keeps 0B/0D exit coverage.
    original_pinned=d_live.LivePaperRunner._pinned_stocks
    def trend_pinned(self):
        result=dict(original_pinned(self))
        rt=getattr(core,"_RUNTIME",None)
        co=getattr(rt,"market_trend",None) if rt else None
        if co is not None:
            for p in tuple(co._positions.values()):
                if p.market!="stock" or p.qty<=0:continue
                identity=(self.market_map or {}).get(p.symbol)
                if identity is None:
                    market_id=p.__dict__.get("market_id","")
                    name=p.__dict__.get("company_name",p.symbol)
                    if market_id in ("KOSPI","KOSDAQ"):
                        identity=(market_id,name)
                if identity and identity[0] in ("KOSPI","KOSDAQ"):
                    if len(result)<200 or p.symbol in result:
                        result.setdefault(p.symbol,identity)
                    else:
                        co._last_fetch_error["stock"]="STOCK_SUBSCRIPTION_LIMIT_FOR_HELD"
                else:
                    co._last_fetch_error["stock"]="HELD_MARKET_IDENTITY_MISSING"
        return result
    d_live.LivePaperRunner._pinned_stocks=trend_pinned

    # Upbit's original WS builds ticker subscriptions from 'tracked', and
    # orderbook subscriptions separately from _both_watched_codes. Include
    # selected and held trend coins in both, even after universe refresh.
    if not isinstance(getattr(d_upbit.UpbitPaperRunner,"tracked",None),property):
        def trend_tracked_get(obj):
            base=set(obj.__dict__.get("_mt43_original_tracked",[]))
            rt=getattr(core,"_RUNTIME",None)
            co=getattr(rt,"market_trend",None) if rt else None
            if co is not None:
                with co.lock:
                    base.update(co._candidates.get("coin",{}))
                    base.update(p.symbol for p in tuple(co._positions.values())
                                if p.market=="coin" and p.qty>0)
            return sorted(base)
        def trend_tracked_set(obj,value):
            obj.__dict__["_mt43_original_tracked"]=list(value or [])
        d_upbit.UpbitPaperRunner.tracked=property(
            trend_tracked_get,trend_tracked_set)

    stock_tick=d_live.LivePaperRunner._new_tick
    def tracker_stock_tick(self,tick):
        rt=getattr(core,"_RUNTIME",None)
        co=getattr(rt,"market_trend",None) if rt else None
        if co:
            try:co.observe_stock_tick(tick)
            except Exception:pass
        return stock_tick(self,tick)
    d_live.LivePaperRunner._new_tick=tracker_stock_tick

    original_start=core.Runtime.start
    def runtime_start(self):
        self.market_trend=Coordinator(self)
        result=original_start(self)
        # The original core init installs the MA connection proxy. Wrap the
        # effective connector only after that setup, not its earlier source.
        if not hasattr(stock_ma_engine,"_mt43_connect"):
            stock_ma_engine._mt43_connect=stock_ma_engine._connect
            @contextlib.contextmanager
            def gated_connect(*a,**kw):
                with stock_ma_engine._mt43_connect(*a,**kw) as raw:
                    yield _NoBuyConnection(raw)
            stock_ma_engine._connect=gated_connect
        self.market_trend.start()
        return result
    core.Runtime.start=runtime_start

    original_stop=core.Runtime.stop
    def runtime_stop(self):
        try:
            trend=getattr(self,"market_trend",None)
            if trend:trend.stop()
        finally:return original_stop(self)
    core.Runtime.stop=runtime_stop

    original_snapshot=core.Runtime.snapshot
    def runtime_snapshot(self):
        raw=original_snapshot(self)
        try:
            out=json.loads(raw)
            if getattr(self,"market_trend",None):
                out["market_trend_status"]=self.market_trend.snapshot(
                    include_performance=True)
            return json.dumps(out,ensure_ascii=False,default=str)
        except Exception:return raw
    core.Runtime.snapshot=runtime_snapshot

    original_hub=core.Runtime.hub_records
    def runtime_hub(self):
        raw=original_hub(self)
        trend=getattr(self,"market_trend",None)
        return trend.attach_history(raw) if trend is not None else raw
    core.Runtime.hub_records=runtime_hub
