# 0004: League Workspace Multi-Tenancy and Data Ownership

> **Superseded in full by [ADR 0010](0010-Personal-Accounts-and-Shared-Canonical-Leagues.md).** The League Workspace, administrator and spectator roles, exclusive provider-league claims, workspace-scoped authorization, and workspace deletion described below are historical. Do not use this document for new work.

## Status

Superseded by ADR 0010

## Date

2026-11-18

## Context

Fantasy What-If v1.0.1 is designed around one self-hosted installation.

The current application assumes:

- Exactly one `Installation`
- One administrator
- One shared spectator
- One active league
- One active season
- One active provider connection
- Globally resolved setup state
- Globally resolved provider synchronization
- Global advisory locks for some setup and import operations

The SaaS direction requires multiple independent customers to use one hosted application and one PostgreSQL deployment without gaining access to each other's data.

The existing `Installation` model already owns:

- Provider connections
- Leagues
- Active league selection
- Active season selection
- Setup lifecycle state

This makes `Installation` the natural migration source for a SaaS tenant. The product-facing target name is **League Workspace**.

The first SaaS release will retain the existing simple account model within each workspace:

- Exactly one administrator identity
- Exactly one shared spectator identity
- At most one active administrator session
- Multiple concurrent spectator sessions
- One claimed provider league

A provider league may not be claimed by multiple workspaces.

## Decision

Fantasy What-If will use a shared-database, shared-schema multi-tenant architecture.

The League Workspace will be the primary tenant, authorization, ownership, synchronization, and deletion boundary.

Every authenticated application request must resolve a workspace context before accessing workspace-owned data.

The SaaS conversion will be implemented as an additive migration of the current modular monolith. It will not begin with microservices, separate databases per tenant, or separate application deployments per tenant.

## League Workspace

A League Workspace represents one isolated Fantasy What-If customer environment.

The target domain name is:

```text
LeagueWorkspace
```

The existing database table may initially remain:

```text
installations
```

The physical table will not be renamed during the first tenancy migration unless a rename materially reduces risk or complexity.

Application code may introduce a `LeagueWorkspace` domain concept while mapping it to the existing `installations` table.

This avoids combining a large semantic rename with the first multi-tenant ownership migration.

## Workspace Ownership

A League Workspace owns:

```text
League Workspace
├── Users
│   ├── One administrator
│   └── Zero or one shared spectator
├── Authentication sessions
├── Provider connections
├── Provider league claims
├── Leagues
│   ├── Seasons
│   │   ├── Teams
│   │   ├── Matchups
│   │   ├── Player-week scores
│   │   ├── Lineup-slot definitions
│   │   ├── Scoring rules
│   │   ├── Schedule rules
│   │   ├── Synchronization runs
│   │   └── Persisted analytics
│   └── Franchises
├── Workspace settings
└── Audit events
```

Every workspace-owned record must have a verifiable ownership path to exactly one League Workspace.

## User Ownership

The `users` table must gain an explicit non-null workspace foreign key after existing production users are backfilled.

Conceptual model:

```text
users.workspace_id
    -> installations.id
```

A user must belong to exactly one workspace in the first SaaS version.

A user may not authenticate into or act on behalf of another workspace.

The first SaaS version will not support:

- One user belonging to multiple workspaces
- Multiple administrators per workspace
- Administrator invitations
- Administrator role transfer
- Team-specific user accounts
- Individual spectator accounts

These restrictions simplify tenant resolution and preserve the established one-administrator, one-spectator model.

## Workspace Roles

The existing roles remain:

```text
admin
spectator
```

Role meaning becomes workspace-scoped.

### Administrator

The administrator may:

- Manage the provider connection
- Trigger synchronization
- Import historical seasons
- Manage the shared spectator account
- Configure workspace settings
- Configure supported administrative fallbacks
- Request workspace deletion
- View all workspace analytics

### Spectator

The spectator may:

- View supported workspace analytics
- Switch between imported seasons
- Use browser-local what-if features
- Use the Playoff Predictor
- Maintain multiple concurrent sessions

