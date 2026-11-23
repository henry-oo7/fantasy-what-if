# Fantasy What-If SaaS Migration Roadmap

## Document Status

- **Status:** Draft
- **Baseline release:** v1.0.1
- **Baseline commit:** `1dbb5c4`
- **Target:** Hosted multi-tenant SaaS
- **Initial hosting:** Henry's homelab
- **Initial capacity target:** 50 to 100 registered users
- **Initial sport:** Fantasy football
- **Initial provider:** ESPN Fantasy Football
- **Planned providers:** Sleeper and Yahoo Fantasy Sports

## Purpose

This roadmap defines the recommended implementation sequence for converting Fantasy What-If from a single self-hosted installation into a hosted multi-tenant SaaS.

The roadmap is intentionally staged.

The project must not attempt to introduce public registration, multi-tenancy, background jobs, provider expansion, mobile redesign, and production migration in one release.

Each phase must preserve a working application and include:

- Automated tests
- Migration validation
- Documentation updates
- Development smoke testing
- Production recovery planning
- A coherent commit and merge boundary

## Governing Documents

Implementation must remain consistent with:

- `docs/product/SAAS_VISION.md`
- `docs/product/SAAS_REQUIREMENTS.md`
- `docs/architecture/0001-Portable-Multi-Provider-Fantasy-Platform.md`
- `docs/architecture/0002-Canonical-Seasons-and-Historical-Analytics.md`
- `docs/architecture/0003-Canonical-Provider-Synchronization.md`
- `docs/architecture/0004-League-Workspace-Multi-Tenancy.md`
- `docs/architecture/0005-SaaS-Identity-Registration-and-Sessions.md`
- `docs/architecture/0006-Resumable-Onboarding-and-Background-Jobs.md`
- `docs/architecture/0007-SaaS-Provider-Data-and-Credential-Strategy.md`
- `docs/architecture/0008-Homelab-SaaS-Deployment-and-Recovery.md`

When implementation reveals a conflict or missing decision, update the governing documentation before silently changing the architecture.

## Migration Principles

### New Hosted Deployment

The hosted multi-workspace SaaS is initialized as a new deployment.

Existing version-1 single-installation databases are not migrated, backfilled, or preserved as League Workspaces in the hosted deployment.

Backfill steps that remain in earlier phases are historical: they described development databases that still contained a legacy installation. They are not a production requirement. The hosted deployment decision in `SAAS_REQUIREMENTS.md` is authoritative, and the v1 production migration plan is superseded.

### Add Before Removing

The migration should generally follow:

1. Add new structures.
2. Backfill existing data.
3. Validate the backfill.
4. Add constraints.
5. Switch application behavior.
6. Remove obsolete compatibility behavior later.

Do not combine destructive cleanup with the first release that depends on new ownership structures.

### Keep Main Deployable

Every merged phase should leave `main`:

- Testable
- Migratable
- Deployable
- Documented
- Recoverable

### Use Vertical Slices

Implementation work should be grouped into complete vertical slices with one feature branch and one coherent merge at the end of each slice.

A slice should include all required:

- Models
- Migrations
- Repositories
- Services
- API contracts
- Frontend behavior
- Tests
- Documentation

### Prove Isolation Before Registration

Public registration must remain disabled until automated tests prove that two workspaces cannot access each other's data.

### Prove Recovery Before Public Launch

Public registration must remain disabled until:

- Automated backups run successfully.
- An off-host backup exists.
- A restore test succeeds.
- Provider credentials can be decrypted after restoration.
- Registration can be disabled operationally.
- Queue and synchronization failures generate alerts.

## High-Level Sequence

```text
Phase 0: Documentation and design baseline
Phase 1: Pre-migration audit and test foundation
Phase 2: Workspace ownership schema
Phase 3: Tenant-aware authentication and authorization
Phase 4: Workspace-scoped reads and analytics
Phase 5: Provider league claims
Phase 6: PostgreSQL-backed background jobs
Phase 7: Resumable SaaS registration and onboarding
Phase 8: ESPN SaaS hardening
Phase 9: Homelab SaaS operations
Phase 10: Private multi-tenant beta
Phase 11: Sleeper provider
Phase 12: Mobile-web refinement
Phase 13: Yahoo provider
Phase 14: Public registration
Phase 15: Native-app foundation and cloud-readiness review
```

