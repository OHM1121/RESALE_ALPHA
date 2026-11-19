# ARTITRADE 0.0.8 test APK delivery (2026-11-19)

## Immutable build identity
- File: ARTITRADE_0.0.8.apk
- Package: com.artitrade.unified.s21dex
- versionName / versionCode: 0.0.8 / 130
- APK bytes: 30492085
- APK SHA256: 99c913a5f3dad481f70fd3293d504bb8aa99cf9bda23372517cbbd8cb243ef59
- Installed app signer SHA256: 37967ed67598f4137045db4a31b16e156993f6c7c88c9d93d0905b27d5f723c6 (same as 0.0.7)
- Exact source commit: a768bdae3fbd3830a4247ee77245ed2815b96f08
- Signed build: https://github.com/OHM1121/RESALE_ALPHA/actions/runs/44450056074
- Source regressions: https://github.com/OHM1121/RESALE_ALPHA/actions/runs/44449911066
- Native KRX regression: https://github.com/OHM1121/RESALE_ALPHA/actions/runs/44449035817
- Direct release: https://github.com/OHM1121/RESALE_ALPHA/releases/download/ARTITRADE-0.0.8-signed-test-20261119/ARTITRADE_0.0.8.apk
- Signed APK artifact ID: 14494518466

## Source-fixed / CI-verified
1. GMO D+1 preplans can re-arm when app connects after venue opening; genuine same-day exchange quotes and original signal are still required. A verified prior D close can independently qualify an entry when an official opening cannot be reconstructed; do NOT label prior close as the session's opening. Original core KRX 0B, D19 and GMO session gates now consult the per-date KRX venue schedule including 2026-11-19 10:00 to 16:30. **This date is not a fixed yearly exam date.** Future schedule exceptions still require new verified KRX calendar data: automatic discovery of every future special trading day has not been proved.
2. Account UI has explicit READ-ONLY Kiwoom ka10075 current outstanding SELL query. The new status label distinguishes verified current unfilled orders from internal first/second target sell INTENTS. It does not register 1st/2nd real resting orders; no real broker stage-ID link exists. Java and Python keep live order execution DISABLED in this PAPER-only trial APK.
3. Bollinger(20,1.5) midpoint angle formerly used atan(100*percentage change). It now uses price-axis pixel projection with a documented internal 21-close viewport, 180px height, 8px candle pitch and explicit axis range. This matches that *defined projection*, **not** third-party chart zoom. User example ONDO 2026-11-19 08:41 on actual phone requires original tick/candle and exchange-screen scale evidence before claiming exact root cause.
4. Original source and 8-strategy PAPER regressions, Android Java compile, signed APK package/certificate checked. Actual Samsung phone quotes, broker orders and fills NOT tested.

## Release safety
- Do not uninstall the old app or reset its on-device SQLite/WAL or Android settings.
- No automatic broker real-mode activation, no fabricated fills, no buy replay from stale OHLCV, no account-secret export.
- New signed APK was built on the older signing-cache branch because the 0.0.8 source branch restored a different debug certificate; source checkout was pinned to immutable `a768bdae...`, independent of later GitHub documentation changes.
- Do not claim completion of #58, #59, #60, #71, #73, #79, #80, #82, #83 or #84 at the on-device/live level.
