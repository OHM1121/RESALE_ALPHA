# ARTITRADE all-strategy source + v005 error audit — v008 (unreleased source)

Review: 2026-11-19 KST. Baseline production/signed APK: **0.0.6, versionCode 128**, source commit `478b5d19bc186168b26b232ff5e149c3695d359e`; original post-epoch v005 logs and pre-reset functional source are references. The changes below live only on the **audit branch** until a separately signed versionCode 129+ is built and validated. **No local app or user SQLite ledger has been reset by this work.**

## Evidence and dependency direction

| Owner/flow | Confirmed code fact | Risk / actual observation | Corrected at source | Issue |
|---|---|---|---|---|
| Coin MA → native 5m wallet | MA V079 `REVERSAL_UP` source features do not contain reference `high/low`; prior v006 guard required both for ALL 5m candidates | 0.0.5 sample MA 5m positions existed; 0.0.6 could reject valid future entries/pending | MA verified-origin vs reference OHLC schema split; missing/unknown still blocks | #67 |
| GMO → 200-slot Kiwoom WS → AT/CW/MA/Trend | GMO 07:58 subscription override used exclusively GMO watch, bypassing common fairness and held exit pins | Actual missed device symbol unverified; source shared-ownership breach | Delegate to one v093 prioritizer with scheduled 08:00 venue clock, pinned held / original broker entitlement preserved | #68 |
| GMO malformed 0B input → shared WS | v097 direct numeric conversions could raise on invalid tick/NaN | Actual malformed tick unverified | Strict shape/finite validation returns no evidence without mutating unrelated strategy queues | #68 |
| CW/Close Research → future due → SQLite/AI | Native followup accepted old `latest` or future replay when no true due quote and marked DONE | Exported CLOSE followup table had zero records, so no observed false DONE; static code path confirmed | Research-only as-of verifier; genuine live within bounded due/receive age, or completed same-day 3m bar with no future look-ahead; otherwise PENDING | #69 |
| Trend 30m/60m/day → Runner start/stop → research/exit | Decorators initialized new Coordinator/PostExit/Recovery owners even when native Runtime.start was already idempotently running | Source path confirmed; actual Android duplicate-start incident unverified | Preserve native idempotence and cleanup abandoned owners | #70 |
| Trend shared positions/virtual state → stock+coin+priority workers | Mutations and iterations of the same dictionaries did not consistently use Coordinator.lock | Racy exception possible; no specific phone observation proved | Lock short registry read/write/snapshot; keep broker/SQLite calls outside lock | #72 |
| AT 5m/30m/60m/day official history → candidate | Native FIX14 REST network exception catch logged but did not schedule next-key retry | **401 DNS exception records observed in v005 logs**; local DNS root cause unconfirmed | NOT YET FIXED: separate vendor core retry fix, bounded backoff and source-verification tests required | #71, #58 |
| Old MA v067 policies → new 11/18 workbook | 8 old assertions fail on BOTH pre-audit and post-audit source | Policy-test baselines conflict (10% vs 30% TP and other prior rules) | Do not silently rewrite source/tests; compare current workbook and save separate history | #73 |
| Upbit WS queue → coin reference/MA/Trend | Existing v005 queue pressure and stale book evidence | Root cause attribution still incomplete | NOT YET FIXED; no broker or queue-loss claims | #59 |
| All 8 wallets → actual Samsung candidate/pending/buy/sell | Native PAPER flow tests exercise candidate-to-exit, but not broker socket and phone lifecycle | Device API, market sessions, actual order/fill or battery/service survival still unverified | NOT YET VERIFIED; #60 remains open | #60 |

## Original strategy ownership matrix
- **Stock GMO / 수급매매:** verified D+1 plan, stock 0B/0D, shared prioritized subscription, native GMO SQLite cash/open/pending, multi-stage exits. Do not confuse healthy empty fresh epoch and unreadable wallet.
- **Stock CW / 종가베팅:** original signed BUY/SELL engine remains authoritative; read-only research followups cannot change broker orders. Historical 3m records must not become invented due prices.
- **Stock reference / 기준봉 AT:** original D19/AT candle-close, pending, original broker 0D order, safe SELL. Official history unavailable is NOT a valid zero-volume or new fill.
- **Market Trend:** stock + coin 30m/60m/day only, preclose/restart, verified book/native wallets, original 5m held SELL recovery; no new Trend 5m buy/research screens.
- **MA:** own signal schema and fixed 5m band, genuine stock/coin quote/book, native staged fills; cannot require another strategy's OHLC schema.
- **Cross-cutting:** one Kiwoom WS per runner; one wallet owner per execution; shared locks for in-memory ownership; SQLite execution ledger is source of truth; UI-only score/price never authorizes broker orders.

## Automated regressions and honesty bounds
- Previously completed local subset: **20** grouped suites; **19 PASS**, one old `test_v067_ma_system.py` suite has eight failures **also reproduced on pre-v008 baseline** (#73). This is not proof the new changes caused them and not proof the old tests are semantically obsolete.
- New `test_v008_all_strategy_rootcause.py` covers MA actual feature schema, GMO 07:58 opening/other strategy pins, malformed quote, CW due price evidence and SQLite persistence, repeated start and short-lock policy.
- Existing native `test_v002_eight_virtual_paper_end_to_end.py`, `test_v004_abnormal_reconnect_six.py`, `test_v005_market_score_empty_wallet.py`, `test_v006_trend_5m_retirement_and_startup.py`, v007 error tests and per-strategy suites are included in branch CI.
- The broad AST/static graph counts original ZIP Python, overlay Python and Android Java and records static imports/monkeypatch surfaces. It **cannot** guarantee absence of an unseen bug; dynamic Android/broker/API evidence is separate.
- Graph: `dependency_graph.json`; machine issue registry: `version_issues.json`; change contract: root `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` and `.agents/rules/artitrade-root-cause.md`.

### Release gate
No APK should be labeled 0.0.7 or safe for live execution until the final current-commit CI pass, exact original signing key fingerprint, applicationId + increasing versionCode, no ledger reset, Android device UI/feed/order test and explicit open-issue report. CI alone is **SOURCE/CI_VERIFIED, not DEVICE_VERIFIED**.