The spectator may not:

- Modify provider credentials
- Trigger synchronization
- Import seasons
- Change workspace configuration
- Manage accounts
- Access administrator details
- Request workspace deletion

## Account Cardinality

Database constraints must enforce workspace account cardinality.

Each workspace may have:

```text
Exactly one administrator
Zero or one spectator
```

A partial unique index should enforce one administrator per workspace.

Conceptual constraint:

```sql
CREATE UNIQUE INDEX
    uq_users_one_admin_per_workspace
ON users (workspace_id)
WHERE role = 'admin';
```

A second partial unique index should enforce one spectator per workspace.

Conceptual constraint:

```sql
CREATE UNIQUE INDEX
    uq_users_one_spectator_per_workspace
ON users (workspace_id)
WHERE role = 'spectator';
```

Workspace activation requires one administrator.

Spectator creation may occur during or after initial onboarding.

## Session Policy

Authentication sessions remain separate rows so sessions can be revoked independently.

### Administrator Sessions

A workspace may have at most one active administrator session.

After successful administrator authentication, one transaction must:

1. Lock the administrator identity or workspace authentication boundary.
2. Revoke all previously active administrator sessions for the workspace.
3. Create the replacement administrator session.
4. Commit the replacement atomically.

A failed login must not revoke the current administrator session.

The old browser loses access when its revoked session is checked on the next protected request.

A database constraint alone cannot reliably express an active-session rule that depends on expiration time. The backend must enforce replacement transactionally.

### Spectator Sessions

A spectator login creates an additional active session.

A new spectator login must not revoke existing spectator sessions.

Resetting the spectator password, disabling the spectator, deleting the spectator, or suspending the workspace must revoke every active spectator session for that workspace.

## Authenticated Workspace Context

Every protected request must resolve an authentication context containing at least:

```text
user_id
workspace_id
role
session_id
```

The workspace context must be derived from the authenticated session and user record.

Client-supplied workspace IDs must not be trusted as authorization.

Repository calls for workspace-owned data should accept an explicit workspace boundary whenever practical.

Conceptual dependency:

```python
@dataclass(frozen=True, slots=True)
class WorkspaceAuthenticationContext:
    user_id: int
    workspace_id: int
    role: UserRole
    session_id: int
```

Protected routes must deny access when:

- The workspace does not exist
- The workspace is inactive or suspended
- The user does not belong to the workspace
- The session has been revoked
- The role lacks the required permission
- The requested resource belongs to another workspace

## Provider League Claims

A provider league may be connected to only one League Workspace.

The exclusive claim key is:

```text
provider
normalized_provider_league_id
```

Examples:

```text
espn + 529250970
sleeper + 112233445566
yahoo + 449.l.123456
```

Provider identifiers are strings.

Provider identifiers are not assumed to be unique across providers.

A dedicated claim record is preferred over relying only on nullable fields in the `leagues` table.

Conceptual model:

```text
ProviderLeagueClaim
├── id
├── workspace_id
├── provider
├── normalized_provider_league_id
├── original_provider_league_id
├── status
├── claimed_at
├── released_at
├── created_at
└── updated_at
```

The authoritative uniqueness constraint is:

```sql
UNIQUE (
    provider,
    normalized_provider_league_id
)
```

The claim is held until final workspace deletion (see Workspace Deletion) and is then removed by cascade with the workspace. Released claims are not retained, so a plain unique constraint remains sufficient.

## Atomic League Claiming

Application-level availability checks are advisory only.

The database uniqueness constraint is the final concurrency authority.

A claim operation must:

1. Normalize the provider league identity.
2. Validate provider access.
3. Begin or reuse the onboarding transaction.
4. Attempt to insert the exclusive claim.
5. Create dependent provider, league, and season state only after the claim is held.
6. Commit the claim and required setup state atomically.
7. Translate a uniqueness conflict into a safe application conflict.

The losing caller in a concurrent claim race must receive:

```text
HTTP 409 Conflict
provider_league_already_claimed
```

Recommended safe message:

