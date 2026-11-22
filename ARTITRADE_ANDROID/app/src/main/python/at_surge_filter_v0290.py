"""0.2.9 (owner sheet 11.22 D5, red text): reference-bar (AT) BUY filter, stock and coin.

"각 시간봉별로 기준봉 포함 이전 10개봉을 조회하고 그중 한 개의 봉에서 +20%이상의 상승이
 나온 봉이 있다면 매수하지 말것."

For the candidate's own timeframe, the 10 completed bars ending with the reference bar
(reference bar included) are checked. A bar "rose +20 %" when its close is >= 20 % above
the previous bar's close (the HTS 등락률 reading; INFERRED - the sheet does not say
close-to-close or intrabar; the owner may correct). Any such bar -> no BUY.

Stock: checked at the native BUY using the bars table of the same SQLite file (D19 orders
resolve the reference bar through signals.signal_id). When the window cannot be read the
filter does NOT block (it forbids a buy only on a proven +20 % bar) and counts the case.
Coin: checked when the original scanner forms the reference record (d_upbit.ref_features),
so a surge-window reference never becomes a candidate. Shadow research records
(allow_strong_bear=True) are left unchanged. Coin MA keeps the original function.
"""
from __future__ import annotations

from decimal import Decimal

WINDOW = 10
SURGE = Decimal("0.20")
REASON = "AT_SURGE_20PCT_IN_LAST_10_BARS_NO_BUY_V0290"
UNVERIFIED = "AT_SURGE_WINDOW_UNVERIFIED_V0290"
UNVERIFIED_COUNT = {"stock": 0}
_INSTALLED = False


def surge(closes):
    """closes: ascending closes ending with the reference bar. Returns (max_rise, index) or (None, None)."""
    vals = [Decimal(str(v)) for v in closes]
    window = vals[-(WINDOW + 1):]
    best, at = None, None
    for i in range(1, len(window)):
        prev = window[i - 1]
        if prev <= 0:
            continue
        rise = window[i] / prev - 1
        if best is None or rise > best:
            best, at = rise, i
    return best, at


def violates(closes):
    best, _ = surge(closes)
    return best is not None and best >= SURGE


def stock_check(c, tf, code, ref_ts):
    """None when the BUY may continue, else a reason. `c` is the native D SQLite connection."""
    try:
        rows = c.execute("SELECT ts,close FROM bars WHERE code=? AND frame=? AND ts<=? "
                         "ORDER BY ts DESC LIMIT ?", (str(code), str(tf), str(ref_ts), WINDOW + 1)).fetchall()
    except Exception:
        rows = None
    if not ref_ts or not rows or str(rows[0][0]) != str(ref_ts):
        UNVERIFIED_COUNT["stock"] += 1
        return None
    closes = [r[1] for r in reversed(rows)]
    best, _ = surge(closes)
    if best is not None and best >= SURGE:
        return "%s:%s:%s:+%.1f%%" % (REASON, tf, code, float(best) * 100)
    return None


def coin_violates(closed_bars, record):
    """True when the coin reference window has a +20 % bar. Unknown layout -> False (scanner owns it)."""
    try:
        ref_at = str(record["ref_at"])
        idx = next(i for i, b in enumerate(closed_bars) if b["start"].isoformat() == ref_at)
    except (KeyError, TypeError, StopIteration, AttributeError):
        return False
    closes = [b["close"] for b in closed_bars[max(0, idx - WINDOW):idx + 1]]
    return violates(closes)


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    import d_upbit
    native = d_upbit.ref_features

    def ref_features(closed_bars, frame, market, tick, at=None, accumulate_top=None,
                     allow_strong_bear=False):
        record = native(closed_bars, frame, market, tick, at, accumulate_top=accumulate_top,
                        allow_strong_bear=allow_strong_bear)
        if record is None or allow_strong_bear:
            return record
        try:
            if coin_violates(closed_bars, record):
                return None
        except Exception:
            pass
        return record

    ref_features._v0290_surge = True
    d_upbit.ref_features = ref_features
    try:
        import coin_ma_engine
        if getattr(coin_ma_engine, "ref_features", None) is ref_features:
            coin_ma_engine.ref_features = native     # coin MA is not a reference-bar strategy
    except Exception:
        pass
    _INSTALLED = True
