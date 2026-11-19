# ARTITRADE GitHub Copilot instructions

Before proposing code, read repository `AGENTS.md` and the linked ARTITRADE dependency graph / version issue ledger. Diagnose root cause, check upstream and downstream coupling and update tracked issue history. Reject code edits that only hide an error, fabricate fills, bypass ledger integrity, or mix UI display data with execution authority. Record issues and release verification level accurately.

## ARTITRADE handoff workflow (user directive, 2026-11-19)
Before responding to requests for an ARTITRADE developer handoff, read docs/artitrade/HANDOFF_LATEST.md and current release metadata; preserve previous dated archives. Provide the FULL sanitized handoff in a directly copyable assistant output, commit the same Markdown to docs/artitrade/handoffs/ARTITRADE_HANDOFF_YYYY-MM-DD_<release>.md and update docs/artitrade/HANDOFF_LATEST.md on both release-source and default main. Include checked GitHub commit links; never claim success without proof. NEVER automatically type/prefill/paste/submit anything in the user's chat input/composer. See AGENTS.md and docs/artitrade/USER_ACTIVE_POLICY.md. Do not leak private source logs/SQLite.
