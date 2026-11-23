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
| [#151](https://github.com/OHM1121/RESALE_ALPHA/issues/151) | 0.3.6 | KRX 0D quantity "0" dropped the book (AT 1 share, GMO 3 of 39); EWY night value lost on restart (GMO ceiling skipped); heartbeat waited on the coin ledger lock; MarketTrend daily RSI watch 4 days | 0.3.6 source fixed; CI pending; device unverified |

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
