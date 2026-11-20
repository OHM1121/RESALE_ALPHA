"""Coordinated DNS outage circuit ONLY for historical KRX chart REST.

Never invent candles or quotes. Workers may skip optional history repair
during NameResolutionError, but 0B/0D live subscriptions, trade callbacks
and held-position exits remain independent of this circuit.
"""
from __future__ import annotations
import threading
import time


def is_dns_failure(exc):
    name=type(exc).__name__.lower()
    detail=(name+" "+str(exc)).lower()
    return any(fragment in detail for fragment in (
        "nameresolutionerror", "gaierror", "getaddrinfo failed",
        "no address associated with hostname", "temporary failure in name resolution",
        "name or service not known", "dns_lookup_failed",
        "failed to resolve"))


class HistoryChartCooldown:
    def __init__(self,clock=time.monotonic):
        self._clock=clock
        self._lock=threading.Lock()
        self._until=0.0
        self._consecutive=0
        self._last_reason=""

    def allow(self):
        with self._lock:
            remaining=self._until-self._clock()
            return (remaining<=0,max(0.0,remaining))

    def failure(self,error):
        if not is_dns_failure(error):
            return 0.0
        with self._lock:
            self._consecutive=min(10,self._consecutive+1)
            backoff=min(300.0,20.0*2**(self._consecutive-1))
            self._until=max(self._until,self._clock()+backoff)
            self._last_reason="DNS_UNAVAILABLE_NO_OFFICIAL_CANDLE"
            return self._until-self._clock()

    def success(self):
        with self._lock:
            self._until=0.0
            self._consecutive=0
            self._last_reason=""

    def snapshot(self):
        with self._lock:
            return {"status":"DNS_RETRY_DEFERRED" if self._until>self._clock()
                    else "REST_ALLOWED",
                    "retry_in_sec":max(0.0,self._until-self._clock()),
                    "failures":self._consecutive,
                    "reason":self._last_reason}


_SHARED=HistoryChartCooldown()


def shared_circuit():
    return _SHARED
