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


def _post_sale_for(con, market, strategy, frame, symbol, pid, epoch):
    # A BUY has no post-sale outcome. A SELL without confirmed time/identity is
    # not sufficient to attach a different episode's observed high.
    if con is None or not pid or epoch is None:
        return {}
    try:
        rows = con.execute('''SELECT source_ref,exit_evidence,exit_ts,peak,peak_pct,
            sample_count,status,cohort FROM cases WHERE market=? AND strategy=?
            AND frame=? AND symbol=? AND exit_evidence='CONFIRMED'
            AND cohort='ACTUAL_FILLED' AND exit_ts BETWEEN ? AND ?
            ORDER BY exit_ts DESC LIMIT ?''',
            (market, strategy, frame, symbol, epoch-120, epoch+120,
             MAX_CASES_PER_SYMBOL))
        candidates = []
        for row in rows:
            # post_exit_sources uses :position:<source-position-id>: with an
            # immutable original entry reference. Do not match substrings.
            if ':position:' + pid + ':' in str(row['source_ref'] or ''):
                candidates.append(row)
        if len(candidates) != 1:
            return {}
        row = candidates[0]
        result = {'post_sale_state': str(row['status'] or ''),
                  'post_sale_samples': int(row['sample_count'] or 0)}
        if row['peak'] is not None and result['post_sale_samples'] > 0:
            peak = float(row['peak'])
            if math.isfinite(peak) and peak > 0:
                result['post_sale_peak'] = peak
        if row['peak_pct'] is not None and result['post_sale_samples'] > 0:
            value = float(row['peak_pct'])
            if math.isfinite(value):
                # Post-exit source defines this against ORIGINAL entry price.
                result['post_sale_peak_pct_from_buy'] = value
        return result
    except (sqlite3.Error, ValueError, TypeError, OverflowError, KeyError):
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
            if side in ('SELL', '매도'):
                output[key].update(_post_sale_for(post, market, strategy, frame,
                                                  symbol, pid, at))
    finally:
        for db in list(opened.values()) + [post]:
            if db is not None:
                db.close()
    return json.dumps(output, ensure_ascii=False, allow_nan=False)
