# ARTITRADE — Versioned issue register

The **GitHub Issues tab** is the version-by-version expandable issue history. [Open ARTITRADE issue tab](https://github.com/OHM1121/RESALE_ALPHA/issues?q=is%3Aissue+ARTITRADE). Each issue records observations, dependency impact, root cause, fix commit and validation level. This markdown is the quick index; the machine source is [version_issues.json](version_issues.json).

| Issue | Found | Function / failure | Status as of 2026-11-19 |
|---|---|---|---|
| [#53](https://github.com/OHM1121/RESALE_ALPHA/issues/53) | 0.0.5 | `for_ui` lost across 6 hub wrappers; repeated snapshot TypeError | 0.0.6 signed APK / CI pass; device unverified |
| [#54](https://github.com/OHM1121/RESALE_ALPHA/issues/54) | 0.0.5 | Chaquopy missing IANA KST in daily backup/AT | 0.0.6 signed APK / CI pass; device unverified |
| [#55](https://github.com/OHM1121/RESALE_ALPHA/issues/55) | 0.0.5 | GMO empty ledger wrongly shown as corruption | 0.0.6 signed APK / CI pass; device unverified |
| [#56](https://github.com/OHM1121/RESALE_ALPHA/issues/56) | 0.0.5 | Coin MA missing `high` caused process-book aborts | 0.0.6 source guard had a cross-strategy false block; #67 addresses regression |
| [#57](https://github.com/OHM1121/RESALE_ALPHA/issues/57) | 0.0.5 | Stock MA per-tick queue pressure | 0.0.6 signed APK / CI pass; device unverified |
| [#58](https://github.com/OHM1121/RESALE_ALPHA/issues/58) | 0.0.5 | Kiwoom DNS unavailable / official bar repair retry cascade | OPEN - environmental proof missing |
| [#59](https://github.com/OHM1121/RESALE_ALPHA/issues/59) | 0.0.5 | Upbit WS queue pressure / stale book | OPEN - unresolved |
| [#60](https://github.com/OHM1121/RESALE_ALPHA/issues/60) | 0.0.5 | End-to-end stock/coin strategy startup / buy / sell | OPEN - on-device evidence missing |
| [#61](https://github.com/OHM1121/RESALE_ALPHA/issues/61) | 0.0.6 | Retire MarketTrend 5m across markets | CI passed; on-device unverified |
| [#62](https://github.com/OHM1121/RESALE_ALPHA/issues/62) | 0.0.3 → 0.0.4 | Historic restart/quote/fill recovery handoff | Historical CI reported; device unverified |
| [#63](https://github.com/OHM1121/RESALE_ALPHA/issues/63) | 0.0.4 → 0.0.5 | UI score-plane and empty-wallet handoff | Historical CI reported; device unverified |
| [#65](https://github.com/OHM1121/RESALE_ALPHA/issues/65) | 0.0.5 → 0.0.6 | Same-signer in-place update and root-cause release candidate | Signed CI 44438752719 passed; device unverified |

| [#67](https://github.com/OHM1121/RESALE_ALPHA/issues/67) | 0.0.6 → unreleased | Coin MA special 5m source provenance confused with reference OHLC | Source fixed; device unverified |
| [#68](https://github.com/OHM1121/RESALE_ALPHA/issues/68) | 0.0.6 → unreleased | GMO 07:58 preopen monopolizes shared Kiwoom subscriptions | Source fixed; device unverified |
| [#69](https://github.com/OHM1121/RESALE_ALPHA/issues/69) | 0.0.6 → unreleased | CLOSE research DONE on a stale or future quote | Source fixed; device unverified |
| [#70](https://github.com/OHM1121/RESALE_ALPHA/issues/70) | 0.0.6 → unreleased | Duplicate Runtime.start creates duplicate Trend/PostExit/Recovery owners | Source fixed; device unverified |
| [#71](https://github.com/OHM1121/RESALE_ALPHA/issues/71) | 0.0.6 | AT official higher-frame DNS exceptions do not schedule retry | OPEN - source/core fix pending |
| [#72](https://github.com/OHM1121/RESALE_ALPHA/issues/72) | 0.0.6 → unreleased | Trend shared position dictionaries lack one locking contract | Source fixed; device unverified |
| [#73](https://github.com/OHM1121/RESALE_ALPHA/issues/73) | historical | Old v067 MA expectations contradict NEW LEGACY(11.18), 8 pre-existing failures | OPEN - baseline/policy review |

| [#75](https://github.com/OHM1121/RESALE_ALPHA/issues/75) | 0.0.6 → 0.0.7 | Same-signer stock/coin cross-strategy test APK | Signed CI passed; device unverified |

| [#140](https://github.com/OHM1121/RESALE_ALPHA/issues/140) | 0.2.5 → 0.2.6 | Stock MA / MarketTrend never reached a BUY decision (rotating 200-slot subscriptions, live-print-only RSI watch start) | 0.2.6 CI verified (run 44982710034); device unverified |
| [#141](https://github.com/OHM1121/RESALE_ALPHA/issues/141) | 0.2.6 → 0.2.8 | Overnight EWY rules: AT/MA/Trend no buys 09:00-09:30 after <= -3 %, pre-emptive stop exits (0.2.7: from 08:00 NXT; 0.2.8 #143: only on <= -3 % nights), GMO D+1 EWY-adjusted ceiling | 0.2.6/0.2.7 CI verified; 0.2.8 source fixed; device unverified |

| [#142](https://github.com/OHM1121/RESALE_ALPHA/issues/142) | 0.2.7 | Stock close-betting realtime queue dropped frames older than 3 s under load; coalesced to the newest row per symbol/type | 0.2.7 CI verified (run 44983487732); device unverified |
| [#143](https://github.com/OHM1121/RESALE_ALPHA/issues/143) | 0.2.8 | Pre-emptive night-EWY stock exit only on nights <= -3 % (owner decision; was every down night) | 0.2.8 CI verified (run 44986172411); device unverified |
| [#144](https://github.com/OHM1121/RESALE_ALPHA/issues/144) | 0.2.9 | Sheet 11.22: market regime UP/DOWN/SIDEWAYS, MA/Trend regime sizing + no-new-buy, AT up-market exception, AT +20 % surge filter | 0.2.9 CI verified (run 44992329644); device unverified |
| [#145](https://github.com/OHM1121/RESALE_ALPHA/issues/145) | 0.3.0 | Regime judged on each bought symbol on its own timeframe (not KOSPI/BTC daily) | 0.3.0 CI verified (run 45017368198); device unverified |
| [#146](https://github.com/OHM1121/RESALE_ALPHA/issues/146) | 0.3.1 | Python engine froze 55 min with the app alive; UI-watchdog restarts could not run behind the synchronized bridge monitor; independent supervisor (dump 90 s, process restart 240 s) added | 0.3.1 CI verified (run 45019130055); device unverified; blocking lock not proven |
| [#147](https://github.com/OHM1121/RESALE_ALPHA/issues/147) | 0.3.2 | Supply-demand D candidates judged on D+1 over the D+1 intraday ranking (appeared a day late); pending card had no buy price and a wrong reason | 0.3.2 CI verified (run 45095721653); device unverified |
| [#148](https://github.com/OHM1121/RESALE_ALPHA/issues/148) | 0.3.3 | Read-only ledger view build (100-500 s) held the bridge monitor: heartbeat blocked, 6 supervisor restarts, Upbit ping timeouts; view moved off the monitor, duration-aware pacing, network-environment record | 0.3.3 CI verified (run 45097454974); device unverified |
| [#149](https://github.com/OHM1121/RESALE_ALPHA/issues/149) | 0.3.4 | Owner strategy change: coin GEN return-to-entry exit armed only after +1.5 %; coin GEN/MA/Trend buy reduced only when gain/loss <= 0.5 | 0.3.4 CI verified (run 45099481587); device unverified |
| [#150](https://github.com/OHM1121/RESALE_ALPHA/issues/150) | 0.3.5 | Owner: reward/risk buy size also for stock AT / MA / Trend; unverifiable lookback keeps the fixed amount | 0.3.5 CI verified (run 45101579398); device unverified |
| [#151](https://github.com/OHM1121/RESALE_ALPHA/issues/151) | 0.3.6 | KRX 0D quantity "0" dropped the book (AT 1 share, GMO 3 of 39); EWY night value lost on restart (GMO ceiling skipped); heartbeat waited on the coin ledger lock; MarketTrend daily RSI watch 4 days | 0.3.6 CI verified (run 45124443820); device unverified |

### State vocabulary
`REPORTED` / `LOG_CONFIRMED` / `ROOT_CAUSE_CONFIRMED` / `SOURCE_FIXED` / `CI_VERIFIED` / `DEVICE_VERIFIED` / `OPEN_UNVERIFIED` / `LATENT_UNOBSERVED` / `NON_ERROR_POLICY_BLOCK`. A defect cannot move to `DEVICE_VERIFIED` merely because a paper simulation or Java compile succeeds.

Raw device SQL and JSON exports are **not** committed to this public repository. No source log is ever reduced to fabricated buy/sell evidence.

### 0.0.6 update build (not phone certified)
- Signed source commit: `478b5d19bc186168b26b232ff5e149c3695d359e`
- Verified workflow: https://github.com/OHM1121/RESALE_ALPHA/actions/runs/44438752719
- Signed APK: `ARTITRADE_0.0.6.apk` (30,475,489 bytes), SHA256 `47e98de3457c544b23960553011afb2913459ca62ecf3142392d75a98f0de2c0`
- Unresolved environment/on-device incidents #58 #59 #60 remain OPEN, even after source CI. Never label these DEVICE_VERIFIED.

### 0.0.7 cross-strategy signed TEST APK (not phone certified)
- Package: `com.artitrade.unified.s21dex`; versionName `0.0.7`; versionCode `129`
- **Exact packaged source:** `901ce172f3c690e3f629915cff63d7f7a8611224`
- **GitHub Actions signed test:** https://github.com/OHM1121/RESALE_ALPHA/actions/runs/44443393545 — SUCCESS
- File: `ARTITRADE_0.0.7.apk` (30,475,505 bytes), SHA256 `f044f2aaef724982ba62827fbe0a0539b8e0ae0549a0524c963594506d532e59`
- Original signer SHA256 `37967ed67598f4137045db4a31b16e156993f6c7c88c9d93d0905b27d5f723c6` unchanged.
- Regression for GMO/CW/AT/Trend/MA cross-coupling and 8 PAPER engines, Java compile, original vendor source SHA, 29-node dependency graph: CI verified.
- **Still OPEN:** #58, #59, #60, #71, #73; testing on the actual Samsung phone is required. Do not reinitialize existing PAPER wallet or enable LIVE.

## #152 — 0.3.7 stock MA hook, reference stop, MarketTrend wording, entry context (2026-11-26)
Root cause: legacy `ma_v061` tick hook retired all v067 stock candidates (132/132 `MA_V061_BAD_OPEN_WINDOW`); stock MarketTrend had an overnight-gap history rejection and silent drops. Owner changes: reference stop = BUY price - bar range, "전환봉" wording, per-position RR + MA slopes. Status SOURCE_FIXED; CI pending; device UNVERIFIED. Open: remaining stock MarketTrend blocker (needs the next export), 1:1 analysis (needs the ledger analysis), NEW LEGACY workbook sheet.

## #153 — 0.3.8 second GMO holding missing from the integrated view (2026-11-27)
Root cause: all GMO positions have id -1, the integrated list key used the id. Fixed with position_uid/code in the key. SOURCE_FIXED; CI pending; device UNVERIFIED.

## #154 — 0.3.9 coin feed outage and saturated book worker (2026-11-27)
Outage 182 min: external network, automatic recovery, no app defect found. Book worker saturated (BOOK_STALE 98k, wait 3.7-22.6 s): cause unknown, measurement added in 0.3.9. SOURCE_FIXED (measurement); CI pending; device UNVERIFIED.

## #155 — 0.4.0 stock WebSocket drops and ledger gate long holds (2026-11-27)
211 disconnects in 8 days; ledger gate held up to 210 s; external DNS failures shared with the coin feed. Cause of the long holds unknown: measurement added (holder stack, scheduling lag, disconnect diagnostics). SOURCE_FIXED (measurement); CI pending; device UNVERIFIED.

## #156 — 0.4.1 GMO fills grouped by position; D+1 no-buy diagnosed (2026-11-27)
GMO fills had no stable identity (placeholder ids) and showed as separate cards: fixed by linking fills to the entry date and ignoring negative placeholder ids. The D+1 candidate was never evaluated at the open because the tick pipeline processed nothing for 20 minutes (ledger gate held by background repair): diagnosed, mitigation awaits the owner's decision. SOURCE_FIXED; CI pending; device UNVERIFIED.

Delivery: signed APK 0.4.1 built by CI run 45925586430 from source 5fe8a03 (SHA-256 42bdbeffe48adc2086595917dd53d35aeada1ca60a4362f9a4aa50a56d7f6414, signer unchanged). CI_VERIFIED only; DEVICE_VERIFIED=false. The first attempt (source e052202) failed in CI because the default flavor versionName in build.gradle duplicated 0.4.1; no app code was involved.

## #157 — 0.4.2 market-wide daily statistics recomputed under the ledger gate (2026-11-27)
Profile on the real stock database: 93 % of the higher-frame repair's gate hold was two research functions recomputed for every symbol and frame. They are now memoised (exact results, bounded lifetime, warmed on a read-only connection). Close betting was rejected with a stale order book in 76 of 77 evaluations; stock ticks expired (31 %). Whether this fixes stock MA / MarketTrend / close betting is unverified; the other long gate holders are still unexplained.

Delivery: signed APK 0.4.2 built by CI run 45927212554 from source 9ae92ba (SHA-256 75e3724c20ff702fc7f5e7ebec9d54c70064a99cde09651283d515fd61cba51d, signer unchanged). CI_VERIFIED only; DEVICE_VERIFIED=false.

## #158 — 0.4.3 owner workbook pink text: coin blocks, trend and score at the BUY (2026-11-27)
Coin reference bar: no buy at score +40 or more or at a negative score even when UP; coin MA: no buy under 3 months of listing or at +40 or more; coin MarketTrend: no buy at +40 or more. "Trend unverified" came from bars the app did not hold (regime fell back to SIDEWAYS); the completed candles before the BUY are now read once per symbol/frame/bar. Every BUY record carries the trend and the market score. MMT entry price checked against exchange candles (consistent). IRYS / CYBER exits explained (no return-to-entry exit in the MA rules; decision pending).

Delivery: signed APK 0.4.3 built by CI run 45933880939 from source 4c1621e (SHA-256 7cafc8e7c626ce7cfff507b80ea7800caeb74c5169c6856e370b22b6f2c141bd, signer unchanged). CI_VERIFIED only; DEVICE_VERIFIED=false.

Findings for the owner's two screenshots (2026-11-27, app 0.3.6 export): stock MA evaluations were skipped with MA_NO_FRESH_OBSERVED_TICK_IN_10_TO_2_SECOND_WINDOW (1000 of 1000 on 11-27 morning, about 99 percent per hour on 11-25/26); 5m bars per slot fell from 127 symbols (09:00) to 16 (09:25); stock bars are KRX regular session only while the owner's chart is the combined KRX+NXT view; stock MarketTrend is 30m/60m/day only (5m retired). Open decisions: combined KRX+NXT bars; coin MA return-to-entry exit.

## #159 — 0.4.4 stock bars on the combined KRX+NXT flow, stage 1 (2026-11-27)
Owner decided to move stock MA / MarketTrend / reference-bar bars to the combined flow and not to add a return-to-entry exit to coin MA. The app cannot assemble the combined flow from stored data and has never requested the broker's combined chart, so stage 1 only measures it (own database file, no behaviour change). 99 % of the stock MA 5m evaluations on 11-25..27 were skipped for lack of a fresh tick: a separate feed cause.

Delivery: signed APK 0.4.4 built by CI run 45934585009 from source 0919158 (SHA-256 4dde6258f87c4445ee58e109ba6a3d603ae68643ff4809a254ba5663bfa277a1, signer unchanged). CI_VERIFIED only; DEVICE_VERIFIED=false.

## #160 — 0.4.5 stock tick feed dead for most of every session; supply-demand BUY 3 of 39 (2026-11-27)
Why earlier fixes did not hold: each shortened one consumer or queue; none separated the real-time reader from the ~10 background analytics threads (shared GIL and ledger gate), none shortened the reconnect pause (60 s after every ping-timeout socket), and the feed counters were never exported. Now: 3 s reconnect after a healthy socket drops, bounded background yield under pressure, one feed-health line per minute. The 11-23 supply-demand case (3 of 39 shares): every 0D book was rejected all day (zero KRX ask quantity), so only the 10 percent print proxy could fill (1 share per print) from 10:44; fixed in 0.3.6 and confirmed valid from 08:50 on 11-26.

## #161 — 0.4.6 UI snapshots off the trading gate; D+1 order from a fresh 0D book (2026-11-27)
First export with the holder stacks: 22 of 60 gate holds >= 5 s were the screen refresh (display-only SELECTs under the trading gate), 36 the higher-frame repair. UI snapshots now read without the gate. The Doosan Bobcat D+1 candidate (11-26) was never ordered because the preplan reacted only to 0B prints (about 10 per hour were delivered) although the ask was below the ceiling from 09:00; the order is now placed from a fresh valid 0D book, fills unchanged.

Delivery: signed APK 0.4.5 (CI run 45950245922, source 139ce45, SHA-256 666b8e8d4bd37b40e15af212c747bc609b4b0d99bbb5f2ef9054dab4095b6f00) and signed APK 0.4.6 (CI run 45951350165, source 0088a38, SHA-256 18ca600ca334e367ea72410b8c1fbcdcd1bb03d88228d9d3c756326c826373df), signer unchanged. CI_VERIFIED only; DEVICE_VERIFIED=false.

## #162 — 0.4.7 supply-demand end-to-end simulation, double matching of one print, consistent UI snapshot (2026-11-27)
The owner asked whether the path candidate -> waiting -> buy candidate -> real buy can be checked without the ledger. A synthetic candidate driven through the real GMO service (ui_tests/sim_gmo_d1_v0470.py) showed that the displayed ask size of one print was matched twice (a book of 3 shares filled 6) and reproduced the Doosan Bobcat case (no order without a 0B print before 0.4.6). Fixed: each print is matched once. The UI read-only snapshot is now one read transaction. Not fixed and stated: one 0D snapshot can be reused by several prints, (the 10 percent print proxy runs only when no size is shown or the book is older than 3 s; corrected in 0.4.8, issue #163). CI_VERIFIED only after the build; DEVICE_VERIFIED=false.

Delivery: signed APK 0.4.7 (CI run 45953023938, source a6186d5, SHA-256 4a8f4f52ea87c23e61341c1d2fdd6a9930c0320310d416a8fb2ee7d3e815f7d6), signer unchanged. CI_VERIFIED only; DEVICE_VERIFIED=false.

## #163 — 0.4.8 GMO PAPER matcher: displayed size of one 0D snapshot taken once (2026-11-27)
Measured with the real service and a virtual candidate: six prints on one 5-share book filled 30 shares; with four prints per 5-second book the per-stock budget was reached in 20 s instead of the 95 s the displayed liquidity supports. Fixed by subtracting the shares already taken from the snapshot (BUY ask and SELL bid) before the original matcher runs. Rejected: a print-volume cap (starves thin names such as the 11-26 Doosan Bobcat case), editing the vendor matcher (engine hash). Unchanged on purpose: fills are sized by the displayed size, not the print volume. The 0.4.7 wording about the 10 percent proxy after exhaustion was inaccurate and is corrected: the proxy runs only when no size is shown or the book is older than 3 s. CI_VERIFIED only after the build; DEVICE_VERIFIED=false.

Delivery: signed APK 0.4.8 (CI run 45954094562, source 1067c58, SHA-256 215096625c9d15476e98c1bd35d1bb8da51f87d5fe4f02915272320713367128), signer unchanged. CI_VERIFIED only; DEVICE_VERIFIED=false.

## #164 — 0.4.9 supply-demand PAPER BUY from two consecutive fresh 0D snapshots (2026-11-27)
Owner decision (policy conflict recorded in USER_ACTIVE_POLICY): allow a fill from displayed liquidity when the quantity is still there at "execution time". Implemented as a confirmation on the next fresh book (1-60 s). Limits stated: it does not solve the 11-26 Doosan Bobcat case (snapshots minutes apart, ask at the ceiling; only a single-snapshot immediate fill reproduces a real 09:00:18 fill but has no phantom protection); the gaps are assumptions; thin symbols cannot separate "unchanged" from a feed gap. CI_VERIFIED only after the build; DEVICE_VERIFIED=false.

Delivery: signed APK 0.4.9 (CI run 45955187149, source 2210f60, SHA-256 9bd44c9e204a71773da04c6d00fba20f6d1657905797036d381c15623f1c9860), signer unchanged. CI_VERIFIED only; DEVICE_VERIFIED=false.

## #165 — 0.5.0 paper fills follow the real-trading process (2026-11-27)
Owner rule R15: paper-only conservatism without a real reason is removed; nothing a real order could not do may be written. Audited the fill models (REAL_PARITY_AUDIT.md). Changed: whole displayed best ask / bid size takeable in the stock paper wallets (was 50 %); GMO paper fills at the displayed price (was one tick + 0.05 % worse). Kept: 3 s freshness, order limit, fees, tax, wallet / budget limits, the cross-wallet sharing of one snapshot, the resting-order and print proxies (listed). Open and listed, not changed: order latency, the sell-floor cancel, `ARTITRADE_REAL_MODE` blocking the D+1 plan (to verify before the real switch), Upbit / MA / MarketTrend wallet fills (not audited yet). Paper results of 0.5.0 are not comparable with earlier versions. CI_VERIFIED only after the build; DEVICE_VERIFIED=false.

Delivery: signed APK 0.5.0 (CI run 45955971640, source 005762f, SHA-256 cb51794586595f7ba002f9de6b46aa6b8e65bd8298ee657e79638b645e14f82c), signer unchanged. CI_VERIFIED only; DEVICE_VERIFIED=false.

## #166 — 0.5.1 supply-demand D+1 entry is a three-rung split BUY decided before the open (2026-11-27)
Owner decision (policy conflict recorded in USER_ACTIVE_POLICY): the single "first ask + 2 ticks" order and its three ceiling scenarios are replaced by rungs at the buy-condition price (30 %), 1 % below (35 %) and 2 % below that (35 %, the owner's 40 % would make 105 %), computed after the EWY change and before the NXT open and recorded; a rung is bought when the price comes to it; a gap down past every rung discards the plan and buys everything at the current price; no chase or top-up; no buy after D+1; one position id; a new order only after the earlier one is confirmed cancelled. Limits stated: a rising stock fills only rung 1; no queue proof for a resting rung; the official-open branch of the former rule is not available before the open; `ARTITRADE_REAL_MODE=1` still stops this plan; spreadsheet columns for the ladder are not added (the ladder is in the audit log, the order purposes and the plan snapshot). CI_VERIFIED only after the build; DEVICE_VERIFIED=false.

## #167 — 0.5.2 split BUY: a start price below rung 1 buys rung 1's share now at the current price (2026-11-27)
Owner example: rung 1 = 4,900, rung 2 = 4,500, start 4,700: 30 % of the limit is bought at 4,700, rungs 2 and 3 stay. Before, the share count was computed at the rung price (about 28 % of the limit was invested). Limit of the order = the current price (a tick up before matching leaves it resting). CI_VERIFIED only after the build; DEVICE_VERIFIED=false.

Delivery: signed APK 0.5.1 (CI run 45961404109, source e42f06f, SHA-256 eccf807933376ae9e4ab8be775624c3a189823aa00cb869297af81d187cd6aac), signer unchanged. CI_VERIFIED only; DEVICE_VERIFIED=false.

Delivery: signed APK 0.5.2 (CI run 45961739454, source c6e1d4e, SHA-256 4b7b325e954a07df8b2c5780242d5f15bd89ed0a9e8b47c60e056fb572806e60), signer unchanged. CI_VERIFIED only; DEVICE_VERIFIED=false.

## #168 — 0.5.3 no top-up after a sell; supply-demand pre-setup; quiet-hours candle completion (2026-11-27)
Owner corrected the split-buy rule (a sell before the rest was bought ends the buying) and set rule R16 (what does not need real time is done between the NXT close and the next open). Done: remaining split orders are cancelled when an exit starts; supply-demand plans and the recorded split-buy prices are created from 06:00 once the EWY value is known; the day's missing finished candles are fetched again after 20:05 and inserted without replacing anything. Not done and listed with reasons in OFFHOURS_PREWORK_PLAN.md: backfilling the entry context (MA above / below, trend) of sold positions, coin candle REST calls at the BUY, post-exit research records and market statistics warm-up (not measured yet), NXT eligibility preload (to verify). CI_VERIFIED only after the build; DEVICE_VERIFIED=false.

Delivery: signed APK 0.5.3 (CI run 45962842903, source 09417cf, SHA-256 1eb73edf0bc62c5db8f7d3797dbd87f404af3814aca5d1e3496d3676f9ce0589), signer unchanged. CI_VERIFIED only; DEVICE_VERIFIED=false.

## #169 — 0.5.4 BUY-time reward / risk in the execution history (2026-11-27)
Owner asked whether the reward / risk at the BUY is recorded in the ledger and wanted it shown in the execution history. It has been recorded since 0.3.4 / 0.3.7 for AT, MA and MarketTrend (coin and stock; the owner's real exports hold 46 coin reference, 45 coin MA, 7 coin trend and 34 stock AT values); supply-demand has no stop and is excluded; the two stock MA / trend databases of the exports hold no positions, so their recording could not be checked. The display was missing: the hub rows did not carry it. Added a read-only join by the native position id and a line on the history trade card. CI_VERIFIED only after the build; DEVICE_VERIFIED=false.


## #170 — 0.5.5 stock MarketTrend history loading from local bars (2026-11-27)
Owner approved ("이거 진행해") the history-loading completion and a virtual-candidate end-to-end test, after asking why 휴림로봇 (11-18) did not buy. Finding: 휴림로봇 was not bought by design (the only eligible bar had a -19 degree Bollinger-middle slope, window -15..+20); separately the stock MarketTrend never bought because the history was empty at the open (every symbol x frame went to the broker; failures left the key empty). Done: `market_trend_history_v0550` (local-first, previous-session carry-over, bounded broker rate, preload) and ui_tests/test_v0550_mt_history_preload.py (virtual candidate buys with the broker down; slope too steep, 휴림로봇 case and first-bar-only cases do not). Not verified on a device.

## #171 — 0.5.5 held supply-demand position first take-profit not evaluated (하림지주) (2026-11-27)
Owner asked why 하림지주 (3 sh @ 9,930, target 10,426.5) was not sold after the first target was reached. Engine and routing are correct in simulation; the prints did not reach the engine in time (single tick consumer behind the heavy engine, 3 s gate, silent drop). Done: `gmo_held_exit_v0550` (watchdog on the newest fresh print, evidence counters and the issue GMO_HELD_TP_TOUCH_UNPROCESSED), ui_tests/test_v0550_gmo_held_exit.py. Open: a print already older than 3 s on arrival still cannot trigger; owner decision on a resting take-profit order (R15) pending. Not verified on a device.

Delivery: signed APK 0.5.5 (CI run 45989141533, source 3ed5a4e, SHA-256 615e15560d71297a5e9ab64021fcec9bc21b22acf57ef065b32d14912f6d38a6), signer unchanged. Issues #170 and #171. CI_VERIFIED only; DEVICE_VERIFIED=false.

## #172 — 0.5.6 supply-demand first take-profit modelled as a resting sell order (2026-11-27)
Owner decision on #171 option C ("그렇게 해"): the first take-profit follows the real process (a limit sell resting at the target), not "see a print, then sell at bid - 3 ticks". Done: `gmo_resting_tp_v0560`, ingress touch record + watchdog application in `gmo_held_exit_v0550`, ui_tests/test_v0560_gmo_resting_tp.py. Not verified on a device.

Delivery: signed APK 0.5.6 (CI run 45990938720, source 19aa2b3, SHA-256 c40b9d639d46d7ba1f55a95f7fae739ee82460d63bc114a099c92979c20a58bc), signer unchanged. Issue #172. CI_VERIFIED only; DEVICE_VERIFIED=false.

## #173 — 0.5.7 resting take-profit recovery from candles (2026-11-27)
Owner screenshot (0.5.6, 20:36): 하림지주 at 10,920 above the 10,427 target but unsold; owner recalled a recovery that sells after the connection returns. That recovery exists only for D+16 overdue holdings; 0.5.6 was installed after the close (20:32) and its resting order uses prints the app saw. Done: `catch_up` from the stored candles (real-data run: 003380 1 sh @ 10,430 at 2026-11-25 15:20, stage PROTECTED). Not verified on a device.

## #174 — 0.5.7 supply-demand dashboard counts (2026-11-27)
The dashboard showed 13 buys for 2 positions (split fills counted as rows). Buys are counted per position and sells per order for the supply-demand strategy only. Java compiled by CI only; not verified on a device.

## #175 — 0.5.7 after-close collection status on the market feed card (2026-11-27)
Owner wanted to see, in one short teal line on the market status card, whether the after-close collect-and-store work is running. Added `status()` and the card line; display only; Java compiled by CI only; not verified on a device.

Delivery: signed APK 0.5.7 (CI run 45992878871, source 5001d37, SHA-256 5d83a9193bb47971d6f1bc3429e01460e0363c725ee309b7539f22e0c5ce2c73), signer unchanged. Issues #173, #174, #175. CI_VERIFIED only; DEVICE_VERIFIED=false.

## #176 — 0.5.8 current-book confirmation for quiet stocks (MA, close-betting, MarketTrend) (2026-11-27)
Owner decision: replace the "stream message within 3 s" requirement by one ka10004 inquiry per stock and window (MA, close-betting and, on the owner's question, MarketTrend). Evidence: 23,329 of 47,943 stock MA decisions blocked by "no fresh tick". Added `rest_book_confirm_v0580` and ui_tests/test_v0580_rest_book_confirm.py. Not verified on a device; the official request limit is unknown.

Delivery: signed APK 0.5.8 (CI run 45994156662, source be8e7cf, SHA-256 bf8470b4724a9445c394bb808638bec6bdbd16da04e45249eaa2dfb06219d32a), signer unchanged. Issue #176. CI_VERIFIED only; DEVICE_VERIFIED=false.
