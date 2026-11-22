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

## Current Checkout Warning

The initial Claude Code setup occurred while `feature/registration-state-foundation` contained substantial pre-existing, uncommitted implementation work.

Always verify the current repository state. Never assume those changes belong to the current Claude task. Never format, stage, commit, restore, reorganize, or otherwise alter those changes without explicit authorization.

## Current Documentation Conflict

The current product decision is to initialize the hosted multi-workspace SaaS as a new deployment without migrating legacy version-1 installation data.

Some roadmap and architecture documents still describe preserving the v1.0.1 installation as the first League Workspace. Treat this as a known conflict. Do not implement the legacy-migration direction. Report the conflict and recommend a dedicated documentation reconciliation task.

## First-Session Restriction

Until Henry explicitly authorizes implementation work:

- Operate read-only.
- Do not edit files.
- Do not run formatting tools with write behavior.
- Do not stage, stash, restore, commit, or push.
- Do not start or stop Docker services.
- Do not run migrations.
- Do not access `.env` files or secrets.