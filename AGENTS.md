# Fantasy What-If Agent Instructions

## Purpose

This file defines repository-wide instructions for AI coding agents and human-assisted automation. These rules apply regardless of model or agent provider.

Fantasy What-If is being converted from a self-hosted, single-installation application into a hosted, shared-database SaaS of independent personal accounts and shared canonical leagues, initially operated from Henry's homelab for approximately 50 to 100 registered users.

The application remains a modular monolith. Do not introduce microservices, Kubernetes, Redis, RabbitMQ, Kafka, or another infrastructure dependency unless a governing architecture decision explicitly requires it.

## Instruction Priority

When instructions conflict, use this order:

1. The current task or GitHub issue approved by Henry
2. This `AGENTS.md`
3. Accepted architecture decision records in `docs/architecture/`, with ADR 0010 governing the account and league model
4. `docs/product/SAAS_REQUIREMENTS.md`
5. `docs/roadmap/SAAS_MIGRATION_ROADMAP.md`
6. `docs/DEVELOPMENT.md`, `docs/OPERATIONS.md`, and other repository documentation
7. Existing implementation behavior
8. Inferences from Git history

Do not silently choose between meaningful conflicts. Stop, identify the conflicting sources, explain their consequences, and request a decision.

## Current Product Direction

ADR 0010 (`docs/architecture/0010-Personal-Accounts-and-Shared-Canonical-Leagues.md`) is the governing account and league model. It supersedes ADR 0004 in full and the conflicting parts of ADRs 0002, 0005, 0006, and 0007. The League Workspace model is historical.

The target is a hosted SaaS with:

- Independent personal accounts (display name, email, password) with verified-email login as the sole login identity
- No normal administrator role, spectator role, League Workspace, workspace slug, workspace owner, shared spectator account, or shared password
- Shared canonical provider leagues that no user owns
- Per-user, per-season authorization persisted as `user_league_season_access`
- Multiple leagues across multiple providers per account
- Multiple concurrent sessions per account
- League-season synchronization through PostgreSQL-backed background jobs
- ESPN as the only implemented provider; Sleeper and Yahoo are intended public-launch providers that are **not implemented**
- Separate development and production environments
- Mobile-first web support before a future native application

Do not describe Sleeper or Yahoo as functional, supported, or available until their provider branches are merged and validated.

The implemented code still contains the workspace model until the roadmap cutover branches land. Treat that code as the legacy baseline being replaced, not as a contract to extend.

## Superseding Deployment Decision

The hosted SaaS deployment will be initialized as a new deployment.

Do not design new work around preserving, backfilling, or migrating legacy single-installation production data into the hosted SaaS unless Henry explicitly changes this decision.

Backward compatibility is not required for:

- Existing version-1 installation data
- Existing single-installation production databases
- The retired single-value `PROVIDER_CREDENTIAL_KEY` setting
- Legacy installations that predate the versioned provider credential keyring

Any remaining text that describes preserving the v1.0.1 installation as the first League Workspace is historical. Do not implement those migration requirements.

Development compatibility paths may remain temporarily when explicitly required by the active roadmap, but they must not expand into permanent SaaS contracts.

## Non-Negotiable Product and Security Invariants

### Independent personal accounts

- Every person has an independent account. No other account administers, shares, or controls it.
- Email is the sole login identity. Generated usernames, where they still exist during the cutover, are internal storage values and never login credentials.
- Do not add administrator roles, spectator roles, invitations that confer control, shared accounts, or shared passwords.
- Support multiple concurrent sessions per account. Password reset and consequential account-security actions revoke all sessions.

### Canonical shared leagues

- A provider league is a shared canonical resource identified by provider and normalized provider league ID. Seasons are identified by canonical league and season year.
- The first importer creates the record when absent, does not own it, cannot administer or remove other users, and cannot decide whether another user retains access.
- ESPN league IDs are validated as digits and stripped of leading zeroes.
- The database uniqueness constraint is the authoritative concurrency control. Application-level existence checks are not sufficient.
- Do not invent Sleeper or Yahoo identity rules. Establish them from provider evidence in their provider branches.

