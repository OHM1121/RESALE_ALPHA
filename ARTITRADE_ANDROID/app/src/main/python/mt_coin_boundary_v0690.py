"""0.6.9 (owner 2026-11-28 "마켓트렌드 신규 매수가 한 건도 없다 - 조건 부합 종목이 없어서인지 코드 문제인지"): the MarketTrend coin entry never saw the just-closed candle in time.

Evidence (device export 2026-11-28 22:06, execution_decisions + Upbit candles replayed through the 11.20 rule): in the last two days at least four coin signals fulfilled the rule
(RSI20 <= 30 watch -> first completed bar closing above its Bollinger middle with a slope inside (-15, +20) degrees: KRW-DOS 60m 11-27 11:00, KRW-ETH 30m 11-28 10:30, KRW-USD1 30m 19:00 and 60m 21:00) and none became a buy.
Three code defects stood in the way:
1. At EVERY candle boundary all ~28 coins x frames logged `OPERATIONAL_WATCH_HISTORY_MISSING` (about 2,000 a day): the history of a coin is refreshed by a lap over all pairs (0.2 s apart + the answer time, 45 s
   for a complete series), so the just-closed candle arrived 45-80 s late; until then the entry had no candle to decide on. Fix here: right after a boundary the pairs that matter (held, armed RSI watches) are fetched first, at
   once, and the lap restarts at the boundary instead of finishing the old order.
2. `market_trend_1118_runtime._entry_window` consumed the print of a coin BEFORE it knew whether the candle was there; when the candle arrived the print was "already seen", so a quiet coin was never evaluated again.
   Now a print is consumed only when at least one frame was really evaluated.
3. The same module still held two fixed "print not older than 10 s (coin) / 3 s (stock)" tests that the 0.6.8 rule had not reached: now `fresh_rule_v0680`.
Nothing about the entry rule, the order, the fill or the wallet changes. A coin that another strategy already holds is still refused (one position per coin).
"""
from __future__ import annotations

import datetime as dt

VERSION = "MT_COIN_BOUNDARY_V0690"
REST_GAP_AFTER_BOUNDARY = 1.5      # an armed / held pair whose newest candle is older than the last boundary may ask again after this many seconds
REST_GAP_OTHER_STALE = 8.0         # every other stale pair
FRAMES = ("30m", "60m", "day")


def armed_pairs(watches, market):
    """{(symbol, frame)} with an RSI watch in status WATCHING (the pairs that can become a BUY at the next completed candle)."""
    out = set()
    try:
        for key, row in list(watches.items()):
            if key[0] == market and isinstance(row, dict) and row.get("status") == "WATCHING":
                out.add((key[1], key[2]))
    except (AttributeError, TypeError, IndexError):
        pass
    return out


def expected_last_end(finish, frame_seconds):
    """End of the newest candle that must already be completed: the end of the forming one minus one frame."""
    return finish - dt.timedelta(seconds=frame_seconds)


def history_is_behind(rows, finish, frame_seconds):
    """True when the stored series does not yet contain the candle that just closed."""
    if not rows:
        return True
    try:
        return rows[-1]["end"] < expected_last_end(finish, frame_seconds)
    except (KeyError, TypeError):
        return True


def retry_after(complete, behind, important):
    """Seconds before the same pair may ask the broker again (the vendor: 45 complete / 12 incomplete)."""
    base = 45.0 if complete else 12.0
    if behind:
        return min(base, REST_GAP_AFTER_BOUNDARY if important else REST_GAP_OTHER_STALE)
    return base


def order_key(pair, held, armed, missing, frame_rank):
    """Lap order: held, then armed watches, then incomplete series, then the rest."""
    return (pair not in held, pair not in armed, pair not in missing, frame_rank(pair[1]), pair[0])


def lap_token(now, finish_fn, frames=FRAMES):
    """Changes whenever any frame crosses a candle boundary: the running lap must restart with the new order."""
    out = []
    for frame in frames:
        try:
            out.append(finish_fn(now, frame))
        except Exception:
            out.append(None)
    return tuple(out)
