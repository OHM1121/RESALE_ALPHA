# ARTITRADE version behavior history: what broke, what is normal

Purpose (owner 2026-11-28): "각 버전별로 어떤 오류가 발생했고 어떤 건 정상작동인지 깃허브에서 이력관리를 하고, 업데이트할 때 참고해서 작업". This file is **for the agent that updates the app**, not a report for the owner.
It is read BEFORE every change (together with `AGENTS.md`, `USER_STANDING_RULES.md`, `DEPENDENCY_MAP.md`, `version_issues.json`) and extended AFTER every device export is analysed and after every delivery (rule R17).
Public repository: only aggregated counts and market symbols, never ledgers, balances, account data or raw device logs.

## 1. How to use it (R17)
1. Before touching a module: find it in section 4 and read what already went wrong there and what looks like an error but is normal.
2. Before calling a reason code a defect: look it up in section 3 (NORMAL). A reason is a defect only when it contradicts the owner's workbook / `USER_ACTIVE_POLICY.md` or blocks a qualified signal.
3. After every export analysis add a row to section 2 (what was measured on the device, with the version that was running) and, when a new normal behaviour or a new defect pattern is found, to section 3 / 5.
4. After every delivery fill the "device" column when the owner's next export confirms or contradicts the fix (never claim a device result from CI).
5. Section 6 holds the device baseline numbers; compare the next export with them and write the result into section 2.