### Per-user, per-season authorization

- Authorization is persisted per user and season in `user_league_season_access` with uniqueness on user and season.
- Every league-season read, analytic, synchronization-status read, and default-season selection requires *readable access* for the requesting user: an `active` or `reverification_required` row. `reverification_required` serves prior successful data with a bounded indicator, and without such data shows only the season summary. Every other league-season mutation requires *active access* (`status = active`).
- *Active access* alone counts for synchronization credential eligibility, orphan retention, and destructive lifecycle decisions. `revoked` is never readable or active.
- Existing records, another user's access, cached data, or another user's credentials never prove authorization for a new user.
- Possessing or guessing an internal League or Season ID never grants access.
- Inaccessible and nonexistent resources return the same uniform response and never reveal that a canonical league exists.
- Add negative cross-user tests whenever league-data behavior changes.
- Revoke access only on definitive provider evidence. Expired credentials, 401 alone, timeouts, outages, rate limits, generic authentication failure, and ambiguous ESPN 403 or 404 mark a connection invalid or set `reverification_required` instead.

### Proof-first private import and atomic import

- Authenticate and verify the user, require CSRF, and persist throttle state in a short committed operation.
- Perform provider proof with no open database transaction, using only anonymous access or the requesting user's own credentials.
- Persist no League, Season, access, or connection state after failed proof.
- After successful proof, one atomic transaction upserts the user's connection, inserts or retrieves the canonical league and season, clears `orphaned_at`, inserts or reactivates the access row, queues at most one synchronization job, and sets a default season when absent.
- Simultaneous imports create one league, one season, one access row per user, and at most one active job. A failed import never deletes another user's records or access.

### Credentials and secrets

- Provider credentials belong to the user who supplied them, never to the canonical league, and are never visible to or used to prove access for another user.
- Never print, log, commit, expose, or place secrets in agent memory.
- Never read `.env`, `.env.prod`, production credentials, private keys, or unrelated home-directory files unless Henry explicitly authorizes the exact access.
- Never include provider credentials, password hashes, session hashes, reset tokens, verification tokens, API keys, or raw provider responses in errors or fixtures.
- Preserve the versioned provider credential keyring design. Do not restore the retired single-key credential design.
- Store account fingerprints only as HMACs.
- Use sanitized deterministic fixtures for provider behavior.

### League-season synchronization

- Synchronization is owned by the league season. The active-job idempotency key is `league-season-sync:{season_id}`.
- Jobs contain no secrets and reveal no credential-owner identity. Status responses never disclose whose credentials are used, connection IDs, or another user's details.
- Public seasons synchronize anonymously where supported. Private seasons use any currently valid connection whose owner holds active access to that season.
- **No provider network call may run inside an open database transaction.** Load and select in a short transaction, fetch with no transaction, then persist in a short transaction under the season advisory lock.

### Shared-data deletion safety and retention

- Removing one user's league connection removes only that user's access rows and never deletes shared data while another user retains active access.
- Account deletion removes personal data, sessions, preferences, user-owned connections, and access rows, but never shared league data while another user retains active access.
- When the last active access disappears, mark the league orphaned, set `orphaned_at`, and retain the data for 30 days. Re-import during retention revives the same record. After 30 days a purge job locks the league and deletes it only after confirming zero active access relationships.
- Do not retain credentials merely because orphaned league data remains.

### Player and scoring boundaries

- Canonical NFL player identity and global raw weekly statistics are later work and are not part of the account cutover. NFL team is not immutable player identity.
- League-specific records (ownership, rosters, lineups, matchups, waivers, transactions, keepers, schedule, provider-applied points) stay season-specific.
- Scoring configuration belongs to the league season, with a normalized form and a deterministic scoring-profile fingerprint.
- Launch continues to use ESPN provider-supplied fantasy points. Independent NFL-statistics ingestion is separately estimated and is not launch-critical.

### Registration

- Registration modes must be enforced by the backend.
- Public registration remains closed until the account-model cutover, authorization, import, synchronization, and frontend selection flows are complete and validated. Current staging is not acceptance evidence for the retired workspace model.
- Email verification and recovery tokens must be hashed, expiring, revocable, and protected against account enumeration.

