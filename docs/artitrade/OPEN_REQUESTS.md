# ARTITRADE owner requests: status ledger

Why this file exists (owner, 2026-11-27: "the more you work the worse you get"): in a long session an agent loses the exact wording of earlier requests, answers the part it remembers and silently drops the rest.
Three drops happened on 2026-11-27 (stock history was not treated like coin history, the 15:30 five-minute order rule was waved off although the owner wants orders until 20:00, "integration" was reported as a stage without saying it was incomplete).
Rule for every agent: **before the first action of a turn, copy each sentence of the owner's message into this table (one row per sentence); before the report, check every row against the delivered work; the report states every row as DONE (version, evidence), PARTIAL (what is missing) or NOT DONE (why).** Nothing is left out of the report because it is awkward.
Do not put private ledgers, device logs or account data here (public repository).

| # | Owner request (short) | Status | Version / issue | What is still open |
|---|---|---|---|---|
| 1 | After the close: fill BUY-time MA above/below, slope, trend for positions (stock) | DONE (stock) | 0.6.0 / #178 | device verification |
| 2 | Same for coin, and record what does not need real time after the close | DONE (BUY-time context, score with age; coin RR of MA / MarketTrend without a stored value stays empty) | 0.6.2 / #181 | other real-time records (price path, follow-ups) not reviewed; device verification |
| 3 | Complete combined (KRX+NXT) candles | PARTIAL: collected into a separate store | 0.6.0 / #179 | strategies do not read it; orders in NXT hours (see 8) |
| 4 | Weekend: gentle pace, detailed work, fill missing candles, verify managed data, clear the delayed backlog | DONE in code | 0.5.9, 0.6.0, 0.6.1 / #177, #179, #180 | runs only while the app is running; device verification |
| 5 | Clear the delayed daily-bar verification; no pending orders created after the close | DONE in code | 0.6.1, 0.6.3 / #180, #182 | device verification |
| 6 | Check whether the official daily candle includes NXT | DONE: it does (evidence in #182) | 0.6.3 / #182 | decide the basis of day bars vs intraday bars (stage 2) |
| 7 | Stock history must be managed like the coin history | PARTIAL: audit numbers in #182; weekend audit fills holes; BUY-time context done for stock AT (34 of 34 on the device export) | 0.5.9, 0.6.0, 0.6.2 | combined basis, day-bar basis mix (860 corrections taken at different times) |
| 8 | Stocks supported until 20:00 must be able to get orders after 15:30 (also five-minute references) | NOT DONE | #182 | stage 2 of the integration: vendor FIX13 rule expires KRX 5m orders at 15:30; 17 vendor files assume 09:00-15:30 |
| 9 | Complete KRX + NXT integration | NOT DONE (stage 1 only) | #179, #182 | stage 2 plan in #182 |
| 10 | Audit held-position exit delivery delay for all strategies and stocks | NOT DONE | - | AT / MA / MarketTrend / coin held positions share the heavy tick consumer |
| 11 | Explain "청산 후 연구 기록, 시장 통계 예열, 일봉 감사, NXT 자격 사전 적재: 장중 비용을 아직 측정하지 못했습니다" | NOT DONE | - | plain-Korean explanation owed |
| 12 | Clear vendor in-memory retry queues | PARTIAL: counted; the daily case backlog is processed (see 5) | 0.5.9, 0.6.1 | frame retries of 5m/30m/60m are session bound and expire |
| 13 | Upbit / MA / MarketTrend wallet fill audit | NOT DONE | - | - |
| 14 | `ARTITRADE_REAL_MODE=1` blocks the GMO D+1 plan | OPEN (unchanged) | - | owner decision |

Update this table in the same commit as the work (the governance docs of the version).
