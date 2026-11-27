# Fantasy What-If SaaS Requirements

## Document Status

- **Status:** Draft, rewritten for ADR 0010
- **Governing decision:** `docs/architecture/0010-Personal-Accounts-and-Shared-Canonical-Leagues.md`
- **Parent document:** `docs/product/SAAS_VISION.md`
- **Target deployment:** Hosted SaaS of independent personal accounts and shared canonical leagues
- **Initial capacity target:** 50 to 100 registered users
- **Initial hosting environment:** Henry's homelab
- **Initial sport:** Fantasy football
- **Registration:** Public registration remains closed (see Launch Gates)

## Purpose

This document defines the functional, security, data, provider, operational, and user-experience requirements for the hosted service. These requirements describe observable behavior. Database tables, API routes, queue design, and deployment topology belong in the architecture documents.

The earlier League Workspace requirements (one administrator, one shared spectator, workspace slug login, exclusive provider-league claims, workspace activation, and workspace deletion) are superseded by ADR 0010 and remain available in Git history and ADRs 0004 through 0007 as historical records only.

## Hosted Deployment Decision

- The hosted SaaS is a new deployment. No v1 data is migrated, imported, or preserved.
- SaaS registration and import are the only setup path. There is no installation setup wizard, bootstrap account, or `/api/setup` API.
- Users authenticate by verified email and password only. Generated internal usernames, where they still exist during the cutover, are never login credentials and are never returned or displayed.
- Provider credentials are encrypted only with the versioned `PROVIDER_CREDENTIAL_KEYS` keyring. There is no single-value key and no environment-based ESPN credential fallback.
- Runtime services resolve league data from the authenticated user's active season access or from a season-keyed job, and never search globally for the only league.

## Implementation Status

The implemented code still follows the retired workspace model. The requirements below describe the target. Until the account-model cutover completes, treat the workspace-era behavior as legacy baseline, and keep public registration closed.

- ESPN: implemented.
- Sleeper: **not implemented.**
- Yahoo: **not implemented.**

## Terminology

### Personal Account

An independent user account with a display name, a verified email, and a password. A person may connect multiple fantasy leagues across multiple providers. A personal account has no role and is not controlled by any other account.

### Canonical League

A shared provider league stored once, identified by provider and normalized provider league ID. A canonical league is not owned by any user. The first importer creates it when absent and gains no authority over it.

### League Season

A season of a canonical league, identified by canonical league and season year. Each season carries its own synchronization state and requires its own access proof.

### Season Access

A persisted per-user, per-season authorization relation (`user_league_season_access`) with statuses `active`, `reverification_required`, and `revoked`.

### Provider Connection

A user-owned, encrypted credential record for one provider. It is never attached to a canonical league and never visible to another user.

## Product Scope

A user can:

1. Open the hosted service and register a personal account.
2. Verify the email address.
3. Import a first league by proving access to a league season.
4. Import additional leagues and seasons across providers.
5. Switch between connected leagues and seasons.
6. View synchronization state and canonical analytics for each connected season.
7. Remove a league from the account, or delete the account.

Fantasy What-If is read-only with respect to providers. It never changes rosters, lineups, teams, scoring, schedules, or league administration. Product messaging targets fantasy players generally and must not imply that only commissioners may connect a league.

## Functional Requirements

### FR-1: Personal Account Registration

Registration collects display name, email, password, and confirmed password. Registration creates an independent account. It never creates a workspace, role, or league claim. Email is verified before the account can import leagues. Registration must be protected against account enumeration.

### FR-2: Registration Availability

The service operator must be able to enable, disable, or place registration into maintenance mode while existing logins continue. When registration is disabled the API returns a stable safe response (`registration_unavailable`) rather than relying on a hidden frontend control. **Public registration remains closed** until every launch gate below is satisfied.

### FR-3: Email Login

Email and password are the sole login. There are no administrator, spectator, workspace-slug, or username logins. Login failures are uniform and throttled.

