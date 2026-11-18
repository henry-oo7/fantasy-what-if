# Fantasy What-If SaaS Target Architecture

## Document Status

- **Status:** Draft
- **Baseline release:** v1.0.1
- **Target:** Hosted multi-tenant SaaS
- **Initial hosting:** Homelab
- **Initial capacity:** 50 to 100 registered users
- **Architecture style:** Modular monolith
- **Primary database:** PostgreSQL
- **Initial job queue:** PostgreSQL-backed
- **Initial providers:** ESPN, followed by Sleeper and Yahoo

## Purpose

This document consolidates the accepted SaaS product and architecture decisions into one implementation-oriented system design.

The governing decisions remain in:

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
- `docs/roadmap/SAAS_MIGRATION_ROADMAP.md`

This document does not replace those decisions. This document explains how the decisions fit together as a complete system.

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

The primary tenant is a:

```text
League Workspace
```

A League Workspace owns:

```text
League Workspace
├── One administrator
├── Zero or one shared spectator
├── Authentication sessions
├── Provider connections
├── Provider league claim
├── Canonical league
│   ├── Seasons
│   ├── Franchises
│   ├── Teams
│   ├── Matchups
│   ├── Player-week scores
│   ├── Lineup rules
│   ├── Scoring rules
│   ├── Schedule rules
│   ├── Synchronization runs
│   └── Analytics
├── Background jobs
├── Workspace settings
└── Audit events
```

Every tenant-owned resource must have a verifiable path to one League Workspace.

## Current-to-Target Terminology

| Current term | Target product term | Initial database strategy |
|---|---|---|
| Installation | League Workspace | Retain `installations` initially |
| First administrator | Workspace administrator | Add explicit workspace ownership |
| Shared spectator | Workspace spectator | Add explicit workspace ownership |
| Setup | Workspace onboarding | Introduce resumable lifecycle |
| Active installation | Authenticated workspace | Resolve from session |
| Global synchronization | Workspace-season synchronization | Queue by workspace and season |

The product and API may use League Workspace terminology before the physical `installations` table is renamed.

A table rename is not required for the first SaaS migration.

## Modular Monolith Boundaries

The backend should evolve toward explicit feature modules.

Recommended target structure:

```text
backend/app/
├── api/
│   ├── authentication/
│   ├── registration/
│   ├── onboarding/
│   ├── workspaces/
│   ├── providers/
│   ├── leagues/
│   ├── analytics/
│   ├── jobs/
│   ├── administration/
│   └── operator/
├── authentication/
├── registration/
├── onboarding/
├── workspaces/
├── providers/
│   ├── espn/
│   ├── sleeper/
│   └── yahoo/
├── leagues/
├── seasons/
├── synchronization/
├── analytics/
├── jobs/
├── audit/
├── notifications/
├── persistence/
└── core/
```

This is a target organization, not a requirement to move every file immediately.

Migration should occur by complete feature slice rather than a large directory-only refactor.

## Layer Responsibilities

### API Layer

The API layer owns:

- HTTP parsing
- Request validation
- Authentication dependencies
- Workspace-context resolution
- Role authorization
- Safe HTTP responses
- Stable application error codes
- Job creation responses
- Status endpoints

The API layer must not:

- Query raw provider SDK objects
- Contain provider-specific mapping logic
- Perform long synchronization inline
- Treat numeric IDs as authorization
- Return raw database exceptions

### Application Services

Application services coordinate use cases such as:

- Register administrator
- Verify email
- Authenticate administrator
- Authenticate spectator
- Claim provider league
- Configure provider connection
- Enqueue synchronization
- Activate workspace
- Import historical season
- Reset spectator password
- Suspend workspace
- Request workspace deletion

Application services own transaction boundaries.

### Domain Layer

The domain layer owns:

- Workspace lifecycle rules
- Account cardinality rules
- Provider claim rules
- Job transition rules
- Provider-neutral data contracts
- Standings behavior
- Analytics behavior
- Safe state-transition validation