> This fantasy league is already connected to Fantasy What-If. Contact the league owner and ask for the workspace's spectator account if you need viewing access.

The response must not identify the existing workspace or its users.

## Duplicate-Claim Rollback

A rejected claim must not leave successful tenant application state.

The rejected flow must not retain:

- A workspace
- An administrator
- An application session
- A provider connection
- Encrypted provider credentials
- A canonical league
- A season
- Teams
- Matchups
- Player data
- Synchronization runs
- An activation marker

Separate rate-limit, abuse-prevention, or security telemetry may record the rejected attempt without identifying the existing tenant.

## Registration Transaction Boundary

Registration is both interactive and potentially long-running.

Provider synchronization must not keep one PostgreSQL transaction open across external network calls and a large canonical import.

The target flow separates account provisioning, league claiming, synchronization, and activation:

```text
Registration request
    -> provisional workspace and administrator
    -> authenticated onboarding session
    -> provider validation
    -> atomic provider league claim
    -> enqueue initial synchronization
    -> persist synchronization result
    -> activate workspace
```

The exact compensation and expiration rules for provisional workspaces remain part of system design.

The following invariants are mandatory:

- Only one workspace may hold a provider league claim.
- A workspace is not active before synchronization succeeds.
- Partial onboarding is not visible to other tenants.
- Failed onboarding can be resumed or safely expired.
- Abandoned provisional workspaces can be cleaned up.
- A duplicate league claim cannot leave an active workspace.

## Login Namespace

The login namespace remains unresolved by this ADR.

Candidate approaches follow.

### Global Usernames

```text
username
password
```

#### Advantages

- Smallest change from v1.0.1
- Simple login screen
- Simple native-app login
- Existing usernames migrate directly

#### Disadvantages

- Spectator usernames must be unique across the whole service
- Common names such as `spectator` will collide
- Workspace identity is not visible during login

### Workspace Slug and Local Username

```text
workspace_slug
username
password
```

#### Advantages

- Each workspace may use a familiar spectator username
- Identity is naturally tenant-scoped
- Workspace-local uniqueness matches the ownership model

#### Disadvantages

- Users must know the workspace slug
- Existing login UX changes
- Workspace enumeration must be prevented
- Password recovery requires workspace context

### Email for Administrator and Workspace-Aware Spectator Login

Administrator:

```text
email
password
```

Spectator:

```text
workspace_slug
spectator password
```

#### Advantages

- Administrator identity is natural for password recovery
- Spectator login remains workspace-specific
- Shared spectator usernames do not need global uniqueness

#### Disadvantages

- Two login flows
- Email delivery becomes required for full recovery
- Existing username-based administrator login must migrate
- Native clients must support separate identity modes

The selected design will be recorded in a later identity and authentication ADR.

## Tenant-Scoped Uniqueness

Existing global uniqueness constraints must be reviewed.

### Username

Current state:

```text
Globally unique
```

Target scope depends on the login-namespace decision.

### Email

Administrator email should likely remain globally unique for the initial SaaS version if one person may own only one workspace.

This is not finalized until email verification and recovery are designed.

### Provider League Identity

Provider league identity must be globally unique by:

```text
provider
normalized_provider_league_id
```

### Season

A season remains unique by:

```text
league_id
year
```

### Team

A team remains unique by:

```text
season_id
provider_team_id
```

### Matchup

A matchup remains unique by its canonical season and week identity.

## Data Access Strategy

The initial tenancy defense uses explicit application-level workspace scoping plus database constraints.

Every repository query for tenant-owned data must begin from or verify the workspace ownership path.

Unsafe pattern:

```python
session.get(
    Season,
    requested_season_id,
)
```

Safer pattern:

```python
select(Season).join(League).where(
    Season.id == requested_season_id,
    League.installation_id == workspace_id,
)
```

Internal numeric IDs are identifiers, not authorization.

A valid ID must still pass workspace ownership checks.

## Direct Workspace Columns

Not every canonical child table requires a duplicated workspace foreign key.

A direct workspace column should be added when the column materially improves:

