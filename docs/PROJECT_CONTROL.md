# Repository Control Policy

**Repository:** https://github.com/elvito2808/Trading-Management-Platform  
**Policy owner:** ALPHA (under repository owner's delegation)  
**Effective baseline:** `main` at `6cb4d2d2d9e6ec00310c672cf822ee46db5e5c22`  
**Policy branch:** `alpha/repository-governance-baseline`

## 1. Authoritative project scope

This repository is being developed as infrastructure for a future modular arbitrage trading platform. The existing application is currently a Python modular monolith focused on metadata management and SQLite persistence. It is not yet an operational arbitrage bot.

Only work explicitly mapped to the current project roadmap and approved by ALPHA is considered active project work. Historical branches, stale progress files, and unrelated upstream project plans are not authoritative.

## 2. Source of truth and stale records

Use this precedence order:
1. Current GitHub default branch and its source code.
2. Current tests and GitHub Actions results for the exact commit under review.
3. Current project-control documents under `docs/`.
4. Historical reports and agent handoffs, only as evidence of past activity.

The root `PROGRESS.md` and `docs/agents/IMPLEMENTATION_STATE.md` contain inherited/upstream project history and stale status claims. They must not be used as the current project status ledger until reconciled. Do not delete them as part of this policy change.

## 3. Branch policy

- `main` is the stable integration baseline.
- All changes must be made on a dedicated feature or maintenance branch.
- No direct commits to `main`.
- No force-push, reset, rebase of shared branches, or history rewriting.
- Do not merge pull requests without the repository owner's explicit approval.
- Existing historical branches are preserved until their contents and references have been audited. Do not delete a branch merely because it is not active.
- Every proposed merge must be reviewed against the exact current `main` SHA and tested evidence.

## 4. Agent autonomy and environment setup

Agents are authorized to prepare ordinary non-paid development environments independently within the assigned task:
- create isolated clones/workspaces;
- install free tools and project dependencies;
- configure local Python/Git;
- use available free execution environments;
- run tests and inspect official documentation.

Agents must not provision paid infrastructure, paid Codespaces, cloud compute, or recurring services without the owner's explicit approval. If network access or a suitable runtime is unavailable, report the exact blocker and stop; do not fabricate code or test results.

## 5. Financial and credential safety

Until a separate explicit authorization for a specific operation is granted:
- market-data work may use public endpoints;
- private account access must be read-only and separately scoped;
- no real orders, swaps, wallet signing, withdrawals, transfers, or other financial transactions;
- no secrets, API keys, private keys, seed phrases, or credential-bearing URLs in repository files, reports, tests, or logs;
- dry-run/paper simulation is the default execution boundary.

## 6. Reporting and acceptance

Every agent task must:
- record detailed work in a task-specific report under `docs/agents/reports/`;
- update the current project status document on its working branch;
- list exact files, commits, commands, tests and actual outcomes;
- distinguish implemented, tested, blocked, and unverified functionality;
- preserve prior blocker history rather than rewriting it;
- provide ALPHA a concise handoff with the report URL and commit SHA.

Statuses used by ALPHA:
`NOT STARTED`, `IN PROGRESS`, `BLOCKED`, `PENDING_ALPHA_REVIEW`, `ACCEPTED`, `COMPLETED`.

## 7. Change control

This policy does not authorize merging this branch, changing repository settings, deleting branches/files, enabling live trading, or incurring costs. Those actions require separate review and, where applicable, explicit owner approval.
