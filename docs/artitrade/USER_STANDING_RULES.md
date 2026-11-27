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
- **Version sequence (owner instruction 2026-11-20):** `0.0.20` is the last
  `0.0.x` version. After it the versions go `0.1.0`, `0.1.1`, ... `0.1.9`, then
  `0.2.0`, ... `0.2.9`, then `0.3.0`, and so on: the last number counts 0 to 9
  and then the middle number increases by one and the last number restarts at 0.
  Never skip, never repeat, never add text. `tools/artitrade/next_version.py`
  computes the next version; the release workflow and CI use it.

## R2 — After every fix or improvement, build and hand over the APK (2026-11-20)
- When the owner asks for a fix or improvement, finish it, build a signed APK
  with the unchanged package and signer, and give it to the owner. A source-only
  result is not a delivery.
- State plainly what is verified: local tests, CI, device. Never describe a CI
  pass as a phone/broker verification (see AGENTS.md).
- If the signed build cannot be produced, say so and say why. Do not substitute
  an unsigned or differently signed APK.
- **Automatic delivery (owner confirmation 2026-11-20):** the APK is handed over as
  soon as the signed build finishes. The owner does not have to ask for it.
- **Hand-over path (owner instruction 2026-11-20, replaces the earlier Quick Share
  wording):** every APK that is produced is saved on the owner's computer in the
  Desktop folder named `artitrade apk` and is shared from that folder to the owner's
  phone with the Windows application "휴대폰과 연결" (Phone Link). Do not use Quick
  Share. This is done without being asked. If the computer, the folder or the share
  cannot be used, send the file in the chat as before and say which step could not be
  done and what the owner must enable. Never claim the phone received it unless the
  share was actually completed.
- **Release page:** the GitHub release upload needs the repository Actions token to have
  write access (Settings > Actions > General > Workflow permissions > Read and write
  permissions). Until it has, the APK comes from the workflow artifact.

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

## R9 — Standing permission to start the signed build on the trusted branch (2026-11-20)
- The signing key cache is visible only on branch `artitrade-phone-main-v070-live-orders-20261112`.
  The owner **permanently allows** pushing to that branch, for every build, **without asking again**.
- Scope (unchanged from earlier builds): add only the single workflow file
  `.github/workflows/artitrade-<digits>-trusted-branch-signer.yml` for the new version, pinned to the
  exact source commit. No other file, no other branch, no force-push, no history rewrite there.
- The package, signer and numeric-version rules (R1, R2) are unchanged. CI is not device verification.


## R10 — Report format to the owner (2026-11-20, replaces the longer wording of R5)
- Every report to the owner has exactly two parts and nothing else:
  1. **문제였던 것과 그에 따른 효과** (what was wrong, and what it caused);
  2. **수정 후 개선된 내용** (what is better after the fix).
- No introductions, no step-by-step narration, no filler. Unverified items are stated inside these two parts
  (never implied as success). Needed decisions are added as one short line at the end of part 2.
- Still never describe a CI pass as phone/broker verification (AGENTS.md).


## R11 — Every APK goes to the owner's phone automatically, in EVERY session (2026-11-20, strengthens R2)
- After every signed build, in this order and without being asked:
  1. Try to attach the owner's computer (`list_computer_folders`, then `request_computer_folder` for the shared
     folder). If attached: download the verified APK into the Desktop folder `artitrade apk` and share it from there to the
     phone with the Windows app "휴대폰과 연결" (Phone Link). Verify the SHA-256 after saving.
  2. In every case also send a phone push notification with the GitHub release download link
     (`https://github.com/OHM1121/RESALE_ALPHA/releases/download/<version>/<version>.apk`) and the APK in the chat.
  3. Report plainly which step worked and which did not. Never say the phone received the file unless the Phone Link
     share was actually completed (a push with a link is only a link).
- If no computer folder is shared, say so in one line and what the owner must enable (Claude desktop app > Settings >
  Claude Code > Remote Control > share the `artitrade apk` folder); do not stop the delivery.
- Report format stays R10 (two parts only).

- **PC fallback (2026-11-20):** a cloud session cannot see the owner's PC even when Remote Control is set up
  (`list_computer_folders` is empty unless a computer actively serves a folder to that session). The owner then runs
  `tools/artitrade/fetch_latest_apk.bat` (or `.ps1`) on the PC: it downloads the newest release APK into
  Desktop `artitrade apk`, verifies SHA-256 and opens the folder for the "휴대폰과 연결" share. Every delivery report names this
  script when step 1 could not be done.

## R12 — Everything the owner can read is in Korean, in EVERY session and for EVERY job (2026-11-22)
- Owner instruction: "영어로 진행상태 적지말고 앞으로 모든세션 어떤직업을 하든 한국어로 진행해".
- This covers ALL text shown to the owner, not only the final report: the short notes written between tool calls
  (what is being checked, what was found, what is being fixed), plan and progress lines, questions, error
  explanations and the final R10 report. No English progress narration.
- Allowed in English: code, file and function names, commands, identifiers, quoted log lines, GitHub issue/commit
  text that other tools read, and tool arguments. Explain anything the owner reads in plain Korean; never leave a bare
  English sentence or an untranslated error message as the explanation.
- Applies to every kind of session: coding, analysis, build and delivery, scheduled or automatically resumed sessions
  and sessions in other repositories started for this owner (for example the claude-auto repository).



## R13 — Error-export folder cleanup, unattended resume, all parts used (2026-11-26)
- **Error exports (owner):** when a new error-history export is downloaded, in the folder the owner downloaded it to, delete the older exports (everything extracted before the newest one that was just downloaded). If the owner already deleted them by hand, do nothing. A cloud session cannot see the owner's PC; this is done only when a computer folder is attached or by the PC script, and the report says which. Never delete anything but earlier ARTITRADE error-history files.
- **Unattended resume (owner):** when the token / usage limit resets, start or continue the unfinished work without being told.
- **Complete data (owner):** every part of an export is used when reviewing the existing buy strategies, including the part that holds the post-exit-high database (PART_007), even when an error analysis does not need it.