## Phase 0: Documentation and Design Baseline

### Objective

Finalize the product and architecture baseline before implementation.

### Deliverables

- SaaS vision
- SaaS requirements
- Multi-tenancy ADR
- Identity and registration ADR
- Background jobs ADR
- Provider strategy ADR
- Homelab deployment ADR
- Migration roadmap
- Target system design
- Initial data model
- API transition plan
- Production migration plan

### Required Decisions

Before leaving Phase 0, confirm:

- League Workspace is the tenant boundary.
- One administrator exists per workspace.
- One shared spectator exists per workspace.
- Administrator login uses verified email.
- Spectator login uses workspace slug and shared password.
- One administrator session remains active at a time.
- Spectator sessions may be concurrent.
- Provider league claims are globally exclusive by provider and normalized league ID.
- PostgreSQL-backed jobs are the initial queue.
- The hosted SaaS initializes as a new deployment without migrating v1 installation data.
- Public registration remains disabled during migration.

### Exit Criteria

- Documentation passes `git diff --check`.
- No unresolved contradiction exists among the SaaS documents.
- Major unresolved decisions are explicitly listed.
- The documentation branch is reviewed and merged.

## Phase 1: Pre-Migration Audit and Test Foundation

### Objective

Make the current single-installation assumptions measurable before modifying the schema.

### Deliverables

- Read-only pre-migration audit command
- Multi-workspace test fixture foundation
- Existing production cardinality checks
- Catalog of global single-installation queries
- Catalog of global lock keys
- Catalog of unscoped repository methods
- Baseline database snapshot test
- Migration rehearsal database

### Pre-Migration Audit

Retired. The read-only v1 migration audit command was removed with the single-installation paths because no v1 database is migrated.

### Global Assumption Inventory

Search for and classify code that assumes:

```text
Exactly one installation
Exactly one active installation
Exactly one league
The first installation row
The first provider connection
Global spectator lookup
Global sync-status lookup
Global setup status
Global advisory lock key
```

### Test Foundation

Add fixtures for:

```text
Workspace A
├── Administrator A
├── Spectator A
├── Provider Connection A
├── League A
└── Season A

Workspace B
├── Administrator B
├── Spectator B
├── Provider Connection B
├── League B
└── Season B
```

No production behavior changes in this phase.

### Exit Criteria

- Complete backend tests pass.
- Complete frontend tests pass.
- Pre-migration audit passes against a production-like snapshot.
- Two-workspace test fixtures are available.
- All known global assumptions are cataloged.

## Phase 2: Workspace Ownership Schema

### Objective

Add explicit tenant ownership while preserving existing behavior.

### Primary Migration

Add nullable workspace ownership to `users`.

Conceptual field:

```text
users.workspace_id
```

Map the field to the existing `installations` table during the first migration.

Backfill the existing administrator and spectator to the current installation.

### Workspace Slug

Add:

```text
installations.slug
installations.normalized_slug
```

Development databases that still contain a legacy installation receive a development slug during migration. The hosted SaaS deployment starts empty and requires no production slug backfill.

### Account Constraints

After backfill validation, add:

- One administrator per workspace partial unique index
- One spectator per workspace partial unique index
- Non-null user workspace ownership
- Workspace-slug uniqueness
- Supporting indexes

### Identity Fields

Add or formalize:

```text
users.email_verified_at
```

If required by the final implementation, add:

```text
users.normalized_email
```

Do not remove the existing username field in this phase.

### Tests

- Existing users backfill correctly.
- Two workspaces may each have an administrator.
- Two workspaces may each have a spectator.
- One workspace cannot have two administrators.
- One workspace cannot have two spectators.
- Existing password hashes remain valid.
- Existing IDs remain unchanged.

### Exit Criteria

- Existing development data migrates into one valid workspace. The hosted SaaS deployment requires no production data migration.
- Workspace ownership is non-null for every user.
- Cardinality constraints pass.
- The application still operates using compatibility paths.
- Rollback or forward-recovery steps are documented.

## Phase 3: Tenant-Aware Authentication and Authorization

### Objective

Resolve workspace context for every protected request.

### Authentication Context

Introduce a workspace-aware authentication context containing:

```text
user_id
workspace_id
role
session_id
```

### Administrator Login

Transition administrator authentication to:

```text
email
password
```

