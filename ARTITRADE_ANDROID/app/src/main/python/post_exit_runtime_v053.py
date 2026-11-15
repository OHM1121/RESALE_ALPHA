"""Install post-exit research independently of trading engines.

The only modifications to feed classes are adding low-priority subscriptions
and queuing received timestamped quotes. No broker, wallet, order, stop, or
strategy methods are replaced.
"""
from __future__ import annotations
import json
import threading
import time

from post_exit_high_v053 import Tracker,report,MAX_WATCH_STOCK,MAX_WATCH_COIN


def install(core):
    import d_live
    import d_upbit

    # Coordinator v043 has already wrapped the source callback. The new
    # listener must retain this chain and NEVER block the strategy tick path.
    native_stock_tick=d_live.LivePaperRunner._new_tick
    def post_sale_stock_tick(self,tick):
        rt=getattr(core,"_RUNTIME",None)
        tracker=getattr(rt,"post_sale_tracker_v053",None) if rt else None
        if tracker:
            try:
                if (isinstance(tick,(tuple,list)) and len(tick)>=8 and
                        str(tick[7]) in ("KRX","NXT")):
                    tracker.quote("stock",str(tick[0]),tick[4],
                                  tick[6],"KRX_TRADE" if tick[7]=="KRX" else "NXT_TRADE")
            except Exception as exc:
                tracker._error="STOCK_FEED:"+type(exc).__name__
        return native_stock_tick(self,tick)
    d_live.LivePaperRunner._new_tick=post_sale_stock_tick

    original_pins=d_live.LivePaperRunner._pinned_stocks
    def tracking_pins(self):
        pins=original_pins(self)
        rt=getattr(core,"_RUNTIME",None)
        tracker=getattr(rt,"post_sale_tracker_v053",None) if rt else None
        if tracker:
            # Live held/pending order subscriptions outrank any research symbol.
            # Leave room under Kiwoom's hard 200-symbol ceiling and never
            # construct market identities when missing from verified maps.
            available=max(0,min(MAX_WATCH_STOCK,190-len(pins)))
            subscribed=0
            for symbol in sorted(tracker.active_symbols("stock")):
                if symbol in pins:continue
                if subscribed>=available:break
                identity=(getattr(self,"market_map",{}) or {}).get(symbol)
                if identity and identity[0] in ("KOSPI","KOSDAQ"):
                    pins[symbol]=identity
                    subscribed+=1
            tracker._last_stock_watch_gap=max(
                0,len(tracker.active_symbols("stock"))-len(set(pins)&tracker.active_symbols("stock")))
        return pins
    d_live.LivePaperRunner._pinned_stocks=tracking_pins

    # Do NOT add research-only symbols to _both_watched_codes(): that
    # method is also used by UPBIT orderbook/buy-sell decisions. For post-sale
    # highs only public TICKER_LAST marks are needed.
    def refresh_research_tickers(rt,tracker):
        runner=getattr(rt,"coin_runner",None)
        if runner is None:return
        with runner._lock:
            before=set(getattr(runner,"tracked",()) or ())
            old=set(getattr(tracker,"_coin_research_only",set()))
            mandatory=before-old
            research=sorted(x for x in tracker.active_symbols("coin")
                            if x.startswith("KRW-") and x not in mandatory)
            room=max(0,MAX_WATCH_COIN-len(mandatory))
            selected=set(research[:room])
            runner.tracked=sorted(mandatory|selected)
            tracker._coin_research_only=selected
            tracker._last_coin_watch_gap=max(0,len(research)-room)

    # Market Trend v043 already wraps latest_prices in _PriceMemory. Its
    # callback sees every valid Upbit public ticker assignment, including
    # research-only tickers (which _dispatch_public_mark otherwise ignores
    # because those coins are not in the trading _held_codes set).
    # Chain this observable, preserving the existing RSI20 callback.
    original_prices=d_upbit.UpbitPaperRunner.latest_prices
    if isinstance(original_prices,property):
        def post_sale_prices_set(runner,values):
            original_prices.fset(runner,values)
            observed=runner.__dict__.get("_market_trend_prices")
            if observed is None:return
            original_observer=getattr(observed,"_callback",None)
            if not callable(original_observer):return
            def both_observers(code,price):
                original_observer(code,price)
                rt=getattr(core,"_RUNTIME",None)
                tracker=getattr(rt,"post_sale_tracker_v053",None) if rt else None
                if tracker:
                    try:
                        import datetime as dt
                        from post_exit_high_v053 import KST
                        tracker.quote("coin",str(code),price,dt.datetime.now(KST),
                                      "UPBIT_PUBLIC_RECEIPT")
                    except Exception as exc:
                        tracker._error="COIN_FEED:"+type(exc).__name__
            observed._callback=both_observers
        d_upbit.UpbitPaperRunner.latest_prices=property(
            original_prices.fget,post_sale_prices_set)

    original_start=core.Runtime.start
    def tracking_start(self):
        outcome=original_start(self)
        try:
            tracker=Tracker(self.root)
            self.post_sale_tracker_v053=tracker
            original_scan=tracker.scan_sources
            def scan_with_ticker_subscription(*a,**kw):
                result=original_scan(*a,**kw)
                refresh_research_tickers(self,tracker)
                return result
            tracker.scan_sources=scan_with_ticker_subscription
            tracker.start()
        except Exception as e:
            # Research errors must not start or stop an unrelated live order.
            self._err("post_sale_tracker_start",e)
        return outcome
    core.Runtime.start=tracking_start

    original_stop=core.Runtime.stop
    def tracking_stop(self):
        tracker=getattr(self,"post_sale_tracker_v053",None)
        if tracker:
            try:tracker.stop()
            except Exception as e:self._err("post_sale_tracker_stop",e)
        return original_stop(self)
    core.Runtime.stop=tracking_stop

    original_hub=core.Runtime.hub_records
    def tracking_hub(self):
        raw=original_hub(self)
        tracker=getattr(self,"post_sale_tracker_v053",None)
        if not tracker:return raw
        try:
            out=json.loads(raw) if isinstance(raw,str) else dict(raw)
            now=time.monotonic()
            cached=getattr(self,"_post_sale_hub_cache_v053",None)
            if cached is None or now-getattr(self,"_post_sale_hub_at_v053",0)>12:
                cached={"stock":tracker.summary("stock",limit=12),
                        "coin":tracker.summary("coin",limit=12)}
                self._post_sale_hub_cache_v053=cached
                self._post_sale_hub_at_v053=now
            out["post_sale_tracking_v053"]=cached
            return json.dumps(out,ensure_ascii=False,default=str)
        except Exception as e:
            tracker._error="HUB:"+type(e).__name__
            return raw
    core.Runtime.hub_records=tracking_hub