### FR-4: Concurrent Sessions

An account may hold multiple concurrent sessions. Creating a session never revokes another. Password reset and consequential account-security actions revoke all sessions.

### FR-5: Provider Selection

The user selects a provider at import. ESPN is the only provider that can be selected. Sleeper and Yahoo may be displayed only as unavailable and must not be described as functional.

### FR-6: Proof-First League Import

Import must:

1. Authenticate and verify the user and require CSRF protection.
2. Persist throttle state in a short committed operation.
3. Perform provider verification with no open database transaction, using only anonymous access where applicable or credentials supplied by the requesting user.
4. Verify the returned provider target and season.
5. Return a uniform failure that does not reveal whether the canonical league already exists.
6. Persist no League, Season, access, or connection state after failed proof.
7. After successful proof, commit one atomic transaction (see FR-7).

### FR-7: Atomic Import and Idempotency

After proof, one transaction upserts the requesting user's provider connection when credentials were supplied, inserts or retrieves the canonical league and season through database uniqueness, coordinates with orphan purging, clears `orphaned_at`, inserts or reactivates the user's access row, queues at most one synchronization job, and sets a default season when absent. A repeat import by the same user is idempotent. Simultaneous imports yield one league, one season, one access row per user, and at most one active job. A failure rolls back only the requesting user's transaction.

### FR-8: Shared Canonical League

A second authorized user reuses the existing canonical records. There is no duplicate-claim rejection, no first-importer ownership, and no requirement that the first importer remain connected.

### FR-9: Per-Season Private Authorization

Every user must independently prove access to every private league season. Existing records, another user's access, cached data, or another user's credentials never prove authorization. Importing a new season requires fresh proof. Possessing or guessing an internal League or Season ID never grants access.

### FR-10: Uniform Inaccessible-Resource Responses

Missing access, revoked access, and nonexistent resources return the same response. Responses must not reveal whether a canonical league or season exists, who else has access, or whose credentials are used.

### FR-11: Access Reverification

Private access is reverified periodically, initially every seven days. Expired credentials, HTTP 401 alone, timeouts, outages, rate limiting, generic authentication failure, and ambiguous ESPN 403 or 404 never revoke access automatically. They mark the connection invalid or set the access to `reverification_required`. Access is revoked only on definitive provider evidence. A season that becomes private requires private proof.

### FR-12: League and Season Selection

The user can list connected leagues, switch between leagues and seasons, import another league, and set a default season. The application verifies that the user retains access to the default season and clears the preference when access disappears. Query caches are isolated by season. No code path may assume a single current league.

### FR-13: Synchronization State

Seasons expose `never_synced`, `queued`, `syncing`, `fresh`, `stale`, and `failed`. Importing an existing season reuses `fresh` data immediately, shows `stale` data while queuing one refresh, shows prior data (or an importing state) while `queued` or `syncing`, and shows prior data with a warning (or a failed-import state) plus a safe retry when `failed`. Status responses never disclose credential owners, connection IDs, secrets, or other users' details.

### FR-14: League-Season Synchronization

Synchronization is keyed by `league-season-sync:{season_id}` with at most one active job per season. Jobs carry no secrets and no credential-owner identity. Public seasons synchronize anonymously where supported. Private seasons use any valid connection whose owner has active access to that season. Provider requests never run inside an open database transaction.

### FR-15: Credential Reconnection

When a user's own provider connection becomes invalid, only that user sees reconnection messaging. Other users' access and synchronization are unaffected unless they depend solely on that connection.

### FR-16: Removing a League Connection

Removing a league removes or deactivates only the requesting user's access to every connected season of that league. It never deletes canonical data while another user retains active access, removes another user's connection, or interrupts another authorized user.

### FR-17: Account Deletion

Account deletion removes the user, sessions, verification and reset records, preferences, user-owned provider connections, season access, and other personal data. It never deletes shared league data while another user retains active access. Deletion confirmation email uses the durable email design of ADR 0009.