Username login has been removed. `POST /api/auth/login`, its schemas, service path, tests, and frontend types were retired together; the route now returns ordinary 404 routing behavior. No v1 administrator is migrated, so no sunset window was required.

### Spectator Login

Introduce spectator authentication using:

```text
workspace_slug
spectator_password
```

### Session Policies

Implement:

- Administrator session replacement
- Concurrent spectator sessions
- Revocation on spectator password reset
- Revocation on spectator disablement
- Revocation on workspace suspension
- Safe `session_replaced` handling

### Authorization Dependencies

Introduce explicit dependencies for:

- Authenticated workspace member
- Workspace administrator
- Workspace spectator
- Active workspace
- Provisional onboarding administrator

### Tests

- Administrator B login does not revoke Administrator A.
- A second Administrator A login revokes the first session.
- Failed Administrator A login does not revoke the active session.
- Multiple Spectator A sessions remain active.
- Spectator A cannot access administrator routes.
- User A cannot construct a context for Workspace B.
- Suspended workspaces cannot create new sessions.
- Session replacement returns a safe error.

### Exit Criteria

- Every protected route resolves workspace context.
- Administrator-session replacement works.
- Spectator concurrency works.
- Existing security controls remain enabled.
- Cross-workspace authentication tests pass.

## Phase 4: Workspace-Scoped Reads and Analytics

### Objective

Remove authorization dependence on globally selected installation state.

### Repository Changes

Every workspace-owned repository must either:

- Accept `workspace_id`, or
- Accept a resource already proved to belong to the workspace

Unsafe direct lookups must be replaced.

Example unsafe pattern:

```python
session.get(
    Season,
    season_id,
)
```

Preferred pattern:

```python
select(Season)
.join(League)
.where(
    Season.id == season_id,
    League.installation_id == workspace_id,
)
```

### Scope

Convert:

- Dashboard
- League inventory
- Season selection
- All-Play standings
- League Average
- Schedule Swap
- Optimal Lineups
- Playoff Predictor
- Synchronization status
- Standings tiebreaker configuration

### Active Context

Active league and season selection must resolve from the authenticated workspace.

No request may resolve the globally active installation.

### Cross-Tenant Tests

For every analytics endpoint:

1. Create data for Workspace A.
2. Create data for Workspace B.
3. Authenticate as Workspace A.
4. Request Workspace B's internal resource ID.
5. Verify a safe not-found or forbidden response.
6. Verify no information about Workspace B is returned.

### Exit Criteria

- All analytics support two workspaces.
- Internal ID substitution cannot bypass ownership.
- No ordinary request queries for the one installation globally.
- Full backend and frontend suites pass.

## Phase 5: Provider League Claims

### Objective

Make provider league ownership globally exclusive and race-safe.

### Schema

Add `provider_league_claims`.

Conceptual fields:

```text
id
workspace_id
provider
normalized_provider_league_id
original_provider_league_id
identity_version
status
claimed_at
released_at
created_at
updated_at
```

### Constraints

Enforce active uniqueness by:

```text
provider
normalized_provider_league_id
```

### Provider Normalization

Implement and test normalization for:

- ESPN league IDs
- Sleeper league IDs
- Yahoo league keys

Sleeper and Yahoo may remain unavailable in the UI until integrations are complete.

### Development Backfill

The hosted SaaS deployment starts empty and requires no production claim backfill.

Development databases that still contain a legacy league may derive its ESPN claim.

The migration must fail safely if the claim cannot be derived or conflicts.

### Claim Service

Create a transaction-safe claim service that:

- Normalizes identity
- Inserts the claim
- Converts a uniqueness violation into `provider_league_already_claimed`
- Returns no existing-owner information
- Creates no partial dependent state on conflict

### Concurrency Test

Run two transactions attempting to claim the same provider league.

Verify:

```text
One succeeds
One receives conflict
Exactly one claim exists
No partial losing workspace state remains
```

### Exit Criteria

- Legacy leagues in development databases have claims. The hosted deployment requires no claim backfill.
- Duplicate claims are rejected atomically.
- Different providers may use the same textual ID.
- Safe conflict response is tested.
- No owner details leak.

## Phase 6: PostgreSQL-Backed Background Jobs

### Objective

Move long provider operations out of HTTP requests.

### Schema

Add `background_jobs` with:

- Workspace ownership
- Job type
- Status
- Priority
- Idempotency key
- Versioned payload
- Progress
- Attempts
- Availability time
- Lease
- Worker identity
- Safe error fields
- Terminal timestamps

