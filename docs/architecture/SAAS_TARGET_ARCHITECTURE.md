# Fantasy What-If SaaS Target Architecture

## Document Status

- **Status:** Draft, rewritten for ADR 0010
- **Governing decision:** `docs/architecture/0010-Personal-Accounts-and-Shared-Canonical-Leagues.md`
- **Target:** Hosted SaaS of independent personal accounts and shared canonical leagues
- **Initial hosting:** Homelab
- **Initial capacity:** 50 to 100 registered users
- **Architecture style:** Modular monolith
- **Primary database:** PostgreSQL
- **Initial job queue:** PostgreSQL-backed
- **Providers:** ESPN is implemented. Sleeper and Yahoo are intended public-launch providers and are **not implemented**.
- **Registration:** Public registration remains closed until the account-model cutover is complete.

## Purpose

This document consolidates the accepted architecture into one implementation-oriented design. ADR 0010 governs the account and league model. The League Workspace design that earlier versions of this document described (workspace tenant, administrator and spectator roles, exclusive league claims, onboarding activation, workspace deletion) is superseded. It survives only in Git history and in ADRs 0004 through 0007 as historical records.

Governing decisions:

- `docs/product/SAAS_VISION.md`
- `docs/product/SAAS_REQUIREMENTS.md`
- `docs/architecture/0001-Portable-Multi-Provider-Fantasy-Platform.md`
- `docs/architecture/0002-Canonical-Season-and-Provider-Schema.md` (partially superseded)
- `docs/architecture/0003-Canonical-Provider-Adapter-contract.md`
- `docs/architecture/0005-SaaS-Identity-Registration-and-Sessions.md` (partially superseded)
- `docs/architecture/0006-Resumable-Onboarding-and-Background-Jobs.md` (partially superseded)
- `docs/architecture/0007-SaaS-Provider-Data-and-Credential-Strategy.md` (partially superseded)
- `docs/architecture/0008-Homelab-SaaS-Deployment-and-Recovery.md`
- `docs/architecture/0009-Durable-Transactional-Email.md`
- `docs/architecture/0010-Personal-Accounts-and-Shared-Canonical-Leagues.md`

## Implementation Status

The implemented code still contains the workspace model. The branch sequence in `docs/roadmap/SAAS_MIGRATION_ROADMAP.md` replaces it. This document describes the target. Where it names a table or route that does not yet exist, it is target direction and not current behavior.

## Architecture Summary

Fantasy What-If will remain a modular monolith deployed through multiple process roles.

The target components are:

```text
Browser or future native client
              |
              v
       Cloudflare edge
              |
              v
      Frontend Nginx
              |
              v
        FastAPI API
              |
              v
         PostgreSQL
          /       \
         v         v
Background      Scheduler
 Worker
```

The same backend source and image may provide:

```text
API process
Worker process
Scheduler process
Migration process
Operator commands
```

The system will not begin with microservices.

## Core Product Boundary

The product boundary is the **personal account** plus the **league season**:

- A personal account owns its credentials, sessions, preferences, and provider connections.
- A canonical league season owns shared provider data and derived analytics, and belongs to no user.
- A `user_league_season_access` row is the only authorization link between them.

There is no tenant boundary, administrator, spectator, workspace slug, or shared password. The security boundary is the per-user, per-season access check applied to every league-data read.

## Modular Monolith Boundaries

```text
backend/app/
├── api/            request and response coordination
├── services/       business workflows (import, access, sync, deletion)
├── repositories/   persistence
├── providers/      provider adapters (ESPN implemented)
├── jobs/           background job handlers
└── domain/         canonical objects and rules
```

API modules coordinate requests. Workflows live in services. Database access lives in repositories. Provider-specific behavior lives only under `providers/` and is normalized into canonical objects before analytics see it.

## Target Data Model

### User

Display name, email, normalized email, password hash, email verification time, account status, and timestamps. Final retirement removes workspace, role, and username fields.

### Authentication Session

Hashed random token bound to a user. Multiple concurrent sessions per user. Revocation records a reason. Password reset and security actions revoke all sessions.

### Email Verification and Password Reset Tokens

Hashed, expiring, single-use, revocable, issued by the durable email worker (ADR 0009).

### User Preferences

`user_id` and `default_league_season_id`. The application verifies the user retains access, and clears the preference when access disappears.

### Provider Connection

User-owned. Fields: `user_id`, provider, encrypted credentials, credential version, schema version, account fingerprint stored as an HMAC, connection status, last validation time, and last safe failure code. Never attached to a league and never visible to another user.

### League

Canonical, unowned. Unique on provider and normalized provider league ID. Carries `orphaned_at`.

### Season

Unique on league and season year. Carries `sync_state` (`never_synced`, `queued`, `syncing`, `fresh`, `stale`, `failed`), `last_successful_sync_at`, `last_sync_attempt_at`, `last_sync_failure_code`, `data_version`, and the last-observed access mode.

