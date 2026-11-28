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
| 8 | Stocks supported until 20:00 must be able to get 5m signals and orders after the close | DONE in code: POST 16:00-20:00 (and NXT PRE) 5m / 60m signals were impossible (100 / 48 previous bars), now 3 / 2 | 0.6.5 / #184 | regular-session 5m orders still expire at 15:30 on purpose (the POST session has its own references); device verification |
| 9 | Complete KRX + NXT integration | PARTIAL: stage 1 data collection (0.6.0), stage 2 step 1 after-hours signals and step 2 day-bar alignment (0.6.5); step 3 (strategies decide on combined bars) NOT switched on purpose | 0.6.5 / #184 | readiness report must reach >= 90 % of managed stocks with 5 days of combined bars; trend label differs for 2 of 11 stocks on the device export |
| 10 | Audit held-position exit delivery delay for all strategies and stocks | NOT DONE | - | AT / MA / MarketTrend / coin held positions share the heavy tick consumer |
| 11 | Explain "청산 후 연구 기록, 시장 통계 예열, 일봉 감사, NXT 자격 사전 적재: 장중 비용을 아직 측정하지 못했습니다" | NOT DONE | - | plain-Korean explanation owed |
| 12 | Clear vendor in-memory retry queues | PARTIAL: counted; the daily case backlog is processed (see 5) | 0.5.9, 0.6.1 | frame retries of 5m/30m/60m are session bound and expire |
| 13 | Upbit / MA / MarketTrend wallet fill audit | NOT DONE | - | - |
| 15 | Compute and record the stock market score after the close (16-20h) | DONE in code (history only; the buy gate is unchanged) | 0.6.4 / #183 | owner decision: should the AT gate use it; device verification |
| 16 | Harim Holdings (003380) first take-profit | STOPPED by the owner on 2026-11-27 ("작업중지"); delivered earlier: 0.5.5-0.5.7 (#171-#173), never verified on a device | - | nothing is worked on until the owner resumes it |
| 17 | Use the after-hours score for the reference-bar buy permission exactly like the regular session ("똑같이 써"); explain "매수 게이트" in Korean without English | DONE in code (explained in the report) | 0.6.5 / #184 | device verification |
| 18 | Are all stock strategies part of the after-hours session? | ANSWERED from code (0.6.5 state, superseded by rows 21-24): only the reference-bar strategy enters in PRE / POST; close-betting buys only 15:10-15:20; MA and supply-demand enter only in continuous regular time; MarketTrend regular only | 0.6.5 / #184 | owner decision: should MA, supply-demand, MarketTrend also enter after hours |
| 19 | Why does the after-close work not run (screenshots: waiting; paused after another Wi-Fi) | DONE in code: environment errors paused it 30 minutes and burnt attempts; now 5 minutes, reason shown, nothing burnt; card explains the wait until 20:05 | 0.6.5 / #184 | device verification |
| 20 | KRX closing auction 15:20-15:30: frozen quote, no matching, single price at 15:30 | DONE (owner 2026-11-28: no fill wanted): no matching / decisions 15:20-15:30; a silent feed is normal; not counted as missing candles (0.6.7) | 0.6.5 / #184, 0.6.7 / #186 | device verification; unobserved minutes stored before 0.6.7 keep the old +10 per night |
| 21 | MA stock: new entries after hours too, reflect in the legacy workbook | DONE in code + workbook sheet 11.27 | 0.6.6 / #185 | device verification |
| 22 | MarketTrend: trade after hours | DONE in code (30m / 60m grid through POST; day frames unchanged) | 0.6.6 / #185 | device verification; no replay of entries |
| 23 | Supply-demand (GMO): trade after hours | DONE in code (6 sites) | 0.6.6 / #185 | device verification |
| 24 | After-hours reference history = one KRX+NXT series ("KRX 것도 보면 되잖아") | DONE in engine patch + bar copy | 0.6.6 / #185 | 5m / 60m after-hours signals still rare; device verification |
| 25 | Readiness 90 %: proceed without asking | PARTIAL: bars continuous across sessions, nightly combined fill + report continue; strategies' own decisions on official combined bars not switched | 0.6.6 / #185 | readiness number from `combined_vs_regular` report each night |
| 26 | 15:20-15:30 must not count as missing candles ("누락봉으로 치지 말라") | DONE in code (engine: 380 minute day, no minute repair there; expected candles already end at 15:15 / 15:00) | 0.6.7 / #186 | device verification |
| 27 | Why is the combined candle not complete | EXPLAINED + fixed delays: fill had no own thread and the readiness report froze at the first turn of the night | 0.6.7 / #186 | strategies deciding on the combined candles (needs live KRX+NXT merged candles) NOT done |
| 28 | Remove the 3 second fresh-trade rule from ALL strategies (stock and coin), unify them | DONE in code: one rule module used by the engine, 16 overlays and the coin engine; manual sales keep 3 s | 0.6.8 / #187 | device verification |
| 29 | Sell without the 3 second rule when the target price has the quantity | DONE in code (book quantity decides: MA / GMO / MarketTrend / coin; print-based exits of the reference bar accept a print up to 115 s old while the line is alive) | 0.6.8 / #187 | device verification; reference-bar exits do not check book quantity (print based, as before) |
| 30 | Coin MarketTrend: no new buy since the legacy patch - market or code? | ANSWERED + fixed: code (4 qualified signals in 2 days never bought: late candle, print consumed early, 2 missed fixed freshness tests) | 0.6.9 / #188 | device verification |
| 31 | Other coin strategy / program errors | PARTIAL: coin book backlog (write lock per book for unheld markets) fixed; 0.6.8 held-exit stale-book blocks confirmed gone in the export; network / DNS errors are the device environment; vendor `process_book` still one write transaction per book | 0.6.9 / #188 | measure the book wait after the install; decide on the vendor path |
| 14 | `ARTITRADE_REAL_MODE=1` blocks the GMO D+1 plan | OPEN (unchanged) | - | owner decision |

Update this table in the same commit as the work (the governance docs of the version).