The domain layer must not depend on FastAPI, SQLAlchemy sessions, provider SDKs, or frontend behavior.

### Repository Layer

Repositories own:

- Database reads
- Database writes
- Workspace-scoped lookup behavior
- Constraint translation
- Persistence mapping
- Row locking
- Idempotent upserts

Repositories must not call provider APIs.

### Provider Layer

Provider packages own:

- Provider API communication
- Provider authentication
- Provider response validation
- Provider identity normalization
- Provider-specific mappings
- Provider token refresh
- Translation into canonical domain objects

Provider packages must not write canonical database records directly.

### Job Layer

The job layer owns:

- Job persistence
- Job claiming
- Leases
- Heartbeats
- Retries
- Job progress
- Idempotency
- Worker execution
- Job cleanup

Job handlers call application services or synchronization services through explicit contracts.

## Target Data Model

The target ownership model is conceptual. Exact column names remain subject to implementation review.

### League Workspace

The existing `installations` table becomes the League Workspace table in behavior.

Target fields include:

```text
id
slug
normalized_slug
status
registration_status
setup_started_at
email_verified_at when appropriate
setup_completed_at
suspended_at
deletion_requested_at
active_league_id
active_season_id
created_at
updated_at
```

Workspace status and onboarding status may remain separate.

This avoids overloading one enum with operational and onboarding concerns.

### User

Target additions include:

```text
workspace_id
normalized_email
email_verified_at
```

The existing fields remain during migration:

```text
id
username
display_name
email
password_hash
role
is_active
last_login_at
created_at
updated_at
```

Target constraints include:

```text
One administrator per workspace
Zero or one spectator per workspace
Globally unique normalized administrator email
```

### Authentication Session

Authentication sessions remain separate records.

Target ownership path:

```text
AuthSession
    -> User
        -> League Workspace
```

A direct `workspace_id` may be added if the column materially improves session revocation and query safety.

The first migration may rely on the verified user relationship.

### Email Verification Token

Conceptual fields:

```text
id
user_id
token_hash
expires_at
used_at
created_at
```

Only the token hash is stored.

### Password Reset Token

Conceptual fields:

```text
id
user_id
token_hash
expires_at
used_at
created_at
```

Only the token hash is stored.

### Provider Connection

Target fields include:

```text
id
workspace_id
provider
display_name
encrypted_credentials
credential_key_version
credential_schema_version
is_active
reauthorization_required
authorized_until
last_validated_at
last_validation_error
credentials_deleted_at
created_at
updated_at
```

The existing `installation_id` may remain physically named during the first migration.

### Provider League Claim

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

Authoritative uniqueness:

```text
provider
normalized_provider_league_id
```

If released claims remain for audit history, active uniqueness should use a partial unique index.

### League

The existing League model remains the canonical cross-season league.

Ownership path:

```text
League
    -> League Workspace
```

Target identity behavior:

```text
provider
provider_league_id
provider league claim
```

The provider claim establishes exclusivity. The league stores canonical identity and product data.

### Season

The existing Season model remains the canonical provider season.

Ownership path:

```text
Season
    -> League
        -> League Workspace
```

Season uniqueness remains:

```text
league_id
year
```

### Background Job

Conceptual fields:

```text
id
workspace_id
job_type
status
priority
idempotency_key
payload
progress
attempt_count
maximum_attempts
available_at
claimed_at
lease_expires_at
claimed_by
started_at
completed_at
failed_at
canceled_at
safe_error_code
safe_error_message
created_at
updated_at
```

### Audit Event

Conceptual fields:

```text
id
workspace_id
actor_user_id
actor_session_id
event_type
resource_type
resource_id
outcome
safe_metadata
source_ip_hash
created_at
```

Audit metadata must be bounded, versioned where necessary, and secret free.

## Workspace Lifecycle

Recommended workspace lifecycle:

```text
provisional
active
suspended
deletion_pending
deleted
```

Recommended onboarding stages:

```text
registration_pending
email_verification_pending
provider_setup_pending
league_claim_pending
initial_sync_pending
initial_sync_running
spectator_setup_pending
complete
provider_validation_failed
league_claim_conflict
initial_sync_failed
```

Workspace status and onboarding stage should not be interchangeable.

Example:

```text
workspace_status = provisional
onboarding_stage = initial_sync_running
```

## Registration Flow

The target registration sequence is:

```text
Submit administrator registration
        |
        v
Create provisional workspace and administrator
        |
        v
Send email verification
        |
        v
Verify administrator email
        |
        v
Create onboarding administrator session
        |
        v
Select and validate provider
        |
        v
Normalize provider league identity
        |
        v
Claim provider league atomically
        |
        v
Persist encrypted provider connection
        |
        v
Create minimal league and season identity
        |
        v
Create initial synchronization job
        |
        v
Worker synchronizes canonical data
        |
        v
Administrator configures spectator password
        |
        v
Activate workspace
```

## Registration Transaction Boundaries

Registration cannot be one database transaction because email verification and provider synchronization span multiple requests and external systems.

Recommended transaction boundaries follow.

### Transaction 1: Provisional Identity

Create:

- Provisional workspace
- Administrator
- Email-verification token
- Audit event

Commit before email delivery.

If email delivery fails, the registration remains provisional and resendable.

### Transaction 2: Email Verification

Lock and validate:

- User
- Verification token
- Workspace status

Update:

- Token used time
- Administrator verification time
- Onboarding stage

Commit before creating the onboarding session or as part of a safely combined authentication transaction.

### Provider Validation

Provider validation occurs outside a long database transaction.

Plaintext credentials remain only in process memory.

### Transaction 3: League Claim and Initial Job

Within one short transaction:

- Lock provisional workspace
- Recheck onboarding state
- Insert exclusive provider league claim
- Persist encrypted provider connection
- Create minimal league identity
- Create minimal season identity
- Create initial synchronization job
- Advance onboarding state

If claim uniqueness fails, roll back the transaction.

The duplicate-claim compensation flow removes the provisional identity state as defined by ADR 0005.

### Worker Provider Calls

Provider network calls occur outside the canonical persistence transaction.

### Transaction 4: Canonical Persistence

After provider data is fetched and validated:

- Lock season synchronization boundary
- Upsert canonical data
- Persist rules and capabilities
- Rebuild analytics
- Complete synchronization run
- Advance onboarding stage

Commit atomically.

### Transaction 5: Spectator Setup

Create:

- Spectator identity
- Spectator password hash
- Audit event

Advance onboarding stage.

### Transaction 6: Workspace Activation

Verify every activation invariant and mark the workspace active.

Activation is idempotent.

## Duplicate Provider League Claim

Provider league ownership is controlled by a database constraint.

The flow must not rely only on:

```text
SELECT claim WHERE provider = ? AND league_id = ?
```

because two registrations may pass the check concurrently.

Required behavior:

```text
Transaction A inserts claim and commits.
Transaction B insert conflicts.
Transaction B rolls back.
Transaction B receives a safe 409 response.
```

The response must not contain:

- Workspace slug
- Owner identity
- Administrator email
- Spectator credentials
- Active seasons
- Synchronization details
- Internal claim ID

## Authentication Design

### Administrator

Administrator login uses:

```text
email
password
```

Administrator email is:

- Required
- Normalized
- Verified
- Globally unique
- Used for recovery

Successful administrator login:

1. Verifies credentials.
2. Locks the administrator or workspace session boundary.
3. Revokes prior active administrator sessions.
4. Creates the replacement session.
5. Commits atomically.

### Spectator

Spectator login uses:

```text
workspace_slug
spectator_password
```

Successful spectator login creates an additional session.

Existing spectator sessions remain active.

### Browser Authentication

