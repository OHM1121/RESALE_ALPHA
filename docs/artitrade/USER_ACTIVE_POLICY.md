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

## 2026-11-19: PAPER preset TP history catch-up MUST replay chronological exits (user clarification)
- User allows a **PAPER-only** counterfactual resting-limit stage match when
  authenticated, same-venue historical price actually exceeds the strategy's
  pre-committed TP limit after its original verified BUY. A high strictly
  greater than TP is NOT, alone, authorization to book a winning trade.
- Order all observed events **after exact source BUY timestamp** by credible
  exchange timestamp, including original hard stop, forced/full exit, expiry,
  trailing/floor stops, TP1 and TP2. If an authorized hard stop or full exit
  was reached **first**, terminate that original position's state machine;
  **never** count a later candle HIGH as a profit or resurrect the holding.
  A TP1 before a later stop only covers TP1's actual planned original share
  fraction; the remaining native shares must follow later chronological exits.
- If high and low triggers occur in the **same OHLC candle** and no original
  authenticated tick/print ordering exists, high/low alone cannot prove the
  take-profit came first: **do not create a profitable PAPER fill**. Surface
  `EXIT_SEQUENCE_UNVERIFIED` or fail closed using a separately disclosed
  conservative loss-first evaluation; never silently choose favorable path.
  Likewise, missing older bars, a trading halt, cross-venue price data or
  timestamps without monotonic ordering must not produce a profitable SELL.
- After any prior full exit, no subsequent TP is eligible. TP2 is only
  considered after its strategy's valid TP1 fill and only for verified
  remaining quantity. Never exceed initial verified BUY quantity or the
  position's original source cash, realized P&L, fee/tax accounting.
  Retry/restart must be exactly idempotent per owner+epoch+position+stage.
- Price **greater than** a pre-persisted TP limit can be a PAPER fill-model
  assumption only when actual prior chronology and order eligibility permit
  it; price **equal to** TP needs original exchange book/execution evidence.
  Never portray an inferred PAPER fill as a broker receipt or actual live trade.
  Do not retroactively add a profitable SELL to an already confirmed source
  full-stop or brokerage position. Any historical PAPER ledger correction
  requires source-position provenance, atomic cash/reason/quantity treatment,
  backup/anti-rollback and a recorded reconciliation audit.
- Apply this chronological contract to all eight separate strategy
  **PAPER** wallets with each owner's ORIGINAL individual thresholds and
  shares; it does NOT make GMO or CW adopt the NEW LEGACY 30%/35% fractions.
  Do not enable on REAL brokerage or use a forming/future candle to decide a
  past event. Preserve each native BUY/SELL source and active-session safety.
- Status at instruction time: **POLICY_SPECIFIED; chronology engine and
  device regression NOT VERIFIED**. Do not claim SOURCE_FIXED, CI_VERIFIED,
  DEVICE_VERIFIED or publish a catch-up APK until the complete chronological
  replay and all eight wallet regressions have proven this invariant.