### FR-18: Orphan Retention

When the last active access relation disappears the canonical league is marked orphaned with `orphaned_at` and retained for 30 days. Re-import during retention revives the same record. After 30 days a purge job locks the league and deletes it only after confirming zero active access relationships. Credentials are not retained merely because orphaned data remains.

### FR-19: Analytics Isolation

Analytics, caches, exports, and derived results are keyed by season and returned only to users with readable access (`active` or `reverification_required`, under AR-1) to that season.

## Provider Requirements

### PR-1: Canonical Provider Contract

All providers produce the canonical domain objects defined by the provider architecture. Analytics must not consume raw provider objects.

### PR-2: Provider Identity Normalization

Each provider adapter defines deterministic, documented identity normalization. For ESPN the canonical identity is provider `espn` plus the normalized league ID (digits validated, leading zeroes stripped), and the season is the canonical league plus season year. Sleeper and Yahoo identity rules must not be invented here. Their provider branches establish identity, authentication, privacy, and season behavior from evidence.

### PR-3: ESPN Hardening

ESPN must support public and private leagues, encrypted `espn_s2` and `SWID` storage per user, correct future schedules, historical seasons, league and playoff settings, stable string identifiers, retry classification, safe authentication errors, sanitized deterministic fixtures, credential replacement, and credential deletion. Environment-level ESPN credential fallback must not be used.

### PR-4: Sleeper Integration (not implemented)

Sleeper is not implemented. Its provider branch must establish identity, authentication, privacy, season ancestry, scoring, and rate-limit behavior from evidence before any support claim.

### PR-5: Yahoo Integration (not implemented)

Yahoo is not implemented. Its provider branch must cover OAuth state and callback validation, token storage and refresh, revocation, league-key normalization, scopes, reauthorization, safe errors, and protection against callback-account confusion. OAuth callback exposure is a later consequential network and deployment review. Yahoo must not be exposed as implemented until token refresh and synchronization recovery are tested end to end.

### PR-6: Provider Capability Honesty

A provider or season advertises only supported analytics. The application never fabricates missing data. **Sleeper and Yahoo must not be claimed as supported until implemented.**

### PR-7: Provider Rate Protection

Synchronization protects providers and the application through per-provider concurrency limits, per-season locks, retry classification, backoff, safe handling of HTTP `429`, and scheduling jitter. Provider-proof attempts are throttled per user.

### PR-8: Player and Scoring Direction

Later work introduces canonical NFL player identity, provider player mappings, globally stored raw weekly statistics, and league-season scoring profiles with a deterministic fingerprint (ADR 0010). These do not block the account cutover. Launch uses ESPN provider-supplied fantasy points. Independent NFL-statistics ingestion is separately estimated.

## Authorization Requirements

### AR-1: League-Season Context

Every league-data request resolves a readable access row (`active` or `reverification_required`) for the authenticated user and season before reading data. A `reverification_required` row serves prior successful data with a bounded indicator, or only the season summary when none exists. `revoked` is never readable. Synchronization eligibility, orphan retention, and destructive lifecycle decisions count only active access (`status = active`). Authorization never relies on selecting the first or only row, or on a client-supplied ID alone.

### AR-2: No Roles

There is no administrator or spectator authorization. Every account has the same capabilities over its own data and its own season access.

### AR-3: Deny by Default

Missing access context denies access with the uniform response of FR-10.

### AR-4: Cross-User Tests

Tests must prove, for every league-data endpoint, that:

1. User A cannot read User B's private season without independent proof.
2. A guessed or known internal League or Season ID grants nothing.
3. Another user's connection or access never authorizes a new user.
4. Revoked and `reverification_required` access follow the documented behavior.
5. User A cannot observe synchronization for a season without readable access, and no user's access counts toward synchronization credential eligibility unless it is active.
6. Responses for inaccessible and nonexistent resources are indistinguishable.

## Session Requirements

### SR-1: Secure Session Storage