Browser clients continue using:

- Secure cookies
- HTTP-only cookies
- Same-site protections
- CSRF protection
- Server-side session revocation

### Future Native Authentication

Native transport is deferred, but the shared authorization context remains:

```text
user_id
workspace_id
role
session_id
```

## Authorization Design

Every protected request resolves:

```python
@dataclass(frozen=True, slots=True)
class WorkspaceAuthenticationContext:
    user_id: int
    workspace_id: int
    role: UserRole
    session_id: int
```

Recommended authorization dependencies:

```text
require_authenticated_workspace_user
require_workspace_administrator
require_workspace_spectator_or_administrator
require_active_workspace
require_provisional_workspace_administrator
```

A protected service must not infer workspace ownership solely from the requested resource ID.

## Safe Resource Lookup

Unsafe:

```python
season = session.get(
    Season,
    season_id,
)
```

Safer:

```python
season = session.scalar(
    select(Season)
    .join(League)
    .where(
        Season.id == season_id,
        League.installation_id == workspace_id,
    )
)
```

A missing resource and a resource owned by another workspace should usually produce the same safe not-found behavior.

## Provider Architecture

The canonical provider sequence is:

```text
Workspace-owned encrypted credentials
        |
        v
Provider adapter
        |
        v
Canonical provider domain objects
        |
        v
Generic validation
        |
        v
Canonical repositories
        |
        v
PostgreSQL
        |
        v
Provider-neutral analytics
```

Generic analytics must not branch by provider.

## Provider Identity

Claims use:

```text
provider
normalized_provider_league_id
```

Normalization versions are recorded.

Examples:

```text
espn + 529250970
sleeper + 112233445566778899
yahoo + 449.l.123456
```

All provider identifiers are strings.

## Credential Flow

Credential handling follows:

```text
Registration or credential replacement
        |
        v
Validate plaintext credentials in process memory
        |
        v
Create versioned credential envelope
        |
        v
Encrypt envelope
        |
        v
Persist encrypted envelope
```

Worker execution follows:

```text
Load workspace-owned provider connection
        |
        v
Decrypt credential envelope
        |
        v
Construct provider adapter
        |
        v
Call provider
        |
        v
Discard plaintext credential object
```

Plaintext credentials must not enter job payloads, logs, audit events, API responses, or provider metadata.

## Background Job Architecture

The API enqueues jobs and returns quickly.

The worker claims jobs through PostgreSQL:

```sql
SELECT id
FROM background_jobs
WHERE status IN (
    'pending',
    'retry_scheduled'
)
  AND available_at <= NOW()
ORDER BY
    priority DESC,
    available_at,
    id
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

The worker then:

1. Marks the job running.
2. Records the worker ID.
3. Sets the lease.
4. Commits the claim.
5. Executes outside the claim transaction.
6. Persists progress in short transactions.
7. Persists terminal state.

## Job Types

Required initial jobs:

```text
initial_workspace_sync
incremental_season_sync
full_season_sync
historical_season_import
```

Later jobs include:

```text
provider_token_refresh
provisional_workspace_cleanup
expired_session_cleanup
expired_token_cleanup
workspace_deletion
credential_reencryption
```

## Job Idempotency

Representative idempotency keys:

```text
initial-sync:{workspace_id}:{season_id}
incremental-sync:{season_id}:{provider_revision}
historical-import:{workspace_id}:{league_id}:{year}
cleanup-provisional:{workspace_id}
credential-reencrypt:{provider_connection_id}:{key_version}
```

Job handlers must recheck invariants at execution time.

The queue is at least once, so handlers must tolerate safe replay.

## Synchronization Design

Synchronization receives explicit context:

```text
workspace_id
season_id
provider_connection_id
provider
full_sync or incremental mode
optional requested week range
```

The synchronization service must verify:

- Workspace exists.
- Workspace permits synchronization.
- Workspace owns the league.
- League owns the season.
- Workspace owns the provider connection.
- Season references the provider connection.
- Provider claim belongs to the workspace.
- Provider connection is active.
- Credentials can be decrypted.

## Synchronization Locks

Synchronization is serialized by season, not globally.

The lock namespace should distinguish job purpose and resource.

Conceptual lock identity:

```text
synchronization
season_id
```

Historical import may use:

```text
historical_import
workspace_id
league_id
```

Unrelated workspaces and seasons must not share one global lock.

## Scheduler Design

The scheduler:

1. Selects active workspaces and eligible seasons.
2. Excludes suspended and deletion-pending workspaces.
3. Detects existing active synchronization jobs.
4. Applies provider-aware scheduling.
5. Applies jitter.
6. Creates jobs.
7. Records the scheduled trigger.
8. Does not call providers directly.

## Provider Concurrency

The worker must support provider-specific concurrency controls.

A conservative initial setting is:

```text
One active synchronization job per provider
```

This is a safe starting point, not a verified provider limit.

Concurrency may be increased only after provider behavior and homelab capacity are measured.

## API Structure

Recommended target route groups:

```text
/api/auth
/api/registration
/api/onboarding
/api/workspace
/api/providers
/api/leagues
/api/seasons
/api/analytics
/api/jobs
/api/admin
/api/operator
```

The final route names may preserve compatibility with existing APIs.

## Representative Public Routes

Conceptual routes:

```text
POST /api/registration
POST /api/registration/verify-email
POST /api/registration/resend-verification
POST /api/auth/admin/login
POST /api/auth/spectator/login
POST /api/auth/logout
POST /api/auth/password-reset/request
POST /api/auth/password-reset/complete
GET  /api/setup/status
```

Enumeration-sensitive routes return generic safe responses.

## Representative Onboarding Routes

Conceptual routes:

```text
GET  /api/onboarding/status
GET  /api/onboarding/providers
POST /api/onboarding/provider/validate
POST /api/onboarding/provider/claim
GET  /api/onboarding/synchronization
POST /api/onboarding/spectator
POST /api/onboarding/activate
```

Provider claim may be combined with validation in the final API if transaction and secret-handling boundaries remain correct.

## Representative Job Routes

Conceptual routes:

```text
GET  /api/jobs/{job_id}
POST /api/admin/synchronizations
POST /api/admin/seasons/import
```

Job status must be workspace scoped.

## Stable Error Contract

Application errors should use a stable structure.

Conceptual response:

```json
{
  "detail": "This fantasy league is already connected to Fantasy What-If.",
  "code": "provider_league_already_claimed"
}
```

Recommended fields:

```text
detail
code
field_errors when applicable
retryable when applicable
```

The API must not expose raw exception names or stack traces.

## Important Error Codes

Initial stable codes should include:

```text
registration_unavailable
email_verification_required
invalid_credentials
session_replaced
workspace_inactive
workspace_suspended
provider_validation_failed
provider_authentication_failed
provider_reauthorization_required
provider_league_already_claimed
synchronization_already_running
job_not_found
job_failed
season_not_found
resource_not_found
```

Exact naming should be centralized.

## Frontend Architecture

The frontend remains React-based and should add explicit feature boundaries.

Recommended target structure:

```text
frontend/src/
├── api/
├── auth/
├── components/
├── features/
│   ├── registration/
│   ├── onboarding/
│   ├── workspace/
│   ├── providers/
│   ├── jobs/
│   ├── analytics/
│   └── administration/
├── pages/
├── types/
├── utils/
└── styles/
```

Migration should occur by feature rather than one directory-only rewrite.

## Frontend Routing

Conceptual public routes:

```text
/register
/verify-email
/login
/forgot-password
/reset-password
```

Conceptual onboarding routes:

```text
/onboarding/provider
/onboarding/synchronization
/onboarding/spectator
```

Protected application routes remain inside the authenticated shell.

## Registration User Experience

Registration should:

- Use a step-based mobile-first layout.
- Work at 320 CSS pixels.
- Avoid horizontal scrolling.
- Explain email verification.
- Explain provider league exclusivity.
- Explain the shared spectator account.
- Preserve nonsecret safe fields after recoverable errors.
- Never preserve password fields after navigation.
- Show persisted synchronization progress.
- Resume from the saved onboarding stage.

## Desktop User Experience

The desktop analytics interface is not a redesign priority.

Desktop changes should be limited to:

- Registration
- Authentication
- Onboarding
- Workspace-aware status
- Job progress
- Provider management
- Operator-safe error states

The existing flat football design system remains the visual baseline.

## Mobile User Experience

Priority mobile workflows are:

- Registration
- Email verification
- Login
- Provider setup
- Synchronization progress
- Dashboard
- Season switching
- Playoff Predictor
- Administration
- Password recovery

Requirements include:

- No page-level horizontal overflow
- Touch-friendly controls
- Visible keyboard focus
- No hover-only actions
- Readable tables and cards
- Reliable mobile browser autofill
- Password-manager compatibility

## Email Architecture

Transactional email is required for:

```text
Email verification
Password reset
Security notification
Provider reauthorization
Workspace suspension
Important service notices
```

Email delivery should use an application notification contract so the external provider can change later.

Conceptual interface:

```python
class TransactionalEmailSender(Protocol):
    def send_email_verification(...): ...
    def send_password_reset(...): ...
    def send_security_notification(...): ...
