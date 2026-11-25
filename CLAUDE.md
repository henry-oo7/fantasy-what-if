# Claude Code Instructions

Read and follow `AGENTS.md` before planning or modifying anything in this repository.

## Startup Check

At the beginning of every task:

1. Run `git status --short --branch`.
2. Identify the active branch, pre-existing changes, and untracked files.
3. Do not alter pre-existing work unless the task explicitly owns it.
4. Read only the governing documentation relevant to the task.
5. Inspect relevant source, tests, and history before proposing changes.

If the current checkout contains unrelated uncommitted work, stop before editing and recommend an isolated Git worktree or clean feature branch.

## Interaction Style

- Be concise but explicit about assumptions, risks, and approval boundaries.
- Prefer repository evidence over guesses.
- Return a concrete plan before consequential or cross-cutting changes.
- Ask for a decision when architecture or product documentation conflicts.
- Do not claim completion until required validation succeeds.
- Do not hide failed commands, skipped tests, or unresolved findings.
- Keep terminal output focused on useful pass or fail evidence.

## Permission Defaults

Treat these actions as requiring approval unless the active task explicitly grants them:

- Commit
- Push
- Pull-request creation or modification
- Runtime dependency changes
- Database migrations
- Public API changes
- Authentication or authorization policy changes
- Commands that access files outside this repository
- Network-exposed services
- Deployment, infrastructure, secrets, or production actions

Never merge to `main`, deploy, access production, rotate credentials, force-push, or destroy data.

## Context Discipline

Do not read the entire repository or every architecture document by default.

Use this order:

1. The current task or GitHub issue
2. `AGENTS.md`
3. Relevant product, roadmap, or architecture sections
4. Relevant source and tests
5. Relevant Git history, pull requests, issues, and CI evidence

Use additional context only when needed to resolve a material uncertainty.

## Current Product Baseline

ADR 0010 (`docs/architecture/0010-Personal-Accounts-and-Shared-Canonical-Leagues.md`) governs the account and league model. Read it, and `AGENTS.md`, before account, league, access, import, or synchronization work.

The hosted SaaS is a new deployment. It does not migrate or preserve version-1 installation data.

The target model is independent personal accounts (display name, email, password) backed by shared canonical league data:

- Email is the sole login identity. There is no normal administrator role, spectator role, League Workspace, workspace slug, workspace owner, shared spectator account, or shared password.
- A provider league is a shared canonical resource. The first importer does not own it.
- Authorization is persisted per user and league season. Every user proves access to every private season independently, and import verifies with the provider before persisting anything.
- Provider credentials belong to the user who supplied them.
- Synchronization is keyed by league season. No provider network call runs inside an open database transaction.
- Removing a user's connection or deleting an account never deletes shared data while another user retains active access. Orphaned leagues are retained 30 days.
- ESPN is the only implemented provider. Sleeper and Yahoo are not implemented and must not be described as functional.
- Public registration remains closed until the account cutover, authorization, import, synchronization, and frontend selection flows are complete and validated. Current staging is not acceptance evidence for the retired workspace model.

The code still contains the legacy workspace model until the roadmap cutover branches land. Treat it as the baseline being replaced, not as a contract to extend.

Always verify the current branch and worktree before editing. Do not alter unrelated changes without explicit authorization.

## First-Session Restriction

Until Henry explicitly authorizes implementation work:

- Operate read-only.
- Do not edit files.
- Do not run formatting tools with write behavior.
- Do not stage, stash, restore, commit, or push.
- Do not start or stop Docker services.
- Do not run migrations.
- Do not access `.env` files or secrets.