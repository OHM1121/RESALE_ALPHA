# ARTITRADE / S21+ Android cloud clients

## Scope and safety
- **Two separate APK variants:** ARTITRADE stock AT/CW and ARTITRADE coin B/MA
- **Landscape screen:** Galaxy S21+; DeX supports resizable window mode
- **Purpose:** mobile cloud **display/client only**; **no local Python trading engine**, **no server deployment**, **no automatic synchronization** at this stage.
- **Not connected to a real trading server yet.** The user must configure a trusted HTTPS endpoint.
- Original private stock and coin trading strategy code, credentials, transaction database, and production configuration are NOT uploaded to this public repository.
- Existing RESALE ALPHA marketplace app and workflow are not modified.

## GitHub Actions
Workflow: `.github/workflows/build-artitrade-s21plus.yml`.
The workflow runs from a PR changing `ARTITRADE_ANDROID/**`, or via manual dispatch once on the default branch.
Artifacts from a successful run contain two DEBUG APK files from:
- `ARTITRADE_ANDROID/app/build/outputs/apk/stock/debug/app-stock-debug.apk`
- `ARTITRADE_ANDROID/app/build/outputs/apk/coin/debug/app-coin-debug.apk`

These are Android cloud client shells. Builds alone do **not** make an end-to-end working trading system.

## Future cloud design
1. Deploy authenticated HTTPS service, with encrypted credentials on the server and audited access controls.
2. Make the server the single **order executor**, with idempotent order IDs and conflict detection.
3. Adapt PC EXEs and Android APK clients to the same versioned API/WS protocol.
4. Scope databases, order logs, simulations, settings, and research/error records by market and strategy.
5. Verify broker/exchange API policies, replay safeguards and disconnect/restart scenarios.
6. Only then enable real trading or continuous paper monitoring.

## Testing limits
No Android emulator or Galaxy S21+/DeX hardware test has been performed here. Debug APKs must be tested on-device before use. Android may ignore fixed orientation in some DeX multiwindow configurations.
