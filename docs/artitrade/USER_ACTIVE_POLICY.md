# ARTITRADE — Dated User-Authorized Active Requirements

Last reconciled: 2026-11-19. This chronological history is authoritative
for resolving user requests; do not silently replace a rule merely because
a new instruction appears different. Cross-check original
`NEW LEGACY(11.18).xlsx` and its exact source rows when changing strategy logic.

## 2026-11-18: NEW LEGACY (11.18) workbook received; primary strategy specification
- **Stock AT/reference and coin reference** buy only when the current signed
  stock/coin market score is **strictly above 0 at the actual BUY**; the
  candidate may have been detected under a negative score. An observed trade
  price hitting the limit while score <=0 must **NOT** create a BUY.
- AT must use original eligible reference candle, +2%/+3%/+3.5%/+4% frame
  TP1 (30% of original shares), respective second-stage +2%/+2%/+2.5%/+3%
  TP2 (35% of original), original reference-range downside and score<-10
  and holding expiry/rebound exit rules. Verify the source workbook if any
  numerical condition is modified.
- **Stock/coin MA** buy only the verified upward transition of MA120 slope,
  with MA5,10,20,60 all below MA120 and their slopes upward. 5m=48h,
  30m=80h, 60m=120h, daily=15 days; -15% hard stop. Retire older completed
  bearish-bar/next-open automatic MA entries.
- **Stock/coin MarketTrend** only 30m,60m,daily. RSI20<=30 initiates a
  24h watch; Bollinger 20/1.5 center slope strictly (-20,+30), genuine middle
  touch and TOP50; -15% hard stop; original workbook TP1/TP2/trailing and
  duration. Never revive retired Trend 5m.
- On confirmed eligible BUY, **prepare original-quantity 30% TP1 and 35%
  TP2 immediately**. Preplanned PAPER rows are not exchange-submitted LIMIT
  orders; never claim actual broker posting without a native authenticated
  broker order ID and ownership proof. Ordinary stop/trailing continues on
  original verified quotes. Re-place stock orders at next eligible session
  if and only if an actual broker-order path is separately authorized and
  implemented.

## 2026-11-19: Explicit strategy-scope exception
- New workbook/purge rule changes apply only to **stock AT, stock MA,
  stock MarketTrend, coin reference, coin MA, coin MarketTrend**.
- **GMO/수급매매 and CW/종가베팅 are expressly excluded.** Their existing
  original strategy BUY/SELL policies, wallets, pending/orders, sources,
  filters, history, research, realized/unrealized P&L and UI evidence
  must be preserved.
- Any shared feed, lock, scheduling or history repair must test for
  cross-strategy regressions; never change GMO/CW buy/sell thresholds in
  order to fix AT latency.

## 2026-11-19: Existing old LEGACY holdings and historical PAPER trades
- Earlier direction: temporarily freeze legacy holdings; new buys only
  follow NEW LEGACY. This was superseded later the same day by the
  following *explicit deletion instruction*.
- Current overriding instruction: remove **proven old-rule PAPER**
  buys/sells, related positions, historical executions, P&L,
  pending/order/related research from affected non-GMO/CW strategy
  ledgers so that they no longer appear in ordinary active history,
  without rewriting or erasing broker-real orders and without damaging
  NEW LEGACY-verified fills. Reverse cash and realized P&L from original
  native receipts. Unknown historical/broker provenance must fail closed
  and be reported as PARTIAL rather than fabricating cash.
- Do not edit private historical ZIP/SQLite user exports in a public repo.
  Keep independent owner audit and signed-migration integrity protocol.

## 2026-11-19: Additional explicit PAPER-only invalid-exit cleanup authorization
- The user additionally authorizes removing an individually **proven invalid
  NEW LEGACY PAPER** cycle from active holdings, executions, P&L, research and
  ordinary UI when a genuine NEW LEGACY sell should have executed but a
  confirmed software malfunction prevented the native PAPER sell.
- This is an explicit narrowly scoped exception to the earlier preservation
  of genuine NEW LEGACY receipts. It does NOT authorize deleting all new
  positions, erasing an otherwise valid BUY, or reclassifying missing
  metadata as an old-rule trade. For historical SELL failure, prove a
  verified post-BUY trade sequence, actual executable contemporary bid/depth,
  correct sell stage, original wallet/native sell decision, and error/absence
  of receipt. Historical candle highs or ticker-only price touches are not
  sufficient evidence that SELL liquidity existed.