- Authorization verification
- Query safety
- Tenant deletion
- Partitioning
- Operator maintenance
- High-volume filtering
- Tenant-scoped uniqueness

The expected first-stage ownership paths are:

```text
Workspace
    -> League
        -> Season
            -> Team
            -> Matchup
            -> PlayerWeekScore
            -> Rules
            -> SyncRun
```

High-volume tables may later receive direct `workspace_id` columns if query plans or operational safety justify the duplication.

## Database Row-Level Security

PostgreSQL Row-Level Security is not required for the first tenancy migration.

The first implementation will use:

- Explicit workspace foreign keys
- Workspace-aware repositories
- Tenant-scoped constraints
- Authorization dependencies
- Cross-tenant integration tests
- Code-review rules
- Audit logging

Row-Level Security remains an optional defense-in-depth layer after the ownership model and connection-management strategy stabilize.

Introducing Row-Level Security too early could complicate migrations, background workers, operator tasks, and test fixtures before the application tenancy boundary is proven.

## Workspace-Scoped Provider Resolution

Provider resolution must no longer locate the one active installation globally.

The target provider-resolution inputs are:

```text
workspace_id
season_id
```

The resolver must verify:

- The workspace owns the season's league.
- The workspace owns the provider connection.
- The season references the provider connection.
- The provider connection is active.
- The league provider matches the connection provider.
- The provider league claim belongs to the workspace.
- Credentials can be decrypted.

Provider adapters remain provider-neutral consumers of decrypted credentials and canonical identifiers.

## Workspace-Scoped Synchronization

Synchronization must be isolated by workspace and season.

A synchronization request must resolve:

```text
workspace_id
season_id
provider_connection_id
provider
```

One workspace's synchronization must not:

- Block every other workspace
- Resolve another workspace's provider connection
- Update another workspace's season
- Rebuild another workspace's analytics
- Reveal another workspace's synchronization status

Global constant advisory lock keys must be replaced with keys derived from stable resource identifiers.

At minimum, synchronization serialization must be scoped by internal season ID.

Season-import serialization must be scoped by workspace and league rather than one global import key.

## Background Work

Long provider operations should move out of HTTP request execution before registration opens publicly.

The modular monolith may run multiple process roles:

```text
frontend
api
worker
scheduler
database
migration
```

These roles may use the same backend codebase and images.

Separating process roles does not require splitting the application into microservices.

Persisted synchronization runs remain the source for progress and recovery.

## Workspace Suspension

A suspended workspace must deny all workspace access.

Suspension must:

- Revoke every administrator and spectator session
- Reject new administrator and spectator logins
- Block analytics, including read-only access
- Stop provider synchronization
- Stop historical imports
- Block provider credential changes
- Block administrative writes
- Preserve workspace data for operator review and possible reactivation
- Return only a generic suspension message to workspace users

The suspension response must not expose the suspension reason, operator notes, investigation details, or security evidence.

Suspension, security investigation, and abuse response must not release the provider league claim.

## Workspace Deletion

Workspace deletion applies to active workspaces. It is a tenant-wide background workflow with a 30-day grace period. FR-20 in `docs/product/SAAS_REQUIREMENTS.md` defines the full behavior; this section records the tenancy consequences.

Only the workspace administrator may request deletion, and only for an `active` workspace. The request requires a verified administrator, a standard session, CSRF and origin validation, the current password, and exact normalized workspace-slug confirmation. Suspended workspaces cannot request self-service deletion.

When deletion is requested, one transaction:

- Sets `registration_status = deletion_pending` and records `requested_at` and `scheduled_for`.
- Revokes every administrator and spectator session.
- Cancels pending and retry-scheduled workspace jobs.
- Queues one scheduled `workspace_deletion` job.
- Blocks all ordinary workspace access, provider changes, and administrative writes.

During the grace period the workspace retains its provider claim, slug, administrator email, provider connection, and encrypted credentials. The claim, slug, and email stay reserved until final deletion.

Cancellation is available only while now is earlier than `scheduled_for`. It uses a restricted `deletion_recovery` session (see ADR 0005). Password reset remains available but never reactivates the workspace.

