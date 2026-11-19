"""Coin MA 0.0.18: held-first, low-latency ORIGINAL WS BID seller.

The original UpbitPaperRunner and websocket are unchanged. A small read-only
poller dispatches newly received, fresh ORIGINAL orderbook frames to the
already installed MA v067 native wallet. Neither a 1m candle HIGH nor an ASK
price can create a PAPER SELL. Stock/AT/GMO/CW and coin reference untouched.
"""
from __future__ import annotations
import asyncio
import datetime as dt
import inspect
import threading
import time

_KST=dt.timezone(dt.timedelta(hours=9))
_INSTALLED=False
_ORIGINAL_RUN=None
_TICK=.12

def dispatch_once(runner,seen):
    """One bounded snapshot pass, at most one evaluation per new original book."""
    from coin_held_depth_v014 import book_sell_evidence
    from ma_v067_coin_runtime import _process_v067_exits
    ledger=getattr(runner,"ma_ledger",None)
    if ledger is None:
        return 0
    holding=tuple(str(x) for x in
        getattr(ledger,"_ma_v067_up_hold_symbols",set()) if str(x).startswith("KRW-"))
    if not holding:
        return 0
    owner_lock=getattr(runner,"_lock",None)
    if owner_lock is not None:
        with owner_lock:
            books={symbol:dict(row) for symbol in holding
                   for row in [(getattr(runner,"last_book",{}) or {}).get(symbol)]
                   if isinstance(row,dict)}
    else:
        books={symbol:dict(row) for symbol in holding
               for row in [(getattr(runner,"last_book",{}) or {}).get(symbol)]
               if isinstance(row,dict)}
    current=dt.datetime.now(_KST)
    processed=0
    for symbol,book in books.items():
        if str(book.get("market") or "")!=symbol:
            continue
        status,_=book_sell_evidence(book,current)
        if status!="OK":
            continue
        try:
            # A changed timestamp/depth is a newly available original event.
            # Preserve dedupe despite ticker/book scheduling reorders.
            signature=(int(book["timestamp"]),str(book["bid"]),
                       str(book["bid_size"]),str(book.get("ask") or ""))
            if seen.get(symbol)==signature:
                continue
            seen[symbol]=signature
            # This is the ORIGINAL, same-wallet strategy-specific seller,
            # with its own 3-second BID, depth, stage and SQLite receipt gate.
            _process_v067_exits(ledger,book,current)
            processed+=1
        except (TypeError,ValueError,KeyError,OverflowError):
            continue
    if len(seen)>500:
        for symbol in list(seen):
            if symbol not in holding:
                seen.pop(symbol,None)
    return processed

def _loop(runner,stopped):
    last={}
    native_stop=getattr(runner,"stop_event",None)
    while not stopped.is_set() and not (
            native_stop is not None and native_stop.is_set()):
        began=time.monotonic()
        try:
            dispatch_once(runner,last)
        except Exception as exc:
            # A native websocket/SQLite failure is never treated as a fill.
            # Keep the native feed alive and try again on the next fresh book.
            try:
                runner._v018_ma_exit_error=type(exc).__name__
            except Exception:
                pass
        remaining=_TICK-(time.monotonic()-began)
        stopped.wait(max(.03,remaining))

def _start(runner):
    thread=getattr(runner,"_v018_ma_exit_worker",None)
    if thread is not None and thread.is_alive():
        return
    stopped=threading.Event()
    runner._v018_ma_exit_stop=stopped
    runner._v018_ma_exit_worker=threading.Thread(
        target=_loop,args=(runner,stopped),daemon=True,
        name="ARTITRADE_V018_UPBIT_MA_HELD_EXIT")
    runner._v018_ma_exit_worker.start()

def install():
    global _INSTALLED,_ORIGINAL_RUN
    if _INSTALLED:
        return
    from d_upbit import UpbitPaperRunner
    original=UpbitPaperRunner.run
    _ORIGINAL_RUN=original
    if inspect.iscoroutinefunction(original):
        async def run(self,*args,**kwargs):
            _start(self)
            try:
                return await original(self,*args,**kwargs)
            finally:
                getattr(self,"_v018_ma_exit_stop",threading.Event()).set()
    else:
        def run(self,*args,**kwargs):
            _start(self)
            try:
                return original(self,*args,**kwargs)
            finally:
                getattr(self,"_v018_ma_exit_stop",threading.Event()).set()
    UpbitPaperRunner.run=run
    _INSTALLED=True
