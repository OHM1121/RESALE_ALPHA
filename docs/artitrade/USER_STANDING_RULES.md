# ARTITRADE — User standing rules (apply to EVERY change and EVERY APK build)

These are explicit, standing instructions from the project owner. They are
read together with `AGENTS.md` (engineering contract) and
`USER_ACTIVE_POLICY.md` (trading rules by date). **Any coding agent or human
must apply them automatically; they are not optional and need not be repeated
by the owner.** When the owner gives a new standing rule, append it here the
same day with its date, keep the older text, and make the next build obey it.
`tools/artitrade/validate_governance.py` fails the build if this file or its
rule headings disappear.

## R1 — Version identifiers are numeric only (since 2026-11-19, restated 2026-11-20)
- Android `versionName`, the delivered APK file name and the GitHub release tag
  are **only digits separated by dots**, e.g. `0.0.20`, `0.0.20.apk`, tag `0.0.20`.
- Nothing else: no `v` prefix, no date, no `FIX`, `TEST`, `SIGNED`, no
  description or any other word, in the version name, APK name or tag.
- The product name ARTITRADE is not part of a version identifier. Release
  notes and issue titles may be descriptive; version identifiers may not.
- `versionCode` stays an increasing integer; package `com.artitrade.unified.s21dex`
  and the existing signing certificate never change (the app must update in place).
- Enforced in the release workflow (`^[0-9]+(\.[0-9]+){2}$`).

## R2 — After every fix or improvement, build and hand over the APK (2026-11-20)
- When the owner asks for a fix or improvement, finish it, build a signed APK
  with the unchanged package and signer, and give it to the owner. A source-only
  result is not a delivery.
- State plainly what is verified: local tests, CI, device. Never describe a CI
  pass as a phone/broker verification (see AGENTS.md).
- If the signed build cannot be produced, say so and say why. Do not substitute
  an unsigned or differently signed APK.

## R3 — After the APK is delivered, update the GitHub feature-connection docs (2026-11-20)
- In the same piece of work, update how functions connect to each other:
  `docs/artitrade/DEPENDENCY_MAP.md` (diagram and contracts) and
  `docs/artitrade/dependency_graph.json` (nodes and edges), plus
  `docs/artitrade/version_issues.json` and the GitHub Issue for the change.
- Record the delivered version, the source commit and the verification level
  (`SOURCE_FIXED` / `CI_VERIFIED` / `DEVICE_VERIFIED`).

## R4 — The owner's rules are written down and then obeyed automatically (2026-11-20)
- Every rule the owner states for future work (naming, format, process,
  reporting) is added to this file and applied to all later builds without being
  asked again.
- Conflicts with the trading rules follow the procedure in AGENTS.md and
  `USER_ACTIVE_POLICY.md`: the earlier dated rule stays until the owner
  explicitly replaces it.

## R5 — How to report to the owner (2026-11-20)
- The owner is not a programmer. Report in plain language, short and to the
  point: what became better in this patch, what is still not fixed, and what
  decision (if any) is needed.
- Do not narrate intermediate steps or tool output. Report results.
- Always say what is unverified instead of implying success.

## R6 — Trading rules come from the owner's workbook (2026-11-18, 2026-11-20)
- `NEW LEGACY(11.18)` workbook sheets are the strategy source. When a newer sheet
  is supplied, red-marked cells are the changes versus the previous sheet; apply
  those, and record the change in `USER_ACTIVE_POLICY.md` with its date.
- Differences that are not red-marked are reported but not applied unless the
  owner confirms.

## R7 — Never touch the owner's chat input (2026-11-19)
- Handoff text is delivered in the assistant's answer and committed to GitHub
  as described in AGENTS.md. Nothing is ever typed, filled or submitted into the
  owner's chat input box.

## R8 — Privacy of the public repository (2026-11-19)
- No private ledgers, SQLite/ZIP exports, account identifiers, credentials or
  personal trade details are committed. Only sanitized counts, signatures and
  root-cause summaries.
