"""v094 legacy MA original-wallet SELL-only watcher.

Earlier MA stock positions were left in the v062 SQLite epoch when v067
started a fresh wallet. This monitor NEVER transfers holdings, copies fills,
creates candidates, submits BUYs or changes user's exit thresholds. It uses
the original v061 1st/2nd/3rd exit policy if its sidecar exists, otherwise
original unmodified native MA exit; writes SELL directly to the same old DB.

No broker live sale may be misrepresented as PAPER: preserve live journal
hard-stop and original KRX 0D reservation and 0B event freshness.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import closing
import threading
import time
from pathlib import Path

_INSTALLED=False
_EXIT_LOCK=threading.RLock()
KST=dt.timezone(dt.timedelta(hours=9))


def _old_stock_paths(root):
    root=Path(root)
    if root.name=='stock':root=root.parent
    return (root/'stock/MA_EPOCH_V062/STOCK_MA_ENGINE_V1/ma_paper.sqlite3',
            root/'stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3')


def _old_coin_paths(root):
    root=Path(root)
    return (root/'coin/ma_epoch_v062/upbit_paper.sqlite3',
            root/'coin/ma/upbit_paper.sqlite3')


def _read_old_stock(root):
    """Open *only existing* SQLite files, without creation or schema migration."""
    from stock_ma_engine import _connect
    rows={}
    for path in _old_stock_paths(root):
        if not path.is_file():
            continue
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',
                                         uri=True,timeout=2)) as c:
                c.row_factory=sqlite3.Row
                for row in c.execute("""SELECT p.code,p.name,COALESCE(x.market,'') market
                    FROM positions p LEFT JOIN candidates x ON x.id=p.candidate_id
                    WHERE p.status='OPEN' AND p.qty>0"""):
                    if len(row['code'])==6 and str(row['code']).isdigit():
                        rows[str(row['code'])]=(str(row['market']),str(row['name']))
        except (sqlite3.Error,OSError):
            continue
    return rows


def legacy_stock_codes(root):
    return set(_read_old_stock(root))


def _legacy_one_stock(root,market_store,tick,book,db):
    from stock_ma_engine import StockMAService,_connect
    from ma_v061_stock_runtime import _ma_exit
    from android_live_position_guard_v049 import refuse_paper_sell_if_live_open
    code=str(tick[0])
    try:
        when=dt.datetime.fromisoformat(str(tick[6]))
    except (TypeError,ValueError):
        return 0
    if when.tzinfo is None or abs((dt.datetime.now(KST)-when).total_seconds())>3:
        return 0
    with book._lock:
        record=book._items.get((code,'KRX'))
        bid_record=book._bid_depth.get((code,'KRX'))
    if not record or not bid_record:return 0
    ask,bid,asksize,quote_at=record
    px,qty,bid_at=bid_record
    if (not 0<bid<=ask or px!=bid or qty<=0 or
        not -.5<=(when-quote_at).total_seconds()<=3 or
        not -.5<=(when-bid_at).total_seconds()<=3):
        return 0

    # Explicitly disallow transforming a physical broker holding into a
    # simulated sale if a previous broker order is unresolved/filled.
    refuse_paper_sell_if_live_open(root,'stock',code)

    class LegacyOwner:
        _sell=StockMAService._sell
        def __init__(self):
            self.db=db
            self.root=db.parent
            self.market_store=market_store
            self.lock=_EXIT_LOCK
    owner=LegacyOwner()
    done=0
    with _EXIT_LOCK,_connect(db) as c:
        positions=c.execute("""SELECT * FROM positions WHERE code=? AND
            status='OPEN' AND qty>0 ORDER BY id""",(code,)).fetchall()
        for p in positions:
            old=c.execute("""SELECT 1 FROM executions WHERE position_id=? AND
                 side='SELL' AND ts=? LIMIT 1""",(p['id'],when.isoformat())).fetchone()
            if old:continue
            before=c.execute("SELECT COUNT(*) FROM executions WHERE position_id=? AND side='SELL'",
                             (p['id'],)).fetchone()[0]
            # Delegate exactly to older class' original exit decision; it
            # falls back to native StockMAService._exit if no old sidecar.
            _ma_exit(owner,c,p,int(bid),when,book,StockMAService._exit)
            fresh=c.execute("SELECT COALESCE(SUM(qty),0) FROM executions WHERE position_id=? AND side='SELL'",
                            (p['id'],)).fetchone()[0]
            if fresh and c.execute("SELECT COUNT(*) FROM executions WHERE position_id=? AND side='SELL'",
                                   (p['id'],)).fetchone()[0]>before:
                done+=int(c.execute("SELECT qty FROM executions WHERE position_id=? AND side='SELL' ORDER BY id DESC LIMIT 1",
                                    (p['id'],)).fetchone()[0])
    return done


def legacy_stock_exit(root,market_store,tick,book):
    if not tick or len(tick)<8 or str(tick[7])!='KRX':
        return 0
    code=str(tick[0])
    if code not in legacy_stock_codes(root):
        return 0
    total=0
    for path in _old_stock_paths(root):
        if not path.is_file():continue
        total+=_legacy_one_stock(root,market_store,tick,book,path)
    return total


def install(core):
    global _INSTALLED
    if _INSTALLED:return
    import stock_ma_engine
    original_pinned=stock_ma_engine.StockMAService.pinned_stocks
    original_watched=stock_ma_engine.StockMAService.watched_codes
    original_tick=stock_ma_engine.StockMAService.on_tick

    def watched(self):
        """Include preserved V061/V062 open holdings in the real tick dispatcher.

        d_live gates StockMAService.on_tick behind watched_codes(), not
        pinned_stocks(). Previously old MA holdings were subscribed but never
        reached legacy_stock_exit, even with valid KRX 0B/0D and a reached TP1.
        Cache historical SQLite lookups to avoid per-tick DB IO on the 200-slot
        shared Kiwoom feed. Never create or migrate a historical position.
        """
        current=set(original_watched(self))
        stamp=time.monotonic()
        if (not hasattr(self,'_v094_legacy_watch_codes') or
                stamp-getattr(self,'_v094_legacy_watch_at',0.0)>=2.0):
            try:
                previous=frozenset(legacy_stock_codes(self.market_store.root))
                self._v094_legacy_watch_codes=previous
                self._v094_legacy_watch_at=stamp
            except (OSError,AttributeError,sqlite3.Error) as exc:
                # Keep last verified open-owner list on a transient file error.
                # No new BUY is possible in the historical SELL-only watcher.
                self._v094_legacy_watch_error=type(exc).__name__+':'+str(exc)[:130]
        return current|set(getattr(self,'_v094_legacy_watch_codes',()))


    def pinned(self):
        entries=dict(original_pinned(self))
        try:
            root=Path(self.market_store.root)
            for code,identity in _read_old_stock(root).items():
                if identity[0] in ('KOSPI','KOSDAQ'):
                    entries.setdefault(code,identity)
        except (OSError,AttributeError,sqlite3.Error):
            pass
        return entries

    def tick(self,data,book):
        result=original_tick(self,data,book)
        if len(data)<8 or data[7]!='KRX':
            return result
        try:
            root=Path(self.market_store.root)
            # No historical network, REST price, or other strategy event can
            # count as an actual quote. This is the same live KRX 0B event
            # already delivered to native MA and its verified shared 0D book.
            legacy_stock_exit(root,self.market_store,data,book)
        except Exception as exc:
            # Do not crash NEW MA on legacy SQLite error; leave it explicit.
            try:self._v094_legacy_exit_error=type(exc).__name__+':'+str(exc)[:130]
            except Exception:pass
        return result

    stock_ma_engine.StockMAService.pinned_stocks=pinned
    stock_ma_engine.StockMAService.watched_codes=watched
    stock_ma_engine.StockMAService.on_tick=tick
    _INSTALLED=True
