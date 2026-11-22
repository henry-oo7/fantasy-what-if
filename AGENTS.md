# Fantasy What-If Agent Instructions

## Purpose

This file defines repository-wide instructions for AI coding agents and human-assisted automation. These rules apply regardless of model or agent provider.

Fantasy What-If is being converted from a self-hosted, single-installation application into a hosted, shared-database, multi-workspace SaaS initially operated from Henry's homelab for approximately 50 to 100 registered users.

The application remains a modular monolith. Do not introduce microservices, Kubernetes, Redis, RabbitMQ, Kafka, or another infrastructure dependency unless a governing architecture decision explicitly requires it.

## Instruction Priority

When instructions conflict, use this order:

1. The current task or GitHub issue approved by Henry
2. This `AGENTS.md`
3. Accepted architecture decision records in `docs/architecture/`
4. `docs/product/SAAS_REQUIREMENTS.md`
5. `docs/roadmap/SAAS_MIGRATION_ROADMAP.md`
6. `docs/DEVELOPMENT.md`, `docs/OPERATIONS.md`, and other repository documentation
7. Existing implementation behavior
8. Inferences from Git history

Do not silently choose between meaningful conflicts. Stop, identify the conflicting sources, explain their consequences, and request a decision.

## Current Product Direction

The target is a hosted multi-tenant SaaS with:

- League Workspace as the tenant and security boundary
- One administrator identity per workspace
- Zero or one shared spectator identity per workspace
- One claimed provider league per workspace in the initial release
- Administrator login by globally unique verified email
- Spectator login by workspace slug and shared spectator password
- At most one active administrator session per workspace
- Multiple concurrent spectator sessions
- ESPN as the first operational provider
- Sleeper and Yahoo as planned providers
- PostgreSQL-backed background jobs
- Separate development and production environments
- Mobile-first web support before a future native application

## Superseding Deployment Decision

The hosted multi-workspace SaaS deployment will be initialized as a new deployment.

Do not design new work around preserving, backfilling, or migrating legacy single-installation production data into the hosted SaaS unless Henry explicitly changes this decision.

Backward compatibility is not required for:

- Existing version-1 installation data
- Existing single-installation production databases
- The retired single-value `PROVIDER_CREDENTIAL_KEY` setting
- Legacy installations that predate the versioned provider credential keyring

Some governing documents still describe preserving the v1.0.1 installation as the first League Workspace. Treat that language as a known documentation conflict. Do not implement those migration requirements. Update the governing documents in a dedicated documentation slice before relying on them for implementation decisions.

Development compatibility paths may remain temporarily when explicitly required by the active roadmap, but they must not expand into permanent SaaS contracts.

## Non-Negotiable Product and Security Invariants

### Workspace isolation

- Every authenticated request must resolve an authorized workspace context before accessing workspace-owned data.
- Queries, mutations, synchronization, analytics, jobs, sessions, and provider credentials must remain scoped to the authenticated workspace.
- Never trust a client-provided workspace ID without independently validating access.
- Add negative tests for cross-workspace access whenever workspace-owned behavior changes.
- Never weaken workspace isolation to preserve legacy global behavior.

### Provider league ownership

- A normalized provider league identity may belong to at most one League Workspace.
- The database constraint is the authoritative concurrency control.
- Application-level availability checks are not sufficient.
- Concurrent attempts to claim the same provider league must allow at most one success.

### Atomic duplicate-claim failure

A rejected duplicate league claim must not leave partial tenant-owned state, including:

- Workspace
- User
- Authentication session
- Provider connection
- Provider credentials
- League
- Season
- Team
- Matchup
- Player data
- Synchronization run
- Successful-registration audit event

The response may reveal only that the provider league is already connected. It must not reveal the existing owner, administrator, spectator, workspace ID, account state, season data, synchronization activity, or credentials.

### Account model

- The initial SaaS release has exactly one administrator per activated workspace.
- Do not add administrator invitations, multiple administrators, role transfer, or one administrator controlling multiple workspaces.
- Each workspace has one shared spectator account, not individual spectator accounts.
- Administrator email login is the target public authentication contract.
- Legacy username login is temporary compatibility behavior.
- Do not add new username-login dependencies or expand username login to newly registered SaaS administrators.
- Eventual removal of public username login must remain tracked in migration planning.

### Credentials and secrets

- Never print, log, commit, expose, or place secrets in agent memory.
- Never read `.env`, `.env.prod`, production credentials, private keys, or unrelated home-directory files unless Henry explicitly authorizes the exact access.
- Never include provider credentials, password hashes, session hashes, reset tokens, verification tokens, API keys, or raw provider responses in errors or fixtures.
- Preserve the versioned provider credential keyring design.
- Do not restore the retired single-key credential design.
- Use sanitized deterministic fixtures for provider behavior.

### Registration and onboarding

- Registration modes must be enforced by the backend.
- Production registration remains closed until isolation, recovery, and operational launch gates are satisfied.
- Email verification and recovery tokens must be hashed, expiring, revocable, and protected against account enumeration.
- Provider network calls must not hold a database transaction open.
- League claiming must remain atomic.
- Initial synchronization must be recoverable and performed through durable background work as required by the architecture.
- Workspace activation requires internally consistent workspace-owned data.

## Architecture Boundaries

- Keep the application as an extraction-ready modular monolith.
- Provider-specific API behavior belongs under `backend/app/providers/`.
- Provider data must be normalized into canonical domain objects before analytics use it.
- API modules should coordinate request and response behavior rather than absorb domain logic.
- Business workflows belong in services.
- Database access belongs in repository or persistence boundaries.
- Workspace authorization must not depend on globally selecting the first or only row.
- Background jobs must include workspace ownership and must not contain secrets.
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

Prioritize signed-in team information, current standings, synchronization state, and clear mobile behavior. Preserve the acceptable desktop experience while improving mobile usability.

## Required Reading

Read only the documents relevant to the task. Do not load every ADR without a reason.

For consequential SaaS work, begin with:

- `docs/product/SAAS_REQUIREMENTS.md`
- `docs/roadmap/SAAS_MIGRATION_ROADMAP.md`
- `docs/architecture/SAAS_TARGET_ARCHITECTURE.md`

Then read only the relevant task-specific ADR:

- Multi-tenancy: `docs/architecture/0004-League-Workspace-Multi-Tenancy.md`
- Identity: `docs/architecture/0005-SaaS-Identity-Registration-and-Sessions.md`
- Onboarding and jobs: `docs/architecture/0006-Resumable-Onboarding-and-Background-Jobs.md`
- Provider data: `docs/architecture/0007-SaaS-Provider-Data-and-Credential-Strategy.md`
- Deployment: `docs/architecture/0008-Homelab-SaaS-Deployment-and-Recovery.md`

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
- Changing authentication, authorization, workspace ownership, or session policy beyond the approved task
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
- Cross-workspace negative cases
- Concurrency and transaction behavior
- Rollback and partial-state assertions
- Privacy-safe errors
- Session replacement or concurrency behavior
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