- Before any such cleanup, keep a *private* independent immutable forensic
  record, original-wallet backup, exact rollback witness and cash/realized
  reversal; reconcile duplicate/partial fills, restarts and linked research.
  Unknown provenance or REAL/broker orders remain protected and PARTIAL.
  No retroactive synthetic SELL and no private trade data in this public repo.
- No on-device per-position delete is claimed without the actual private
  source wallet and event evidence. This dated authorization alone is not
  evidence that a device cleanup has executed. GMO/CW owners remain excluded.

## 2026-11-19: Holdings screen layout
- In the holding-card **left rectangular badge**, show actual timeframe
  `5분`, `30분`, `60분`, `일봉` rather than `보유`. Remove redundant
  timeframe after the stock/coin name.
- GMO/CW without a factual candle timeframe may show grounded `수급` or
  `종가` source labels, not fabricated minute frames. The order/SELL
  badges and strategy logic are unaffected.

## 2026-11-19: Highest-priority stock pending-to-BUY correction
- An eligible AT or MA BUY must flow from eligible original candidate,
  genuinely fresh official 5m parent/reference and exchange stock tick +
  appropriate venue orderbook, score>0 when applicable, wallet
  authorization, native transaction and confirmed original BUY receipt.
- An **unavailable/negative/stale** market score, missed official candle,
  delayed exchange trade or unavailable bid/ask is not grounds to fabricate
  a fill or skip safety. But prevent avoidable processing latency, invalid
  source/queue coupling and starvation; record exact reason per attempted
  candidate. No retrospective order book fill.
- A CI-only simulation proves regression coverage, **not** real Android
  device, LIVE broker or actual future market session.

## 2026-11-19: Numeric-only version identifiers
- Android `versionName`: strictly numeric dot-separated, e.g. `0.0.13`.
- Delivered APK filename and GitHub release tag: `0.0.13.apk`, `0.0.13`.
- Do NOT append `FIX`, `TEST`, dates, v-prefix, descriptive text or
  other characters to user-facing *version identifiers*. Internal issue
  titles, build job names and human-readable release notes are not app
  version identifiers.
- Keep existing install package, signing certificate and monotonic
  integer versionCode so app can upgrade in place.

## 2026-11-19: Collision-warning procedure for ALL future agents
1. Read current request and compare its changes against every dated active
   rule above and the full original workbook, including indirect changes
   from shared data sources, execution, history, UI and ledger reset.
2. If a collision is expected, **before changing the conflicting behavior**
   respond in Korean with: **기존 기준 설정일**, **기존 기준**,
   **충돌하는 신규 지시**, **예상 결함(affected strategy + source/wallet/
   misfill, missed fill or missing P&L)**, and the precise user choice
   needed to supersede/retain. State concrete settings, not generalities.
3. Preserve the existing active rule until the user explicitly confirms a
   replacement after hearing the consequence. Newer chat text with
   ambiguous intent is not automatic supersession.
4. When a user explicitly supersedes a rule, append a new dated entry here,
   preserving the historical entry and identifying what changed. Update
   `AGENTS.md`, version issues, dependency graph and targeted regression
   tests. Never conceal a conflict by modifying test expectations.

## 2026-11-19: Permanent chat handoff and GitHub dual-publication instruction
- Every time the user requests an ARTITRADE handoff, show the **complete document directly in the assistant's answer, with copy control**, and save the identical sanitized Markdown during that work in GitHub.
- Keep two markdown paths: a new dated archive under docs/artitrade/handoffs/ and the refreshed docs/artitrade/HANDOFF_LATEST.md. Commit to the current source branch and default main so the latest handoff can be found regardless of source/main divergence. Verify commit IDs and link them in the response; report a failed write instead of inventing an upload.
- Do not automatically enter any text into the user's ChatGPT message/input/composer box, do not prefill it and do not automatically submit follow-up chat turns, even if the text is a handoff or a link. Only answer-side copyable text and verified GitHub files.
- Keep handoffs up-to-date on release SHA, CI, version, APK, open errors, exact strategy/wallet policy, original provenance and device-verification status; no personal on-device private data in public GitHub. Archive revisions rather than replacing prior dated copies.

## 2026-11-19: Original eight-wallet stable position identifiers
- A durable, globally unique position UID covers active and completed original
  positions in stock AT/MA/GMO/CW/Trend and coin Reference/MA/Trend.
- A UID is namespaced by verified epoch + original owner-wallet + immutable
  original native position ID/BUY receipt. Original source primary keys,
  execution foreign keys, strategy policies and cash remain unchanged.
