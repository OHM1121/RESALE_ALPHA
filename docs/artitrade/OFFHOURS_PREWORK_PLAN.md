# ARTITRADE - quiet-hours work (rule R16)

Quiet hours = after the NXT close (20:00) until before the next trading day's preparation (07:50). Nothing here decides or sends an order.

| # | Task | Where | When | Status |
|---|---|---|---|---|
| 1 | Complete the finished regular-session candles (5m, 30m, 60m, day) of the trading day that the live feed missed (`INSERT OR IGNORE`, vendor checks, no signals) | `offhours_prework_v0530` | trading day 20:05 until 07:45 of the next morning, resumable, <= 1,500 requests, yields to the live feed | **0.5.3** |
| 2 | Supply-demand D+1 plans and the recorded split-buy ladder from the completed D data and the EWY value | `Preplanner.presetup` | trading day 06:00 until the preparation window (07:50 / 08:50), once the EWY value is VERIFIED | **0.5.3** |
| 3 | Entry context (MA above / below, slopes, trend) of sold positions filled in afterwards from complete bars | sidecars of the three stock wallets | after close | Open: the record lives in three different sidecars (`rr_size_v0340.record`, state / plan / payload); needs its own change |
| 4 | Coin entry-context candle REST calls at the BUY (0.4.3) | `buy_record_v0430` | coins never close | Open: needs a quiet-hours design for 24 h markets |
| 5 | Post-exit-high research records, market-wide daily statistics warm-up (0.4.2 memo), daily-frame audit | `post_exit_high_v053`, `market_ctx_memo_v0420`, `d_daily_revision_evidence` | after close | Open: intraday cost not measured yet |
| 6 | NXT eligibility snapshot loaded before 07:50 | `nxt_eligibility` | before 07:50 | Open: to verify |

Not moved on purpose: the market score, signal evaluation, order placement, fills, exits, the live feed itself.
Open limits: combined (KRX + NXT) candles are not completed (0.4.4 stage 2 pending); a phone that is off overnight runs nothing until it is on again before 07:45.