Final cleanup is one ordered transaction:

1. Lock the workspace and recheck that it is `deletion_pending` and due.
2. Delete synchronization runs not otherwise removed.
3. Delete the workspace's leagues, cascading seasons and imported data.
4. Write an anonymized `workspace_deletion_record`.
5. Delete the workspace, cascading provider connections, users, sessions, tokens, provider league claims, and jobs.

The workspace row is hard-deleted. `deleted` is not a persisted `registration_status`. Final deletion releases the claim, slug, and email together. The existing `RESTRICT` foreign key from `seasons.provider_connection_id` is retained and satisfied by the explicit ordering above. It must not become `CASCADE`, `SET NULL`, or `DEFERRABLE` unless implementation testing proves explicit ordering insufficient.

Credentials are removed by cascade at final deletion, not before. Provider-side authorization revocation (for example, OAuth token revocation) is not part of the approved cleanup transaction and remains an open item for the provider credential and Yahoo OAuth design.

Anonymized `workspace_deletion_records` are retained for 365 days and contain only the former workspace ID, provider, normalized provider League ID, requested, scheduled, and completed timestamps, outcome, and retention expiration. Tenant-owned job and synchronization history is deleted with the tenant. Login-attempt and registration-rate-limit rows expire normally. Audit-event retention is deferred.

A terminal cleanup failure leaves the workspace `deletion_pending` and inaccessible; operators re-enqueue after repair.

Provisional duplicate-claim cleanup is separate from this workflow. Operator suspension, security investigation, or abuse action must not release the provider league claim.

Encrypted tenant data may remain in backups until the applicable backup-retention period expires.

## No Existing Production Migration

The hosted deployment is new. No v1.0.1 installation, user, session, provider connection, claim, or imported data is migrated or preserved. Every League Workspace is created through SaaS onboarding, which is the sole setup path. See the hosted deployment decision in `docs/product/SAAS_REQUIREMENTS.md`.

The schema migrations listed below were applied to development databases only. They are not a production data migration.

## Migration Sequence

The expected migration sequence is additive.

### Phase 1: Add Ownership Structures

- Add provider league claims.
- Add nullable workspace ownership to users.
- Add new lifecycle fields when required.
- Add audit-event structures.
- Backfill the existing production workspace.

### Phase 2: Validate Backfill

- Verify every user has one workspace.
- Verify one administrator exists.
- Verify zero or one spectator exists.
- Verify provider connections match league ownership.
- Verify active league and season ownership.
- Verify the provider claim matches the existing league.

### Phase 3: Add Constraints

- Make user workspace ownership non-null.
- Add one-administrator partial unique index.
- Add one-spectator partial unique index.
- Add provider-claim uniqueness.
- Add supporting indexes.

### Phase 4: Replace Global Resolution

- Replace global setup-state queries.
- Replace single-installation provider resolution.
- Replace global season import.
- Replace global synchronization status.
- Replace global spectator management.
- Replace global advisory locks.

### Phase 5: Enable Multiple Workspaces

- Add public registration controls.
- Add tenant-aware onboarding.
- Add duplicate-claim handling.
- Add background initial synchronization.
- Add cross-tenant test fixtures.
- Add operator controls.

## Testing Requirements

The tenancy foundation requires the following tests.

### Cardinality

- One administrator per workspace
- One spectator per workspace
- Multiple workspaces, each with a separate administrator
- Multiple workspaces, each with a separate spectator

### Sessions

- A new administrator login revokes the prior administrator session.
- A failed administrator login does not revoke the active session.
- Multiple spectator sessions remain active.
- A spectator password reset revokes all spectator sessions.
- Workspace suspension revokes required sessions.

### Provider Claims

- Different provider leagues can be claimed concurrently.
- The same numeric identifier may exist for different providers.
- The same provider league cannot belong to two workspaces.
- Concurrent duplicate claims produce exactly one successful claim.
- The losing transaction leaves no partial tenant state.
- Duplicate-claim responses reveal no owner details.

### Authorization

