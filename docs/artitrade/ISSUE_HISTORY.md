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

| [#75](https://github.com/OHM1121/RESALE_ALPHA/issues/75) | 0.0.6 → 0.0.7 | Same-signer stock/coin cross-strategy test APK | Sign/build/device verification pending |

### State vocabulary
`REPORTED` / `LOG_CONFIRMED` / `ROOT_CAUSE_CONFIRMED` / `SOURCE_FIXED` / `CI_VERIFIED` / `DEVICE_VERIFIED` / `OPEN_UNVERIFIED` / `LATENT_UNOBSERVED` / `NON_ERROR_POLICY_BLOCK`. A defect cannot move to `DEVICE_VERIFIED` merely because a paper simulation or Java compile succeeds.

Raw device SQL and JSON exports are **not** committed to this public repository. No source log is ever reduced to fabricated buy/sell evidence.

### 0.0.6 update build (not phone certified)
- Signed source commit: `478b5d19bc186168b26b232ff5e149c3695d359e`
- Verified workflow: https://github.com/OHM1121/RESALE_ALPHA/actions/runs/44438752719
- Signed APK: `ARTITRADE_0.0.6.apk` (30,475,489 bytes), SHA256 `47e98de3457c544b23960553011afb2913459ca62ecf3142392d75a98f0de2c0`
- Unresolved environment/on-device incidents #58 #59 #60 remain OPEN, even after source CI. Never label these DEVICE_VERIFIED.