- Fills without an independently verified source-position relationship are
  marked UNLINKED_EXECUTION, never assigned a fabricated BUY or joined by
  ticker alone. Missing provenance is a visible error, not a blank screen.
- Historical rows are indexed read-only from original native SQLite; newly
  recorded rows gain identifiers from the same source evidence, including after
  restart. Sidecar failures must not block genuine SELL or hide original balances.

## 2026-11-19: Specifically authorized one-cycle CFG exception
- User explicitly authorizes deleting exactly ONE earlier reported KRW-CFG
  coin MA 5-minute PAPER position with malfunctioning SELL, notwithstanding
  unresolved historical book-liquidity proof. This exception does not apply
  to another CFG entry, other frame, other owner, live/broker or other ticker.
- Require a unique historical original position with native source BUY proof,
  exact pre-incident entry time and unsold original MA state; ambiguity skips
  rather than deletes. Restore native PAPER cash, realized P&L and linked
  history, back up and rebase independent witness before trading starts.
- Never claim a real phone position was erased or IDs were already indexed
  solely because a synthetic CI suite or Android APK built. Record exact
  DELETE_ONE / SKIPPED result privately on the phone.


## 2026-11-19: Visible and verified native preload on a new UI entry
- Show the existing source-data verification screen again on every new
  Activity entry even when the existing service is already healthy; do not
  create a second Kiwoom/Upbit login, reset original candles, duplicate
  market subscriptions or interrupt held-position SELLs.
- Preserve original mandatory native preflight: verified closed bars (including
  missing/reconnect newest parent), verified exchange ticker/0B and genuine
  executable bid/ask book/0D, market trend RSI20/MA frame coverage and original
  eight wallet ledger integrity. Historical REST bars must never create fills.
- Show evidence-count-based 0..100% and measured ETA. Unknown/stalled progress
  must be labelled 'cannot estimate', not an invented deadline or false 100%.
  The existing 40-second bounded UI escape remains: an outage/holiday cannot
  block all holdings/UI indefinitely, while missing checks stay visible and
  original background recovery continues. UI dismiss is not trading readiness.
- Actual venue quote, orderbook and official bar source provenance must be
  preserved. Coin UPBIT_CLOSED from original REST verified completed candles
  is accepted after high/low/close and timestamp validation; stale newest
  completed coin candles remain missing.

## 2026-11-19: MarketTrend native BUY cost/returns source
- For stock and coin MarketTrend, show each original MT54 position's committed
  BUY execution entry price, quantity, source execution ID and source-position
  matched SELL net PnL and realized % across holdings, executions and research.
  Calculate SELL return from original confirmed buy unit price and proportional
  original buy fee, never inferred candles or symbol-only joins.
- For stock Trend holdings lacking fresh 0B display, source SQLite saved MARK
  may be shown only if clearly labelled LAST SAVED (NOT LIVE/EXECUTABLE).
  Never substitute this for native quote/book SELL permission or broker fill.
- No changes to the original trend 30m/60m/day entry rules, original broker
  live/PAPER fill ownership, GMO/CW wallets or cash accounting; old Trend 5m
  entry remains retired, and legacy held 5m SELL recovery stays available.

## 2026-11-20: MarketTrend entry condition 2 superseded by workbook sheet `11.20` (issue #114)
- **Source:** user-provided `NEW LEGACY(11.18)_rev1.xlsx`, sheets `11.19` vs `11.20`. The only red-marked change is the stock+coin MarketTrend second buy condition. The workbook file itself is not stored in this public repository.
- **Superseded (kept above, dated 2026-11-18):** Bollinger(20,1.5) MIDDLE slope strictly (-20,+30) degrees and a genuine live price reaching the MIDDLE line.
- **Now active:** slope strictly (-15,+20) degrees AND the just-COMPLETED bar closes strictly ABOVE the Bollinger MIDDLE. RSI20<=30 watch (24h), 30m/60m/day only, TOP50 candidates, -15% hard stop, holding periods and every sell rule are unchanged. GMO/CW/AT/MA are not affected.
- **Scope:** stock and coin MarketTrend, new BUYs only. Existing holdings keep their exits. `entry_signal_policy` stays `NL1118_RSI20_24H_BOLL_20_1_5` (the purge guard identifies protected NEW LEGACY positions by it); the new rule is recorded as `entry_rule=NL1120_BAR_CLOSE_ABOVE_BOLL_MID`.
- **Interpretation recorded as an assumption (INFERRED):** "the bar formed at that time closes above the MIDDLE" = a completed bar; only bars that ended after the RSI watch began qualify; equality is not "above"; the signal is valid right after that bar and the BUY still needs a fresh verified quote/book, fees, slot and wallet fill. Ask the user to confirm if the intended reading is the forming bar's current price instead.
- **Observed but NOT applied:** in the MA cell the sentence "remove the older five-MA-down / long-bearish-bar trades; only case 1 is the MA strategy" disappeared in `11.20`. It is not red-marked and was already in force, so MA behaviour is unchanged.
- **Verification:** SOURCE_FIXED + local regression only (`test_nl1120_trend_close_above_mid.py`); not CI, not device.