### Worker

Add a worker entry point.

Conceptual command:

```text
python -m app.scripts.run_worker
```

Implement:

- `FOR UPDATE SKIP LOCKED`
- Leases
- Heartbeats
- Graceful shutdown
- Retry classification
- Exponential backoff
- Jitter
- Idempotency
- Safe progress
- Workspace verification

### Job Types

Implement in this order:

1. Incremental season synchronization
2. Full season synchronization
3. Historical season import
4. Initial workspace synchronization

### API Changes

Administrator synchronization and historical import should return:

```text
HTTP 202 Accepted
```

with a job identifier.

### Frontend Changes

Replace long-request recovery as the normal synchronization path with:

- Job creation
- Persisted status polling
- Progress display
- Safe retry guidance
- Existing-job reuse

### Exit Criteria

- API requests do not remain open for full synchronization duration.
- Accepted jobs survive API restart.
- Worker restart recovers safe jobs.
- Job payloads contain no secrets.
- One workspace's job cannot access another workspace.
- Existing synchronization functionality remains correct.

## Phase 7: Resumable SaaS Registration and Onboarding

### Objective

Replace the one-time installation bootstrap with public tenant-aware registration.

### Registration State

Implement provisional workspace lifecycle states.

### Email

Add:

- Administrator email verification
- Verification-token hashing
- Verification expiration
- Verification resend throttling
- Password-reset token hashing
- Password-reset expiration
- Generic recovery responses

### Registration Controls

Implement backend-enforced modes:

```text
open
closed
maintenance
```

Keep production registration `closed` during initial development and migration.

### Onboarding Flow

Implement:

1. Administrator registration
2. Provisional workspace creation
3. Email verification
4. Onboarding administrator session
5. Provider selection
6. Provider validation
7. Atomic league claim
8. Initial-sync job creation
9. Synchronization progress
10. Spectator password setup
11. Workspace activation

### Duplicate Claim Cleanup

When claim insertion conflicts:

- Remove provisional workspace
- Remove provisional administrator
- Revoke onboarding session
- Remove temporary provider credentials
- Create no synchronization job
- Return the safe conflict response

### Cleanup

Implement bounded cleanup for:

- Unverified registrations
- Abandoned provisional workspaces
- Expired verification tokens
- Expired password-reset tokens
- Expired onboarding sessions

### Exit Criteria

- A new workspace can complete onboarding in development.
- Onboarding survives browser refresh and reconnect.
- Duplicate claims leave no tenant state.
- Initial synchronization runs through the worker.
- Workspace activation validates all invariants.
- Registration remains disabled in production.

### Explicit Deferrals From the Registration Foundation

The registration-foundation slice deliberately defers the following work. Each item has a required completion point.

