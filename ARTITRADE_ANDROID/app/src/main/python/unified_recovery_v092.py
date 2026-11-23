"""V092 cross-strategy recovery planner and risk-first quote dispatch.

PAPER ONLY. No new HTTP requests, no new broker orders and, critically, no
historical/hypothetical SELL or BUY. Each of the eight original engines remains
the sole authority for fills, fees, positions, stage state and loss stops.

The process-off gap is observable AFTER restart. A dead handset cannot submit
orders. Repaired official candles are reference/diagnostic evidence, NEVER an
executed order or a substitute for genuine current book/print liquidity.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import queue
import threading
import time
from collections import deque
from pathlib import Path

SCHEMA="ARTITRADE_RECOVERY_SCENARIOS_V092"
STRATEGIES={
    "stock":("at","ma","close","gmo","trend"),
    "coin":("general","ma","trend")
}
LABELS={
    "stock/at":"기준봉", "stock/ma":"MA", "stock/close":"종가베팅",
    "stock/gmo":"수급매매","stock/trend":"마켓트렌드",
    "coin/general":"일반","coin/ma":"MA","coin/trend":"마켓트렌드"
}
# Exit evidence states are evaluated AGAIN by the original strategy engine.
# This table is for recovery routing, never a replacement for its thresholds.
EXIT_PROOF={
    "stock/at":"KRX/NXT 거래소별 신규 0B 실체결",
    "stock/ma":"검증된 0D 매수호가·잔량 (봉 신호는 신규 0B 별도 필요)",
    "stock/close":"원본 CW 정책과 현재 KRX 0B/0D 실증",
    "stock/gmo":"현재 거래소 0D 매수호가·잔량",
    "stock/trend":"현재 거래소 호가·잔량 및 독립 원장",
    "coin/general":"업비트 신규 WS 체결·매수호가·잔량",
    "coin/ma":"업비트 신규 WS 체결·매수호가·잔량",
    "coin/trend":"업비트 신규 WS 체결·매수호가·잔량"
}
# Critical in MARKET_DATA_REPAIR mode: exits/stop first, then existing
# pending orders with proven execution, then new entries and research.
EVENT_PRIORITY=("HELD_STOP","HELD_TP_STAGE","UNCERTAIN_FILL_RECONCILE",
                "PENDING_EXISTING_ENTRY","NEW_SIGNAL","RESEARCH")
_INSTALLED=False


def plan_scenario(*, process_running=True, market_open=True,
                  internet_ok=None, ws_connected=False, subscribed=None,
                  exit_proof=False, entry_proof=False,
                  reference_ready=False, minute_ready=False,
                  has_holding=False, outstanding_order=False,
                  recovering=False, policy_proof=True):
    """Pure deterministic decision, no execution side effects.

    Whether an allowed trade actually fills is decided only by the
    strategy's original native signed-quote + original SQLite machinery.
    None of these results is a fill receipt.
    """
    result={"scenario":"","entry":"WAIT","exit":"WAIT",
            "repair":[],"reason":"","fill_allowed":False}
    def end(name,why,repairs=(),buy="WAIT",sell="WAIT"):
        result.update(scenario=name,reason=why,repair=list(repairs),
                      entry=buy,exit=sell)
        # Deliberately no "fill_allowed=True": the original engine must
        # separately prove a persisted executed BUY/SELL journal row.
        return result
    if not process_running:
        return end("PROCESS_STOPPED","프로그램 비구동 중 체결 불가",
                   ("RESTART_ORIGINAL_ENGINE","INSPECT_DURABLE_JOURNALS"))
    if internet_ok is False:
        return end("NETWORK_OFFLINE","휴대전화 인터넷 연결 불가",
                   ("NETWORK_BACKOFF","KEEP_PENDING_NOT_FILLED"))
    if not market_open:
        return end("MARKET_CLOSED","거래 가능 시간 아님",
                   ("REPAIR_OFFICIAL_CLOSED_CANDLES","WAIT_NEXT_LIVE_SESSION"))
    if not ws_connected:
        return end("BROKER_FEED_DOWN","거래소 WebSocket 확인되지 않음",
                   ("REAUTH_AND_RESUBSCRIBE_HELD_FIRST",
                    "REPAIR_OFFICIAL_CANDLES_READ_ONLY",
                    "KEEP_PENDING_NOT_FILLED"))
    if subscribed is False:
        return end("HELD_NOT_SUBSCRIBED","보유종목 실시간 구독 누락",
                   ("RESUBSCRIBE_HELD_FIRST","WAIT_VERIFIED_QUOTE"))
    if outstanding_order:
        return end("UNRESOLVED_EXECUTION","원장·접수된 주문 상태 불확실",
                   ("RECONCILE_ORIGINAL_UUID_AND_FILL_BEFORE_RETRY",))
    if not policy_proof:
        return end("POLICY_MISSING","원본 정책/목표 기준 확인 불가",
                   ("VERIFY_ORIGINAL_BUY_EXECUTION",
                    "PROVEN_POLICY_MIGRATION_ONLY"))
    if has_holding and not exit_proof:
        return end("NO_FRESH_EXIT_EVIDENCE","현재 유효한 매도 시세·호가 증거 없음",
                   ("PRIORITIZE_HELD_LIVE_QUOTE","REST_ONLY_FOR_CANDLES",
                    "DO_NOT_FILL_FROM_LAST_DISPLAY_PRICE"))
    if not has_holding and not entry_proof:
        return end("WAIT_ENTRY_SOURCE","신규 진입을 증명할 실시간 자료 확인 전",
                   ("RESTORE_COMPLETED_REFERENCE_BARS","WAIT_NEW_ORIGINAL_ENTRY_SIGNAL"),
                   sell="NO_HOLDING")
    # Held risk management must not be disabled only because a new-entry
    # reference frame or 1m bar is missing. Native strategies requiring
    # indicator bars may still wait for those specific signal bars.
    if not minute_ready or not reference_ready:
        return end("CANDLE_GAP_EXIT_FIRST","기준봉/1분봉 정합성 복구 중",
                   ("EVALUATE_EXISTING_PRICE_BASED_EXITS_NOW",
                    "REPAIR_OFFICIAL_CLOSED_FRAMES_READ_ONLY",
                    "PAUSE_INDICATOR_DEPENDENT_ENTRIES"),
                   sell="NATIVE_EXIT_EVALUATION" if has_holding else "NO_HOLDING")
    if recovering:
        return end("RESTART_CATCH_UP","앱 재시작·시세 재연결 후 상태 복구",
                   ("RECHECK_PRIOR_EXECUTED_ROWS",
                    "NATIVE_HELD_EXIT_STAGES_ON_NEW_LIVE_QUOTE",
                    "ENTRY_ONLY_IF_CURRENT_ORIGINAL_CRITERIA_VALID"),
                   buy="NATIVE_ENTRY_EVALUATION" if entry_proof else "WAIT_FRESH_ENTRY",
                   sell="NATIVE_EXIT_EVALUATION" if has_holding else "NO_HOLDING")
    return end("LIVE_VERIFIED","거래소 실시간 연결과 봉 정합성 확인",
               ("NATIVE_EXIT_BEFORE_NEW_ENTRY",),
               buy="NATIVE_ENTRY_EVALUATION" if entry_proof else "WAIT_FRESH_ENTRY",
               sell="NATIVE_EXIT_EVALUATION" if has_holding else "NO_HOLDING")


class HeldFirstQueue(queue.Queue):
    """Bounded FIFO inside each band; admission protects genuine held prints.

    Reorders received events only BEFORE their original 3s clock gate.
    It never re-stamps, replays, fabricates or evaluates a tick itself.
    Burst=8 avoids starving unrelated candidate observations.
    """
    def __init__(self,maxsize=0,held=None):
        self._held_predicate=held or (lambda event:False)
        super().__init__(maxsize=maxsize)
    def _init(self,maxsize):
        self._urgent=deque()
        self._ordinary=deque()
        self._consecutive=0
        self.dropped_candidate_on_full=0
        self.queue=self._ordinary
    def _qsize(self):
        return len(self._urgent)+len(self._ordinary)
    def _put(self,item):
        try:urgent=bool(self._held_predicate(item))
        except Exception:urgent=False
        (self._urgent if urgent else self._ordinary).append(item)
    def _get(self):
        if self._urgent and (self._consecutive<8 or not self._ordinary):
            self._consecutive+=1
            return self._urgent.popleft()
        self._consecutive=0
        return self._ordinary.popleft()
    def put_nowait(self,item):
        try:return super().put_nowait(item)
        except queue.Full:
            try:critical=bool(self._held_predicate(item))
            except Exception:critical=False
            if not critical:raise
            # Prefer a newly received REAL held exit print over an old
            # unrelated candidate print. This is still only observation,
            # NOT an executed trade. Preserve Queue.unfinished_tasks count.
            with self.not_full:
                if not self._ordinary:raise
                self._ordinary.popleft()
                self.dropped_candidate_on_full+=1
                self._urgent.append(item)
                self.not_empty.notify()
                return None


def _near_now(ts,seconds=3):
    if not ts:return False
    try:
        value=dt.datetime.fromisoformat(str(ts))
        if value.tzinfo is None:return False
        diff=(dt.datetime.now(value.tzinfo)-value).total_seconds()
        return -1<=diff<=seconds
    except (TypeError,ValueError,OverflowError):
        return False


def _price_book_recent(book,now=None):
    if not isinstance(book,dict):return False
    try:
        bid=float(book.get("bid") or 0)
        qty=float(book.get("bid_size") or 0)
        stamp=float(book.get("timestamp") or 0)
        current=now if now is not None else time.time()
        event=stamp/1000 if stamp>100000000000 else stamp
        return bid>0 and qty>0 and -0.5<=current-event<=3
    except (ValueError,TypeError,OverflowError):return False


class RecoveryObserver:
    def __init__(self,root):
        self.root=Path(root)
        self.path=self.root/"recovery_scenarios_v092.json"
        self._lock=threading.RLock()
        self._stocks={}
        self._coins={}
        self._held={}
        self._held_updated=0.0
        self._last_persist=0.0
        self._previous={}
        try:
            if self.path.exists():
                data=json.loads(self.path.read_text(encoding="utf8"))
                if isinstance(data,dict) and data.get("schema")==SCHEMA:
                    self._previous=data
        except (OSError,ValueError,TypeError):
            self._previous={}
        self.started_at=int(time.time())
        self.gap_seconds=(max(0,self.started_at-int(
            self._previous.get("observed_at") or self.started_at))
            if self._previous else None)
        self._last_states={}
        self._last_report={}
        self._stopped=False
        self._last_ws={"stock":None,"coin":None}
        self._return_until={"stock":0.0,"coin":0.0}
        self.android_network=None

    def set_android_network(self,validated):
        with self._lock:self.android_network=bool(validated)

    def stock_tick(self,tick):
        if not isinstance(tick,(tuple,list)) or len(tick)<9:return
        code=str(tick[0])
        if not code or str(tick[7]) not in ("KRX","NXT"):return
        if not _near_now(tick[6],3):return
        # Native exchange freshness validation remains authoritative. Merely
        # observing a callback never marks an executable fill.
        with self._lock:
            self._stocks[code]={"at":time.monotonic(),"venue":str(tick[7]),
                                "source":"KIWOOM_VERIFIED_NEW_0B"}

    def coin_tick(self,code,received):
        try:
            stamp=float(received)
            if abs(time.monotonic()-stamp)>2:return
        except (TypeError,ValueError,OverflowError):return
        with self._lock:
            self._coins[str(code)]={"at":stamp,"source":"UPBIT_AUTH_WS_TICKER"}

    def _bounded_watched(self,ledger,key):
        """ledger.watched_codes() without ever waiting more than 0.5 s for the ledger lock (see _held_symbols)."""
        cache=self.__dict__.setdefault("_watched_cache_v0360",{})
        lock=getattr(ledger,"lock",None)
        got=False
        try:
            if lock is not None:
                got=lock.acquire(timeout=0.5)
                if not got:return set(cache.get(key,()))
            value=set(ledger.watched_codes())
            cache[key]=value
            return value
        except Exception:
            return set(cache.get(key,()))
        finally:
            if got:
                try:lock.release()
                except Exception:pass

    def _held_symbols(self,rt):
        now=time.monotonic()
        with self._lock:
            if now-self._held_updated<6:return {k:set(v) for k,v in self._held.items()}
        symbols={market+"/"+strategy:set() for market,scopes in STRATEGIES.items()
                 for strategy in scopes}
        stock=getattr(rt,"stock_runner",None)
        coin=getattr(rt,"coin_runner",None)
        if stock:
            symbols["stock/at"].update(getattr(stock,"_paper_interest_held",()) or ())
            try:symbols["stock/ma"].update(stock.ma.pinned_stocks())
            except Exception:pass
            try:symbols["stock/gmo"].update(stock.gmo.holding_codes())
            except Exception:pass
            try:
                from at_close_bridge import close_pinned
                symbols["stock/close"].update(close_pinned())
            except Exception:pass
        if coin:
            # 0.3.6 (#151): this runs inside the engine HEARTBEAT (Runtime.snapshot). The coin ledger lock is
            # held for tens of seconds by scans / book writes; waiting for it here froze the heartbeat (all
            # 0.3.5 hang dumps show snapshot() parked in CoinLedger.watched_codes) and made the supervisor
            # restart the process. Wait at most 0.5 s and otherwise reuse the last known symbols.
            for key,ledger in (("coin/general",getattr(coin,"ledger",None)),
                               ("coin/ma",getattr(coin,"ma_ledger",None))):
                if ledger is not None:
                    symbols[key].update(self._bounded_watched(ledger,key))
        trend=getattr(rt,"market_trend",None)
        if trend is not None:
            got=False
            try:
                got=trend.lock.acquire(timeout=0.5)
                if got:
                    for p in tuple(trend._positions.values()):
                        key=str(p.market)+"/trend"
                        if key in symbols and p.qty>0:
                            symbols[key].add(str(p.symbol))
                else:
                    with self._lock:
                        for key in ("coin/trend","stock/trend"):
                            if key in symbols:symbols[key].update(self._held.get(key,()))
            except Exception:pass
            finally:
                if got:
                    try:trend.lock.release()
                    except Exception:pass
        with self._lock:
            self._held={k:frozenset(str(x) for x in v if str(x)) for k,v in symbols.items()}
            self._held_updated=now
            stock_critical=set().union(*(set(v) for k,v in self._held.items()
                                         if k.startswith("stock/")))
        if stock is not None:stock._v092_critical_held=frozenset(stock_critical)
        return {k:set(v) for k,v in self._held.items()}

    def _snapshot_one(self,rt,market,strategy,hold,connected,reference_ready,
                      session_open,subscribed):
        runner=(getattr(rt,"stock_runner",None) if market=="stock"
                else getattr(rt,"coin_runner",None))
        with self._lock:
            quotes=dict(self._stocks if market=="stock" else self._coins)
        now_mono=time.monotonic()
        any_fresh=any(0<=now_mono-q.get("at",0)<=3 for code,q in quotes.items()
                      if not hold or code in hold)
        if market=="stock":
            # The original stock D19/MA/GMO engines make execution decisions
            # only from KRX 0B or venue-matched 0D with real age/depth gates.
            book=False
            try:
                entries=getattr(runner.engine.book,"_bid_depth",{})
                for symbol in hold:
                    for venue in ("KRX","NXT"):
                        bid=entries.get((symbol,venue))
                        if not bid:continue
                        price,qty,seen=bid
                        age=(dt.datetime.now(seen.tzinfo)-seen).total_seconds()
                        if float(price)>0 and float(qty)>0 and 0<=age<=3:
                            book=True;break
                    if book:break
            except (AttributeError,ValueError,TypeError):pass
            exit_fresh=any_fresh if strategy=="at" else book
            entry_fresh=any_fresh or book
        else:
            books=getattr(runner,"last_book",{}) if runner else {}
            book=any(_price_book_recent(books.get(symbol)) for symbol in hold)
            exit_fresh=book
            entry_fresh=any_fresh and book
        # A subscription ACK is a transport proof only. Never claim a single
        # quiet symbol is receiving trades just because group REG succeeded.
        status=plan_scenario(
            process_running=runner is not None and not self._stopped,
            market_open=session_open,ws_connected=connected,
            internet_ok=True if connected else self.android_network,
            subscribed=subscribed,exit_proof=exit_fresh,
            entry_proof=entry_fresh,reference_ready=reference_ready,
            minute_ready=reference_ready,has_holding=bool(hold),
            recovering=now_mono<=self._return_until.get(market,0.0),
        )
        status.update(strategy=strategy,market=market,label=LABELS[market+"/"+strategy],
                      held_symbols=len(hold),live_quote_observed=any_fresh,
                      fresh_book_observed=book,
                      source_required=EXIT_PROOF[market+"/"+strategy],
                      evidence_scope="DIAGNOSTIC_ONLY_NATIVE_ENGINE_AUTHORITY")
        return status

    def snapshot(self,rt,original=None):
        original=original if isinstance(original,dict) else {}
        held=self._held_symbols(rt)
        stock=getattr(rt,"stock_runner",None)
        coin=getattr(rt,"coin_runner",None)
        src=original.get("stock") or {}
        stock_connected=bool(src.get("connected",False) and stock is not None)
        coin_connected=bool(coin and getattr(coin,"_ws_connected",False))
        # A live socket transition, not a remembered display price, arms
        # bounded 45s recovery for the next genuine per-symbol quote.
        now_mono=time.monotonic()
        for which,online in (("stock",stock_connected),("coin",coin_connected)):
            before=self._last_ws.get(which)
            if online and (before is False or (before is None and
                    self.gap_seconds is not None and self.gap_seconds>=30)):
                self._return_until[which]=now_mono+45
            self._last_ws[which]=online
        stock_ref=False;coin_ref=False
        try:stock_ref=bool(stock.preload_gate.snapshot().get("ready"))
        except Exception:pass
        try:coin_ref=bool(coin.preload_gate.snapshot().get("ready"))
        except Exception:pass
        from datetime import timezone,timedelta
        kst=dt.datetime.now(timezone(timedelta(hours=9)))
        stock_open=False
        if kst.weekday()<5:
            try:
                from d_v19_portfolio import live_session
                stock_open=bool(live_session(kst,"KRX") or live_session(kst,"NXT"))
                # The same official calendar/override used by the original
                # Kiwoom feed must decide holidays; weekday alone is unsafe.
                if stock_open and stock is not None:
                    from d_calendar import calendar_status,load_closed_overrides
                    active,_why=calendar_status(
                        kst,load_closed_overrides(stock.store.root))
                    stock_open=bool(active)
            except Exception:pass
        joined={}
        subscriptions=None
        if stock is not None:
            try:
                joined={str(x).replace("_NX","") for codes in
                        getattr(stock,"_subscribed_groups",{}).values()
                        for x in codes}
            except Exception:joined=set()
        active_coin=set(getattr(coin,"_v083_active_codes",()) or ()) if coin else set()
        all_rows={}
        for market,scopes in STRATEGIES.items():
            for strategy in scopes:
                key=market+"/"+strategy
                symbols=held.get(key,set())
                subscribes=(None if not symbols else
                             bool(symbols.issubset(joined)) if market=="stock" and joined else
                             bool(symbols.issubset(active_coin)) if market=="coin" and active_coin else None)
                all_rows[key]=self._snapshot_one(rt,market,strategy,symbols,
                    stock_connected if market=="stock" else coin_connected,
                    stock_ref if market=="stock" else coin_ref,
                    stock_open if market=="stock" else True,subscribes)
        scenarios={key:row["scenario"] for key,row in all_rows.items()}
        result={"schema":SCHEMA,"observed_at":int(time.time()),
                "scenario_by_strategy":all_rows,
                "scenario_counts":{name:sum(1 for value in scenarios.values() if value==name)
                                   for name in sorted(set(scenarios.values()))},
                "process_restart_gap_seconds":self.gap_seconds,
                "stock_ws_verified":stock_connected,
                "coin_ws_verified":coin_connected,
                "android_validated_internet":self.android_network,
                "history_backfill_is_not_execution":True,
                "current_quote_required_for_catch_up_execution":True,
                "order_executor":"ORIGINAL_STRATEGY_8_WALLETS_ONLY",
                "api_requests_added":0,
                "new_real_orders_enabled":False,
                "held_first_queue_dropped_candidates":(
                    getattr(getattr(stock,"_tick_queue",None),
                            "dropped_candidate_on_full",0)
                    if type(getattr(getattr(stock,"_tick_queue",None),
                                    "dropped_candidate_on_full",0)) is int else 0)
                }
        self._save_if_due(result)
        return result

    def _save_if_due(self,result):
        now=time.monotonic()
        transitions={key:row["scenario"] for key,row in result["scenario_by_strategy"].items()}
        with self._lock:
            changed=transitions!=self._last_states
            if not changed and now-self._last_persist<12:return
            self._last_states=transitions
            self._last_report=result
            self._last_persist=now
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.path.with_name(self.path.name+".tmp")
            tmp.write_text(json.dumps(result,ensure_ascii=False,separators=(",",":")),
                           encoding="utf8")
            os.replace(tmp,self.path)
        except (OSError,TypeError):pass

    def stopped(self):
        self._stopped=True
        try:
            prev=dict(self._last_report)
            if prev:
                prev["observed_at"]=int(time.time())
                prev["stopped_at"]=prev["observed_at"]
                self.path.write_text(json.dumps(prev,ensure_ascii=False),encoding="utf8")
        except OSError:pass


def install(core):
    """Observe already-received events; original eight strategy sell paths own all fills."""
    global _INSTALLED
    if _INSTALLED:return
    import d_live,d_upbit

    old_init=d_live.LivePaperRunner.__init__
    def stock_init(self,*args,**kwargs):
        old_init(self,*args,**kwargs)
        # No worker starts until the original start sequence, so the queue
        # replacement never loses an in-flight original event.
        native=self._tick_queue
        self._tick_queue=HeldFirstQueue(maxsize=native.maxsize,
            held=lambda event:(len(event)>=8 and str(event[0]) in
                (set(getattr(self,"_paper_interest_held",()) or ()) |
                 set(getattr(self,"_v092_critical_held",()) or ()))))
    d_live.LivePaperRunner.__init__=stock_init

    original_stock=d_live.LivePaperRunner._new_tick
    def stock_observe(self,tick):
        result=original_stock(self,tick)
        rt=getattr(core,"_RUNTIME",None)
        planner=getattr(rt,"_v092_recovery",None) if rt else None
        if planner:planner.stock_tick(tick)
        return result
    d_live.LivePaperRunner._new_tick=stock_observe

    original_coin=d_upbit.UpbitPaperRunner._v084_accept_tick
    def coin_observe(self,code,price,received,exchange_timestamp):
        result=original_coin(self,code,price,received,exchange_timestamp)
        rt=getattr(core,"_RUNTIME",None)
        planner=getattr(rt,"_v092_recovery",None) if rt else None
        if planner:planner.coin_tick(code,received)
        return result
    d_upbit.UpbitPaperRunner._v084_accept_tick=coin_observe

    original_start=core.Runtime.start
    def start(self):
        # A duplicate start is a NO-OP in the original Runtime. Keep the
        # existing recovery observer and its lifecycle/quote evidence too.
        if getattr(self,"started",False):
            return original_start(self)
        prior=getattr(self,"_v092_recovery",None)
        if prior is not None:
            prior.stopped()
        observer=RecoveryObserver(self.root)
        self._v092_recovery=observer
        try:
            return original_start(self)
        except Exception:
            observer.stopped()
            self._v092_recovery=None
            raise
    core.Runtime.start=start

    original_stop=core.Runtime.stop
    def stop(self):
        p=getattr(self,"_v092_recovery",None)
        if p:p.stopped()
        return original_stop(self)
    core.Runtime.stop=stop

    original_snapshot=core.Runtime.snapshot
    def snapshot(self):
        raw=original_snapshot(self)
        try:
            value=json.loads(raw) if isinstance(raw,str) else dict(raw)
            p=getattr(self,"_v092_recovery",None)
            if p:value["recovery_scenarios_v092"]=p.snapshot(self,value)
            return json.dumps(value,ensure_ascii=False,default=str)
        except Exception:return raw
    core.Runtime.snapshot=snapshot
    _INSTALLED=True