## 2. Device measurements by version (what the exports showed)
| Version running | Export | Measured on the device | Meaning |
|---|---|---|---|
| 0.3.6 - 0.6.7 (before the 0.6.8 install, up to 2026-11-28 ~16:00) | coin export 2026-11-28 22:06 | Held coin exits blocked by a stale book: `MA_V014_HELD_EXIT_BLOCK` 14,797 rows in two days, about 400 an hour (`BOOK_STALE` 13,454). Coin MA final stage blocked by stale / missing tick or book in 77-83 % of its decisions every day (11-24 .. 11-28 15:59). 11-27 07:14 -> 11-28 00:19: no coin MA buy (17 h). | The fixed 3 s / 10 s freshness tests, not the market, hid most coin MA / exit decisions. |
| 0.6.8 (from ~2026-11-28 16:00) | same export | `BOOK_STALE` exit blocks fell to about 1 an hour; coin MA stale share 82 % -> 24 %; coin MA entries 0.3 -> 1.2 an hour (7 entries 16:44-21:29). | Fix of #187 works on the device. |
| 0.6.8 (same period) | same export | Coin book path: `UPBIT_WS_BOOK_BACKLOG` 4,088 rows, a book waits 3-4 s, `proc_avg_ms` 140, `proc_max_ms` 10,994; profile of 25 books: partial-entry top-up 6.7 s, NL1118 coin exits 3.1 s, vendor `process_book` 2.7 s, MA 0.9 s. | Three overlay layers took a SQLite write lock for every book of every market (fixed in 0.6.9, #188). |
| 0.3.6 - 0.6.8 | same export | Coin MarketTrend: no buy since 11-26 09:00. Replay of the 11.20 rule on Upbit candles: at least 4 qualified signals in 2 days (DOS 60m 11-27 11:00, ETH 30m 11-28 10:30, USD1 30m 19:00 / 60m 21:00), none bought. | See section 4.4 and #188 (fixed in 0.6.9). |
| 0.6.0 - 0.6.7 | stock export 2026-11-27 15:09 | Combined (KRX+NXT) candles: only 11 of 511 managed stocks (2 %) - taken BEFORE the first quiet window with the fill code. | Not a regression: the report was a once-per-day snapshot (fixed 0.6.7). |

## 3. NORMAL behaviour (do not "fix")
| Observation | Why it is normal | Since |
|---|---|---|
| `MA_PREWATCH_*` reasons (`NO_VERIFIED_TICK`, `SMA_HISTORY_REQUIRED`, `SHORT4_NOT_BELOW_120`, ...) | Observation-only window 30..10 s before a candle close; never an order. | 0.0.5 |
| `UP_RULE_*` reasons (`SHORT4_NOT_BELOW_120`, `NO_SIMULTANEOUS_SHORT4_UP_120_DOWN`, `UPWARD_ALIGNMENT_ALREADY_EXISTED`) | The MA rule was evaluated and is not met = market condition. | 0.6.7 policy |
| `UP_RULE_SMA_HISTORY_REQUIRED` for thinly traded coins (about 176 coin-frame pairs in 6 hours) | A coin needs 121 closed candles without gaps; Upbit omits candles of minutes without trades; no candle is invented. | 0.6.7 policy |
| `MA_REGIME_NO_NEW_BUY_V0290` / `SYMBOL_REGIME_SCORE_NO_NEW_BUY_V0290` (21 rows in two days) | Owner workbook sheet 11.22: DOWN regime and score >= +5, SIDEWAYS and score >= +10 -> no NEW buy. | 0.2.9 |
| `COIN_LISTED_UNDER_3_MONTHS_V0430` | Owner pink text 2026-11-27: coin listed under 3 months is not bought by MA. | 0.4.3 |
| Coin / stock score >= +40 blocks coin buys | Owner pink text 2026-11-27 (0.4.3). Coin score >= +40 was only 0-10 % of the time in 11-20 .. 11-28, so it is not the reason for missing buys. | 0.4.3 |
| `ALREADY_OWNED_OR_PENDING` for a coin another strategy holds (e.g. MA holds ETH) | One position per coin across strategies (0.0.15 global position id). | 0.0.15 |
| `RSI30_OBSERVED_NOT_AN_ORDER` | A watch was armed (RSI20 <= 30); the order needs the first completed bar above the Bollinger middle later. | 0.1.8 |
| Coin MarketTrend buys 0 for 1-2 days | Happened already in the first week: 11-22 07:01 -> 11-24 01:05 (42 h) without a buy. It depends on the market (crash days arm many watches: 11-21 492 observations -> 13 buys). | 0.2.x |
| `OPERATIONAL_WATCH_HISTORY_MISSING` in the first seconds after a candle boundary (coin, ~2,000 rows a day) | The just-closed candle arrived 42-64 s late (median) every day from 11-21 on. Latent defect fixed in 0.6.9 (held / armed pairs fetched first); expect it to drop sharply after 0.6.9. | 0.2.x |
| `NETWORK_COOLDOWN`, `ConnectionError`, `NameResolutionError`, `UPBIT_WS_RECONNECT` bursts (e.g. 11:09-11:16 on 11-28) | Phone network / DNS; the app retries. | always |
| `mserver_monitor` `ConnectException 127.0.0.1:8765` (1,137 rows since 11-23) | The optional MSERVER app is not installed; probing already backs off (2.5 s .. 30 s). | 0.3.x |
| KRX 15:20-15:30: no price movement, no trade, feed looks silent; 15:30-16:00 nothing | Closing single-price auction and the gap before the after-hours session. Never a missing candle (0.6.7). | 0.6.5 / 0.6.7 |
| NXT / after-hours reference signals rare for 5m / 60m | The volume must beat the unified regular + after-hours series (strict rule, 0.6.6). | 0.6.6 |
| `UPBIT_PUBLIC_API`, `UPBIT_CANDLE_PARTIAL`, `UPBIT_SHADOW_1M_UNAVAILABLE` warnings | Consequences of the network outages above. | always |
| 43 AT pending on negative stock score | Positive-score policy of the reference bar. | 0.0.9 |
| Manual sales keep the 3 s rule | The price on the screen is the price sold (0.6.8 left them out on purpose). | 0.6.8 |

## 4. Per-module memory (read before editing the module)
### 4.1 Freshness rules (stock and coin)
* 0.6.8 replaced ~70 fixed "3 s book / print" and "10 s coin print" tests with `fresh_rule_v0680` (alive line: book <= 2 min, print <= 115 s; dead line: 3 s). The sweep MISSED `market_trend_1118_runtime._entry_window` (form `>(10 if market=="coin" else 3)`), found by the device data and fixed in 0.6.9.
* The overlay scan in `test_v0680_one_freshness_rule.py` must be extended whenever a new spelling of a fixed age test is found (it now also catches `>(10 if market ... else 3)`).
* New freshness tests must use `fresh_rule_v0680`; never add a new numeric 3 / 10 s comparison.
### 4.2 Coin order books (`d_upbit`, `coin_partial_entry_v094`, `reference_1118_runtime`, `ma_v067_coin_runtime`)
* Every layer that wraps `process_book` runs for every book of every market. A layer must first check whether the ledger holds a position / order in that market (`coin_book_gate_v0690.nobody_holds`) before it takes a write transaction. The vendor `CoinLedger.process_book` still takes one `BEGIN IMMEDIATE` per book (open item: measure `UPBIT_WS_BOOK_BACKLOG` after 0.6.9).
### 4.3 Coin MA (`ma_v067_coin_runtime`)
* Decisions exist only for coins that had a ticker event in the 30..2 s before a candle close (`due` in the tick worker); a coin without an event in the 10..2 s window is reported by the watchdog as `MA_10_TO_2_SECOND_DECISION_OUTCOME_NOT_RECORDED` (1,035 rows in 6 hours after 0.6.8, was `MA_NO_FRESH_OBSERVED_TICK...` before). Open item: a clock-driven poke at close - 6 s for the top-ranked coins (not built).
* `NATIVE_BOOK_PROCESSING_TIMEOUT` (259 rows) = a held-exit pass longer than 2.5 s because of slow SQLite; expect it to fall with the 0.6.9 book path change.
### 4.4 Coin MarketTrend (`market_trend_runtime_v043`, `market_trend_1118_runtime`, `new_legacy_1118/1120_policy`)
Entry rule (owner workbook 11.20): RSI20 <= 30 watch -> the FIRST completed bar after the watch that closes above its Bollinger(20, 1.5) middle -> middle slope inside (-15, +20) degrees -> fresh quote / book -> buy. Frames 30m, 60m, day (5m retired).
Version line (code that decides a coin entry; device days from the 11-28 export):
| Day | Version(s) running (approx.) | RSI<=30 watches armed | Buys | `WATCH_HISTORY_MISSING` | Candle arrival lag after a boundary (median) |
|---|---|---|---|---|---|
| 11-21 | 0.2.x | 492 | 13 | 1,951 | 53 s |
| 11-22 | 0.2.7 - 0.3.1 | 35 | 1 | 1,783 | 82 s |
| 11-23 | 0.3.2 - 0.3.6 | 36 | 0 | 2,005 | 42 s |
| 11-24 | 0.3.6 | 39 | 2 | 2,047 | 47 s |
| 11-25 | 0.3.6 | 173 | 1 | 1,964 | 50 s |
| 11-26 | 0.3.6 | 31 | 6 | 2,113 | 64 s |
| 11-27 | 0.3.7 - 0.6.7 | 24 | 0 | 2,068 | 42 s |
| 11-28 | 0.6.8 -> 0.6.9 | 140 | 0 | 2,312 | 43 s |
Findings (2026-11-28, #188): the entry code (`_entry_window`, `_prefetch`, `_confirmed_recent`) is byte-identical in the decisive parts since the first export (3ec9ca2, 11-21): the print consumed before the candle, the two fixed 10 s / 3 s tests and the 45 s refresh lap were latent from the start; 0.3.7 only added `_signal_blocked` logging (so blocked signals became visible, not new); 0.4.3 added the coin score +40 block (rarely active); 0.6.6 touched only stock paths. **No version regression was found.** The stop of buys on 11-27/28 = few qualified signals (5 in two days) of which most were thin coins (USD1, DOS) and one ETH held by MA, hitting the latent defects. Liquid coins bought normally on 11-26 (+44..65 s after the boundary).
* Open items: after 0.6.9 check `WATCH_HISTORY_MISSING` per day (expect far below ~2,000), the candle lag (expect < 10 s for held / armed pairs) and whether qualified signals now buy.
### 4.5 Stock after-hours, closing auction, combined candles
* 15:20-15:30 is never a missing candle (380 minute observation day, no repair of those minutes, 5m expected candles end at 15:15, 30m at 15:00).
* The combined (KRX+NXT) fill runs on its own thread; the readiness report repeats every 30 minutes until the fill of the day is finished. Strategies still decide on regular candles; live combined candles are not built.

## 5. Defect patterns that came back (check them on every change)
1. A fixed numeric age test copied into many modules (the 3 s rule): scan for new spellings, not only the known ones.
2. Consuming an input (print, event key, queue item) before the prerequisite for using it exists: the input is then lost for good (quiet coins, late candles).
3. A silent `continue` without a decision record: the export shows nothing and the owner sees "no buys". Every qualified-but-not-bought case must leave a reason row.
4. A database write transaction (`BEGIN IMMEDIATE`, `CREATE TABLE IF NOT EXISTS`) on a per-message path for markets without a position.
5. A once-per-day measurement taken at the start of a long job (readiness report, audit): report repeatedly until the job is final.
6. A rule applied to "all strategies" by pattern search: verify with the device export that the blocked-reason counts really fell (0.6.8 did for MA / held exits; MarketTrend was missed).
7. Test fixtures that open a position through the code path under test make caches (0.5 s open-market cache) look stale: invalidate on the event that creates the state, not only by time.
8. Offline replay of an owner rule on public candles (Upbit REST) is a valid way to tell "no signal" from "signal lost"; use it before answering "market or code?".

## 6. Device baseline (export 2026-11-28 22:06, app 0.6.8) - compare the next export with these
| Metric | Baseline |
|---|---|
| Coin MA final-stage decisions blocked by stale / missing tick or book | 82 % before 16:00, 24 % after the 0.6.8 install |
| `MA_V014_HELD_EXIT_BLOCK` `BOOK_STALE` | about 400 an hour before, about 1 an hour after |
| `UPBIT_WS_BOOK_BACKLOG` | 29-30 rows an hour, book wait 3.5-4.3 s, `proc_avg_ms` 140, `proc_max_ms` 10,994 |
| Coin MarketTrend `OPERATIONAL_WATCH_HISTORY_MISSING` | about 2,000 a day (862 in the 6 h after 16:00) |
| Coin MarketTrend buys | 0 since 2026-11-26 09:00 |
| Coin MA entries | 14 on 11-28 (7 after 16:00) |
| Candle arrival lag after a 30m / 60m boundary | median 42-64 s |

## 7. Register of versioned issues from 0.2.0 on (generated from `version_issues.json`; the file stays the source)
| Version | Id | Issue | Title | Status | CI | Device |
|---|---|---|---|---|---|---|
| 0.2.0 | SZ-0200-01 | #132 | Market score <= -10 halves the per-symbol buy limit for MA and MarketTrend (stock+coin) | SOURCE_FIXED | PENDING | UNVERIFIED |
| 0.2.0 | UI-0200-02 | #133 | Preload shows 0% then opens; generic buy basis; no stop-loss / first profit target on holdings | SOURCE_FIXED | PENDING | UNVERIFIED |
| 0.2.1 | UI-0210-01 | #134 | MA (and every strategy) execution history shows sells without their buy | SOURCE_FIXED | PASSED | UNVERIFIED |
| 0.2.1 | UI-0210-02 | #135 | "매수 근거" drawer closes by itself a few seconds after opening | SOURCE_FIXED | PASSED | UNVERIFIED |
| 0.2.2 | RT-0220-01 | #136 | Engine stopped at 22:49 and stayed stopped (heartbeat 7,000 s old) with the screen open; non-crash OS process exits were not logged | SOURCE_FIXED | PASSED | UNVERIFIED |
| 0.2.3 | FD-0220-02 | #137 | Coin order-book frames queued without coalescing: full queue dropped the newest frames, worker processed minutes-old books (gaps 2.5-9 min, BOOK_STALE) | SOURCE_FIXED | PASSED | UNVERIFIED |
| 0.2.4 | UI-0240-01 | #138 | Line charts coloured by sign; foldable dashboard markets and holdings strategies; confirmed vs holding profit; categorised holding card with price map | SOURCE_FIXED | PASSED | UNVERIFIED |
| 0.2.5 | UI-0250-01 | #139 | Dashboard cleanup, merged foldable time-frame cards, obvious fold state, MarketTrend win rate never computed, next profit target after first sell | SOURCE_FIXED | PASSED | UNVERIFIED |
| 0.2.6 | NF-0260-01 | #141 | Overnight EWY rules: no AT/MA/Trend buys 09:00-09:30 after <= -3 %, pre-emptive stop exits, supply-demand D+1 EWY-adjusted ceiling | CI_VERIFIED | PASSED_RUN_4 | UNVERIFIED |
| 0.2.6 | ST-0260-01 | #140 | Stock MA and stock MarketTrend never reached a BUY decision (rotating subscriptions + live-print-only RSI watch start) | CI_VERIFIED | PASSED_RUN_4 | UNVERIFIED |
| 0.2.7 | CW-0270-01 | #142 | Stock close-betting never bought: its single realtime queue drops frames older than 3 s under load (books go stale) | CI_VERIFIED | PASSED_RUN_4 | UNVERIFIED |
| 0.2.8 | NF-0280-01 | #143 | Pre-emptive night-EWY stock exit only on SEVERE nights (EWY <= -3.0 %), no longer on every down night | CI_VERIFIED | PASSED_RUN_4 | UNVERIFIED |
| 0.2.9 | LG-0290-01 | #144 | Market regime UP/DOWN/SIDEWAYS: MA/Trend regime sizing + no-new-buy, AT up-market exception, AT +20 % surge-window filter | CI_VERIFIED | PASSED_RUN_4 | UNVERIFIED |
| 0.3.0 | RG-0300-01 | #145 | Regime judged on each bought symbol on its own timeframe instead of KOSPI/BTC daily | CI_VERIFIED | PASSED_RUN_4 | UNVERIFIED |
| 0.3.1 | RT-0310-01 | #146 | Python engine froze for 55 min with the app alive; UI-watchdog feed restarts could not run behind the synchronized bridge monitor | CI_VERIFIED | PASSED_RUN_4 | UNVERIFIED |
| 0.3.2 | GM-0320-01 | #147 | Supply-demand D=11-20 candidates appeared on D+1 from the D+1 intraday ranking; pending card had no buy price and a wrong reason | CI_VERIFIED | PASSED_RUN_4 | UNVERIFIED |
| 0.3.3 | UI-0330-01 | #148 | Read-only ledger view build held the bridge monitor for 100-500 s: heartbeat blocked, 6 supervisor restarts, Upbit ping timeouts | CI_VERIFIED | PASSED_RUN_4 | UNVERIFIED |
| 0.3.4 | ST-0340-01 | #149 | Coin GEN return-to-entry armed after +1.5 %; coin GEN/MA/Trend buy reduced only when gain/loss <= 0.5 | CI_VERIFIED | PASSED_RUN_4 | UNVERIFIED |
| 0.3.5 | ST-0350-01 | #150 | Reward/risk buy size extended to stock AT / MA / Trend; unverifiable lookback keeps the fixed amount | CI_VERIFIED | PASSED_RUN_4 | UNVERIFIED |
| 0.3.6 | ST-0360-01 | #151 | KRX 0D quantity "0" dropped the book (AT 1 share, GMO 3 of 39); EWY night value lost on restart; heartbeat on the coin ledger lock; card facts lost; MarketTrend daily wat | CI_VERIFIED | PASSED_RUN_4 | UNVERIFIED |
| 0.3.7 | ST-0370-01 | #152 | Stock MA retired every v067 candidate (MA_V061_BAD_OPEN_WINDOW); reference stop = BUY price - bar range; MarketTrend wording; stock Trend silent drops; per-position RR an | SOURCE_FIXED | PENDING | UNVERIFIED |
| 0.3.8 | ST-0380-01 | #153 | Second supply-demand holding vanished from the integrated dashboard / holdings (all GMO positions have id -1) | SOURCE_FIXED | PENDING | UNVERIFIED |
| 0.3.9 | CF-0390-01 | #154 | Coin price feed down 182 min (external network); held-exit BOOK_STALE 98k from a saturated book worker (measurement added) | SOURCE_FIXED | PENDING | UNVERIFIED |
| 0.4.0 | SF-0400-01 | #155 | Kiwoom stock WebSocket drops every few minutes; ledger gate held up to 210 s (measurement) | SOURCE_FIXED | PENDING | UNVERIFIED |
| 0.4.1 | SG-0410-01 | #156 | GMO fills not grouped by position (placeholder id -1, unlinked fills); GMO D+1 candidate never evaluated at the open (tick pipeline starved) | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.4.2 | MC-0420-01 | #157 | Market-wide daily statistics recomputed per symbol/frame under the SQLite ledger gate (tick expiry, stale close-betting book) | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.4.3 | BR-0430-01 | #158 | Coin score +40 / negative-UP / listing blocks; trend and market score recorded at every BUY; trend unverified fixed | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.4.4 | AV-0440-01 | #159 | Stock bars on the combined KRX+NXT flow: stage 1 read-only measurement | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.4.5 | FR-0450-01 | #160 | Stock tick feed dead for most of every session; reconnect pause, background load and missing feed telemetry; supply-demand BUY filled 3 of 39 | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.4.6 | UI-0460-01 | #161 | Screen refresh held the trading ledger gate (22 of 60 holds >= 5 s); supply-demand D+1 order only placed on a 0B print | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.4.7 | GMO-0470-01 | #162 | One print's displayed ask size was matched twice by the D+1 path; UI read-only snapshot spanned several transactions | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.4.8 | GMO-0480-01 | #163 | The displayed size of one 0D snapshot filled again for every print within the 3 s book validity | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.4.9 | GMO-0490-01 | #164 | Supply-demand PAPER BUY needed a 0B print even when two fresh 0D snapshots showed the ask within the limit | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.5.0 | PARITY-0500-01 | #165 | Paper fills took only half of the displayed size (stock wallets) and paid one tick + 0.05 percent above the displayed ask (GMO) | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.5.1 | GMO-0510-01 | #166 | The D+1 entry was one aggressive order up to the first ask + 2 ticks with three ceiling scenarios | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.5.2 | GMO-0520-01 | #167 | A rung at or above the start price was sized at the rung price, so less than the intended share of the limit was invested | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.5.3 | OFFHOURS-0530-01 | #168 | Remaining split-buy orders could fill after an exit started; D+1 setup and candle repair were done at or after the open | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.5.4 | UI-0540-01 | #169 | The BUY-time reward / risk was recorded in the position sidecars but the hub rows and the history card did not carry it | SOURCE_FIXED | PENDING | UNVERIFIED |
| 0.5.5 | GMO-0550-02 | #171 | Held supply-demand position: prints at or above the first target never reached the exit engine (silent drop in the single tick consumer) | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.5.5 | MT-0550-01 | #170 | Stock MarketTrend asked the broker for every symbol x frame at the open and stayed without history when the calls failed | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.5.6 | GMO-0560-03 | #172 | Paper first take-profit waited for the app to see a print and then sold at the best bid minus 3 ticks; a real take-profit is a resting limit sell at the target | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.5.7 | GMO-0570-04 | #173 | A resting take-profit that the exchange would have filled while the app was off, disconnected or restarting was never recovered | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.5.7 | UI-0570-05 | #174 | Supply-demand dashboard counted every split fill row as a buy (13 for 2 positions) | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.5.7 | UI-0570-06 | #175 | The quiet-hours data collection ran invisibly; the owner could not tell whether the collect-and-store structure works | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.5.8 | FEED-0580-01 | #176 | A quiet stock (no trade, no book change) was treated as stale by the 3 s stream-message rule, so MA, close-betting and MarketTrend never evaluated or never bought it | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.5.9 | OFFHOURS-0590-02 | #177 | The quiet-hours work covered only the last day and stopped on Saturday morning; weekend time was unused and older holes were not checked | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.6.0 | BARS-0600-02 | #179 | The combined 5m/30m candles were only probed for ~10 stocks; the quiet hours did not complete them; the weekend audit was tight | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.6.0 | CTX-0600-01 | #178 | Stock positions bought before 0.3.7 or with short bars had no or partial entry_ctx | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.6.1 | DAYREPAIR-0610-01 | #180 | Daily-bar verification never confirms: the official candle of the day changes until 20:00 and the vendor loop is off after 15:40 | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.6.2 | ENTRYCTX-0620-01 | #181 | Coin positions have no BUY-time MA / trend context, stock after-hours buys no market score, and the workbook rebuilt trend from a sparse candle table | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.6.3 | DAYREPAIR-0630-01 | #182 | The 0.6.1 daily repair could queue pending day orders at night; the official daily candle includes NXT volume and is mixed with KRX-only intraday bars | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.6.4 | SCORE-0640-01 | #183 | The stock market score fetch stops at the KRX close although stock buys happen on the NXT after-market until 20:00 | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.6.5 | STAGE2-0650-01 | #184 | KRX+NXT stage 2: after-hours 5m/60m signals were impossible; after-hours score used by the reference-bar buy permission; closing auction 15:20-15:30 no matching; after-cl | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.6.6 | AFTER-HOURS-TRADING-0660-01 | #185 | After-hours entries were blocked for MA / MarketTrend / supply-demand by explicit regular-session-only rules, and the after-hours volume history ignored the regular-sessi | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.6.7 | CLOSING-AUCTION-NOT-MISSING-0670-01 | #186 | The closing auction counted as 10 unobserved minutes every night and as repairable minutes; the combined-candle fill sat behind the shared night loop and its readiness re | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.6.8 | ONE-FRESHNESS-RULE-0680-01 | #187 | The 3 second fresh-trade / fresh-book rule sat in about 70 places; only quiet-stock buys of three strategies had been relaxed (0.5.8) | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |
| 0.6.9 | COIN-MT-ENTRY-0690-01 | #188 | Coin MarketTrend never saw the just-closed candle in time, consumed a print before the candle, kept two fixed 10 s / 3 s tests; the coin book path took a write lock per b | SOURCE_FIXED | CI_VERIFIED | UNVERIFIED |

Older versions (0.0.x, 0.1.x) are in `version_issues.json` and `ISSUE_HISTORY.md`.
