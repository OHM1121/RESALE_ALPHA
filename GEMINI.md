# ARTITRADE — Gemini CLI and Google Antigravity workspace instructions

@./AGENTS.md

This repository is a trading system. Before any modification or code review, read the full root-cause contract in `AGENTS.md`, then consult `docs/artitrade/DEPENDENCY_MAP.md`, `docs/artitrade/dependency_graph.json`, `docs/artitrade/version_issues.json`, and the GitHub ARTITRADE Issues tab.

Every change requires finding and repairing the upstream defect (not hiding the symptom), checking all impacted **GMO, CW, AT, MarketTrend and MA** stock/coin pathways, preserving original SQLite positions and trade audit evidence, updating the version-specific Issue and critical dependency map, adding regression tests, and reporting unverified actual phone/broker activity. Never invent prices, positions, orders or fills. Do not restore retired MarketTrend 5m entry.

For Gemini CLI, `@./AGENTS.md` imports the full shared contract; for Antigravity, root `AGENTS.md` and this `GEMINI.md` are discoverable workspace rules. The file `.agents/rules/artitrade-root-cause.md` is also always-on for Antigravity.

Also read `docs/artitrade/USER_STANDING_RULES.md` (owner's standing rules, e.g. numeric-only version names, build and hand over the signed APK after each fix, update the feature-connection docs after each delivery). They apply to every APK build automatically.
