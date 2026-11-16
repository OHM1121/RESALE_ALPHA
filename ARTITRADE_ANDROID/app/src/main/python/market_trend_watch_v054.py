"""v054 RSI20 observation-only watchlist. It never creates orders or virtual fills.

Watch rows are a UI research lifecycle, not broker PENDING. Their appearance
cannot be used to infer an RSI30 confirmed entry or an executed trade.
"""
from __future__ import annotations
import math
import threading
import time


class WatchBook:
    def __init__(self, ttl=45.0):
        self.ttl=float(ttl)
        self._lock=threading.RLock()
        self._rows={}
        self.virtual_signals=0   # Explicitly no trading research side-effects.

    def observe(self,market,symbol,frame,bar_end,rsi,armed,observed_at=None,
                price=None,fresh=True):
        """Consume a verified live RSI value; return its watch disposition."""
        key=(str(market),str(symbol),str(frame))
        try:value=float(rsi)
        except (TypeError,ValueError,OverflowError):value=float('nan')
        seen=time.time() if observed_at is None else float(observed_at)
        with self._lock:
            if not fresh or not math.isfinite(value):
                self._rows.pop(key,None)
                return 'UNAVAILABLE'
            if value>35.0:
                removed=self._rows.pop(key,None)
                return 'REMOVED' if removed is not None else 'INTERNAL'
            prior=self._rows.get(key)
            bar_key=str(bar_end)
            if prior is None or prior['bar_end']!=bar_key:
                prior=None
            try:mark=float(price) if price is not None else None
            except (TypeError,ValueError,OverflowError):mark=None
            self._rows[key]={
                'market':key[0], 'code':key[1], 'frame':key[2],
                'bar_end':bar_key, 'status':'WATCH_PENDING',
                'rsi20_live':round(value,6), 'armed':bool(armed),
                'touched_30':value<=30.0 or bool(prior and prior['touched_30']),
                'observed_at':seen,
                'entered_at':prior['entered_at'] if prior else seen,
                'price':mark,
                'ledger_is_virtual_only':False,
                'external_order_pending':False,
            }
            return 'WATCH_PENDING'

    def discard(self,market,symbol,frame):
        with self._lock:
            self._rows.pop((str(market),str(symbol),str(frame)),None)

    def expire(self,market,frame,bar_end):
        """At or after the evaluation cut-off, discard all watches for a bar."""
        with self._lock:
            for key,row in tuple(self._rows.items()):
                if key[0]==market and key[2]==frame and row['bar_end']<=str(bar_end):
                    del self._rows[key]

    def snapshot(self,market,now=None):
        now=time.time() if now is None else float(now)
        with self._lock:
            for key,row in tuple(self._rows.items()):
                if row['observed_at']>now+5 or now-row['observed_at']>self.ttl:
                    del self._rows[key]
            return sorted((dict(row) for row in self._rows.values()
                           if row['market']==market),
                          key=lambda x:(x['rsi20_live'],x['code'],x['frame']))