- **Session-replacement signaling:** Implemented by the session-lifecycle slice. Every session revocation now persists an explicit reason in `auth_sessions.revocation_reason` (`replaced`, `logout`, `password_reset`, `spectator_password_reset`, or `spectator_disabled`). A replaced, unexpired administrator session receives HTTP 401 with the stable `session_replaced` code. Every other failure, including rows revoked before the reason column existed, keeps the generic unauthenticated response.
- **Authoritative workspace lifecycle in `/api/auth/me`:** Implemented by the auth-context lifecycle slice. `GET /api/auth/me` returns a `workspace` object (`workspace_id`, `workspace_slug`, `installation_status`, `registration_status`, `onboarding_stage`, `workspace_access`) computed by the single mapping in `app/services/workspace_lifecycle.py`, which also backs `require_active_workspace`, administrator and spectator login, and password-reset eligibility. `workspace_access` is `active`, `provisional` (verified administrators only), `suspended`, or `inactive`; inactive responses carry no lifecycle detail, spectators never receive `onboarding_stage`, and the compatibility `username` is no longer returned. The frontend routes from this object and no longer probes `GET /api/leagues/seasons` to infer lifecycle state.
- **Provider selection and ESPN league-access validation:** Implemented by the onboarding provider-validation slice. A verified provisional administrator selects ESPN (Sleeper and Yahoo appear as "Coming later"), enters the League ID and season year, and the server detects public or private access. Credentials are requested only when ESPN requires them. Success stores the validated target on the single active provider connection and advances onboarding to `league_claim_pending`. Validation does not claim the league, import data, or start synchronization. A per-process limit on concurrent outbound ESPN requests is deferred to production hardening.
- **League claim confirmation:** Implemented by the onboarding league-claim slice. The administrator confirms the safe validated target. One transaction inserts the authoritative claim (provider plus League ID, independent of season) and advances to `initial_sync_pending`. A duplicate claim returns `409 provider_league_already_claimed`, deletes the losing provisional registration in the same transaction, clears its cookies and discloses no existing workspace details.
- **Initial synchronization:** Implemented by the onboarding initial-sync slice. The start request only queues one idempotent `initial_workspace_sync` job. The worker fetches ESPN data, creates the League and Season from real provider values, attaches the claim, and runs the existing full-season importer. Success advances to `spectator_setup_pending`; terminal failure advances to `initial_sync_failed` with an administrator retry. Spectator setup is the next slice, and activation follows it.
- **Shared spectator access:** Implemented by the onboarding spectator-access slice. At `spectator_setup_pending`, the administrator creates the single shared spectator password; onboarding then moves to `complete` without activating the workspace. Administration shows the workspace slug and the configured and enabled flags, changes the password, disables access, and re-enables access only with a new password. Each of these revokes spectator sessions with the existing explicit reasons. Spectators sign in with the workspace slug and the shared password only. Workspace activation is the next slice.
- **Workspace activation gate:** Implemented by the onboarding workspace-activation slice. At `complete`, the administrator reviews a safe summary and activates the workspace. The server independently rechecks every persisted invariant in one locked transaction, sets the active League and Season, and marks the workspace active, without provider I/O or synchronization. It is idempotent under repeats and concurrency. SaaS onboarding is now complete end to end.
- **Single-installation path retirement:** Implemented by the retirement slice. The `/api/setup` API, setup services and schemas, `POST /api/auth/login`, username authentication, the `create_admin` and v1 migration-audit scripts, the frontend `/setup` route, setup pages, legacy spectator panel and installation route guard were removed. `/setup` is now an unknown frontend route. Historical season import is scoped to the authenticated workspace. Removed endpoints return ordinary 404 responses with no aliases. Workspace deletion, which still fails on the restrictive `seasons.provider_connection_id` foreign key, is deferred to FR-20 with a deferrable `NO ACTION` recommendation.
- **Durable asynchronous email delivery and response-timing equality:** Verification and password-reset email delivery currently runs synchronously after commit. Durable asynchronous delivery and timing equality for enumeration-sensitive routes are required before public registration opens.
- **Audit-event infrastructure:** Structured identity audit events required by ADR 0005 are required before public SaaS launch.

## Phase 8: ESPN SaaS Hardening

### Objective

Make ESPN safe and reliable for multiple hosted workspaces.

### Required Work

- Disable shared environment credential fallback for SaaS tenants.
- Confirm workspace-owned encrypted credential use.
- Add credential replacement.
- Add credential-schema versioning.
- Add credential-key rotation tooling.
- Add sanitized ESPN fixtures.
- Expand provider contract tests.
- Test public leagues.
- Test private leagues.
- Test invalid and expired credentials.
- Test historical imports.
- Test future schedules.
- Test playoff settings.
- Test scoring settings.
- Test lineup slots.
- Test capability persistence.
- Test safe errors.
- Add conservative provider concurrency.
- Add provider-outage behavior.
- Add reauthentication guidance.

### Live Verification

Use controlled development leagues only.

Do not place production credentials or raw provider payloads into the repository.

### Exit Criteria

- All ESPN tests run without network access.
- Live smoke tests pass against controlled leagues.
- Environment fallback is not required for SaaS workspaces.
- Credential replacement works.
- Key rotation rehearsal succeeds.
- Provider outage does not break existing analytics.
- Multiple ESPN workspaces synchronize independently.

## Phase 9: Homelab SaaS Operations

### Objective

Prepare the homelab to host tenant data safely.

### Production Roles

Add or verify:

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

### Backups

Implement:

- Automated PostgreSQL backup
- Encrypted off-host copy
- Retention policy
- Backup-failure alert
- Integrity verification
- Quarterly restore test

### Monitoring

Monitor:

- API health
- Worker health
- Scheduler health
- PostgreSQL health
- Queue depth
- Oldest job age
- Failed jobs
- Synchronization freshness
- Disk usage
- CPU and memory
- Backup freshness
- Certificate expiration
- Email delivery failure