Session tokens are random, stored only as hashes, revocable, expiring, and bound to a user.

### SR-2: Concurrent Sessions

Multiple concurrent sessions per account are permitted.

### SR-3: Bulk Revocation

Password reset and consequential account-security actions revoke all sessions.

### SR-4: Session Cleanup

Expired and revoked sessions are removable through maintenance that exposes no other user's session counts.

## Data Requirements

### DR-1: Explicit Ownership

Personal data is owned by the user. Provider connections are owned by the user. Canonical league data is shared and unowned. The access relation links them.

### DR-2: Canonical Constraints

The database enforces uniqueness on provider plus normalized league ID, on league plus season year, and on user plus season in the access relation. Database constraints are authoritative.

### DR-3: Canonical Numeric Data

Scores and statistics use deterministic decimal serialization.

### DR-4: Provider Metadata

Provider metadata and identifiers are stored as stable strings with version information where needed.

### DR-5: Audit Events

Structured audit events cover registration, login, password reset, session revocation, league import, access revocation, reverification outcomes, connection invalidation, account deletion, and orphan purge. They contain no secrets or credential-owner identity visible to other users. Audit events are required before public launch.

## Synchronization Requirements

### SYR-1: Season-Scoped Resolution

Synchronization resolves its season from the job and never searches globally.

### SYR-2: Season Locks

Persistence acquires the season advisory transaction lock and compares `data_version` when needed.

### SYR-3: Background Execution

Synchronization runs through durable PostgreSQL-backed jobs. Network calls never occur inside an open transaction.

### SYR-4: Idempotency

The active-job key is `league-season-sync:{season_id}`.

### SYR-5: Failure Isolation

One season's failure never blocks another season or another user's access to prior data.

### SYR-6: Scheduling

Scheduled synchronization refreshes stale seasons and triggers periodic access reverification.

## Mobile and API Requirements

### MR-1: Responsive Registration

Registration and league import must work at a viewport width of 320 CSS pixels without page-level horizontal scrolling.

### MR-2: Responsive Analytics

Core analytics must remain usable on mobile web.

Touch targets should generally be at least 44 by 44 CSS pixels where practical.

### MR-3: Native-App-Compatible APIs

Authentication and API contracts must not assume that all clients are browser pages.

The future native application may require a different token transport, but per-user, per-season authorization must remain shared.

### MR-4: Desktop Stability

The SaaS conversion should not redesign the desktop analytics interface unless a change is required for league and season selection, registration, security, or provider support.

## Operational Requirements

### OR-1: Initial Capacity

The homelab deployment must support the initial target of 50 to 100 registered users.

Capacity testing must model:

- Concurrent logins
- Concurrent authenticated analytics reads
- Background synchronization
- Database connection usage
- Provider latency
- Analytics-query latency
- Backup activity

### OR-2: Backups

PostgreSQL backups must be automated.

At least one backup copy must be stored off the application host.

A restore test must be completed before public registration.

### OR-3: Observability

The hosted service must expose or record:

- API health
- Database health
- Queue health
- Scheduler health
- Synchronization success and failure
- Request latency
- Error counts
- Disk usage
- Backup success
- Certificate expiration

### OR-4: Maintenance Mode

The operator must be able to disable:

- Registration
- Provider-connection changes
- Synchronization
- All writes when required

Read-only analytics should remain available during maintenance when safe.

### OR-5: Deployment Safety

Production deployment must retain:

- Immutable images
- Explicit version tags
- Automated migrations
- Health checks
- Rollback instructions
- Separation from development
- Secret files excluded from version control

### OR-6: Cloud Migration Triggers

Cloud migration should be evaluated when one or more of these conditions persist:

- Homelab availability no longer meets user expectations
- Synchronization load interferes with interactive requests
- Database storage or backup windows exceed safe local capacity
- Internet upload capacity becomes a bottleneck
- More than 100 active users materially use the service
- Recovery objectives cannot be met from the homelab
- Provider OAuth callback reliability requires higher availability