```

The initial implementation may enqueue email delivery later, but token state remains canonical in PostgreSQL.

## Operator Boundary

The SaaS operator is not a workspace administrator.

Operator capabilities include:

- Registration control
- Workspace suspension
- Queue inspection
- Eligible-job retry
- Backup verification
- Migration audit
- Provider claim inspection
- Incident response
- Workspace deletion execution

Workspace administrators must not access global operator functions.

The first operator interface may be CLI based.

## Security Model

The target preserves:

- Argon2 password hashes
- Hashed session tokens
- CSRF protection
- Trusted-origin validation
- Secure cookies
- Login throttling
- Session revocation
- Encrypted provider credentials
- Safe errors
- Server-side role authorization

The SaaS design adds:

- Workspace authorization
- Cross-tenant tests
- Administrator session replacement
- Shared spectator concurrency
- Registration throttling
- Email verification
- Password recovery
- Provider claim constraints
- Audit events
- Job ownership
- Operator separation

## Workspace Suspension

Suspension denies all workspace access.

Suspension must:

- Revoke all administrator and spectator sessions
- Reject new administrator and spectator logins
- Block analytics, including read-only access
- Block provider changes
- Stop synchronization
- Stop historical imports
- Block sensitive administrative writes
- Preserve workspace data for operator review and possible reactivation
- Return a generic suspension message without exposing the reason

Suspension, security investigation, and abuse response must not release the provider league claim.

## Workspace Deletion

Workspace deletion is a background workflow with a 30-day grace period.

When deletion is requested:

- The workspace enters `deletion_pending`.
- All administrator and spectator sessions are revoked.
- New login and workspace activity are blocked.
- Synchronization and historical imports stop.
- The provider league claim remains reserved.
- Verified cancellation remains available during the grace period.

After 30 days, a background deletion job must:

1. Recheck that the workspace remains `deletion_pending`.
2. Revoke provider authorization when supported.
3. Cancel pending workspace jobs.
4. Delete provider credentials and OAuth tokens.
5. Delete tenant-owned application data.
6. Apply the audit-event retention policy.
7. Release the active provider league claim.
8. Mark deletion complete.

A released claim may remain as a nonblocking audit record.

Operator suspension, security investigation, or abuse action must not release the claim.

Deleted data may remain in encrypted backups until backup retention expires.

## Audit Architecture

Audit events are distinct from logs.

Audit events must record:

- Actor
- Workspace
- Event type
- Target
- Outcome
- Safe metadata
- Timestamp

Audit events must not contain secrets.

Representative event types:

```text
administrator_registered
email_verified
administrator_login_succeeded
administrator_session_replaced
spectator_login_succeeded
spectator_password_reset
provider_connection_created
provider_credentials_replaced
provider_league_claimed
provider_league_claim_conflict
synchronization_requested
workspace_suspended
workspace_deletion_requested
```

## Observability

Required operational signals include:

- API availability and latency
- PostgreSQL connectivity and capacity
- Worker availability
- Scheduler availability
- Queue depth
- Oldest job age
- Failed jobs
- Synchronization freshness
- Provider reauthorization
- Email failures
- Backup freshness
- Disk usage
- Certificate expiration

Metrics must avoid tenant-sensitive high-cardinality labels.

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

## Data Migration Strategy

Migration from v1.0.1 is additive.

Recommended sequence:

### Migration A: Workspace Identity Preparation

- Add workspace slug.
- Add nullable user workspace ownership.
- Backfill existing users.
- Add email-verification fields.
- Validate cardinality.
- Add indexes and constraints.

### Migration B: Provider Claims

- Add provider league claim table.
- Normalize existing ESPN identity.
- Create existing claim.
- Validate ownership.

### Migration C: Job Infrastructure

- Add background jobs.
- Add job indexes and active-idempotency constraints.
- Preserve existing synchronization runs.

### Migration D: Registration State

- Add provisional lifecycle fields.
- Add verification tokens.
- Add password-reset tokens.
- Add registration controls.

### Migration E: Audit Events

- Add audit-event table.
- Add bounded metadata.
- Add retention support.

Each migration must include:

- Precondition checks
- Backfill
- Validation
- Constraints
- Tests
- Recovery strategy

## Compatibility Strategy

Compatibility behavior may temporarily support:

- Existing username administrator login
- Existing setup routes
- Existing synchronous synchronization
- Existing ESPN environment fallback
- Existing frontend setup flow

Every compatibility path must have:

- One owner
- A removal condition
- Tests
- A documented sunset phase

Compatibility behavior must not become permanent by accident.

## Testing Strategy

### Unit Tests

Test:

- Domain state transitions
- Identity normalization
- Credential envelopes
- Job transitions
- Retry classification
- Workspace slug behavior
- Error-code mapping

### Repository Tests

Test:

- Workspace-scoped reads
- Workspace-scoped writes
- Claim uniqueness
- Account cardinality
- Job claiming
- Idempotency
- Row locking
- Token single use

### Service Tests

Test:

- Registration
- Email verification
- Login
- Session replacement
- Spectator concurrency
- Provider claiming
- Initial synchronization
- Workspace activation
- Suspension
- Deletion preparation

### API Tests

Test:

- Safe errors
- Role authorization
- Workspace isolation
- Duplicate claims
- Job ownership
- Enumeration protection
- Registration modes

### Provider Contract Tests

Test every provider against the shared canonical contract.

### Migration Tests

Test migrations against:

- Empty database
- Clean integration fixture
- Production-like v1.0.1 snapshot
- Invalid cardinality snapshot
- Duplicate-claim snapshot
- Missing administrator email
- Undecryptable provider credentials

### Frontend Tests

Test:

- Registration
- Login modes
- Onboarding resume
- Job polling
- Safe errors
- Mobile layouts
- Session replacement
- Spectator access

### Live Smoke Tests

Use controlled development provider leagues.

Never require live provider access for the automated suite.

## Cross-Tenant Test Standard

Every new tenant-owned feature must include a negative isolation test.

Required pattern:

```text
Create Workspace A.
Create Workspace B.
Authenticate as Workspace A.
Attempt to access Workspace B's resource.
Verify safe denial.
Verify no Workspace B information appears.
```

Internal IDs must be deliberately substituted in tests.

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

### Provider Unavailable

Expected behavior:

- Existing analytics remain readable.
- New jobs wait, retry, or fail safely.
- Provider credentials remain protected.
- Other providers continue processing.

### Worker Unavailable

Expected behavior:

- Jobs remain pending.
- API reads continue.
- Queue-age alert fires.
- Work resumes after worker recovery.

### Scheduler Unavailable

Expected behavior:

- Existing jobs continue.
- Administrator-triggered jobs continue.
- Automatic job creation pauses.
- Scheduler alert fires.

### PostgreSQL Unavailable

Expected behavior:

- API readiness fails.
- Worker readiness fails.
- No jobs are lost after committed creation.
- Processes back off rather than reconnecting tightly.

### Email Provider Unavailable

Expected behavior:

- Registration remains provisional.
- Verification and reset delivery may retry.
- Existing authenticated users continue.
- Safe messages do not claim successful delivery when none occurred.

### Duplicate League Claim

Expected behavior:

- Database rejects the duplicate.
- Losing transaction rolls back.
- No synchronization job is created.
- No owner information is exposed.
- Provisional registration is removed according to policy.

### Encryption Key Missing

Expected behavior:

- Readiness or provider operations fail safely.
- No credential envelope is overwritten.
- Existing canonical analytics may remain readable.
- Operator alert identifies the missing key version.

## Implementation Boundaries

Implementation should not begin with:

- Table renaming
- Microservice extraction
- PostgreSQL Row-Level Security
- Native mobile tokens
- Multiple administrators
- Individual spectator accounts
- Paid subscriptions
- Provider migration between workspaces
- Cloud-provider-specific dependencies

These features may be considered after the tenancy foundation is stable.

## Open Decisions

The following decisions remain open and must be resolved before their implementation phase:

1. Final email provider
2. Final email normalization implementation
4. Provisional workspace cleanup implementation
5. Exact worker lease duration
6. Exact retry schedule
7. Exact PostgreSQL pool sizes
8. Initial monitoring stack
9. Off-host backup destination
10. Audit-event retention duration
11. Native authentication transport
12. Cloud platform, if migration becomes necessary

Open decisions must not be guessed inside implementation code.

## First Implementation Slice

After this document set is merged, the first implementation slice is:

```text
Pre-Migration Audit and Multi-Workspace Test Foundation
```

The slice should include:

- Read-only SaaS migration audit
- Global-assumption inventory
- Two-workspace integration fixture
- Cardinality assertions
- Ownership-path assertions
- Migration rehearsal instructions
- No public registration
- No production schema change
- No production behavior change

This provides evidence for the first tenancy migration.

## Success Criteria

The target architecture succeeds when:

1. Multiple League Workspaces coexist safely.
2. Every user belongs to exactly one workspace.
3. Every active workspace has one administrator and one spectator.
4. Administrator sessions are exclusive.
5. Spectator sessions are concurrent.
6. Provider league claims are atomic.
7. Cross-workspace access is denied.
8. Synchronization is asynchronous and workspace scoped.
9. ESPN is stable for multiple tenants.
10. Sleeper passes the canonical provider contract.
11. Yahoo OAuth is tenant safe.
12. Existing v1.0.1 production data is preserved.
13. Registration and onboarding are resumable.
14. Core mobile workflows are usable.
15. Backups and restoration are proven.
16. Homelab operations support the initial user population.
17. Cloud migration remains possible without rewriting the domain.

## Existing Production Identity Migration Decisions

The existing v1.0.1 production workspace will use the workspace slug `jimmyandfriends`.

The existing administrator email will be marked verified during migration because the administrator account predates SaaS registration and is already the established production owner.

For new workspaces, provider validation may suggest a slug derived from the provider league name, but the registering administrator chooses the final available slug before league claiming.

Changing a league name at ESPN, Sleeper, or Yahoo must not automatically change the saved workspace slug.

The existing spectator username remains temporarily for compatibility. The target spectator login uses `jimmyandfriends` and the existing shared spectator password.