- Workspace A cannot read Workspace B's league.
- Workspace A cannot read Workspace B's season.
- Workspace A cannot read Workspace B's analytics.
- Workspace A cannot trigger Workspace B's synchronization.
- Workspace A cannot modify Workspace B's spectator.
- A spectator cannot call administrator routes.
- Numeric ID substitution does not bypass ownership.

### Migration

- A valid v1.0.1 database becomes one valid workspace.
- Existing canonical IDs remain stable.
- Existing credentials remain decryptable.
- Existing league and season selections remain active.
- Invalid pre-migration cardinality fails safely.

## Operational Consequences

Shared-schema tenancy is appropriate for the initial 50 to 100 user target because shared-schema tenancy:

- Preserves the modular-monolith architecture
- Avoids one PostgreSQL database per customer
- Simplifies migrations and backups
- Allows pooled database connections
- Keeps deployment suitable for the homelab
- Supports later cloud migration

The primary risk is accidental cross-tenant access.

That risk must be controlled through:

- Explicit workspace context
- Workspace-aware repositories
- Database ownership constraints
- Deny-by-default authorization
- Cross-tenant tests
- Audit logging
- Careful review of every global query

## Consequences

### Positive

- Existing `Installation` ownership relationships can be migrated incrementally.
- The one-administrator, one-spectator model is preserved.
- Spectator concurrency remains supported.
- Administrator-session exclusivity becomes explicit.
- Provider-league ownership is race-safe.
- ESPN, Sleeper, and Yahoo can share one tenant model.
- Existing production data can become the first workspace.
- The application can remain a modular monolith.

### Negative

- Most services currently assuming one installation must change.
- Existing users lack workspace ownership.
- Authentication uniqueness must be redesigned.
- Test fixtures must support at least two workspaces.
- Setup and synchronization flows become more complex.
- Tenant deletion requires careful cascade and retention planning.
- Provider claims introduce a new lifecycle separate from canonical league data.

## Rejected Alternatives

### One Deployment per Customer

Rejected for the SaaS target because separate deployments would retain operational burden, multiply upgrades and backups, and prevent efficient shared hosting.

### One PostgreSQL Database per Workspace

Deferred because separate databases add provisioning, migration, connection-management, and backup complexity disproportionate to the initial 50 to 100 user target.

### Multiple Administrators in the First Release

Rejected to preserve the established security and account model.

### Individual Spectator Accounts

Rejected because the product requirement is one shared spectator identity with concurrent sessions.

### Application-Only Duplicate Claim Check

Rejected because an application-only check cannot prevent concurrent claim races.

### Immediate Microservice Split

Rejected because tenancy and provider correctness are the current risks, not service decomposition.

## Follow-Up Decisions

The following subjects require separate architecture decisions:

1. Login namespace and workspace discovery
2. Registration transaction and provisional-workspace cleanup
3. Background synchronization execution
4. Provider credential and Yahoo OAuth lifecycle
5. Tenant-aware operator controls
6. Operator-initiated deletion policy for suspended workspaces (self-service deletion and claim release are decided in this ADR and FR-20)
7. Homelab SaaS deployment and recovery
8. Mobile API authentication strategy

## Requirement Traceability

This decision supports:

- `FR-3`: Workspace creation
- `FR-4`: One administrator per workspace
- `FR-5`: One active administrator session
- `FR-6`: Shared spectator account
- `FR-7`: Concurrent spectator sessions
- `FR-11`: Exclusive provider league claim
- `FR-12`: Atomic duplicate-claim rejection
- `FR-13`: Duplicate-claim privacy
- `FR-17`: Workspace-scoped season import
- `FR-19`: Analytics isolation
- `AR-1`: Workspace context
- `AR-4`: Deny by default
- `AR-5`: Cross-tenant tests
- `SR-2`: Administrator replacement
- `SR-3`: Spectator concurrency
- `DR-1`: Explicit ownership
- `DR-2`: Tenant-scoped constraints
- `SYR-1`: Tenant-scoped resolution
- `SYR-2`: Tenant-scoped locks