These are planning triggers, not automatic thresholds.

## Security Requirements

### SER-1: Existing Controls

The SaaS conversion must preserve:

- Argon2 password hashing
- Hashed session tokens
- CSRF protection
- Trusted-origin validation
- Login throttling
- Session revocation
- Encrypted provider credentials
- Safe error handling
- Secure cookie configuration
- Per-user, per-season authorization

### SER-2: Registration Abuse Protection

Before open registration, implement:

- Per-source rate limiting
- Per-identity throttling
- Duplicate-submission protection
- Request-body limits
- Safe validation messages
- Operator controls for disabling registration

CAPTCHA is deferred unless observed abuse justifies the additional friction.

### SER-3: Email and Recovery

Before public launch, the product must define:

- Whether account email is mandatory
- Email verification
- Password-reset delivery
- Reset-token expiration
- Reset-token single use
- Account recovery when provider credentials are unavailable

Decision (ADR 0009): account email is mandatory and verified. Transactional email uses Resend on the permanent free plan at $0 spend, with durable delivery, a configured daily limit (initially 100) and monthly ceiling (initially 2,900), and reserved capacity for recovery and deletion notices. Registration success means delivery was queued. Quota exhaustion or failed email readiness closes public registration. Resend acceptance means sent, and inbox delivery is not claimed. Required initial email: verification, verification resend, password reset, password-changed confirmation, deletion-request confirmation, seven-day deletion reminder, and deletion-completion notice.

### SER-4: Credential Encryption

Provider credentials must remain encrypted at rest.

The design must support:

- Key versions
- Key rotation
- Credential replacement
- Decryption-failure handling
- Secure deletion
- Yahoo token refresh

### SER-5: Secret Logging

Application logs, audit logs, traces, and error responses must not contain provider credentials, session tokens, password-reset tokens, or OAuth authorization codes.

## Performance Targets

Initial targets for normal homelab operation:

- Cached or database-backed analytics request: p95 under 500 ms
- Login response excluding deliberate password-hash cost: p95 under 1 second
- Registration form validation response: p95 under 1 second
- Health endpoint: p95 under 250 ms
- Ordinary page API payload: preferably under 1 MB
- Synchronization: asynchronous, with visible persisted progress

Provider-dependent operations may exceed these targets and must not hold frontend reverse-proxy requests open indefinitely.


## Launch Gates

Public registration stays closed until all of these are satisfied:

1. The account-only identity cutover is complete: no administrator, spectator, workspace-slug, or username login remains.
2. Per-user, per-season authorization, proof-first import, and atomic import are implemented and validated, including concurrency tests.
3. League-season synchronization replaces workspace synchronization and performs no network call inside an open transaction.
4. Frontend league and season selection, the empty-account import flow, and synchronization states are implemented.
5. Account deletion and 30-day orphan retention are implemented and tested for shared-data safety.
6. Cross-user negative tests pass for every league-data endpoint.
7. Durable email (ADR 0009), audit events, operational gates (OR-2, OR-3, OR-4, OR-5), and published policies are satisfied.
8. Homelab availability limitations are disclosed.

Current staging validated the retired workspace model and is not acceptance evidence for these gates. Sleeper and Yahoo cannot be claimed as supported until implemented.

## Deferred Decisions

- Sleeper and Yahoo identity, authentication, privacy, and season behavior.
- Yahoo OAuth callback exposure review.
- ESPN evidence for definitive loss of access.
- Independent NFL-statistics source, licensing, corrections, player matching, defensive scoring, bonuses, and rounding.
- Operator administration interface.
- Native-app token transport.

## Traceability

```text
FR-6, FR-7: Proof-first atomic import
FR-9, AR-4: Per-season authorization and cross-user tests
FR-14, SYR-2: League-season synchronization and season locks
FR-16, FR-17, FR-18: Shared-data deletion safety and orphan retention
PR-2: ESPN canonical identity
```
