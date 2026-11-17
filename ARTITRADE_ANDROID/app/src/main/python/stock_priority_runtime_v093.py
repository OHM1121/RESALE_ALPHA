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


def allocate_priority(bands, protected=(), capacity=KIWOOM_MAX_SUBSCRIPTIONS, phase=0):
    """Return unique, bounded broker symbols; no permanent discard of overflow.

    Protected means existing holdings / orders needing monitored exits, not a
    substitute for an executable fresh orderbook. The hard limit still applies.
    Candidates across the five strategy bands are weighted 5:4:3:2:1. Unused
    capacity from an empty band flows to the others. Over-cap candidates cycle
    in real broker REG subscriptions; unsubscribed names remain NOT LIVE.
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
    if limit == 0:
        return []

    groups = []
    for band in bands:
        values = []
        for code in band:
            code = str(code)
            if code and code not in assigned:
                assigned.add(code)
                values.append(code)
        groups.append(values)

    room = limit - len(chosen)
    quotas = [0] * len(groups)
    while room > 0:
        progressed = False
        for i, weight in enumerate(STRATEGY_WEIGHTS):
            take = min(weight, room, len(groups[i]) - quotas[i])
            if take > 0:
                quotas[i] += take
                room -= take
                progressed = True
            if room <= 0:
                break
        if not progressed:
            break

    for group, amount in zip(groups, quotas):
        if amount <= 0:
            continue
        # Keep the most highly ranked few in each band connected, rotating the
        # remainder by actual slot count. This prevents permanent starvation.
        anchor = min(12, amount // 3)
        chosen.extend(group[:anchor])
        remainder = amount - anchor
        tail = group[anchor:]
        if remainder > 0:
            shift = (max(0, int(phase)) * remainder) % len(tail)
            ring = tail[shift:] + tail[:shift]
            chosen.extend(ring[:remainder])
    return chosen


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

        # D+1 preopen is a time-specific KRX/NXT venue switch with its own
        # safety/arming gates, not a regular-session ranking contest.
        if gmo_priority and "0830" <= hhmm < "0900":
            return native(self, at)

        venue = (
            "NXT" if d_live.live_session(now, "NXT")
            else "KRX" if d_live.live_session(now, "KRX")
            else None
        )
        if not venue:
            return []
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
        try:
            ma_codes = list(self.ma.pinned_stocks()) + list(self.ma.watched_codes())
        except Exception:
            # Existing pinned holdings are retained even if a stale research
            # scope briefly fails; do not reconnect the WS on that failure.
            pass

        trend_codes = []
        try:
            runtime = getattr(core, "_RUNTIME", None)
            trend = getattr(runtime, "market_trend", None) if runtime else None
            if trend is not None:
                with trend.lock:
                    trend_codes = list(trend._candidates.get("stock", {}))
                    trend_codes.extend(
                        str(position.symbol)
                        for position in tuple(trend._positions.values())
                        if position.market == "stock" and position.qty > 0
                    )
        except Exception:
            pass

        try:
            gmo_codes = gmo_priority + list(self.gmo.watchlist()) + list(self.gmo.holding_codes())
        except Exception:
            gmo_codes = gmo_priority

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

        eligible_pinned = [str(code) for code in pinned if valid(code)]
        eligible = [[str(code) for code in band if valid(code)]
                    for band in raw_bands]
        phase = int(time.monotonic() // OVERFLOW_ROTATION_SECONDS)
        selected = allocate_priority(eligible, protected=eligible_pinned,
                                     capacity=KIWOOM_MAX_SUBSCRIPTIONS,
                                     phase=phase)
        all_interest = set(eligible_pinned)
        for band in eligible:
            all_interest.update(band)
        self._v093_priority_snapshot = {
            "priority": list(STRATEGY_NAMES),
            "weights": list(STRATEGY_WEIGHTS),
            "requested_unique": len(all_interest),
            "subscribed": len(selected),
            "unsubscribed_now": max(0, len(all_interest) - len(selected)),
            "pinned_total": len(eligible_pinned),
            "phase": phase,
            "venue": venue,
            "groups_requested": [len(set(b)) for b in eligible],
            "subscribed_ids_are_exchange_verified": False,
        }
        return [code + "_NX" if venue == "NXT" else code
                for code in selected]

    d_live.LivePaperRunner._desired_subscriptions = prioritized
    _INSTALLED = True
