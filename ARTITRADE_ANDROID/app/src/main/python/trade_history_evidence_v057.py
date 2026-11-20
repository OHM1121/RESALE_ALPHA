"""Strict, read-only per-trade AI/post-sale lookup for Android history tables.

This module never runs on the broker/order thread, never writes a source DB and
never guesses a post-exit high for a different position in the same symbol.
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from contextlib import closing

LIMIT_BATCH = 60
MAX_CASES_PER_SYMBOL = 32


def _utc_second(value):
    try:
        if value is None or isinstance(value, bool):
            return None
        x = float(value)
        if not math.isfinite(x) or x < 1000000000:
            return None
        return int(x / 1000) if x > 1e12 else int(x)
    except (ValueError, TypeError, OverflowError):
        return None


def _read_only(path):
    if not path.is_file():
        return None
    # URI mode=ro is essential: SQLite never creates a missing DB as a side effect.
    con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    return con


def _ai_for(con, scope, frame, symbol, pid):
    if con is None or not pid:
        return {}
    try:
        rows = con.execute('''SELECT execution_mode,kind,realized_roi,case_json
            FROM trade_cases WHERE scope=? AND frame=? AND symbol=?
            ORDER BY updated_at DESC LIMIT ?''',
            (scope, frame, symbol, MAX_CASES_PER_SYMBOL))
        for row in rows:
            if str(row['kind']).upper() != 'FILLED':
                continue
            details = json.loads(row['case_json'] or '{}')
            if not isinstance(details, dict) or str(details.get('source_id') or '') != pid:
                continue
            features = details.get('features')
            sources = details.get('sources')
            result = {'ai_source_count': len(sources) if isinstance(sources, list) else 0,
                      'ai_factor_count': len(features) if isinstance(features, list) else 0,
                      'ai_execution_mode': str(row['execution_mode'] or '미기록')}
            roi = row['realized_roi']
            if roi is not None:
                try:
                    val = float(roi)
                    if math.isfinite(val):
                        result['ai_realized_roi'] = val
                except (ValueError, TypeError, OverflowError):
                    pass
            return result
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        return {}
    return {}



def _entry_signal_for(con, market, strategy, frame, symbol, pid):
    """Link a real execution to its original SAME position and candidate.

    The native Upbit MA BUY fill stores a generic execution reason, while its
    actual MA turnpoint reason and numerical inputs live in the original
    candidates.features JSON. Neither this function nor the UI re-runs MA.
    """
    if con is None or not pid or strategy != 'ma' or market != 'coin':
        return {}
    try:
        row = con.execute('''SELECT tf,market,ref_at FROM positions
            WHERE id=? AND tf=? AND market=? LIMIT 1''',
            (pid, frame, symbol)).fetchone()
        if row is None:
            return {}
        candidate = con.execute('''SELECT reason,features FROM candidates
            WHERE tf=? AND market=? AND ref_at=? LIMIT 1''',
            (frame, symbol, row['ref_at'])).fetchone()
        if candidate is None:
            return {'entry_signal_state':'CANDIDATE_NOT_LINKED_TO_BUY'}
        try:
            features=json.loads(candidate['features'] or '{}')
        except (ValueError,TypeError):
            features={}
        if not isinstance(features,dict):
            features={}
        result={'entry_signal_state':'ORIGINAL_CANDIDATE_MATCHED'}
        reason=str(candidate['reason'] or '').strip()
        if reason:
            result['candidate_reason']=reason[:240]
        kind=str(features.get('ma_v067_kind') or
                 features.get('ma_v061_kind') or '').strip()
        if kind:
            result['entry_signal_kind']=kind[:64]
        if kind=='REVERSAL_UP':
            result['entry_signal_reason'] = (
                'MA 상승전환 매수 · 단기 이동평균선 4개 상승 / '
                'MA120 기울기 허용기준 확인')
        elif reason:
            result['entry_signal_reason']=reason[:240]
        for source_key,result_key in (
            ('source','entry_signal_source'),
            ('ma_v067_bar_start','entry_signal_bar_start'),
            ('ma_v067_120_delta','entry_signal_ma120_delta'),
            ('ma_v061_first_target_pct','entry_signal_first_target_pct'),
            ('ma_v061_kind','entry_signal_ma_kind')):
            value=features.get(source_key)
            if value is not None and str(value).strip():
                result[result_key]=str(value)[:128]
        return result
    except (sqlite3.Error,ValueError,TypeError,OverflowError,KeyError):
        return {'entry_signal_state':'ORIGINAL_ENTRY_LOOKUP_UNAVAILABLE'}


def _post_sale_for(con, market, strategy, frame, symbol, pid, epoch,
                   execution_id=''):
    # A BUY has no post-sale episode. SELL must match its actual recorded
    # original position and exact episode, not a same-code neighboring fill.
    if con is None or not pid or epoch is None:
        return {}
    try:
        rows = con.execute('''SELECT source_ref,exit_evidence,exit_ts,peak,peak_pct,
            sample_count,status,cohort FROM cases WHERE market=? AND strategy=?
            AND frame=? AND symbol=? AND exit_evidence='CONFIRMED'
            AND cohort='ACTUAL_FILLED' AND exit_ts BETWEEN ? AND ?
            ORDER BY exit_ts DESC LIMIT ?''',
            (market,strategy,frame,symbol,epoch-120,epoch+120,
             MAX_CASES_PER_SYMBOL))
        cases=list(rows)
        # A partial SELL case includes the immutable execution row ID, whereas
        # the legacy final-close case names the immutable position and entry.
        # New partial cases may have the same timestamp as a final close:
        # choose ONLY the matching execution episode, never merge peaks.
        partial_tag=':partialfill:'+pid+':'+str(execution_id)
        partial=([row for row in cases if execution_id and
                  str(row['source_ref'] or '').endswith(partial_tag)]
                 if execution_id else [])
        if len(partial)>1:
            return {}
        if len(partial)==1:
            matched=partial[0]
            anchor_type='PARTIAL_SELL'
        else:
            final=[row for row in cases if
                   ':position:'+pid+':' in str(row['source_ref'] or '')]
            if len(final)!=1:
                return {}
            matched=final[0]
            anchor_type='FINAL_SELL'
        samples=int(matched['sample_count'] or 0)
        result={'post_sale_state':str(matched['status'] or ''),
                'post_sale_samples':samples,
                'post_sale_anchor_kind':anchor_type}
        if matched['peak'] is not None and samples>0:
            peak=float(matched['peak'])
            if math.isfinite(peak) and peak>0:
                result['post_sale_peak']=peak
        if matched['peak_pct'] is not None and samples>0:
            value=float(matched['peak_pct'])
            if math.isfinite(value):
                result['post_sale_peak_pct_from_buy']=value
        return result
    except (sqlite3.Error,ValueError,TypeError,OverflowError,KeyError):
        return {}


def lookup_batch(root: str, requests_json: str) -> str:
    """Return {opaque_ui_row_key: confirmed facts} only, no synthesized fills.

    Requests: [{key, market, strategy, frame, symbol, position_id, side, at}].
    The caller may render a compact '미기록' when the dictionary is empty.
    """
    try:
        requests = json.loads(requests_json)
    except (ValueError, TypeError):
        requests = []
    if not isinstance(requests, list):
        requests = []
    base = Path(root)
    output = {}
    opened = {}
    original = {}
    post = None
    try:
        try:
            post = _read_only(base / 'POST_EXIT_HIGH_V053.sqlite3')
        except (sqlite3.Error, OSError):
            post = None
        for item in requests[:LIMIT_BATCH]:
            if not isinstance(item, dict):
                continue
            key = str(item.get('key') or '')[:256]
            if not key:
                continue
            output[key] = {}
            market = str(item.get('market') or '').lower()
            strategy = str(item.get('strategy') or '').lower()
            frame = str(item.get('frame') or '').lower()
            symbol = str(item.get('symbol') or '')
            pid = str(item.get('position_id') or '')
            execution_id = str(item.get('execution_id') or '')
            side = str(item.get('side') or '').upper()
            at = _utc_second(item.get('at'))
            if market not in ('stock', 'coin') or strategy not in (
                'at', 'ma', 'general', 'trend', 'close', 'gmo'):
                continue
            if frame not in ('5m', '30m', '60m', 'day', 'gmo', 'close') or not symbol or not pid:
                continue
            path_key = market + '/' + strategy
            if path_key not in opened:
                try:
                    opened[path_key] = _read_only(base / market / 'ai' /
                        'AI_COMPREHENSIVE_V052.sqlite3')
                except (sqlite3.Error, OSError):
                    opened[path_key] = None
            output[key].update(_ai_for(opened[path_key], path_key, frame, symbol, pid))
            if (market,strategy)==('coin','ma') and side in ('BUY','매수'):
                # Only explicit user-opened rows access the original native
                # MA candidate DB. No HTTP, REST, ticker or extra WS here.
                path='coin/ma/upbit_paper.sqlite3'
                if path not in original:
                    try:original[path]=_read_only(base/path)
                    except (sqlite3.Error,OSError):original[path]=None
                output[key].update(_entry_signal_for(
                    original[path],market,strategy,frame,symbol,pid))
            # Original source candidate / executed fill, NOT a generic
            # exchange best-ask price provenance. Joins by position + exact
            # BUY/SELL execution and actual fill time, read-only.
            if strategy in ('ma','trend') and side in ('BUY','SELL'):
                try:
                    from trade_decision_basis_v095 import from_original
                    output[key].update(from_original(base,market,strategy,frame,
                        symbol,pid,side,execution_id,at=at))
                except (ImportError,sqlite3.Error,ValueError,TypeError,KeyError):
                    pass
            if side in ('SELL', '매도'):
                output[key].update(_post_sale_for(post, market, strategy, frame,
                                                 symbol, pid, at, execution_id))
    finally:
        for db in list(opened.values()) + list(original.values()) + [post]:
            if db is not None:
                db.close()
    return json.dumps(output, ensure_ascii=False, allow_nan=False)
