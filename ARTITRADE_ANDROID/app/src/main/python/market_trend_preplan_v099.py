"""v099: fast prepared RSI(20) first-cross entry for coin MarketTrend TOP50.

PAPER-only fast lane; the native TrendWalletV054 remains the only owner of
BUY, fill, cash, stops, original strategy position and confirmation. It never
creates a position or fill from a watch, a stale quote or a scenario. A 35-or-
lower WATCH merely precomputes three limit-price and size possibilities. Only
verified first cross from PREVIOUS COMPLETED RSI>30 to live RSI<=30 may BUY.

The existing near-close entry thread still works when no preplan is available.
This additional worker serves real crosses as soon as the watched live candle
permits them, rather than performing 50 x 4 synchronous checks at order time.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D, InvalidOperation
import math
import threading
import time

from partial_entry_v094 import tick_size, two_ticks_max
from market_trend_wallet_v043 import STOCK_FEE, COIN_FEE, MIN_COIN_KRW, _coin_qty
import market_trend_v043 as mt

_INSTALLED=False
FAST_POLL_SEC=.16
MAX_TRADE_AGE_SEC=10.
MAX_BOOK_AGE_SEC=3.

def _dec(v):
    try:
        n=D(str(v))
        return n if n.is_finite() and n>0 else None
    except (ValueError,TypeError,InvalidOperation):
        return None

def _limits(first,symbol,instrument_tick=None,market="coin"):
    p=_dec(first)
    if market not in ("coin","stock"):return ()
    if p is None or (market=="coin" and symbol in ("KRW-USDT","KRW-USDC")):
        return ()
    tick=tick_size(market,p,symbol,instrument_tick)
    if tick is None or p%tick:return ()
    second=p+tick
    third=two_ticks_max(market,p,symbol,instrument_tick)
    if third is None:return ()
    return (p,second,third)

def _qty_by_level(budget,ask_size,limits,market="coin"):
    money=_dec(budget);depth=_dec(ask_size)
    if money is None or depth is None or len(limits)!=3:
        return (D(0),D(0),D(0))
    if market not in ("stock","coin"):
        return (D(0),D(0),D(0))
    result=[]
    fee=STOCK_FEE if market=="stock" else COIN_FEE
    for price in limits:
        max_cost=money/(price*(D(1)+D(str(fee))))
        if market=="stock":
            quantity=D(max(0,min(int(depth),int(max_cost))))
        else:
            quantity=_coin_qty(min(depth*D(".5"),max_cost))
            if quantity<0:quantity=D(0)
            if quantity*price<D(str(MIN_COIN_KRW)):quantity=D(0)
        result.append(quantity)
    return tuple(result)

class PreparedCoinTrend:
    def __init__(self,coordinator,market="coin"):
        if market not in ("stock","coin"):raise ValueError("unknown market")
        self.market=market
        self.co=coordinator
        self._lock=threading.RLock()
        self._plans={}
        self._cache_accounts={}
        self._active=set()
        self._last_skip={}
        self._last_refresh=0.
        self._last_cleanup=0.

    def _allocation(self,frame):
        now=time.monotonic()
        v=self._cache_accounts.get(frame)
        if v is not None and now-v[0]<1.0:
            return v[1]
        wallet=self.co.standalone_wallet
        account=wallet.allocation(self.market,frame)
        self._cache_accounts[frame]=(now,account)
        return account

    def _quote_mark(self,symbol):
        if self.market=="stock":
            runner=self.co.rt.stock_runner
            if runner is None:return None
            identity=(getattr(runner,"market_map",{}) or {}).get(symbol)
            if not identity or identity[0] not in ("KOSPI","KOSDAQ"):
                return None
            depth=getattr(getattr(runner,"engine",None),"book",None)
            if depth is None:return None
            try:
                with depth._lock:
                    raw=depth._items.get((symbol,"KRX"))
                if raw is None or len(raw)<4:return None
                ask,bid,ask_size,observed_at=raw[:4]
                now=dt.datetime.now(mt.KST)
                if (observed_at.tzinfo is None or
                    not -.5<=(now-observed_at).total_seconds()<=MAX_BOOK_AGE_SEC):
                    return None
                ask=_dec(ask);bid=_dec(bid);size=_dec(ask_size)
                if ask is None or bid is None or size is None or ask<bid:
                    return None
                return {"ask":ask,"ask_size":size,
                        "book_timestamp_ms":int(observed_at.timestamp()*1000),
                        "instrument_tick":None}
            except (AttributeError,ValueError,TypeError,ArithmeticError,OverflowError):
                return None
        coin=self.co.rt.coin_runner
        if coin is None:return None
        with coin._lock:
            book=dict(coin.last_book.get(symbol) or {})
        try:
            stamp=float(book.get("timestamp",0))/1000.
            age=time.time()-stamp
            ask=_dec(book.get("ask"));size=_dec(book.get("ask_size"))
            bid=_dec(book.get("bid"))
            if (not -.5<=age<=MAX_BOOK_AGE_SEC or ask is None or
                    bid is None or ask<bid or size is None):
                return None
            return {"ask":ask,"ask_size":size,"book_timestamp_ms":int(stamp*1000),
                    "instrument_tick":book.get("sr_tick_size")}
        except (ValueError,TypeError,ArithmeticError):
            return None

    def _setup_plan(self,row,now):
        symbol=str(row.get("code") or "")
        frame=str(row.get("frame") or "")
        barid=str(row.get("bar_end") or "")
        if not symbol or frame not in mt.FRAMES or not barid:return None
        try:
            val=float(row.get("rsi20_live"))
            if not math.isfinite(val) or val<0 or val>35:return None
        except (ValueError,TypeError):return None
        with self.co.lock:
            if symbol not in self.co._candidates[self.market]:return None
        try:finish=dt.datetime.fromisoformat(barid)
        except ValueError:return None
        if finish.tzinfo is None or now>=finish or (finish-now).total_seconds()>mt.FRAME_SECS[frame]:
            return None
        key=(symbol,frame,barid)
        with self._lock:
            plan=self._plans.get(key)
            if plan is None:
                plan={"code":symbol,"frame":frame,"bar_end":barid,
                      "status":"RSI35_PREPARED","first_seen_at":now.isoformat(),
                      "armed":bool(row.get("armed")),"scenarios":(),
                      "anchor_price":None,"scenario":0,
                      "budget_won":0,"tries":0,"last_quote_key":None,
                      "last_error":None,"filled":False,"locked":False}
                self._plans[key]=plan
            plan["rsi20_live"]=row.get("rsi20_live")
            plan["armed"]=bool(row.get("armed"))
            if plan["filled"] or plan["locked"]:
                return plan
        try:
            a=self._allocation(frame)
            if int(a["positions"])>=int(a["max_positions"]):
                plan["status"]="FRAME_FULL"
                return plan
            budget=min(int(a["cash"]),int(a["seed"])//max(1,int(a["max_positions"])))
        except (KeyError,ValueError,TypeError,AttributeError,OverflowError):
            plan["status"]="ACCOUNT_UNAVAILABLE"
            return plan
        q=self._quote_mark(symbol)
        with self._lock:
            plan["budget_won"]=max(0,budget)
            if q is None:
                plan["status"]="WAIT_FRESH_BOOK"
                return plan
            levels=_limits(q["ask"],symbol,q.get("instrument_tick"),self.market)
            if len(levels)!=3:
                plan["status"]="PRICE_TICK_UNVERIFIED"
                return plan
            plan["anchor_price"]=str(levels[0])
            plan["scenarios"]=tuple(str(x) for x in levels)
            plan["precomputed_qty"]=tuple(str(x) for x in
                                            _qty_by_level(budget,q["ask_size"],levels,self.market))
            plan["book_timestamp_ms"]=q["book_timestamp_ms"]
            plan["book_ask_size"]=str(q["ask_size"])
            plan["updated_at"]=now.isoformat()
            plan["status"]="RSI35_PREPARED"
            return plan

    def prepare(self,now=None):
        now=now or dt.datetime.now(mt.KST)
        if now.tzinfo is None:raise ValueError("timezone required")
        co=self.co
        if co.stop_event.is_set():return []
        if co.standalone_wallet._is_live(self.market):return []
        if self.market=="stock":
            from krx_session_v041 import is_regular_open
            if not is_regular_open(now):return []
        raw=co.watch.snapshot(self.market)
        prepared=[]
        for r in raw:
            try:rsi=float(r.get("rsi20_live"))
            except (ValueError,TypeError):continue
            if not math.isfinite(rsi) or rsi>35:continue
            try:p=self._setup_plan(r,now)
            except Exception as exc:
                co._last_fetch_error[self.market]="MT99_PREPLAN:"+type(exc).__name__
                continue
            if p is not None:prepared.append(p)
        if time.monotonic()-self._last_cleanup>5:
            self._last_cleanup=time.monotonic()
            with self._lock:
                for key,plan in tuple(self._plans.items()):
                    try:end=dt.datetime.fromisoformat(key[2])
                    except ValueError:end=now
                    if now>end+dt.timedelta(seconds=45):
                        del self._plans[key]
        return prepared

    def _emit_skip(self,code,frame,barid,reason,rsi=None):
        k=(code,frame,barid,reason)
        mono=time.monotonic()
        with self._lock:
            if mono-self._last_skip.get(k,0)<2:return
            self._last_skip[k]=mono
            if len(self._last_skip)>1500:
                self._last_skip={key:value for key,value in self._last_skip.items()
                                 if mono-value<90}
        self.co._record_entry_block(self.market,code,frame,barid,
                                   "MT99_"+reason,rsi)

    def _candidate_decision(self,plan,now):
        co=self.co
        code=plan["code"];frame=plan["frame"];barid=plan["bar_end"]
        finish=dt.datetime.fromisoformat(barid)
        if now>=finish or (finish-now).total_seconds()>mt.FRAME_SECS[frame]:
            plan["status"]="EXPIRED"
            return
        # Stock keeps the original KRX 12-to-7-seconds-before-close entry
        # decision gate; preflight itself never creates a BUY.
        if self.market=="stock" and not mt.preclose_due(now,finish,frame,"stock"):
            return
        key=(self.market,code,frame,barid)
        if key in co._ordered:return
        # An earlier rejected/filled/sold status is not a fresh BUY signal.
        if plan["status"] in ("FILLED","STOP_LOCK","ALREADY_OWNED",
                               "FRAME_FULL","BUDGET_UNAVAILABLE","BROKER_UNCERTAIN"):
            return
        with co.lock:
            if code not in co._candidates[self.market]:return
            quote_record=co._quotes[self.market].get(code)
        if quote_record is None:
            return
        trade,received=quote_record
        age=time.monotonic()-received
        max_age=3.0 if self.market=="stock" else MAX_TRADE_AGE_SEC
        if not -.5<=age<=max_age:
            plan["status"]="STALE_TRADE"
            return
        # A quote+book pair is evaluated once. 40 candidates every 160ms
        # would otherwise repeat cached RSI and SQLite reads while nothing
        # has changed, recreating the original missed-deadline bottleneck.
        fresh_book=self._quote_mark(code)
        observed_key=(received, fresh_book["book_timestamp_ms"] if fresh_book else None)
        if plan.get("_last_seen_market_sample")==observed_key:
            return
        plan["_last_seen_market_sample"]=observed_key
        rows=co._closed_series(self.market,code,frame,finish)
        if not rows:
            self._emit_skip(code,frame,barid,"COMPLETED_BARS_MISSING")
            return
        closes=[x["close"] for x in rows]
        prior=mt.rsi20(closes)
        if prior is None:
            self._emit_skip(code,frame,barid,"PRIOR_RSI_UNVERIFIED")
            return
        if prior<=30:
            plan["status"]="PRIOR_CLOSED_RSI_ALREADY_BELOW_30"
            return
        live=mt.rsi20(closes,trade)
        if live is None or live>30:return
        if co.journal.stop_reentry_blocked(self.market,code,now) or co.standalone_wallet.stop_reentry_blocked(self.market,code,now):
            plan["status"]="STOP_LOCK"
            self._emit_skip(code,frame,barid,"STOP_REENTRY_LOCK",live)
            return
        # A live RSI reading below 30 is NOT proof of executable liquidity.
        q=co._quote(self.market,code)
        book=self._quote_mark(code)
        if q is None or book is None:
            plan["status"]="WAIT_FRESH_BOOK"
            self._emit_skip(code,frame,barid,"VERIFIED_BOOK_MISSING",live)
            return
        ask=_dec(q.get("ask"))
        if ask is None or ask!=book["ask"]:
            return
        with self._lock:
            levels=tuple(_dec(x) for x in plan.get("scenarios",()))
            if len(levels)!=3 or any(v is None for v in levels):
                # Preflight did not yet see a real book. Build it now before
                # submitting; the original wallet still rechecks everything.
                levels=_limits(ask,code,book.get("instrument_tick"),self.market)
                if len(levels)!=3:return
                plan["anchor_price"]=str(levels[0])
                plan["scenarios"]=tuple(str(x) for x in levels)
                plan["precomputed_qty"]=tuple(str(x) for x in _qty_by_level(
                    plan.get("budget_won",0),book["ask_size"],levels,self.market))
                plan["book_timestamp_ms"]=book["book_timestamp_ms"]
            plan["locked"]=True
        scenario=next((i+1 for i,limit in enumerate(levels) if ask<=limit),0)
        if not scenario:
            plan["status"]="ABOVE_SCENARIO_3_CEILING"
            self._emit_skip(code,frame,barid,"ASK_ABOVE_PLUS2_TICK",live)
            return
        plan["scenario"]=scenario
        proof_key=(scenario,book["book_timestamp_ms"],str(ask),str(book["ask_size"]))
        if plan["last_quote_key"]==proof_key:return
        with self._lock:
            if key in self._active:return
            self._active.add(key)
            plan["last_quote_key"]=proof_key
            plan["tries"]+=1
        try:
            # A signal is strictly the ORIGINAL first 30 cross. A prepared
            # RSI35 watch never authorizes an order by itself.
            latch=co.journal.latch(self.market,code,frame)
            if not latch.offer(barid,live):
                plan["status"]="SIGNAL_NOT_ARMED"
                return
            if co.standalone_wallet.owned(self.market,code):
                plan["status"]="ALREADY_OWNED"
                return
            if co.standalone_wallet._is_live(self.market):
                plan["status"]="BROKER_UNCERTAIN"
                return
            name=str(co._candidates[self.market][code][1])
            # Re-read the current decision clock at commit time. A test
            # fixture may inject the same KST clock; production uses the
            # actual wall clock, never an expired stored watch timestamp.
            at=(co._decision_clock() if hasattr(co,"_decision_clock")
                else dt.datetime.now(mt.KST))
            if at>=finish or co.stop_event.is_set():return
            if self.market=="stock" and not mt.preclose_due(at,finish,frame,"stock"):
                plan["status"]="ENTRY_WINDOW_EXPIRED"
                return
            # The existing wallet recalculates its own quantities, SQLite
            # cash, maximum holdings, same market book and filled provenance.
            position,reason=co.standalone_wallet.buy(
                self.market,frame,code,name,float(q["ask"]),float(q["ask_size"]),
                at,barid,live)
            if position is None:
                plan["last_error"]=str(reason)
                plan["status"]="WAIT_NEXT_BOOK" if reason=="INSUFFICIENT_DEPTH_OR_CASH" else str(reason)
                self._emit_skip(code,frame,barid,"NATIVE_"+str(reason)[:55],live)
                if str(reason).startswith(("BROKER:","UNSAFE:")):
                    plan["status"]="BROKER_UNCERTAIN"
                return
            # Commit signal-latch / decision evidence ONLY after original
            # wallet reports an authoritative PAPER fill.
            co.journal.save_latch(self.market,code,frame,latch)
            co._positions[(self.market,"trend",frame,code)]=position
            co._ordered.add(key)
            co.watch.discard(self.market,code,frame)
            plan["status"]="FILLED";plan["filled"]=True
            plan["filled_at"]=at.isoformat()
            plan["fill_price"]=float(position.entry)
            plan["filled_qty"]=float(position.qty)
            co.journal.event("MT54:SIG:"+self.market+":"+code+":"+frame+":"+barid,
                self.market,"trend",frame,code,
                "ACTUAL" if position.execution_mode=="LIVE" else "PAPER",
                ("MT081_STOCK_PREPARED_S" if self.market=="stock" else
                 "MT99_PREPARED_S")+str(scenario)+"_BOUGHT",
                price=float(position.entry),rsi20_live=live,
                payload={"scenario":scenario,"preplan_anchor":plan.get("anchor_price"),
                         "max_price":str(levels[-1]),"verified_book_ms":book["book_timestamp_ms"],
                         "original_fill_mode":position.execution_mode,
                         "top50":True})
        except Exception as exc:
            plan["status"]="UNSAFE_EXCEPTION"
            plan["last_error"]=type(exc).__name__
            self._emit_skip(code,frame,barid,"UNSAFE_"+type(exc).__name__,live)
        finally:
            with self._lock:self._active.discard(key)

    def evaluate(self,now=None):
        now=now or dt.datetime.now(mt.KST)
        if now.tzinfo is None:raise ValueError("timezone required")
        if self.co.stop_event.is_set():return
        if self.co.standalone_wallet._is_live(self.market):return
        if self.market=="stock":
            from krx_session_v041 import is_regular_open
            if not is_regular_open(now):return
        with self._lock:
            selected=[p for p in self._plans.values()
                      if p["status"] not in ("FILLED","EXPIRED","STOP_LOCK",
                        "ALREADY_OWNED","FRAME_FULL","BROKER_UNCERTAIN")]
        # Dedicated priority: only <=35 watch plans, not the full TOP50 x 4.
        selected.sort(key=lambda p:(float(p.get("rsi20_live") or 100),
                                    mt.PRIORITY.get(p["frame"],0)*-1))
        for p in selected[:40]:
            if self.co.stop_event.is_set():break
            try:self._candidate_decision(p,now)
            except Exception as e:
                self.co._last_fetch_error[self.market]="MT_FAST:"+type(e).__name__

    def _loop(self):
        c=self.co
        while not c.stop_event.is_set():
            try:
                now=dt.datetime.now(mt.KST)
                # Update a small prepared set once per second; decision
                # processing then runs at 160ms cadence with no rank scan.
                if time.monotonic()-self._last_refresh>=1.0:
                    self._last_refresh=time.monotonic()
                    self.prepare(now)
                self.evaluate(now)
            except Exception as e:
                c._last_fetch_error[self.market]="MT_WORKER:"+type(e).__name__
            c.stop_event.wait(FAST_POLL_SEC)

    def snapshot(self):
        with self._lock:
            return [dict((k,v) for k,v in dict(p,scenarios=list(p.get("scenarios",())),
                         precomputed_qty=list(p.get("precomputed_qty",()))).items()
                         if not k.startswith("_"))
                    for p in self._plans.values() if p.get("status")!="EXPIRED"][-80:]


def install():
    global _INSTALLED
    if _INSTALLED:return
    import market_trend_runtime_v043 as native
    native_start=native.Coordinator.start
    native_attach=native.Coordinator.attach_history

    def start(self):
        if not hasattr(self,"_mt99_preplans"):
            self._mt99_preplans=PreparedCoinTrend(self)
        result=native_start(self)
        # Original close/holds/RSSI45 worker and native Upbit single socket
        # keep their normal schedule. The coin entry fast lane is independent.
        worker=threading.Thread(target=self._mt99_preplans._loop,
            daemon=True,name="ARTITRADE_MT99_COIN_35_PREPLAN_FAST")
        worker.start()
        self.threads.append(worker)
        return result

    def attach_history(self,raw):
        out=native_attach(self,raw)
        planner=getattr(self,"_mt99_preplans",None)
        if planner is None:return out
        try:
            import json
            snap=json.loads(out) if isinstance(out,str) else out
            if not isinstance(snap,dict):return out
            plans=planner.snapshot()
            snap["market_trend_preplans_v099"]={"coin":plans}
            status=snap.setdefault("market_trend_status",{})
            if isinstance(status,dict):
                coin=status.setdefault("coin",{})
                if isinstance(coin,dict):
                    coin["coin_market_trend_rank_target"]=50
                    coin["prepared_entries"]=len(plans)
            watches=snap.get("market_trend_watch")
            if isinstance(watches,dict):
                by_key={(p["code"],p["frame"],p["bar_end"]):p for p in plans}
                for row in watches.get("coin",[]):
                    if not isinstance(row,dict):continue
                    plan=by_key.get((row.get("code"),row.get("frame"),row.get("bar_end")))
                    if plan:
                        row["preplan_status"]=plan.get("status")
                        row["preplan_scenarios"]=plan.get("scenarios")
                        row["preplan_qty"]=plan.get("precomputed_qty")
                        row["preplan_budget"]=plan.get("budget_won")
            return json.dumps(snap,ensure_ascii=False,default=str)
        except (ValueError,TypeError):
            return out

    native.Coordinator.start=start
    native.Coordinator.attach_history=attach_history
    _INSTALLED=True
