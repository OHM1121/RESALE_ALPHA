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
