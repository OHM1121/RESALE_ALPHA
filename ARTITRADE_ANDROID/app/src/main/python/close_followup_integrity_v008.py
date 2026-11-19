"""CLOSE research-only future-horizon price integrity (v008).

The original CLOSE candidate/BUY/SELL engine is authoritative and unchanged.
A due followup is not a fill and must not label stale last quotes or yesterday's
daily close as the observation at its requested research horizon.
"""
from __future__ import annotations
from datetime import datetime,timedelta
import json,math
from at_close_store import KST,now_kst

_INSTALLED=False
MAX_LIVE_AGE_SEC=3
MAX_HORIZON_DISTANCE_SEC=90
MAX_SAVED_3M_BAR_DISTANCE_SEC=360


def _timestamp(value):
    if isinstance(value,datetime):
        stamp=value
    else:
        raw=str(value or '').strip()
        try:
            stamp=datetime.fromisoformat(raw)
        except (ValueError,TypeError):
            digits=''.join(x for x in raw if x.isdigit())
            if len(digits)<12:return None
            try:stamp=datetime.strptime(digits[:14].ljust(14,'0'),'%Y%m%d%H%M%S')
            except ValueError:return None
    if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=KST)
    return stamp.astimezone(KST)


def _positive_price(value):
    try:
        p=float(value)
    except (TypeError,ValueError,OverflowError):return None
    if not math.isfinite(p) or p<=0:return None
    return int(p) if p.is_integer() else None


def asof_price(live,saved,due,now):
    """Return (verified price, source, source timestamp), or None.

    This function does not write to SQLite or read broker order state.
    """
    due=_timestamp(due)
    now=_timestamp(now)
    if due is None or now is None:return None
    if isinstance(live,dict):
        price=_positive_price(live.get('price'))
        ts=_timestamp(live.get('ts'))
        if (price is not None and ts is not None
            and 0<=(now-ts).total_seconds()<=MAX_LIVE_AGE_SEC
            and abs((ts-due).total_seconds())<=MAX_HORIZON_DISTANCE_SEC):
            return price,'LIVE_VERIFIED_AT_DUE',ts.isoformat(timespec='seconds')
    if isinstance(saved,dict):
        # An end-of-day 'RESEARCH_DAY' record has only a DATE. It cannot
        # prove the 15:15 requested due time or any shorter 5/15/30m horizon.
        if saved.get('source')!='RESEARCH_3M':return None
        price=_positive_price(saved.get('price'))
        bar=_timestamp(saved.get('bar_ts'))
        # KRX 3-minute bar_ts is the bar START. Its close price is
        # not observed/confirmed until bar_start + 3m. A 15:15 candle
        # must NOT be labeled the 15:15 research price before 15:18.
        bar_end=bar+timedelta(minutes=3) if bar is not None else None
        if (price is not None and bar_end is not None
            and bar.date()==due.date()
            and 0<=(due-bar_end).total_seconds()<=MAX_SAVED_3M_BAR_DISTANCE_SEC):
            return price,'RESEARCH_3M_VERIFIED_ASOF',bar_end.isoformat(timespec='seconds')
    return None


def install():
    global _INSTALLED
    if _INSTALLED:return
    from at_close_runner import AtCloseRunner
    original=AtCloseRunner._process_followups
    if getattr(original,'_v008_verified_followups',False):
        _INSTALLED=True;return

    def process_verified_followups(self):
        now=now_kst()
        with self.store.lock,self.store.connect() as con:
            due_rows=con.execute(
                "SELECT * FROM research_followups WHERE status='PENDING' "
                "AND due_ts<=? ORDER BY due_ts LIMIT 100",
                (now.isoformat(timespec='seconds'),)).fetchall()
            for row in due_rows:
                due=_timestamp(row['due_ts'])
                if due is None or int(row['base_price'])<=0:continue
                latest=self.latest.get(str(row['code']))
                saved=self.store.followup_price(row['code'],row['due_ts'])
                evidence=asof_price(latest,saved,due,now)
                if evidence is None:
                    # An unavailable old observation stays PENDING. We do
                    # NOT re-label a later live quote as the historic due.
                    continue
                price,source,source_time=evidence
                ret=(price/int(row['base_price'])-1)*100
                ext=self.store.followup_extremes(row['code'],row['base_ts'],
                                                  row['due_ts'],row['base_price']) or {}
                outcome={
                    'return_pct':ret,'horizon':row['horizon'],
                    'decision_source':'signal' if row['source_signal_id'] else 'candidate',
                    'observation_source':source,'observation_bar_ts':source_time,
                    'processing_delay_sec':round((now-due).total_seconds(),3),
                    'verified_due_ts':due.isoformat(timespec='seconds'),
                    'touches':ext.get('touches') or {}
                }
                con.execute(
                    "UPDATE research_followups SET observed_ts=?,observed_price=?,"
                    "return_pct=?,max_price=?,min_price=?,mfe_pct=?,mae_pct=?,"
                    "bars_observed=?,outcome_json=?,status='DONE' WHERE id=? AND status='PENDING'",
                    (now.isoformat(timespec='seconds'),price,ret,ext.get('max_price'),
                     ext.get('min_price'),ext.get('mfe_pct'),ext.get('mae_pct'),
                     ext.get('bars_observed'),
                     json.dumps(outcome,ensure_ascii=False,separators=(',',':')),
                     row['id']))
    process_verified_followups._v008_verified_followups=True
    process_verified_followups._v008_original=original
    AtCloseRunner._process_followups=process_verified_followups
    _INSTALLED=True
