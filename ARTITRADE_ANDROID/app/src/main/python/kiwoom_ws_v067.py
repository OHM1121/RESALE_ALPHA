"""Kiwoom single-socket receive-loop responsiveness without changing orders.

Background universe refresh is scheduled from, rather than awaited IN, the
0B/0D receiver. A single task is allowed per socket; superseded sessions
cancel their old work. No second WebSocket/login or invented quote.
"""
from __future__ import annotations
import asyncio
import threading
import time

_INSTALLED=False


def install():
    global _INSTALLED
    if _INSTALLED:return
    from d_live import LivePaperRunner
    old_refresh=LivePaperRunner._refresh_universe_if_due
    async def nonblocking_refresh(self,ws):
        # The native receive loop invokes this on every one-second timeout.
        # A sync network/rank/DB backlog must never prevent ws.recv/PONG.
        task=getattr(self,'_v067_universe_refresh_task',None)
        owner=getattr(self,'_v067_universe_refresh_socket',None)
        if task is not None and not task.done():
            if owner is ws:
                return
            task.cancel()
        now=time.monotonic()
        from d_storage import now_kst
        hour=now_kst().strftime('%H%M')
        morning='0745'<=hour<'0850'
        interval=60 if morning else 300 if hour>='1600' else 1800
        if now-float(getattr(self,'last_universe_check',0))<interval:
            return
        # The ORIGINAL refresh handles rate limits, venue entitlement,
        # identity, source persistence and the existing subscribed socket.
        async def perform():
            try:
                await old_refresh(self,ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                try:self.store.write_runtime_error(exc,'KIWOOM_V067_UNIVERSE_REFRESH_BACKGROUND')
                except Exception:pass
        new=asyncio.create_task(perform())
        self._v067_universe_refresh_socket=ws
        self._v067_universe_refresh_task=new
        # Avoid orphaned task exception and allow a later retry. Do not
        # promote a still-running task as a second active feed.
        def done(task):
            try:task.result()
            except asyncio.CancelledError:pass
            except Exception as exc:
                try:self.store.write_runtime_error(exc,'KIWOOM_V067_UNIVERSE_REFRESH_RESULT')
                except Exception:pass
        new.add_done_callback(done)
    LivePaperRunner._refresh_universe_if_due=nonblocking_refresh
    _INSTALLED=True