## Architecture Boundaries

- Keep the application as an extraction-ready modular monolith.
- Provider-specific API behavior belongs under `backend/app/providers/`.
- Provider data must be normalized into canonical domain objects before analytics use it.
- API modules should coordinate request and response behavior rather than absorb domain logic.
- Business workflows belong in services.
- Database access belongs in repository or persistence boundaries.
- League-season authorization must not depend on globally selecting the first or only row, or on a client-supplied league or season ID alone.
- Background jobs are keyed by league season and must not contain secrets or credential-owner identity.
- Do not introduce an abstraction without a current concrete use.
- Prefer descriptive names over generic names such as `canonical.py` when a clearer domain name exists.

## Frontend Design Rules

Use the existing mobile-first flat football design system:

- Deep football green, white, and neutral grays
- Barlow for body text
- Barlow Condensed for sports headings, scores, and compact labels
- Desktop sidebar and mobile sliding drawer
- Square or minimally rounded rectangular surfaces
- Color, typography, spacing, and borders for hierarchy

Do not introduce:

- Drop shadows
- Gradients
- Neon or glow effects
- Pop-in, lift, or scaling hover effects
- Excessively rounded cards
- A new component library without approval

Prioritize the selected league season's team information, current standings, synchronization state, and clear mobile behavior. Preserve the acceptable desktop experience while improving mobile usability.

## Required Reading

Read only the documents relevant to the task. Do not load every ADR without a reason.

For consequential SaaS work, begin with:

- `docs/product/SAAS_REQUIREMENTS.md`
- `docs/roadmap/SAAS_MIGRATION_ROADMAP.md`
- `docs/architecture/SAAS_TARGET_ARCHITECTURE.md`

Read ADR 0010 for any account, league, access, import, or synchronization work. Then read only the relevant task-specific ADR:

- Account model, canonical leagues, access, import, synchronization: `docs/architecture/0010-Personal-Accounts-and-Shared-Canonical-Leagues.md`
- Identity mechanics (verification, recovery, throttling): `docs/architecture/0005-SaaS-Identity-Registration-and-Sessions.md` (partially superseded)
- Job mechanics: `docs/architecture/0006-Resumable-Onboarding-and-Background-Jobs.md` (partially superseded)
- Provider data and credentials: `docs/architecture/0007-SaaS-Provider-Data-and-Credential-Strategy.md` (partially superseded)
- Schema direction: `docs/architecture/0002-Canonical-Season-and-Provider-Schema.md` (partially superseded)
- Email: `docs/architecture/0009-Durable-Transactional-Email.md`
- Deployment: `docs/architecture/0008-Homelab-SaaS-Deployment-and-Recovery.md`
- Historical only: `docs/architecture/0004-League-Workspace-Multi-Tenancy.md` (superseded; do not use for new work)

Use `docs/DEVELOPMENT.md` for development commands and `docs/OPERATIONS.md` for operational behavior.

Inspect relevant source, tests, Git history, pull requests, issues, and CI results when needed. Do not inspect every historical merge for every task.

## Working Tree and Branch Safety

Before changing files:

1. Run `git status --short --branch`.
2. Identify the current branch and its relationship to `main`.
3. Identify all pre-existing modified and untracked files.
4. Do not alter, format, stage, stash, restore, or commit pre-existing work unless the task explicitly includes it.
5. Stop if ownership of existing changes is unclear.
6. Use a dedicated feature branch or isolated Git worktree.
7. Never work directly on `main`.

Do not use destructive Git commands, including:

- `git reset --hard`
- `git clean`
- `git restore` on user-owned changes
- Force push
- History rewriting

Do not stage unrelated changes.

## Decision and Approval Boundaries

Within an approved task, agents may:

- Read repository files and documentation
- Inspect Git history, GitHub issues, pull requests, and CI results
- Edit in-scope source, tests, and documentation
- Run approved development commands and local tests
- Diagnose failures and make in-scope corrections
- Summarize changes and validation evidence