### User League Season Access

One row per user and season (unique). Fields: `user_id`, `season_id`, nullable `provider_connection_id`, `proof_mode`, `verified_at`, `last_reverified_at`, `status` (`active`, `reverification_required`, `revoked`), `created_at`, `updated_at`.

### Background Job

Season-keyed synchronization and email delivery jobs. Payloads contain no secrets and no credential-owner identity. Existing leases, retries, and active-idempotency uniqueness are reused.

### Audit Event

Structured events with no secrets. See Audit Architecture.

## Account Lifecycle

```text
Register (display name, email, password)
  -> verification email queued durably
  -> verify email
  -> authenticated
      no accessible leagues -> "Import your first league"
      one or more           -> personal league dashboard
```

Registration never creates a workspace, role, or league claim.

## Authentication Design

- Email and password only. Login failures are uniform and throttled.
- Browser sessions use secure cookies and CSRF protection on every mutation.
- Multiple concurrent sessions per account.
- Password reset and consequential security actions revoke every session.
- Future native authentication remains a separate decision. Authorization is shared across clients.

## Authorization Design

Every league-data request:

1. Authenticates the user.
2. Resolves the requested season.
3. Requires readable access for that user and season: an `active` row, or a `reverification_required` row that serves prior successful data with a bounded indicator (only the season summary when no such data exists). A `revoked` row is never readable.
4. Returns the uniform inaccessible-resource response otherwise.

Authorization never selects a first or only row, never trusts a client-supplied ID alone, and never infers access from existing records, another user's access, or another user's credentials.

Active access means `status = active`. Synchronization credential selection, orphan retention, and purge decisions count only active access; `reverification_required` and `revoked` never count.

## Safe Resource Lookup

Lookups for league data join through the user's access row, so a missing row, a revoked row, and a nonexistent season are indistinguishable. Responses never reveal whether a canonical league exists, who else has access, or whose credentials are used.

## League Import Architecture

Target endpoint: `POST /api/league-imports` (path may be refined).

```text
T0   short committed transaction
       authenticate verified user, require CSRF, persist throttle state
PROOF  no database transaction open
       provider request using anonymous access or the requesting user's credentials
       verify returned target and season; uniform failure; persist nothing on failure
T1   atomic transaction
       upsert user's provider connection when credentials supplied
       insert or retrieve canonical league (unique provider + normalized ID)
       coordinate with orphan purge; clear orphaned_at
       insert or retrieve season
       insert or reactivate user-season access
       queue one league-season sync job if needed
       set default season when absent
```

Same-user repeats are idempotent. Concurrent imports yield one league, one season, one access row per user, and at most one active job. A failure rolls back only the requester's transaction.

## Access Reverification

Private access is reverified every seven days initially. Expired credentials, HTTP 401 alone, timeouts, outages, rate limits, generic authentication failure, and ambiguous ESPN 403 or 404 mark the connection invalid or set `reverification_required`. Only definitive provider evidence revokes access. A previously public season that becomes private requires private proof.

Implemented for ESPN as the durable `access_reverification` job, one per user and season (`access-reverification:{user_id}:{season_id}`). ESPN cannot distinguish removed membership from an unavailable target, so automation never sets `revoked`; the terminal automated state is `reverification_required`, retried weekly. Proof is anonymous first, then only the user's own stored credentials, outside any transaction, and a result is persisted only if the access row, user, connection and job lease are unchanged since preparation. Rejected credentials invalidate only the owner's connection; anonymous success moves the user to public proof without deleting the connection.

## Provider Architecture

Provider adapters implement the canonical provider contract and live under `backend/app/providers/`.

- ESPN is implemented. Canonical identity is `espn` plus the normalized league ID (digits validated, leading zeroes stripped). Season identity is the canonical league plus the season year.
- Sleeper is **not implemented.** Identity, authentication, privacy, and season behavior must be established from evidence in its provider branch.
- Yahoo is **not implemented.** OAuth callback exposure is a later consequential network and deployment review.

Provider credentials are encrypted with the versioned `PROVIDER_CREDENTIAL_KEYS` keyring, belong to the supplying user, and are decrypted only inside a short transaction immediately before a provider call.

## Credential Flow

1. A user submits credentials for import or reconnection.
2. Proof runs with those credentials and no open transaction.
3. On success, T1 encrypts and upserts the connection for that user.
4. Synchronization selects a connection by joining active access rows to active user-owned connections. No identity of the owner is returned or placed in a job payload.

Credentials are not retained merely because orphaned league data remains.

## Background Job Architecture

PostgreSQL-backed jobs with leases, retries, and active-idempotency uniqueness are reused. Process roles are API, worker, scheduler, and migration.