### Operator Controls

Add protected operator commands for:

- Registration mode
- Synchronization mode
- Failed-job inspection
- Eligible-job retry
- Pending-job cancellation
- Workspace suspension
- Workspace reactivation
- Claim inspection
- Migration audit
- Backup verification

### Incident Response

Document procedures for:

- Suspected cross-tenant access
- Provider credential leak
- Database corruption
- Backup failure
- Queue backlog
- Provider outage
- Email outage
- Disk exhaustion
- Failed migration
- Cloudflare Tunnel outage

### Exit Criteria

- Off-host backup succeeds.
- Isolated restore succeeds.
- Required encryption keys are recoverable.
- Alerts reach an external channel.
- Registration can be disabled immediately.
- Queue backlog and failed jobs are visible.
- Production deployment and rollback are documented.

## Phase 10: Private Multi-Tenant Beta

### Objective

Validate the SaaS model with a small controlled user group before open registration.

### Beta Size

Recommended initial beta:

```text
5 to 10 League Workspaces
```

Prefer known users who can provide timely feedback.

### Enrollment

Use one of:

- Operator-created registration codes
- Temporary invite-only registration mode
- Manually opened registration windows

Invite-only mode was not required by the first requirements document, but it is appropriate as a temporary operational beta control.

If implemented, invite codes must be:

- Random
- Single-use or explicitly limited
- Expiring
- Stored as hashes
- Rate limited

### Beta Validation

Measure:

- Registration completion rate
- Email delivery
- ESPN credential failures
- Initial-sync duration
- Queue wait time
- API p95 latency
- Database connections
- Worker memory
- Backup size
- Mobile onboarding usability
- Spectator login usability
- Administrator session replacement
- Cross-workspace isolation
- Support burden

### Exit Criteria

- At least five workspaces complete onboarding.
- No cross-tenant defects are found.
- Backup and restore remain healthy.
- Queue wait times remain acceptable.
- ESPN failure modes are understandable.
- Mobile registration is usable.
- Operator workload is sustainable.
- Critical beta findings are resolved.

## Phase 11: Sleeper Provider

### Objective

Add Sleeper through the canonical provider architecture.

### Research First

Verify current Sleeper behavior for:

- User lookup
- League discovery
- League IDs
- Prior-league chains
- Seasons
- Rosters
- Matchups
- Weekly scores
- Lineup positions
- Scoring settings
- Playoff settings
- Historical data
- Rate limits
- Error responses

### Claim Identity

Resolve how continuing Sleeper leagues are claimed across season-specific league IDs.

Do not implement claims until the league-chain identity rule is documented.

### Implementation

Add:

- Sleeper client
- Sleeper protocols
- Sleeper adapter
- Identifier normalization
- Canonical mappings
- Sanitized fixtures
- Provider contract tests
- Setup flow
- Synchronization
- Capability persistence
- Provider-specific safe errors

### Exit Criteria

- Sleeper passes shared provider contract tests.
- Sleeper league claim cannot duplicate across workspaces.
- Large IDs remain strings without precision loss.
- Historical and current seasons behave as documented.
- Unsupported analytics remain disabled.
- Controlled live smoke tests pass.

## Phase 12: Mobile-Web Refinement

### Objective

Polish the mobile browser experience without redesigning the acceptable desktop interface.

### Priority Workflows

Optimize:

- Registration
- Email verification
- Administrator login
- Spectator login
- Provider setup
- Onboarding progress
- Season selection
- Dashboard
- Standings
- Schedule Swap
- Optimal Lineups
- Playoff Predictor
- Administration
- Synchronization status

### Standards

At minimum:

- Support 320 CSS pixels.
- Prevent page-level horizontal scrolling.
- Use touch targets near 44 by 44 CSS pixels where practical.
- Preserve visible focus indicators.
- Support keyboard navigation.
- Maintain sufficient color contrast.
- Avoid hover-only actions.
- Keep dense tables readable through responsive presentation.
- Preserve the flat football design system.
- Avoid unnecessary animation.
- Keep desktop layouts stable unless required.

### Testing

Add viewport tests for representative widths:

```text
320 px
375 px
390 px
768 px
1024 px
1440 px
```

Use browser automation when introduced.

Suitable options include:

- Playwright
- Testing Library
- Axe accessibility checks

