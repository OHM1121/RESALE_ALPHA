# ARTITRADE — Versioned issue register

The **GitHub Issues tab** is the version-by-version expandable issue history. [Open ARTITRADE issue tab](https://github.com/OHM1121/RESALE_ALPHA/issues?q=is%3Aissue+ARTITRADE). Each issue records observations, dependency impact, root cause, fix commit and validation level. This markdown is the quick index; the machine source is [version_issues.json](version_issues.json).

| Issue | Found | Function / failure | Status as of 2026-11-19 |
|---|---|---|---|
| [#53](https://github.com/OHM1121/RESALE_ALPHA/issues/53) | 0.0.5 | `for_ui` lost across 6 hub wrappers; repeated snapshot TypeError | 0.0.7 source fix; CI/device verification tracked separately |
| [#54](https://github.com/OHM1121/RESALE_ALPHA/issues/54) | 0.0.5 | Chaquopy missing IANA KST in daily backup/AT | 0.0.7 source fix |
| [#55](https://github.com/OHM1121/RESALE_ALPHA/issues/55) | 0.0.5 | GMO empty ledger wrongly shown as corruption | 0.0.7 source fix |
| [#56](https://github.com/OHM1121/RESALE_ALPHA/issues/56) | 0.0.5 | Coin MA missing `high` triggered process-book aborts | 0.0.7 guarded source evidence |
| [#57](https://github.com/OHM1121/RESALE_ALPHA/issues/57) | 0.0.5 | Stock MA per-tick queue pressure | 0.0.7 latest-per-symbol coalescing |
| [#58](https://github.com/OHM1121/RESALE_ALPHA/issues/58) | 0.0.5 | Kiwoom DNS unavailable / official bar repair retry cascade | OPEN - environmental proof missing |
| [#59](https://github.com/OHM1121/RESALE_ALPHA/issues/59) | 0.0.5 | Upbit WS queue pressure / stale book | OPEN - unresolved |
| [#60](https://github.com/OHM1121/RESALE_ALPHA/issues/60) | 0.0.5 | End-to-end stock/coin strategy startup / buy / sell | OPEN - on-device evidence missing |
| [#61](https://github.com/OHM1121/RESALE_ALPHA/issues/61) | 0.0.6 | Retire MarketTrend 5m across markets | CI passed; on-device unverified |
| [#62](https://github.com/OHM1121/RESALE_ALPHA/issues/62) | 0.0.3 → 0.0.4 | Historic restart/quote/fill recovery handoff | Historical CI reported; device unverified |
| [#63](https://github.com/OHM1121/RESALE_ALPHA/issues/63) | 0.0.4 → 0.0.5 | UI score-plane and empty-wallet handoff | Historical CI reported; device unverified |

### State vocabulary
`REPORTED` / `LOG_CONFIRMED` / `ROOT_CAUSE_CONFIRMED` / `SOURCE_FIXED` / `CI_VERIFIED` / `DEVICE_VERIFIED` / `OPEN_UNVERIFIED` / `LATENT_UNOBSERVED` / `NON_ERROR_POLICY_BLOCK`. A defect cannot move to `DEVICE_VERIFIED` merely because a paper simulation or Java compile succeeds.

Raw device SQL and JSON exports are **not** committed to this public repository. No source log is ever reduced to fabricated buy/sell evidence.