Job types: `league_season_sync`, `access_reverification`, `orphan_league_purge`, `transactional_email_delivery`, and deletion-related jobs defined by the lifecycle branches.

Idempotency: the active key for synchronization is `league-season-sync:{season_id}`. Only one active synchronization job may exist per season.

## Synchronization Design

1. **Short transaction:** load the season, select and decrypt an eligible connection when private, commit and close.
2. **Provider request:** no database transaction open. Public seasons use anonymous access where supported.
3. **Short persistence transaction:** acquire the season advisory transaction lock, compare `data_version` when needed, persist replacement canonical data, rebuild derived analytics, update sync state and the `SyncRun`, commit.

| Sync state | User-visible behavior on import of an existing season |
| --- | --- |
| `fresh` | Reuse immediately. |
| `stale` | Show existing data and queue one refresh. |
| `queued` / `syncing` | Show prior successful data if present, otherwise an importing state. |
| `failed` | Show prior data with a warning and permit retry, otherwise a failed-import state with retry. |

Status responses never disclose credential owners, connection IDs, secrets, or other users' details.

## Synchronization Locks

The season advisory transaction lock serializes persistence per season. The per-season active-job uniqueness prevents duplicate queued work. Locks are held only inside short persistence transactions.

## Scheduler Design

The scheduler enqueues refreshes for stale seasons, periodic access reverification, orphan-purge sweeps, and email maintenance. Scheduled work uses jitter and respects provider concurrency. The personal-maintenance scan runs every six hours independently of workspaces, enqueues at most 100 season refreshes and 100 reverifications per scan, and staggers their availability by ten seconds each.

## Provider Concurrency

## Provider Concurrency

The worker must support provider-specific concurrency controls.

A conservative initial setting is:

```text
One active synchronization job per provider
```

This is a safe starting point, not a verified provider limit.

Concurrency may be increased only after provider behavior and homelab capacity are measured.

## Lifecycle and Deletion

### Removing a league connection

Removes or deactivates only the user's access rows for every season of that league. Clears a default-season preference that pointed at those seasons. Never deletes canonical data while another user retains active access, and never touches another user's connection.

### Account deletion

Removes the user, sessions, verification and reset records, preferences, user-owned provider connections, access rows, and other personal data. Shared league data is untouched while another user retains active access. Deletion email follows ADR 0009.

### Orphan retention

When the final active access disappears: set `orphaned_at`, retain for 30 days, revive the same record on re-import (clearing `orphaned_at`), and after 30 days let a purge job lock the league and delete it only after confirming zero active access relationships. T1 coordinates with the purge by locking the league row.

## API Structure

Representative target routes (names may be refined):

```text
POST   /api/auth/register
POST   /api/auth/login
POST   /api/auth/logout
GET    /api/auth/me
POST   /api/league-imports
GET    /api/leagues                      connected leagues and seasons
DELETE /api/leagues/{league_id}/access    remove this user's access
GET    /api/seasons/{season_id}/sync-status
PUT    /api/preferences/default-season
DELETE /api/account
```

Every `{league_id}` and `{season_id}` route resolves access through the user's access row.

## Stable Error Contract

Errors use stable machine-readable codes and safe messages. Inaccessible and nonexistent resources return the same code and status. Errors never include credentials, raw provider responses, or the existence of a canonical league.

## Frontend Architecture

Preserve the existing mobile-first flat football design system. Target capabilities: connected-leagues list, league and season switcher, import another league, default-season preference, empty-account state, import and synchronization states, owner-only credential-reconnection messaging, and query-cache isolation by season.

Registration fields: display name, email, password, confirm password.

Retired or rewritten: administrator and spectator split login, workspace slug login, workspace registration fields, league-claim onboarding, shared spectator setup, activation step, league-already-connected page, workspace-unavailable page, workspace deletion and recovery, the admin route and page, and implicit single-current-league assumptions.

## Email Architecture

Durable transactional email (ADR 0009) delivers verification, resend, password reset, password-changed, and account-deletion notices through queued deliveries and the worker. Registration success means delivery was queued.

## Operator Boundary

Operator commands may disable registration, pause synchronization, re-enqueue jobs, and inspect queue health. Operators never see provider credentials or session secrets.

## Security Model

- Argon2 password hashing, hashed session tokens, CSRF, trusted-origin validation, throttling, encrypted credentials, safe errors, secure cookies.
- Per-user, per-season authorization on every league-data read.
- Provider-proof attempts throttled per user before any provider call.
- Account fingerprints stored as HMACs.
- No provider network call inside an open transaction.
- Jobs and status responses carry no secrets or credential-owner identity.

## Audit Architecture

Structured audit events for registration, login, password reset, session revocation, league import, access creation, reverification outcome, access revocation, connection invalidation, account deletion, and orphan purge. No secrets in events. Audit infrastructure is required before public launch.

## Observability