### Exit Criteria

- Registration and onboarding work at 320 pixels.
- All core analytics work without page-level overflow.
- Touch interactions are reliable.
- Accessibility checks pass at the agreed threshold.
- Desktop smoke tests show no material regression.

## Phase 13: Yahoo Provider

### Objective

Add Yahoo Fantasy Sports with secure OAuth lifecycle management.

### Prerequisites

- Stable public HTTPS domain
- Reliable callback availability
- Yahoo developer application
- Verified OAuth scopes
- Encrypted token envelope
- Token-refresh locking
- Reauthorization state
- Callback-state persistence
- Background-job integration

### Implementation

Add:

- OAuth authorization initiation
- Random single-use state
- Callback validation
- Code exchange
- Encrypted access and refresh tokens
- Token expiration
- Token refresh
- Refresh concurrency control
- Revocation
- League discovery
- League-key normalization
- Yahoo adapter
- Sanitized fixtures
- Provider contract tests
- Safe reauthorization workflow

### Exit Criteria

- OAuth callbacks are tenant safe.
- Tokens never enter logs or job payloads.
- Concurrent refresh cannot overwrite newer credentials.
- Revoked tokens pause synchronization safely.
- Existing analytics remain readable during reauthorization.
- Yahoo passes shared provider contract tests.
- Controlled live smoke tests pass.

## Phase 14: Public Registration

### Objective

Open SaaS registration only after the platform is operationally and technically ready.

### Launch Gate

Public registration must remain closed unless all of the following are true:

#### Tenancy

- Two or more workspaces coexist safely.
- Cross-tenant endpoint tests pass.
- Provider claims are atomic.
- Duplicate claims reveal no owner information.
- Workspace deletion behavior is documented.

#### Authentication

- Administrator email verification works.
- Administrator password recovery works.
- One administrator session is enforced.
- Concurrent spectator sessions work.
- Login and registration throttling work.
- Enumeration-safe responses are tested.

#### Jobs

- Synchronization is asynchronous.
- Jobs survive service restarts.
- Queue depth is monitored.
- Failed jobs are visible.
- Retry classification is tested.
- Provider concurrency is configured.

#### ESPN

- SaaS credentials are workspace-owned.
- Environment fallback is disabled for tenants.
- Sanitized fixtures exist.
- Public and private league smoke tests pass.
- Historical and future schedules work.

#### Operations

- Off-host backup exists.
- Restore test succeeds.
- Alerts reach the operator externally.
- Disk monitoring is active.
- Registration can be disabled.
- Maintenance mode works.
- Incident-response runbook exists.
- Production rollback is documented.

#### Product

- Privacy policy is published.
- Terms of service are published.
- Acceptable-use expectations are published.
- Data-deletion process is documented.
- Homelab availability limitations are disclosed.
- Mobile registration works at 320 pixels.

### Launch Strategy

Use a gradual opening:

```text
Stage 1:
10 to 20 workspaces

Stage 2:
25 to 50 workspaces

Stage 3:
Up to the initial 50 to 100 user target
```

Pause expansion when:

- Queue delay grows materially
- API latency exceeds targets
- Provider failures increase
- Backup windows become unsafe
- Support workload becomes unsustainable
- Security concerns emerge

## Phase 15: Native-App Foundation and Cloud-Readiness Review

### Objective

Prepare for native mobile clients and evaluate whether the homelab remains appropriate.

### Native Authentication ADR

Before app development, decide:

- Opaque bearer sessions or access tokens
- Refresh-token rotation
- Device binding
- Administrator one-session behavior across web and app
- Spectator shared-session behavior
- Secure storage using Apple Keychain and Android Keystore
- Token revocation
- App deep links
- Email verification links
- Password-reset links

### API Review

Verify:

- APIs do not depend on browser-only navigation.
- Tenant authorization is transport independent.
- Error codes are stable.
- Pagination exists where needed.
- Payload sizes are mobile appropriate.
- Versioning strategy is documented.
- Provider secrets never reach clients.

### Cloud-Readiness Review

Evaluate:

- Active users
- Concurrent traffic
- Queue wait time
- PostgreSQL size
- Backup duration
- Restore duration
- Internet reliability
- Power reliability
- OAuth callback availability
- Operator workload
- Support obligations
- Monetization plans
- Required service commitments

### Exit Criteria

