# Claude project instructions — ARTITRADE

**Read `AGENTS.md` completely before touching ARTITRADE Android or build code.** It is the same mandatory cross-AI root-cause contract for Claude and all other coding agents.

Always consult `docs/artitrade/DEPENDENCY_MAP.md`, `docs/artitrade/dependency_graph.json`, `docs/artitrade/version_issues.json`, and GitHub Issues (search `ARTITRADE`). Find the true upstream defect and propagate fixes through all impacted interfaces. Do not patch around user-visible symptoms. Version-specific Issue + dependency impact + regression evidence are required per change. Never claim physical-device or broker verification from CI, and never upload unsanitized device logs to this public repo.