Track API, database, queue, and scheduler health, synchronization success and failure by state, reverification outcomes, request latency, errors, disk, backups, and certificate expiry. Avoid high-cardinality labels that expose user or league identity.

## Backup and Recovery

## Backup and Recovery

The target recovery design requires:

- Automated PostgreSQL backup
- Encrypted off-host backup
- Backup integrity verification
- Encryption-key recovery
- Quarterly restore test
- Documented deployment recovery
- Documented migration recovery

Initial targets:

```text
Recovery Point Objective:
24 hours

Recovery Time Objective:
8 hours
```

These are operational targets, not contractual service guarantees.

## Initial Deployment Roles

Production target:

```text
frontend
backend-api
backend-worker
sync-scheduler
database
migrate
backup
monitoring
```

The API, worker, scheduler, migration, and operator commands may use one backend image.

## Initial Capacity Strategy

The first production configuration should begin with:

```text
1 API container
1 worker container
1 scheduler container
1 frontend container
1 PostgreSQL container
```

Scale only after measuring:

- CPU
- Memory
- Database connections
- Queue wait time
- API latency
- Provider concurrency
- Backup impact
- Disk growth

## Player and Scoring Architecture Direction

Later work (not part of the account cutover):

- Canonical identity: `nfl_players`, `provider_player_mappings`, unresolved mappings. NFL team is not immutable identity. Affiliation is season-aware or week-aware.
- Global raw weekly NFL statistics stored once by canonical player, NFL season, week, and statistics source.
- League-season records remain season-specific: ownership, rosters, lineup slots, matchups, waivers, transactions, keepers, schedule, provider-applied points.
- Scoring configuration belongs to the league season: raw provider payload, normalized representation, normalizer version, and deterministic scoring-profile fingerprint. Identical normalized configurations reuse one profile.
- Calculated points are versioned by canonical player, NFL season, week, scoring-profile fingerprint, and calculator version.
- Launch continues to use ESPN provider-supplied fantasy points. Independent NFL-statistics ingestion is separately estimated and not launch-critical.

## Migration Strategy

The deployment is new, with no legacy-data migration. Schema work is additive first (canonical access schema), then behavior (import, synchronization, authorization), then the account-only identity cutover, frontend selection, deletion and retention, and finally retirement of the workspace schema. See the roadmap. Public registration stays closed throughout.

## Testing Strategy

- Unit, repository, service, API, provider-contract, migration, and frontend tests as in prior practice.
- **Cross-user test standard:** every league-data endpoint has negative tests proving that another user, a guessed ID, another user's connection, and cached data grant nothing, and that inaccessible and nonexistent responses are identical.
- Concurrency tests: simultaneous imports yield one league, one season, one access row per user, and at most one active job. Import versus orphan purge. Account deletion versus import.
- Transaction tests: no provider call occurs inside an open transaction.
- Reverification tests: ambiguous outcomes never revoke.
- Frontend tests: empty-account routing, switcher, season cache isolation, owner-only reconnection messaging.

## Performance Targets

Initial application targets:

```text
Database-backed analytics p95:
Under 500 ms

Login response p95:
Under 1 second excluding deliberate hashing cost

Registration validation p95:
Under 1 second

Health endpoint p95:
Under 250 ms

Ordinary API payload:
Preferably under 1 MB

Provider synchronization:
Asynchronous
```

These targets must be measured under representative homelab load.

## Failure Modes

- **Provider unavailable:** show prior successful data with a warning, keep access, mark reverification as needed.
- **Worker unavailable:** jobs stay queued and visible. Prior data remains readable.
- **PostgreSQL unavailable:** the service is unavailable. No partial writes.
- **Email provider unavailable:** deliveries retry durably. Registration readiness may close registration.
- **Credential owner's connection invalid:** private synchronization falls back to another eligible connection, otherwise the season becomes `failed` or `stale`, and only the owner sees reconnection messaging.
- **Encryption key missing:** fail closed and report operator-visible, secret-free errors.

## Open Decisions

- Sleeper and Yahoo identity, authentication, privacy, and season behavior.
- Yahoo OAuth callback exposure review.
- ESPN evidence for definitive loss of access.
- ESPN all-zero league ID and identity-version handling.
- Credential-derived value for the account HMAC fingerprint.
- Independent NFL-statistics source and licensing.

## Success Criteria

1. A person registers one personal account and imports multiple leagues across providers.
2. Several people in one provider league each prove access and share canonical data.
3. No user can read a season without independent proof and readable (`active` or `reverification_required`) access.
4. Inaccessible and nonexistent resources are indistinguishable.
5. Removing a connection or deleting an account never harms another authorized user.
6. Orphaned leagues are retained 30 days and purged only with zero active access.
7. No provider call runs inside an open transaction.
8. Public registration opens only after the cutover is complete and validated.
