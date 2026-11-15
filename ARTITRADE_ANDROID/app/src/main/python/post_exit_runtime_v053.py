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

    native_coin_watch=d_upbit.UpbitPaperRunner._both_watched_codes
    def tracking_coin_watch(self):
        original=set(native_coin_watch(self))
        rt=getattr(core,"_RUNTIME",None)
        tracker=getattr(rt,"post_sale_tracker_v053",None) if rt else None
        if tracker:
            available=max(0,MAX_WATCH_COIN-len(original))
            tracked=sorted(x for x in tracker.active_symbols("coin")
                           if x.startswith("KRW-") and x not in original)
            original.update(tracked[:available])
            tracker._last_coin_watch_gap=max(0,len(tracked)-available)
        return sorted(original)
    d_upbit.UpbitPaperRunner._both_watched_codes=tracking_coin_watch

    native_coin_mark=d_upbit.UpbitPaperRunner._dispatch_public_mark
    def post_sale_coin_mark(self,code,price,seen=None):
        try:
            return native_coin_mark(self,code,price,seen)
        finally:
            rt=getattr(core,"_RUNTIME",None)
            tracker=getattr(rt,"post_sale_tracker_v053",None) if rt else None
            if tracker:
                try:
                    from post_exit_high_v053 import KST
                    import datetime as dt
                    stamp=seen or dt.datetime.now(KST)
                    tracker.quote("coin",str(code),price,stamp,"UPBIT_PUBLIC_LAST")
                except Exception as e:
                    tracker._error="COIN_FEED:"+type(e).__name__
    d_upbit.UpbitPaperRunner._dispatch_public_mark=post_sale_coin_mark

    original_start=core.Runtime.start
    def tracking_start(self):
        outcome=original_start(self)
        try:
            tracker=Tracker(self.root)
            self.post_sale_tracker_v053=tracker
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
