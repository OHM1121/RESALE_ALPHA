"""v093: fair one-socket Kiwoom subscriptions for five active stock strategies.

User priority: 1 stock supply/demand (수급매매), 2 close-betting (종가베팅),
3 MA, 4 market trend, 5 GMO reference candle. These are priority weights,
not strategy kill switches. Existing held/pending positions are always selected
first (up to the physical 200-slot single-WS cap); excess research candidates
take turns every 180 seconds. No quote/order/fill is synthesized.
"""
from __future__ import annotations

import os
import time

_INSTALLED = False
STRATEGY_NAMES = ("SUPPLY", "CLOSE", "MA", "TREND", "GMO")
STRATEGY_WEIGHTS = (5, 4, 3, 2, 1)
KIWOOM_MAX_SUBSCRIPTIONS = 200
OVERFLOW_ROTATION_SECONDS = 180


#: Band 0 is the WHOLE-MARKET scan list (every valid KRX symbol). Bands 1..4
#: are strategy-specific candidate lists, which are always subsets of it.
UNIVERSE_BAND = 0


def allocate_priority(bands, protected=(), capacity=KIWOOM_MAX_SUBSCRIPTIONS, phase=0):
    """Return unique, bounded broker symbols; no permanent discard of overflow.

    Protected means existing holdings / orders needing monitored exits, not a
    substitute for an executable fresh orderbook. The hard limit still applies.
    Over-cap candidates cycle in real broker REG subscriptions; unsubscribed
    names remain NOT LIVE.

    De-duplication order matters. The whole-market band contains EVERY symbol,
    so when it claimed its symbols first (as before 0.0.20) the CLOSE / MA /
    TREND / GMO candidate bands became EMPTY after de-duplication, their
    weights never applied, and their candidates were live only while the
    rotating whole-market slice happened to include them (about 40 % of the
    time). Now the strategy-specific candidate lists claim their symbols
    FIRST; the whole-market band keeps its weighted floor (5/15 of the free
    room) and receives whatever the candidate bands do not need. Output order
    (band order, anchors first, then the rotating tail) is unchanged.
    """
    if len(bands) != len(STRATEGY_WEIGHTS):
        raise ValueError("EXPECTED_FIVE_STRATEGY_BANDS")
    limit = max(0, min(int(capacity), KIWOOM_MAX_SUBSCRIPTIONS))
    if limit == 0:
        return []
    chosen = []
    assigned = set()
    for code in protected:
        code = str(code)
        if code and code not in assigned:
            chosen.append(code)
            assigned.add(code)
            if len(chosen) >= limit:
                return chosen

    groups = [[] for _ in bands]
    claim_order = [i for i in range(len(bands)) if i != UNIVERSE_BAND] + [UNIVERSE_BAND]
    for i in claim_order:
        for code in bands[i]:
            code = str(code)
            if code and code not in assigned:
                assigned.add(code)
                groups[i].append(code)

    room = limit - len(chosen)
    total_weight = sum(STRATEGY_WEIGHTS)
    quotas = [0] * len(groups)
    # The whole-market scan keeps its weighted share of the free room ...
    floor = min(len(groups[UNIVERSE_BAND]),
                room * STRATEGY_WEIGHTS[UNIVERSE_BAND] // total_weight)
    quotas[UNIVERSE_BAND] = floor
    # ... and the candidate bands share the rest by weight (4:3:2:1).
    specific_room = room - floor
    while specific_room > 0:
        progressed = False
        for i, weight in enumerate(STRATEGY_WEIGHTS):
            if i == UNIVERSE_BAND:
                continue
            take = min(weight, specific_room, len(groups[i]) - quotas[i])
            if take > 0:
                quotas[i] += take
                specific_room -= take
                progressed = True
            if specific_room <= 0:
                break
        if not progressed:
            break
    # Capacity the candidate bands do not need flows to the whole-market scan.
    quotas[UNIVERSE_BAND] = min(len(groups[UNIVERSE_BAND]),
                                floor + specific_room)

    for group, amount in zip(groups, quotas):
        if amount <= 0:
            continue
        # Keep the most highly ranked few in each band connected, rotating the
        # remainder by actual slot count. This prevents permanent starvation.
        anchor = min(12, amount // 3)
        chosen.extend(group[:anchor])
        remainder = amount - anchor
        tail = group[anchor:]
        if remainder > 0 and tail:
            shift = (max(0, int(phase)) * remainder) % len(tail)
            ring = tail[shift:] + tail[:shift]
            chosen.extend(ring[:remainder])
    return chosen


#: 종가베팅 (close-betting) scan window. AtCloseRunner pins its 80-symbol universe
#: (top 40 KOSPI + 40 KOSDAQ) from 14:50, scans from 15:00 and may buy 15:10-15:20.
CLOSE_WINDOW_START = "1450"
CLOSE_WINDOW_END = "1520"
CLOSE_WINDOW_MAX = 100


def close_window_codes(hhmm, close_band, already=(), cap=CLOSE_WINDOW_MAX):
    """0.1.0: the whole close-betting universe is connected during its window.

    The 2026-11-20 error history (0.0.18) shows 70 of 90 close-betting
    evaluations rejected with NO_FRESH_ORDERBOOK (book age 3-300 s) and 157
    scan skips for missing/stale trades, including candidates whose only
    blocker was the stale book: the 80 scanned symbols were connected only in
    rotation, so most of them had no live quote when evaluated. The strategy
    needs a fresh book AND trade for every scanned symbol at the same time,
    so inside the window its universe is taken like holdings (never rotated
    out). Outside the window the unchanged weighted allocation applies. No
    quote is invented; a symbol still needs a real fresh message to qualify.
    """
    if not (CLOSE_WINDOW_START <= str(hhmm) <= CLOSE_WINDOW_END):
        return []
    seen = set(str(code) for code in already)
    result = []
    for code in close_band:
        code = str(code)
        if code and code not in seen:
            result.append(code)
            seen.add(code)
        if len(result) >= cap:
            break
    return result


def critical_priority_codes(supply_active=(), close_active=(),
                            ma_active=(), trend_active=(), gmo_held=()):
    """Protect actual held/pending subscriptions, not ordinary GMO candidates.

    Within the critical lane use the user-specified SR > CW > MA > MT > GMO
    ordering. One symbol shared by several strategies uses only one slot.
    """
    result = []
    seen = set()
    for band in (supply_active, close_active, ma_active, trend_active, gmo_held):
        for symbol in band:
            code = str(symbol)
            if code and code not in seen:
                result.append(code)
                seen.add(code)
    return result


def install(core):
    """Install a subscription-only wrapper; never change the native broker."""
    global _INSTALLED
    if _INSTALLED:
        return
    import d_live

    native = d_live.LivePaperRunner._desired_subscriptions

    def prioritized(self, at=None):
        now = at or d_live.now_kst()
        hhmm = now.strftime("%H%M")
        gmo_priority = list(self.gmo.priority_watchlist(now))

        # Normal mornings keep the established native GMO preopen contract.
        # On the 2026 CSAT day, NXT premarket is CLOSED and KRX is at 10:00.
        # Do not allow native 08:30..09:00 selection to pose as live venue.
        from krx_session_v041 import SPECIAL_SESSIONS, START, END, is_regular_open
        from gmo_d1_preplan_v097 import NXT_PREMARKET_CLOSED
        day=now.date().isoformat()
        special=day in SPECIAL_SESSIONS
        regular_start=SPECIAL_SESSIONS.get(day,(START,END))[0]
        minute=now.hour*60+now.minute
        preopen=(gmo_priority and regular_start-30<=minute<regular_start)
        if gmo_priority and not special and "0830" <= hhmm < "0900":
            return native(self, at)

        if day in NXT_PREMARKET_CLOSED and minute<regular_start:
            venue="KRX" if preopen else None
        else:
            venue=(
                "NXT" if d_live.live_session(now, "NXT")
                else "KRX" if d_live.live_session(now, "KRX")
                else "KRX" if is_regular_open(now)
                else None
            )
        if not venue:return []
        pinned = self._pinned_stocks()
        base = (self.nxt_market_map or self.krx_market_map or self.market_map
                if venue == "NXT" else self.krx_market_map or self.market_map)
        if not base and not pinned:
            return []

        # Retain the original research evidence scanner's verified-ranked
        # symbols; ranking changes only the shared Kiwoom quote subscriptions.
        if venue == "KRX":
            ordered_e = list(gmo_priority) + list(pinned) + list(base)
            self.e_live.current_symbols = [
                str(code) for code in ordered_e if str(code) in self.market_map
            ][:int(self.e_live.config["max_candidates_per_minute"])]
        else:
            self.e_live.current_symbols = []

        close = []
        if os.environ.get("ARTITRADE_AT_CLOSE_COMBINED") == "1":
            try:
                from at_close_bridge import close_pinned
                close = list(close_pinned())
            except Exception:
                close = []

        ma_codes = []
        ma_active = []
        try:
            ma_active = list(self.ma.pinned_stocks())
            ma_codes = ma_active + list(self.ma.watched_codes())
        except Exception:
            # Existing pinned holdings are retained even if a stale research
            # scope briefly fails; do not reconnect the WS on that failure.
            pass

        trend_codes = []
        trend_active = []
        try:
            runtime = getattr(core, "_RUNTIME", None)
            trend = getattr(runtime, "market_trend", None) if runtime else None
            if trend is not None:
                with trend.lock:
                    # 0.0.20: stock Trend candidates are the whole AT universe,
                    # which the whole-market band already carries.
                    trend_codes = ([] if getattr(trend, "_v020_stock_universe_candidates", False)
                                   else list(trend._candidates.get("stock", {})))
                    trend_active = [
                        str(position.symbol)
                        for position in tuple(trend._positions.values())
                        if position.market == "stock" and position.qty > 0
                    ]
                    trend_codes.extend(trend_active)
        except Exception:
            pass

        gmo_held = []
        try:
            gmo_held = list(self.gmo.holding_codes())
            gmo_codes = gmo_priority + list(self.gmo.watchlist()) + gmo_held
        except Exception:
            gmo_codes = gmo_priority

        # _pinned_stocks() includes ordinary GMO D+1 BUY candidates. Those
        # must remain eligible as FIFTH priority, not silently leapfrog SR
        # and CW as first-priority "held" symbols. Read actual SR open/pending
        # orders from their existing SQLite evidence; never fabricate one.
        supply_active = []
        try:
            with self.store.connect() as db:
                supply_active = [row["code"] for row in db.execute(
                    "SELECT DISTINCT code FROM positions WHERE status='OPEN' AND qty>0")]
                supply_active.extend(row["code"] for row in db.execute(
                    "SELECT DISTINCT code FROM v19_orders WHERE status='PENDING' "
                    "AND venue IN ('KRX','NXT')"))
        except Exception:
            supply_active = list(getattr(self, "_paper_interest_held", ()) or ())

        critical = critical_priority_codes(
            supply_active, close, ma_active, trend_active, gmo_held)
        known_non_gmo = set(supply_active) | set(close) | set(ma_codes) | set(trend_codes)
        gmo_codes.extend(code for code in pinned
                         if code not in known_non_gmo)

        raw_bands = [
            list(base),                       # 1. 수급매매
            close,                            # 2. 종가베팅
            ma_codes,                         # 3. MA
            trend_codes,                      # 4. 마켓트렌드
            gmo_codes,                        # 5. 기준봉
        ]

        # Check official exchange entitlement, share identity, and ETF/ETN
        # policy *before* allocating capacity. Never mark absent data live.
        valid_cache = {}
        def valid(code):
            code = str(code)
            if code not in valid_cache:
                ident = self.market_map.get(code) or pinned.get(code)
                ok = bool(ident and ident[0] in ("KOSPI", "KOSDAQ")
                          and not d_live.is_etf_or_etn(ident[1]))
                if ok and venue == "NXT":
                    ok = bool(self.nxt_snapshot.allows(code, now.strftime("%Y%m%d")))
                valid_cache[code] = ok
            return valid_cache[code]

        eligible_critical = [str(code) for code in critical if valid(code)]
        if preopen and special:
            # The few D+1 GMO candidates are mandatory first-session pins,
            # AFTER existing held/pending SELL symbols. Shared slots remain
            # available to AT/CW/MA/Trend and all non-GMO subscriptions.
            eligible_critical.extend(str(code) for code in gmo_priority
                                     if valid(code) and str(code) not in eligible_critical)
        eligible = [[str(code) for code in band if valid(code)]
                    for band in raw_bands]
        # 0.1.0: close-betting needs every scanned symbol live at once.
        eligible_critical.extend(close_window_codes(hhmm, eligible[1],
                                                    eligible_critical))
        phase = int(time.monotonic() // OVERFLOW_ROTATION_SECONDS)
        selected = allocate_priority(eligible, protected=eligible_critical,
                                     capacity=KIWOOM_MAX_SUBSCRIPTIONS,
                                     phase=phase)
        all_interest = set(eligible_critical)
        for band in eligible:
            all_interest.update(band)
        self._v093_priority_snapshot = {
            "priority": list(STRATEGY_NAMES),
            "weights": list(STRATEGY_WEIGHTS),
            "requested_unique": len(all_interest),
            "subscribed": len(selected),
            "unsubscribed_now": max(0, len(all_interest) - len(selected)),
            "pinned_total": len(eligible_critical),
            "legacy_mixed_pinned_count": len(pinned),
            "phase": phase,
            "venue": venue,
            "groups_requested": [len(set(b)) for b in eligible],
            "subscribed_ids_are_exchange_verified": False,
        }
        return [code + "_NX" if venue == "NXT" else code
                for code in selected]

    d_live.LivePaperRunner._desired_subscriptions = prioritized
    _INSTALLED = True
