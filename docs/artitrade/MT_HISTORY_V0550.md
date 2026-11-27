# 0.5.5 — stock MarketTrend history loading (issue #170) and the held supply-demand exit lane (issue #171)

Owner 2026-11-27: "이력 적재 단계 보완 ... 이거 진행해" and "하림지주 ... 1차 매도조건 도달했는데 매도 안하고 있는지 파악하고 오류수정".

## A. MarketTrend: why a watch that armed never bought (real device data 2026-11-23..26)
* `execution_decisions_v074.sqlite3`: the stock MarketTrend decisions are `OPERATIONAL_WATCH_HISTORY_MISSING` and `OPERATIONAL_HISTORY_ConnectionError`; no stock MarketTrend position exists.
* Vendor chain (CONFIRMED in `market_trend_runtime_v043.py`): `_raw_history` serves the local bars only if the newest local bar is <= 3 frames old. At the open the newest bar is the
  previous session's last bar, so every symbol x frame (about 200 x 3) goes to the broker (ka10080 / ka10081) in the first minutes, when the feed is busiest. A failed call leaves the
  key empty and `_watch_loop` records the missing history for the whole window. `_prefetch` also sleeps 0.35 s per pair, so even local reads needed minutes.
* Changed (`market_trend_history_v0550`): previous session's last bar is current until the first bar of today can have completed; broker only for a really missing bar at one call per
  second overall and one per symbol/frame per 120 s (a throttled call returns the local bars: `_closed_series` still refuses an entry on a hole); a preload thread copies the local bars
  into empty history slots every 30 s (pre-open included). Entry rules are NOT changed.

## B. 휴림로봇 2026-11-18 (owner screenshot): not bought by design, not by missing data
Recomputed with the real policy code on the stored 30m bars: RSI20 27.6 on 11-17 09:00 (watch armed, 24 h). First completed bar after it: 11-18 09:00 close 4,490 > Bollinger(20,1.5) middle 4,422,
but the middle line was still falling: angle -19.0 degrees, allowed window (-15, +20) -> no entry. The rule lets ONLY the first bar above the middle buy
(`new_legacy_1120_policy.first_close_above_middle_end`); the next bars (09:30 +12 deg, ...) are not "first" and the 24 h watch ended at about 09:30. The owner's chart is the combined
(KRX+NXT) series; the app's bars are KRX regular session only, so the slope on the owner's screen and in the app can differ (stage 2 of the combined flow is still open).
Limit of this check: the app's bars of that day were produced by an older version and the ledger was reset on 11-20, so the original run could not be replayed.

## C. 하림지주 (003380): first take-profit 10,426.5 touched, no sell
Position (state file): 3 sh @ 9,930, first target 5 % = 10,426.5, stage NORMAL, `first_profit_triggered=False`, no pending sell, last quote processed 11-26 19:54.
Live prints >= target were received (bars `LIVE_WS_0B` 11-26: 10,520 at 09:15:07, 10,510-10,530 09:57-10:05, 10,430 10:12:00).
* The vendor `GMOService` + `Engine` are CORRECT: the real classes, simulated clock, same position: a fresh print of 10,520 creates `TP5_FIRST_20PCT` at once (tests).
* The vendor routing (`d_live._new_tick` -> `_gmo_tick_queue` -> `_gmo_priority_tick_job_loop`) is correct on a free consumer (simulated).
* So the prints never reached `Engine.on_quote` in time. Candidate cause (CONFIRMED mechanism, device occurrence UNVERIFIED): ONE consumer thread runs, per print, GMO first and then the heavy
  DIVERSE/MA engine; its DB-gate waits were measured at 1.0-14.2 s on 11-26 ("가상주문 평가 잠금 대기 1초 초과", 103 issues, 16:00-19:59 only; none logged 09:00-15:30), so a print queued behind it
  is older than the 3 s executable-freshness gate when dequeued and is dropped silently (`HELD_RISK_PRIORITY_STALE_TICK` has no handler in the vendor diagnostics loop). The same signature is
  in 11-23: the 3/39 BUY order got no further fill for 5 hours of prints inside its limit and was cancelled only at 16:03:27. The phone's socket also dropped 40+ times on 11-26 (1011 keep-alive).
  The stored data cannot tell which stage dropped the 11-26 prints.
* Changed (`gmo_held_exit_v0550`): a watchdog thread offers the newest FRESH (3 s, same test) print of each held code to `GMOService.on_live_tick` every 0.4 s (the engine's own
  `q.at <= last_at` guard makes duplicates harmless); per-code counters at the feed ingress / watchdog / engine; issue `GMO_HELD_TP_TOUCH_UNPROCESSED` when a print at/above the target did not start
  the exit within 5 s; `stock/gmo_held_lane_v0550.json` keeps the day's counters. The 3 s gate is not weakened; no order, fill or price is invented.
* NOT fixed (needs an owner decision, see Issue #171): a print that was already older than 3 s when it arrived (phone network stalls) still cannot trigger the exit. A real resting sell at the target
  would have filled regardless of the phone's latency (R15). Options are in the Issue.

## Verification
SOURCE_FIXED + local regression (25 new tests, full list of the 0.5.4 workflow). CI and device: not verified. Never claim device verification from CI.