## 2026-11-20: Stock MarketTrend universe and unaffordable AT pending orders (issue #117)
- **Owner decision 1 (supersedes, for STOCK only, the 2026-11-18 "MarketTrend watches TOP50 instead of TOP35"):** the trading-value TOP50 list applies to **COIN only**. Stock MarketTrend uses exactly the symbol universe of the reference-bar (AT) strategy (`LivePaperRunner.krx_market_map`: top-N per market by trading value, ETF/ETN excluded, plus pinned holdings). Coin MarketTrend is unchanged (TOP50 by 24h trading value).
- **Owner decision 2 (answers the open question in issue #115):** a stock AT pending order whose one-share cost (limit price plus buy fee) is above the per-symbol budget (timeframe seed / max positions) is **excluded from "buy pending"**, with a recorded reason, instead of staying pending until it expires. Raising the budget later affects new references only.
- **Not changed:** every buy/sell rule, the signed-market-score gate for AT/reference, the 3 s quote freshness, depth and fee checks, holdings, cash and the budget itself.
- **Known effect to watch:** the stock MarketTrend candidate list grows from 50 per market to the AT universe (up to about 200 symbols), so history/RSI work per cycle grows. Not measured; DEVICE_VERIFIED=false.

## 2026-11-20: Market score gates ONLY the reference-bar (AT) strategy (owner statement, issue #118)
- **Owner statement:** besides the reference-bar strategy, where the signed market score is a buy condition, stock MA and stock MarketTrend must buy; the market score must not stop them.
- **State in code (verified 2026-11-20):** the signed-score gate (`at_market_score_gate_v107`, `reference_1118_runtime.buy_d19/buy_legacy/buy_coin`) wraps only the stock AT BUY functions and the base coin reference-bar BUY. Stock MA (`ma_v067_stock_runtime`) and MarketTrend (`market_trend_1118_runtime`, `market_trend_runtime_v043`) contain no score gate. Pinned by `test_r010_market_score_scope.py`.
- **Why they still did not buy (0.0.18 history, issue #118):** missing fresh ticks for the candidates (about 90 % of audited MA candidates on 2026-11-20 09-15h; fixed in 0.0.20/0.1.0, not yet seen in market hours), history gaps at session start, and the strict entry rules themselves. A replay of the 11.20 Trend rule on the stored 30m/60m/day bars of 2026-11-19/20 finds only about 6 theoretical entries (30m) in two days.
- **Not changed:** any MA / Trend entry rule. Loosening them is an owner decision.


## 2026-11-20: Workbook sheet `11.20(2)` — MA / MarketTrend stop-loss and entry (issue #120)
- **Source:** user-provided `NEW LEGACY(11.18)_rev1.xlsx`, sheet `11.20(2)` versus `11.20(1)`. Blue text = delete, red text = add (rule R6). The workbook is not stored in this public repository.
- **기존 기준 설정일 / 기존 기준:** 2026-11-18 workbook, kept by rev1 (2026-11-19/20): MA and MarketTrend exit before the first target when the entry-signal candle LOW was touched and a later completed bar closed below it. Also 2026-11-20 (#114): MarketTrend buys when the completed bar closes above the Bollinger MIDDLE with slope (-15,+20) during the 24 h RSI watch.
- **새 지시와 충돌하는 부분:** (a) that low-touch/close-below exit is deleted (blue) for MA and MarketTrend; (b) MarketTrend may buy only on the FIRST completed bar closing above the MIDDLE after RSI<=30 (the old rule accepted any such bar during the 24 h watch).
- **예상 결함 / 영향:** positions opened before 0.1.2 keep the exit they were bought under (new rule: new BUYs only); a new position with fewer than 20 completed bars of history gets no lookback stop (only -15% and expiry protect it); the first-close rule makes MarketTrend entries rarer; MA stop fires on a completed-bar close, so a gap-down inside a bar is exited only after that bar ends.
- **Now active (stock and coin, new BUYs only):**
  - MA (5m/30m/60m/day): stop = lowest LOW of the last 50 completed bars before the BUY; a later completed bar CLOSING below it sells everything at any stage; ignored if that stop is higher than the buy price. Reasons `MA_NL1120_LOOKBACK_LOW_CLOSE_STOP`.
  - MarketTrend: stop = AVERAGE of the LOWs of the last 200 (30m) / 90 (60m) / 90 (day) completed bars; same close rule and ignore rule. Reason `NL1120_LOOKBACK_LOW_CLOSE_STOP`. -15% remains the first check and holding expiry the second.
  - MarketTrend entry: only the first completed bar after the RSI20<=30 touch that closes above its Bollinger MIDDLE (and passes the slope test) may buy; a missed/failed first bar consumes the cycle; a new cycle needs RSI>30 then RSI<=30 again (post-BUY re-arm was already so).
  - Already implemented, unchanged: after TP1, sell all at buy+1% (`NL1118_AFTER_FIRST_PLUS_1_STOP`); -15% hard stop; holding periods; AT/reference, GMO, CW untouched; market score still gates only AT.
- **Interpretations recorded as assumptions (INFERRED, owner may correct):** "the bar just before the buy" = the newest COMPLETED bar at the buy time, counted as bar 1 of N; the MA sentence "if the average of lows is above the buy price, ignore" is applied to the MA lowest-low stop too (stop above buy price = ignored); fewer bars than the window (>=20) use all existing bars and record how many; the 60-minute bar is the app's 60m frame; MA/Trend cell text changes that are not red/blue (`1~3차 매도 목표가격 중 …`) are wording only and not applied as logic.
- **Verification:** SOURCE_FIXED + local regression (`test_nl1120_2_lookback_stop_and_first_close.py`, `test_v014_cfg_native_sell_quote_regression.py` end-to-end on the original native MA coin ledger). CI and device not yet verified.


## 2026-11-20: One-time PAPER ledger reset of everything except GMO (issue #121, version 0.1.3)
- **Owner instruction:** delete every ledger except the GMO (수급매매) ledger so the effect of the new policy 11.20(2) can be measured from zero; dashboards then show no net profit / target-reach figures for the deleted strategies; the trading engine and processes must not be touched.
- **기존 기준 설정일 / 기존 기준:** 2026-11-19 (AGENTS.md and this file): FreshEpochGate / LedgerAntiRollback may not be bypassed; the 2026-11-19 purge exception covered only attributable pre-NEW LEGACY AT/MA/Trend PAPER trades and excluded NEW LEGACY, GMO/CW, REAL broker and ambiguous fills.
- **새 지시와 충돌하는 부분:** NEW LEGACY AT/MA/Trend and close-betting (CW) PAPER ledgers are now erased as well (GMO is kept).
- **예상 결함:** irreversible loss of the old PAPER history; rows that are REAL/broker-linked or whose cash/P&L cannot be reconciled are NOT deleted (status PARTIAL) so some old rows may remain; research/score journals (market score history, candidate research, post-exit highs) are kept because the engines read them, so research-side panels may still show old research rows.
- **Now active:** `ledger_reset_v0130.run_once`, once per phone, before engines start, with backup, exact cash/realized reversal, witness rebase for the touched wallets only and a DONE marker. Failure leaves wallets restored and never blocks trading. Superseded for the one-time reset only; the rule "no ledger reset without explicit owner instruction" stays.
- **Verification:** SOURCE_FIXED + local regression; CI and device not verified.


## 2026-11-20: Ledger reset phase 2 - independent research / error / AI history (issue #123, version 0.1.4)
- **Owner request:** old research history, error history, AI analysis, execution history and buy-pending data must not stay mixed with new-policy data after the ledger reset (#121).
- **기존 기준:** AGENTS.md - engines and their state must not be touched; research history is evidence kept for analysis. The #121 reset removes the ledger only.
- **새 지시와 충돌하는 부분 / 예상 결함:** old research/error/AI evidence is removed irreversibly. Wallet-database research tables (`candidate_snapshots`, `candidate_forward`, shadow tables) are NOT cleared because `d_candidate_history` back-fills snapshots from `signals`; clearing signals/pending would change engine state. Pending buys are engine-owned and expire by their own rules, so some old-policy research rows and pending buys remain visible until they expire or are decided otherwise.
- **Now active:** `history_reset_v0140.run_once` (once per phone, before engines start): AI_RESEARCH_V040, AI_COMPREHENSIVE_V052, phone_buy_evidence_v035, execution_decisions_v074, execution_entry_plans_v107, forward_high_v041, ANDROID_RESEARCH_OBSERVATIONS, MarketTrend RSI watch file cleared in full; MarketTrend journal logs; issues / runtime_error_summary / issue_lifecycle of non-GMO DBs; non-GMO post-exit-high rows; `*error*.jsonl`; app fault log. Kept: signals, candidates, pending, candidate_*/shadow tables, bars, market score, accounts, MarketTrend latch and stop-reentry, all GMO files.
- **Open owner decision:** clearing candidate/signal-linked research and pending buys needs engine-state surgery (re-detection risk).
- **Verification:** SOURCE_FIXED + local regression; CI and device not verified.


## 2026-11-20: Reset phase 3 - candidate / signal-linked research and buy-pending (issue #124, version 0.1.5)
- **Owner decision:** answering the open question of #123, candidate / signal-linked research records and buy-pending (매수대기) must be cleared as well.
- **기존 기준:** phase 2 (#123) kept engine-owned state because clearing signals/pending was thought to risk re-detection of old signals.
- **새 지시와 충돌하는 부분 / 예상 결함:** engine tables are now emptied (rows only). Checked in the vendor source: normal discovery only handles the just-completed bar, the full re-discovery function is never called, the engine tolerates an empty `pending`; remaining risk: a NEW gap-repair may recreate a still-valid reference for a symbol (normal behaviour), and the old research evidence is lost irreversibly.
- **Now active:** `candidate_reset_v0150.run_once` (once per phone, before engines start): stock AT `signals, pending, v19_orders, v19_events, v19_higher_frame_audit14, v19_pending_5m_audit, v19_frame_revision_confirm, candidate_* , stock_shadow_*`; stock MA `candidates, research`; coin `orders, candidates, candidate_decisions, scan_runs, coin_scan_decisions, coin_shadow_*, coin_v24_shadow_exit, coin_top35_*, strong_bear_shadow_*`; close-betting `signals, research_*` (not research_bars/rule versions/settings); MarketTrend `virtual_position_state`. Kept: positions, executions, accounts, bars, market score, gap holds, audit dedupe keys, MarketTrend latch / stop-reentry, GMO.
- **Verification:** SOURCE_FIXED + local regression on the real native stores; CI and device not verified.

## 2026-11-20: UI footnotes removed (owner request, presentation only)
- Explanatory footnote lines (unique trade id, Kiwoom official REST wording, "screen only" captions, recovery captions) are removed from lists and dashboards.
- Unchanged: price freshness is still shown, as a colour dot (green live exchange tick / amber last or stale / grey unknown); no trading, ledger, order or reset rule is affected.
- Execution history is displayed one card per unique trade (buy and sell together). Display only; ledger rows are untouched.

## 2026-11-21: MarketTrend lookback stop = LOWEST low (owner decision, issue #131, version 0.1.9)
- **기존 기준 설정일 / 기존 기준:** 2026-11-20 workbook `11.20(2)`: MarketTrend stop = AVERAGE of the LOWs of the last 200 (30m) / 90 (60m, day) completed bars before the buy; a stop above the buy price is ignored.
- **새 지시와 충돌하는 부분:** the owner reported NEAR below its stop point but not sold. Cause: the average was above the buy price (MarketTrend buys a rebound after RSI<=30), so the stop was ignored for NEAR, XLM and PENGU (all open Trend positions in the 2026-11-21 export). Owner chose option 2.
- **예상 결함:** the lowest low is lower than the average, so the stop triggers later than an average stop would; a position can still be ignored if even the lowest low is above its buy price.
- **Now active (stock and coin MarketTrend):** stop = lowest LOW of the same 200/90/90 completed bars before the buy; completed-bar CLOSE below it sells everything; ignore-if-above-buy-price, -15%, holding expiry and every other rule unchanged. MA stop (lowest low of 50) unchanged. Rule name `NL1120_3_LOOKBACK_MIN_LOW_CLOSE_STOP`.
- **Open positions:** MarketTrend positions that are still open, not triggered and carry the old average stop are re-fixed once, only from the full window, and bars already closed after the buy are judged against the new stop. This can sell an already-open position at its next sell check (owner request, NEAR).
- **Verification:** SOURCE_FIXED + local regression; CI and device not verified.

## 2026-11-21: Market score <= -10 halves the per-symbol buy limit for MA and MarketTrend (owner, issue #132, version 0.2.0)
- **기존 기준 설정일 / 기존 기준:** 2026-11-20 (#118): the signed market score gates ONLY the reference-bar (AT) strategy; stock MA and stock/coin MarketTrend must buy and the score must not stop them.
- **새 지시와 충돌하는 부분:** none in direction - the score still does not stop a buy. It now also changes the SIZE of an MA / MarketTrend buy.
- **예상 결함:** the reduction is evaluated at the moment of each BUY/top-up; a position bought small stays small after the score recovers (no automatic top-up to the full amount, no selling); a missing/stale score is treated as unknown (full size), never as -10; stock MA sizing lives inside the vendor engine and is clamped through the shared order-book quantity during the MA tick only.
- **Now active (stock + coin MA and MarketTrend, new BUYs and top-ups):** per-symbol limit = frame seed // frame max positions; while the market's signed score is <= -10 the limit used is HALF (floor) of that (200,000 -> 100,000; 400,000 -> 200,000). When the score is above -10 the full limit applies again automatically. AT, GMO and CW are not affected. The score and the factor are stored with the position (`market_score_at_buy`, `buy_size_factor`) and shown in the MarketTrend buy basis.
- **Verification:** SOURCE_FIXED + local regression; CI and device not verified.

## 2026-11-21: Preload progress, buy basis, first profit target and stop-loss on holdings (owner, issue #133)
- Preload: ONE progress = done/total of all measured start-up work (official bar/gate checks, stock loading counter, screen data); percent and bar move together, never go backwards, stay <= 99% until the real completion gate; remaining time only after a measured rate; footnotes removed. Supersedes the 0.0.17 note that dynamic check totals are not a progress denominator (owner instruction).
- Holdings: label "1차 수익목표"; stop-loss shows the nearest stop price with the expected loss rate in brackets as a number only; buy basis states the stored facts (reference bar start time and low, buy price and time; MA signal bar; MarketTrend RSI/Bollinger conditions and the size note). GMO stop is not shown (rule not verified).

## 2026-11-22: Overnight Korea signal (EWY) for stock buys, held-position exits and the supply-demand D+1 price (owner, issue #141, version 0.2.6)
- **Owner instruction (final wording of the day; an earlier same-day draft "-2.6 % / first 3 minutes / including GMO and CW" is superseded):** night futures window = D 18:00 -> D+1 06:00. (1) Stock reference bar (AT), MA and MarketTrend: if the night change is <= -3 %, no BUY for 30 minutes after the regular open. (2) Held AT/MA/MarketTrend stock positions: if current price x (1 + night decline) reaches the stop, sell the whole position as early as possible after the open. (3) Supply-demand: no time block; D+1 BUY when the current price <= D close x (1 + night change) (down night lower, up night higher). Kiwoom value first, Yahoo second.
- **기존 기준 설정일 / 기존 기준:** 2026-11-19: GMO/CW are excluded from new rule changes and keep their original BUY policy (GMO D+1: below the D+1 open (+2 ticks) OR strictly below the D close).
- **새 지시와 충돌하는 부분 / 예상 결함:** the GMO D+1 price rule is replaced by one ceiling (owner explicit). On a down night GMO buys only after a decline at least as large as EWY's; on an up night it may buy up to D close x (1 + rise). CW is gated by timing only (it buys 15:10-15:20, so no effect). The pre-emptive exit can sell a position that would have recovered later.
- **Now active:** night value = Yahoo EWY US-session open->close (latest complete session after the previous KRX close; the app's Kiwoom REST paths are domestic-stock only). Unverified value -> no buy block, no pre-emptive exit, GMO keeps the former OR rule. Pre-emptive exits are decided 5 minutes before the open from the holding-card stops and executed through each strategy's own wallet with reason AUTO_NIGHT_EWY_PREEMPTIVE_STOP.
- **Interpretations recorded as assumptions (INFERRED, owner may correct):** "EWY 하락/상승 마감" = EWY US session close vs its open; the pre-emptive exit applies on ANY down night (the stop-distance test itself is the condition), not only on <= -3 % nights; "현재가" = the latest KRX trade before the open (else the saved mark); the nearest stop is the one shown on the holding card.
- **Verification:** SOURCE_FIXED + local regression; CI and device not verified.

## 2026-11-22: Stock MarketTrend RSI20<=30 watch may also start from a completed bar (issue #140, version 0.2.6)
- **Owner request:** stock MA and MarketTrend did not trade at all last week; make them work from 2026-11-23.
- **기존 기준:** 2026-11-18/11-20: RSI20<=30 starts a 24 h watch; the code observed it only on a fresh live print (forming RSI).
- **Change / interpretation (INFERRED):** a COMPLETED stock bar with RSI20<=30 is also an RSI20<=30 observation and starts the same watch (once per bar). Entry (first completed bar closing above the Bollinger MIDDLE, slope window), freshness, wallet and every exit rule are unchanged. Coin path unchanged.
- **Verification:** SOURCE_FIXED + local regression; CI and device not verified.

## 2026-11-22: Pre-emptive night-EWY exits start at 08:00 (owner, issue #141, version 0.2.7)
- **Owner instruction:** the held-position exit when the night decline reaches the stop must happen "at 08:00, as early as possible".
- **Now active:** decided at 07:55; from 08:00 the exit uses the NXT pre-market best bid (the engine's NXT window is 08:00-08:30; only NXT-eligible symbols), afterwards the KRX regular open until +30 minutes. NXT prices are used ONLY for this automatic exit; user sells keep the KRX path. The 2026-11-22 rule otherwise unchanged.
- **Verification:** SOURCE_FIXED + local regression; CI and device not verified.

## 2026-11-22: Pre-emptive night-EWY exits only on nights <= -3 % (owner, issue #143 / #141, version 0.2.8)
- **Owner decision:** "-3% 이하인 날에만" (answer to the 0.2.7 decision line).
- **기존 기준 (2026-11-22, 0.2.6 INFERRED):** the pre-emptive exit applied on ANY down night (the stop-distance test was the only condition). That interpretation is superseded.
- **Now active:** the held AT/MA/MarketTrend stock pre-emptive exit is planned only when the verified EWY US-session change is <= -3.0 % (the same threshold as the 30-minute buy block) AND current price x (1 + change) <= the nearest stop. Decision 07:55, 08:00 NXT, then KRX open..+30 min — unchanged. GMO D+1 ceiling still uses any verified night value.
- **Verification:** SOURCE_FIXED + local regression; CI and device not verified.

## 2026-11-22: NEW LEGACY sheet 11.22 — market regime and per-regime buy settings (owner, issue #144, version 0.2.9)
- **Owner instruction (NEW_LEGACY11.18_rev1.xlsx, sheet 11.22, red = added, no blue = nothing deleted):**
  - C11 (all strategies, stock and coin): detect UP / DOWN / SIDEWAYS markets. UP: buy whether the score is below 0 or not; delete the former "score <= -10 -> per-symbol limit" rule. DOWN / SIDEWAYS settings are per strategy.
  - D6 MA and D7 MarketTrend: DOWN: score >= -10 -> 50 % of the per-symbol limit, -20 <= score < -10 -> 70 %, < -20 -> 100 %, >= +5 at the BUY -> no new buy. SIDEWAYS: >= -10 -> 50 %, < -10 -> 100 %, >= +10 -> no new buy.
  - D5 reference bar: for each timeframe, if any of the 10 bars ending with the reference bar (reference included) rose +20 % or more, do not buy.
- **기존 기준 설정일 / 기존 기준:** 2026-11-21 (0.2.0): MA and MarketTrend use HALF the per-symbol limit while the score is <= -10, otherwise full; no score-based block. 2026-11-18: reference-bar BUY needs a fresh strictly positive score.
- **새 지시와 충돌하는 부분 / 예상 결함:** the 0.2.0 rule is explicitly replaced (owner red text). Sizing becomes inverted in DOWN/SIDEWAYS markets (weaker score -> larger buy). MA/Trend make no new buy in a DOWN market from score +5 and in a SIDEWAYS market from +10. In an UP market AT may buy with a negative or unknown score. Close-betting ("기존 방식 유지") and supply-demand do not use the score -> unchanged.
- **Now active:** regime from completed daily bars, stock KOSPI (Yahoo ^KS11) and coin Upbit KRW-BTC. Top-ups of an already filled entry use the factor and are never blocked. A position bought with a negative score in an UP market is not subject to the "bought while positive, score < -10 -> sell" AT exit.
- **Interpretations recorded as assumptions (INFERRED, owner may correct):**
  - UP = close > MA20 > MA60 and MA20 higher than 5 bars ago; DOWN = close < MA20 < MA60 and MA20 lower; otherwise SIDEWAYS.
  - KOSPI stands for the whole stock market (also KOSDAQ symbols).
  - An unverified regime uses the SIDEWAYS table; an unknown score never reduces or blocks.
  - "+20 % rise" = close >= 1.20 x previous close (HTS 등락률), not intrabar high.
  - An unreadable stock bar window does not block.
- **Verification:** SOURCE_FIXED + local regression; CI and device not verified. Live check 2026-11-22: KOSPI DOWN (as of 11-20), BTC DOWN (as of 11-21).

