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
import sqlite3
import threading
import time
from pathlib import Path

import market_trend_v043 as mt
from market_trend_wallet_v043 import TrendWallet, UnsafeFill

_CORE=None
_INSTALLED=False
_MA_GUARD=threading.local()
_STOCK_TICK_LOCK=threading.RLock()


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
    if frame=="day":
        return now.replace(hour=15,minute=30,second=0,microsecond=0)
    if not mt._krx_day(now.date()):return None
    start=now.replace(hour=9,minute=0,second=0,microsecond=0)
    elapsed=(now-start).total_seconds()
    if elapsed<0 or elapsed>=390*60:return None
    size=mt.FRAME_SECS[frame]
    step=int(elapsed//size)+1
    return min(start+dt.timedelta(seconds=step*size),
               start+dt.timedelta(minutes=390))


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
    return sorted(rows.values(),key=lambda x:x["start"])[-80:]


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
        self.wallet=TrendWallet(rt,self.journal)
        self.stop_event=threading.Event()
        self.lock=threading.RLock()
        self.threads=[]
        self.modes={"stock":"PAUSED","coin":"PAUSED"}
        self.scores={"stock":None,"coin":None}
        self.mode_details={"stock":"","coin":""}
        self._candidates={"stock":{},"coin":{}}
        self._last_rank={"stock":0.0,"coin":0.0}
        self._quotes={"stock":{},"coin":{}}
        self._history={}
        self._history_requests={}
        self._last_closed={}
        self._prechecked={}
        self._ordered=set()
        self._fair={"stock":0,"coin":0}
        self._positions={}
        self._virtual={}
        self._last_fetch_error={}
        self._watch_after={}
        self._recover_positions()

    def _recover_positions(self):
        for record in self.journal.open_positions():
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

    def mode(self,market):
        return self.modes.get(market,"PAUSED")

    def active_symbol(self,market,code):
        return any(p.market==market and p.symbol==code and p.qty>0
                   for p in tuple(self._positions.values())) or self.wallet.trend_owned(market,code)

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
        score=result.get("score")
        mode=mt.market_mode(score,result.get("status",""),
                result.get("stale",True),
                result.get("market_open",True))
        prior=self.modes.get(market,"PAUSED")
        self.scores[market]=score
        self.mode_details[market]=str(result.get("status") or "")
        if mode!=prior:
            if mode=="MARKET_TREND":
                self._cancel_native_pending(market)
            # On unknown score, existing pending orders remain as requested.
            with self.journal.lock,self.journal.connect() as c:
                c.execute("INSERT INTO gate_events VALUES(?,?,?,?,?)",
                         (time.time(),market,prior,mode,self.mode_details[market]))
            self.modes[market]=mode

    def _cancel_native_pending(self,market):
        # FOK new-mode order is not a persistent unfilled broker order.
        # Existing original pending only expire when original is disabled.
        if True:
            parent=_parent(market)
            for strategy in parent:
                try:
                    with self.wallet._connect(market,strategy) as c:
                        if market=="stock" and strategy=="at":
                            c.execute("UPDATE v19_orders SET status='MT43_MODE_SWITCH' WHERE status='PENDING'")
                            c.execute("DELETE FROM pending")
                        elif market=="stock":
                            c.execute("UPDATE candidates SET status='MT43_MODE_SWITCH' WHERE status='PENDING'")
                        else:
                            c.execute("UPDATE orders SET status='MT43_MODE_SWITCH' WHERE status='PENDING'")
                except sqlite3.Error as exc:
                    self._last_fetch_error[market]="PENDING_CANCEL:"+type(exc).__name__

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
        if raw is None or time.monotonic()-raw[1]>3.0:return None
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
        if any(x is None or x<=0 for x in (bid,ask,size,bid_size)):return None
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
                if len(observed)<40:raise RuntimeError("UPBIT_TOP40_INCOMPLETE")
                observed.sort(reverse=True)
                picked=[(code,("CRYPTO",code)) for _,code in observed[:40]]
            with self.lock:
                self._candidates[market]=dict(picked)
                self._last_rank[market]=time.monotonic()
            if market=="coin" and runner is not None:
                with runner._lock:
                    runner.tracked=sorted(set(runner.tracked)|set(self._candidates["coin"]))
        except Exception as exc:
            self._last_fetch_error[market]="RANK:"+type(exc).__name__
            if time.monotonic()-self._last_rank[market]>600:
                with self.lock:self._candidates[market]={}

    def _raw_history(self,market,code,frame):
        now=dt.datetime.now(mt.KST)
        if market=="coin":
            client=self.rt.coin_runner.minute_client
            data=client.candles(code,frame,count=65)
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
            return sorted(converted.values(),key=lambda x:x["end"])[-80:]
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
            if not pairs:
                self.stop_event.wait(1)
                continue
            for code,frame in pairs:
                if self.stop_event.is_set():return
                key=(market,code,frame)
                if time.monotonic()-self._history_requests.get(key,0)<45:
                    continue
                self._history_requests[key]=time.monotonic()
                try:
                    bars=self._raw_history(market,code,frame)
                    if len(bars)>=21:
                        with self.lock:self._history[key]=bars
                    else:self._last_fetch_error[market]="HISTORY_SHORT:"+str(len(bars))
                except Exception as exc:
                    self._last_fetch_error[market]="HISTORY:"+type(exc).__name__
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
                if changed:self.journal.save_position(p,p.position_id)
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

    def _preclose_candidates(self,market,now):
        if self.mode(market)!="MARKET_TREND":return
        with self.lock:candidates=dict(self._candidates[market])
        due=collections.defaultdict(list)
        for code in candidates:
            q=self._quote(market,code)
            if q is None:continue
            for frame in mt.FRAMES:
                finish=_end_clock(now,market,frame)
                if finish is None:continue
                barid=finish.isoformat()
                entry=(market,code,frame,barid)
                check=mt.live_rsi_check_due(now,finish,frame,market)
                submit=mt.preclose_due(now,finish,frame,market)
                if not check and not submit:continue
                closed=self._closed_series(market,code,frame,finish)
                if not closed:continue
                rsi=mt.rsi20([x["close"] for x in closed],q["trade"])
                if rsi is None:continue
                if check:
                    self._prechecked[entry]=rsi
                    self.journal.record_watch(market,code,frame,q["trade"],rsi,barid)
                if submit and (self._prechecked.get(entry,100)>30 or rsi>30):continue
                if submit and entry not in self._ordered:
                    due[code].append((frame,rsi,barid,q))
        for code,signals in due.items():
            chosen=mt.choose_frame([x[0] for x in signals])
            if chosen is None:continue
            frame,rsi,barid,q=next(x for x in signals if x[0]==chosen)
            self._ordered.add((market,code,frame,barid))
            latch=self.journal.latch(market,code,frame)
            if not latch.offer(barid,rsi):continue
            self.journal.save_latch(market,code,frame,latch)
            assigned=_parent(market)[self._fair[market]%2]
            self._fair[market]+=1
            name=str(candidates[code][1]) if isinstance(candidates[code],(tuple,list)) else code
            disposition="NO_FILL"
            result=None
            try:
                if self.mode(market)!="MARKET_TREND":
                    disposition="SCORE_SWITCHED"
                else:
                    p,disposition=self.wallet.buy(market,assigned,frame,code,name,
                        q["ask"],q["ask_size"],now,barid,rsi)
                    if p is not None:
                        self._positions[(market,assigned,frame,code)]=p;result=p
            except Exception as exc:
                disposition="UNSAFE:"+type(exc).__name__
                self._last_fetch_error[market]=disposition
            self.journal.event("MT43:SIG:"+market+":"+code+":"+frame+":"+barid,
                market,assigned,frame,code,"ACTUAL" if result else "VIRTUAL",
                disposition,price=q["ask"],rsi20_live=rsi,
                payload={"selected_frame":frame,"score":self.scores.get(market)})
            if not result:
                # Missed actual entry is shadow-only; never reserves wallet cash.
                try:
                    v=mt.TrendPosition(market,assigned,frame,code,now,q["ask"],1,barid)
                    key=(market,assigned,frame,code,barid)
                    self._virtual[key]=v
                    self.journal.save_virtual(json.dumps(key,ensure_ascii=False),v)
                except (ValueError,TypeError):pass

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
                    sold,status=self.wallet.sell(p,q["bid"],now,result[0],bid_size=q["bid_size"])
                    if sold and p.qty<=0:self._positions.pop(key,None)
                except Exception as exc:
                    self._last_fetch_error[market]="SELL:"+type(exc).__name__
            else:
                self.journal.save_position(p,p.position_id)
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
                self._update_score(market)
                self._process_closed(market)
                if self.mode(market)=="MARKET_TREND":
                    self._preclose_candidates(market,dt.datetime.now(mt.KST))
                self._process_holds(market,dt.datetime.now(mt.KST))
            except Exception as exc:
                self._last_fetch_error[market]="COORD:"+type(exc).__name__
                self.modes[market]="PAUSED"
            self.stop_event.wait(.45)

    def _watch_loop(self,market):
        """Record RSI<=40 and ten minutes of following price evidence.

        This runs separately from the -15/-12 second order scheduler. Its
        lower-frequency writes cannot delay time-critical trade decisions.
        """
        while not self.stop_event.is_set():
            try:
                if self.mode(market)=="MARKET_TREND":
                    now=dt.datetime.now(mt.KST)
                    since=time.monotonic()
                    with self.lock:
                        candidates=list(self._candidates[market])
                        quotes=dict(self._quotes[market])
                    for code in candidates:
                        raw=quotes.get(code)
                        if raw is None or since-raw[1]>3:continue
                        price=raw[0]
                        for frame in mt.FRAMES:
                            finish=_end_clock(now,market,frame)
                            if finish is None:continue
                            rows=self._closed_series(market,code,frame,finish)
                            if not rows:continue
                            rsi=mt.rsi20([x["close"] for x in rows],price)
                            if rsi is None:continue
                            key=(market,code,frame)
                            if rsi<=40:
                                self._watch_after[key]=time.time()+600
                            after=time.time()<self._watch_after.get(key,0)
                            if after:
                                self.journal.record_watch(market,code,frame,
                                    price,rsi,finish.isoformat(),
                                    after_trigger=True)
            except Exception as exc:
                self._last_fetch_error[market]="WATCH:"+type(exc).__name__
            self.stop_event.wait(30)

    def _rank_loop(self,market):
        while not self.stop_event.is_set():
            try:
                if self.mode(market)=="MARKET_TREND":
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
                           lambda m=market:self._watch_loop(m),
                           lambda m=market:self._loop(m)):
                thread=threading.Thread(target=target,daemon=True,
                       name="artitrade-market-trend-v043-"+market)
                thread.start();self.threads.append(thread)

    def stop(self):
        self.stop_event.set()

    def snapshot(self):
        return {market:{"mode":self.mode(market),"score":self.scores[market],
                 "status":self.mode_details[market],
                 "candidates":len(self._candidates[market]),
                 "error":self._last_fetch_error.get(market,"")}
                for market in ("stock","coin")}

    def attach_history(self,raw):
        """Expose mode inside original AT/MA tabs and send research-only signals."""
        try:out=json.loads(raw) if isinstance(raw,str) else dict(raw)
        except (ValueError,TypeError):return raw
        out["market_trend_status"]=self.snapshot()
        for p in self._positions.values():
            packet={"id":p.position_id,"strategy":p.strategy,"code":p.symbol,
              "market":p.symbol if p.market=="coin" else "KOSPI",
              "tf":p.frame,"qty":p.qty,"entry_qty":p.original_qty,
              "qty0":p.original_qty,"entry_price":p.entry,"entry":p.entry,
              "entry_at":p.entry_at.isoformat(),"last_price":p.last_price,
              "mark":p.last_price,"paper_pnl":(p.last_price-p.entry)*p.qty,
              "status":"OPEN","entry_mode":"마켓트렌드",
              "rsi20_live":p.__dict__.get("rsi20_live"),
              "rsi20_closed":p.__dict__.get("rsi20_closed")}
            if p.market=="stock":
                node=out.get("stock",{}).get("at",{}).get("ledger",{})
                if p.strategy=="ma":node=node.get("ma_subledger",{})
            else:node=out.get("coin",{}).get(p.strategy,{}).get("ledger",{})
            if isinstance(node,dict):
                node.setdefault("positions",[]).append(packet)
        for x in self.journal.events_recent(250):
            if x["reason"] in ("BROKER_ORDER_PREPARE",):continue
            item={"id":x["event_key"],"tf":x["frame"],
                  "code":x["symbol"],"market":x["market"],
                  "ts":x["at"],"strategy":x["strategy"],"entry_mode":"마켓트렌드",
                  "rsi20_live":x["rsi20_live"],"rsi20_closed":x["rsi20_closed"],
                  "score":None,"bought":x["mode"] in ("PAPER","ACTUAL"),
                  "reason":x["reason"],"price":x["price"]}
            try:info=json.loads(x["payload"]);item["score"]=info.get("score")
            except (ValueError,TypeError):pass
            if x["market"]=="stock":
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
        return json.dumps(out,ensure_ascii=False,default=str)


def install(core):
    """Install guarded legacy buy paths before starting shared runtime."""
    global _CORE,_INSTALLED
    if _INSTALLED:return
    _INSTALLED=True;_CORE=core
    import d_v19_portfolio,d_upbit,stock_ma_engine,d_live

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
                out["market_trend_status"]=self.market_trend.snapshot()
            return json.dumps(out,ensure_ascii=False,default=str)
        except Exception:return raw
    core.Runtime.snapshot=runtime_snapshot

    original_hub=core.Runtime.hub_records
    def runtime_hub(self):
        raw=original_hub(self)
        trend=getattr(self,"market_trend",None)
        return trend.attach_history(raw) if trend is not None else raw
    core.Runtime.hub_records=runtime_hub