- Native authentication design is accepted.
- API gaps are documented.
- Cloud migration recommendation is recorded.
- Homelab continuation or cloud transition is an explicit decision.

## Migration and Release Strategy

The SaaS conversion should span multiple releases.

A representative release sequence is:

```text
v1.1.0:
Workspace ownership foundation

v1.2.0:
Tenant-aware authentication and analytics isolation

v1.3.0:
Provider claims and background jobs

v1.4.0:
Resumable registration and onboarding

v1.5.0:
ESPN SaaS hardening and private beta

v1.6.0:
Sleeper provider

v1.7.0:
Mobile-web refinement

v1.8.0:
Yahoo provider

v2.0.0:
Public SaaS release
```

These version numbers are planning placeholders, not commitments.

Release scope should be based on completed, validated vertical slices rather than forcing work into predetermined version numbers.

## Branch Strategy

Documentation work remains on:

```text
docs/saas-product-architecture
```

Implementation should use one feature branch per complete vertical slice.

Representative branches:

```text
feature/workspace-ownership
feature/tenant-authentication
feature/workspace-scoped-analytics
feature/provider-league-claims
feature/background-jobs
feature/saas-registration
feature/espn-saas-hardening
feature/sleeper-provider
feature/mobile-web-refinement
feature/yahoo-provider
```

Each branch should:

1. Start from updated `main`.
2. Include one coherent vertical slice.
3. Include migrations and tests.
4. Pass all backend and frontend validation.
5. Include documentation updates.
6. Be smoke tested.
7. Be merged only after the slice is complete.

## Validation Standard

### Backend

```bash
cd backend

uv run ruff format --check .
uv run ruff check .
uv run pytest -q
uv run alembic check
uv run alembic heads
uv run alembic current
```

### Frontend

```bash
cd frontend

npm test
npm run lint
npm run build
npm audit --audit-level=low
```

### Repository

```bash
git diff --check
git status --short --branch
```

### Migration Rehearsal

For every ownership or identity migration:

1. Create or reuse a disposable PostgreSQL database. Never use the normal development or production database.
2. Run the pre-migration audit.
3. Apply migrations.
4. Run post-migration validation.
5. Start the application.
6. Verify administrator access.
7. Verify spectator access.
8. Verify provider credential decryption.
9. Run synchronization.
10. Verify analytics.
11. Record duration and failures.

## Definition of Done for a Vertical Slice

A vertical slice is complete only when:

- Product behavior matches the requirements.
- Architecture remains consistent with accepted ADRs.
- Database migration is included when required.
- Migration upgrades, downgrades to base, and re-upgrades cleanly on a disposable database.
- Backend tests pass.
- Frontend tests pass.
- Cross-tenant tests pass when applicable.
- Lint and format checks pass.
- Production build succeeds.
- Safe error behavior is verified.
- Secrets are absent from logs and payloads.
- Documentation is updated.
- Development smoke test passes.
- Rollback or forward-recovery strategy is documented.

## Immediate Next Step After Documentation

After the documentation branch is merged, implementation should begin with:

```text
Phase 1: Pre-Migration Audit and Test Foundation
```

The first implementation slice should not add public registration or modify production tenant behavior.

The first slice should deliver:

- Read-only SaaS migration audit
- Two-workspace integration fixtures
- Global-assumption inventory
- Migration rehearsal procedure
- Tests that describe the desired cardinality and isolation boundaries

This creates evidence for the first schema migration instead of changing ownership based on assumptions.

## Roadmap Completion Criteria

The SaaS migration is complete when:

1. Public users can register without self-hosting.
2. Each registration creates an isolated League Workspace.
3. Administrator email verification and recovery work.
4. Each workspace has one administrator.
5. Each workspace has one shared spectator.
6. Administrator sessions are exclusive.
7. Spectator sessions are concurrent.
8. Provider leagues are claimed atomically.
9. Duplicate claims reveal no owner information.
10. Synchronization runs through background jobs.
11. The hosted deployment initializes without migrating v1 installation data.
12. ESPN is stable for multiple tenants.
13. Sleeper is operational.
14. Yahoo is operational with secure OAuth.
15. Core mobile-web workflows are polished.
16. Backups and restoration are proven.
17. Monitoring and incident response are operational.
18. The homelab safely supports the initial user population.
19. Cloud migration triggers are measurable.
20. The API is ready for a future native application.