Stop and request approval before:

- Adding, removing, or upgrading a runtime dependency
- Creating or modifying a database migration unless explicitly in scope
- Changing a public API contract
- Changing authentication, league-season authorization, canonical league lifecycle, or session policy beyond the approved task
- Changing an architecture decision
- Adding a network-exposed service
- Reading secrets or files outside the repository
- Pushing a branch
- Opening or materially modifying a pull request
- Performing a deployment
- Changing Cloudflare, DNS, firewall, GitHub, or billing configuration

Agents must not:

- Merge to `main`
- Approve their own pull request
- Access or modify production data
- Rotate credentials
- Force-push
- Delete databases, Docker volumes, branches, or user data
- Disable tests or security controls to make validation pass
- Modify unrelated projects under the user's home directory

## Implementation Workflow

For each implementation task:

1. Read the task and relevant governing documentation.
2. Inspect the worktree, branch, implementation, tests, and relevant history.
3. Return a plan before consequential or cross-cutting edits.
4. Identify assumptions, risks, affected modules, and approval-required decisions.
5. Implement only the approved scope.
6. Run targeted validation first.
7. Repair failures caused by the change.
8. Run the full required validation for affected areas.
9. Review the complete diff for unrelated changes.
10. Map acceptance criteria to code and tests.
11. Report unresolved risks and decisions.
12. Wait for approval before commit, push, or pull-request actions unless explicitly authorized.

Do not combine unrelated roadmap slices in one branch.

## Validation

Run commands from the documented directories.

### Backend

From `backend/`, run:

```bash
uv run ruff format --check app tests
uv run ruff check app tests
uv run pytest -q
uv run alembic heads
uv run alembic current
```

Use targeted tests during implementation. Run the complete backend suite before declaring backend work complete unless Henry explicitly approves a narrower validation gate.

### Frontend

From `frontend/`, run:

```bash
npm run lint
npm test
npm run build
npm audit --audit-level=low
```

Run frontend validation when frontend code, shared contracts, dependencies, or build configuration change.

### Documentation-only work

At minimum, run:

```bash
git diff --check
```

Also verify filenames, links, cross-references, and consistency with governing architecture decisions.

### Database migrations

Migration work must follow `docs/DEVELOPMENT.md`.

At minimum:

- Verify one Alembic head.
- Upgrade a disposable PostgreSQL database to head.
- Compare the migrated schema with SQLAlchemy metadata when applicable.
- Downgrade the disposable database to base.
- Verify application tables and owned enum types are removed.
- Upgrade the disposable database to head again.
- Run the complete backend test suite.
- Confirm the normal development database was not used for destructive testing.

Never test downgrade behavior against production.

## Test Quality

Tests must establish requirements rather than merely repeat implementation details.

Include relevant:

- Success behavior
- Validation failures
- Authorization failures
- Cross-user and cross-season negative cases
- Concurrency and transaction behavior
- Rollback and partial-state assertions
- Privacy-safe errors
- Concurrent-session behavior and session revocation
- Provider failure behavior
- Regression coverage

Do not delete, weaken, skip, or broadly mock tests merely to obtain a passing suite.

## Definition of Done

Work is complete only when:

- The approved acceptance criteria are satisfied.
- The implementation follows governing architecture.
- Product and security invariants remain intact.
- Relevant tests were added or updated.
- Targeted validation passes.
- Required full validation passes.
- The final diff contains no unrelated changes.
- Documentation is updated when behavior or operations changed.
- Migration and rollback behavior is documented when applicable.
- Known limitations and risks are reported.
- Commit, push, pull-request, merge, and deployment actions remain within granted authority.

A passing test suite is necessary but does not by itself prove the work is correct.

## Completion Report

Before requesting approval, report:

- Objective completed
- Acceptance-criteria mapping
- Files changed and why
- Architecture and security impact
- Tests added or changed
- Exact validation commands run
- Pass or fail results
- Failures encountered and how they were resolved
- Unresolved risks or follow-up work
- Decisions made that were not explicitly stated in the task
- Whether the worktree contains pre-existing or unrelated changes
