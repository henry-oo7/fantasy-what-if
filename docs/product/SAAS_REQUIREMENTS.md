# Fantasy What-If SaaS Requirements

## Document Status

- **Status:** Draft
- **Product baseline:** v1.0.1
- **Parent document:** `docs/product/SAAS_VISION.md`
- **Target deployment:** Hosted multi-tenant SaaS
- **Initial capacity target:** 50 to 100 registered users
- **Initial hosting environment:** Henry's homelab
- **Initial sport:** Fantasy football

## Purpose

This document defines the functional, security, data, provider, operational, and user-experience requirements for converting Fantasy What-If from a single self-hosted installation into a hosted multi-tenant service.

These requirements describe observable behavior. Detailed database tables, API routes, migrations, queue design, and deployment topology belong in the architecture and system-design documents.

## Terminology

### League Workspace

A League Workspace is the tenant and security boundary for one registered league owner and the fantasy league claimed during onboarding.

A League Workspace owns its:

- Administrator
- Shared spectator account
- Authentication sessions
- Provider connections
- Claimed provider league
- Imported seasons
- Canonical fantasy data
- Analytics results
- Synchronization runs
- Configuration
- Audit events

The existing `Installation` model is the migration source for League Workspace behavior.

### Administrator

The administrator is the single account authorized to configure and manage a League Workspace.

The administrator is the person who creates and operates the Fantasy What-If workspace. They do not need to be the provider league manager, the commissioner, or the owner of any particular fantasy team. Any verified administrator who can identify a public league, or provide valid access to a private league, may connect it. Fantasy What-If is read-only with respect to the provider and never changes rosters, lineups, teams, scoring, schedules, or league administration.

Product messaging targets fantasy players generally, for example: "Connect your fantasy league to explore deeper statistics, alternate outcomes, and what-if scenarios." It must not state or imply that only league managers may create a workspace, that commissioner verification is required, or that the administrator must prove league ownership.

### Spectator

The spectator is one shared read-only account for a League Workspace. Multiple people may use the spectator account concurrently.

The administrator creates and manages the single shared spectator password. Spectators sign in with the workspace slug plus that shared password. Spectators do not register, provide or verify an email address, choose a username, recover their own password, receive individual invitations, or receive team-specific accounts. Any spectator username retained in the database is an internal compatibility value: it is never chosen by the administrator, entered by a spectator, shown in the modern interface, or returned by the modern spectator API.

The public entry points are worded "Create a workspace", "Administrator sign in", and "Spectator sign in". The wording "Join as spectator" must not be used, because it implies self-registration or an access-request workflow.

### Provider League Identity

A provider league identity is the normalized combination of:

- Provider name
- Provider league ID or provider league key

Examples include:

- `espn` plus ESPN league ID
- `sleeper` plus Sleeper league ID
- `yahoo` plus Yahoo league key

### League Claim

A league claim is the exclusive association between one provider league identity and one League Workspace.

## Product Scope

The first SaaS release must allow a user to:

1. Open the hosted Fantasy What-If service.
2. Register one workspace administrator.
3. Connect a supported fantasy provider.
4. Claim one provider league.
5. Complete the initial synchronization.
6. Configure one shared spectator account.
7. Access canonical analytics.
8. Manage synchronization and workspace settings.
9. Use the service without hosting infrastructure.

The first SaaS release supports one claimed fantasy league per League Workspace.

## Functional Requirements

### FR-1: Public Registration

The service must provide public administrator registration when registration is enabled.

Registration must collect:

- Display name
- Email address
- Password
- Password confirmation
- Workspace display name when required
- Workspace slug or slug preference
- Acceptance of required legal terms when those terms are introduced

The service must normalize email and workspace-slug values before uniqueness checks.

The current schema still requires a `users.username` value. Registration generates an internal unique value for that column. The value is not collected from the registrant, is never returned by registration responses, and must not become a public SaaS login dependency.

Password handling must retain the existing Argon2 hashing behavior.

The API must never return a password, password hash, session token hash, or provider credential.

### FR-2: Registration Availability

The service operator must be able to:

- Enable public registration
- Disable public registration
- Place registration into maintenance mode
- Preserve existing login access while new registration is disabled

When registration is disabled, the API must return a stable safe response rather than relying only on a hidden frontend control.

Recommended application error code:

```text
registration_unavailable
```

### FR-3: Workspace Creation

A successful registration flow must create one provisional League Workspace associated with the new administrator.

A provisional workspace must not be visible to other tenants.

A provisional workspace must not be treated as active until provider validation, league claiming, initial synchronization, and required account setup are complete.

Workspace lifecycle states should include at least:

- `pending_registration`
- `setup_in_progress`
- `synchronizing`
- `active`
- `setup_failed`
- `suspended`
- `deletion_pending`

Exact enum names will be finalized during system design.

### FR-4: One Administrator per Workspace

Each League Workspace must have exactly one administrator identity after activation.

The first SaaS version must not support:

- Multiple administrators
- Administrator invitations
- Administrator role transfer
- One administrator account managing multiple workspaces

These capabilities may be added later through explicit migrations and authorization changes.

### FR-5: One Active Administrator Session

Each League Workspace may have at most one active administrator session.

After successful administrator authentication:

- The application must revoke every previously active administrator session for that workspace.
- The application must create the new administrator session.
- Revocation and session creation must be transactionally consistent.
- The previous browser must lose authenticated administrator access on its next protected request.

A failed login must not revoke the current administrator session.

The application may later provide a warning or session-replacement notice, but the one-session rule must be enforced by the backend.

### FR-6: Shared Spectator Account

Each active League Workspace may have exactly one spectator identity.

The administrator must be able to:

- Create the spectator account
- Change the spectator display name
- Change the spectator password
- Enable spectator access
- Disable spectator access

A spectator password reset or account disable operation must revoke all existing spectator sessions for that workspace. Re-enabling spectator access must not reveal the stored password.

Spectator setup occurs during onboarding after initial synchronization succeeds and before workspace activation.

The administrator chooses only the shared password: at least 14 and at most 128 characters, never trimmed. The server generates the internal compatibility username and stores no spectator email. Changing the password revokes all spectator sessions. Disabling access revokes them and blocks sign-in with the generic credential failure. Re-enabling access always requires a new shared password, so the previous one is never revealed or silently restored. Completing spectator setup completes onboarding; workspace activation is a separate step.

### FR-7: Concurrent Spectator Sessions

The spectator identity must support multiple concurrent sessions.

A new spectator login must not revoke other spectator sessions.

Session storage and cleanup must support multiple active session records for the spectator user.

The initial SaaS release does not require a configurable spectator-session limit. The architecture must allow a limit to be added later if abuse or resource usage requires one.

### FR-8: Tenant-Aware Login

A login attempt must resolve exactly one workspace identity without exposing another workspace's account information.

The selected login design must prevent ambiguous credentials across workspaces.

The system design must choose one of these supported approaches:

- Globally unique administrator and spectator usernames
- Workspace slug plus workspace-local username
- Email-based administrator login plus workspace-specific spectator login

The final choice must address:

- Username collisions
- Workspace discovery
- Spectator usability
- Password reset
- Mobile-app compatibility
- Information disclosure
- Migration of existing usernames

Decision: email-based administrator login plus workspace-slug spectator login with the shared spectator password (ADR 0005).

This remains an explicit system-design decision.

### FR-9: Provider Selection

The onboarding interface must display supported providers and their implementation status.

Providers planned for the SaaS architecture are:

- ESPN Fantasy Football
- Sleeper
- Yahoo Fantasy Sports

A provider must not be selectable as operational until the integration supports end-to-end validation, synchronization, persistence, and recovery.

### FR-10: Provider Validation

Provider validation must:

- Validate required identifiers
- Validate credentials or authorization when required
- Confirm that the provider league exists
- Confirm that the account may access the league
- Return safe errors
- Avoid persisting canonical league data before the claim transaction
- Avoid exposing raw provider responses

Validation must not create a permanent league claim by itself.

Validation must not synchronize or import league data, and must not require the administrator to be the provider league manager, commissioner, or a team owner.

For ESPN, the administrator provides the League ID and season year. Fantasy What-If first makes one minimal, bounded anonymous request for that exact league season:

- If anonymous access succeeds, the league is public. No credentials are requested or stored.
- If ESPN clearly requires authentication, the interface then asks for `SWID` and `espn_s2`, validates authenticated access, and stores the credentials encrypted.
- Any ambiguous failure (nonexistent league, wrong season, timeout, outage, malformed response) returns one safe failure, reveals no credential fields, stores nothing, and does not advance onboarding.

A successful validation stores the validated target (League ID, initial season year, public or private access mode, validation time, and encrypted credentials only for private access) and advances onboarding to league claim confirmation.

### FR-11: Exclusive Provider League Claim

A provider league identity may belong to only one League Workspace.

The database must enforce uniqueness for the normalized pair:

- `provider`
- `provider_league_id`

Season is not part of claim uniqueness. The workspace represents the continuing fantasy league, seasons belong to that workspace, and the same provider league must not create a separate workspace each year.

Application-level availability checks may improve the interface but must not be the authoritative concurrency control.

### FR-12: Atomic Duplicate-Claim Rejection

If two registrations attempt to claim the same provider league concurrently, at most one transaction may succeed.

The losing transaction must return:

- HTTP `409 Conflict`
- Stable application error code `provider_league_already_claimed`
- A safe user-facing message

Recommended message:

> This fantasy league is already connected to Fantasy What-If. Contact the league owner and ask for the workspace's spectator account if you need viewing access.

The rejected transaction must not leave behind:

- A League Workspace
- A user
- An authentication session
- A provider connection
- Encrypted credentials
- A league
- A season
- Teams
- Matchups
- Player data
- Synchronization runs
- Audit records indicating a successful registration

Security and abuse audit records may be retained separately if they do not create tenant-owned application state.

Validation and claim are separate steps. Validation confirms access; the claim happens only after the administrator explicitly confirms the validated target. The losing registration is removed in the same transaction that detects the conflict, its authentication cookies are cleared, and the browser shows a public outcome page with Administrator sign in and Spectator sign in actions. The response discloses no owner identity, email, workspace slug, claim identifier or account detail.

### FR-13: Duplicate-Claim Privacy

Duplicate-claim responses must not expose:

- Workspace ID
- Administrator username
- Administrator display name
- Administrator email
- Spectator username
- Spectator activity
- Existing season data
- Provider credentials
- Account status
- Last synchronization time
- Whether a specific person owns the workspace

The response may reveal only that the provider league is already connected.

### FR-14: Resumable Onboarding

Onboarding should be resumable after safe intermediate steps.

A user who signs out or loses browser connectivity should be able to resume from the last committed setup stage.

Resumable onboarding must not weaken atomic league claiming. The system design must distinguish between:

- Provisional registration data
- Exclusive league claim creation
- Long-running initial synchronization
- Final workspace activation

### FR-15: Initial Synchronization

Workspace activation requires a successful initial synchronization.

The initial synchronization must:

- Use the claimed provider league
- Create canonical league and season data
- Persist provider capabilities
- Persist lineup and schedule rules when available
- Record a synchronization run
- Preserve safe failure information
- Avoid exposing provider credentials
- Be recoverable after an HTTP timeout or browser disconnect

A failed initial synchronization must leave the workspace resumable or safely recoverable.

### FR-16: Workspace Activation

A League Workspace may become active only when:

- The administrator exists
- The provider connection is valid
- The provider league claim exists
- The active league exists
- The active season exists
- Initial synchronization succeeded
- Required canonical settings exist
- The workspace ownership graph is internally consistent

Spectator creation may occur before or immediately after activation, depending on the final onboarding design.

### FR-17: Workspace-Scoped Season Import

Historical season import must operate within the authenticated League Workspace.

Historical season import must not search globally for the only installation or only league.

The service must:

- Resolve the workspace from the authenticated session
- Resolve the workspace's claimed league
- Use the workspace-owned provider connection
- Prevent duplicate seasons within the league
- Prevent access to another workspace's seasons
- Record workspace-scoped synchronization results

### FR-18: Workspace Selection

The first SaaS release does not require one user to belong to multiple workspaces.

Authenticated requests may derive the League Workspace directly from the authenticated user.

API design should avoid unnecessary workspace IDs in client-controlled paths when the session already determines the workspace.

If a workspace ID is accepted from the client, the backend must verify ownership independently.

### FR-19: Analytics Isolation

Every analytics query must be constrained to the authenticated League Workspace's league and season.

This includes:

- Dashboard
- Current standings
- All-Play standings
- League Average
- Schedule Swap
- Optimal Lineups
- Playoff Predictor
- Historical season browsing

Internal numeric IDs must never be considered authorization.

### FR-20: Workspace Deletion

The SaaS design must support administrator-requested workspace deletion.

Workspace deletion must use a 30-day grace period.

When deletion is requested:

- The workspace enters `deletion_pending`.
- Administrator and spectator sessions are revoked.
- New login and workspace activity are blocked.
- Synchronization and imports stop.
- The provider league claim remains reserved.
- The administrator may cancel deletion through a verified recovery process during the grace period.

After the 30-day grace period, a background deletion job must:

- Revoke provider authorization when supported.
- Delete provider credentials and OAuth tokens.
- Delete tenant-owned canonical data.
- Apply the audit-event retention policy.
- Complete tenant deletion.
- Release the active provider league claim.

A released claim may remain as a nonblocking audit record.

Operator suspension, security investigation, or abuse response must not release the provider league claim.

Deleted data may remain in encrypted backups until backup retention expires. The deletion workflow and user-facing policy must disclose this limitation.

## Provider Requirements

### PR-1: Canonical Provider Contract

All providers must produce the canonical domain objects defined by the provider architecture.

Analytics must not consume raw ESPN, Sleeper, or Yahoo objects.

### PR-2: Provider Identity Normalization

Each provider adapter must define how provider league identity is normalized.

Normalization must be deterministic and documented.

The application must preserve the original provider identifier when useful for diagnostics while enforcing claims against the normalized identity.

### PR-3: ESPN Hardening

Before open SaaS registration, ESPN must support:

- Multiple workspace-owned provider connections
- Public and private leagues
- Encrypted `espn_s2` and `SWID` storage
- Correct future schedules
- Historical seasons
- League settings
- Playoff settings
- Stable string identifiers
- Retry classification
- Safe authentication errors
- Sanitized deterministic fixtures
- Credential replacement
- Credential deletion

Environment-level ESPN credential fallback must not be used for ordinary SaaS tenants.

### PR-4: Sleeper Integration

Sleeper support must implement the same canonical provider protocol.

Sleeper investigation and implementation must cover:

- User lookup when needed
- League discovery
- League ID normalization
- Season ancestry
- Rosters
- Matchups
- Weekly scores
- Lineup slots
- Scoring rules
- Playoff settings
- Historical availability
- Provider capabilities
- Error handling
- Rate-limit behavior

### PR-5: Yahoo Integration

Yahoo support must include:

- OAuth authorization
- State and callback validation
- Access-token storage
- Refresh-token storage
- Token refresh
- Authorization revocation
- Yahoo league-key normalization
- Required scopes
- Reauthorization behavior
- Safe provider errors
- Tenant-scoped callback resolution
- Protection against callback-account confusion

Yahoo must not be exposed as implemented until token refresh and synchronization recovery are tested end to end.

### PR-6: Provider Capability Honesty

A provider or season must advertise only supported analytics.

The application must not fabricate missing data to make an analytics feature appear available.

### PR-7: Provider Rate Protection

Synchronization must protect provider services and the hosted application through:

- Per-provider concurrency limits
- Per-workspace synchronization locks
- Per-season synchronization locks
- Retry classification
- Exponential backoff where appropriate
- Safe handling of HTTP `429` responses
- Synchronization scheduling with jitter

Exact provider limits must be documented when verified.

## Authorization Requirements

### AR-1: Workspace Context

Every protected request must resolve an authenticated user and League Workspace context.

The context must include at least:

- User ID
- Workspace ID
- Role
- Session ID

### AR-2: Administrator Authorization

Administrator-only capabilities include:

- Provider connection changes
- Synchronization
- Historical import
- Spectator management
- Workspace settings
- Tiebreaker fallback configuration
- Account security changes
- Workspace deletion request

### AR-3: Spectator Authorization

Spectators may read supported workspace analytics.

Spectators must not:

- Modify provider settings
- Trigger synchronization
- Import seasons
- Change spectator credentials
- Access encrypted credentials
- Change workspace settings
- View administrator account details
- Request workspace deletion

What-if selections that remain local browser state are permitted.

### AR-4: Deny by Default

New workspace-owned routes must deny access unless tenant context and role authorization are explicitly applied.

### AR-5: Cross-Tenant Tests

Every repository or service that reads workspace-owned data must have tests proving that another workspace cannot access the data.

Representative tests must cover:

- Direct internal-ID substitution
- Season-query substitution
- League-query substitution
- Provider-connection substitution
- Synchronization-target substitution
- Administrator endpoint access as spectator
- Workspace-deletion boundaries

## Session Requirements

### SR-1: Secure Session Storage

Session tokens must remain:

- Random
- Stored only as hashes
- Revocable
- Expiring
- Bound to a user
- Resolvable to a workspace

### SR-2: Administrator Replacement

Creating a new administrator session must revoke active administrator sessions for that workspace in the same transaction.

### SR-3: Spectator Concurrency

Creating a spectator session must not revoke other spectator sessions.

### SR-4: Role Change Revocation

Disabling or changing an account must revoke affected sessions.

### SR-5: Session Cleanup

Expired and revoked sessions must be removable through workspace-safe maintenance.

Cleanup may operate globally as an operator task because session deletion does not expose tenant data, but application-facing results must not reveal other tenants' session counts.

## Data Requirements

### DR-1: Explicit Ownership

Every workspace-owned aggregate must have an explicit ownership path to a League Workspace.

Direct ownership columns should be added where the columns materially improve:

- Authorization
- Query safety
- Uniqueness constraints
- Deletion
- Operational maintenance

Ownership must not rely solely on assumptions about one global installation.

### DR-2: Tenant-Scoped Constraints

Uniqueness constraints must reflect intended scope.

Examples requiring design review include:

- Username
- Email
- Provider league identity
- Provider connection
- League identity
- Season year
- Spectator identity
- Active league
- Active season

### DR-3: Canonical Numeric Data

Scores and fantasy points must continue using decimal-safe persistence.

### DR-4: Provider Metadata

Provider metadata may preserve noncanonical fields but must exclude:

- Passwords
- Cookies
- OAuth access tokens
- OAuth refresh tokens
- API secrets
- Authorization headers
- Raw private-account payloads

### DR-5: Audit Events

Security-sensitive SaaS actions must produce structured audit events.

Required categories include:

- Registration attempt
- Registration success
- Duplicate league claim
- Login success
- Login failure
- Administrator session replacement
- Spectator password reset
- Provider connection creation
- Provider credential replacement
- Synchronization request
- Workspace suspension
- Workspace deletion request

Audit events must not contain secrets.

## Synchronization Requirements

### SYR-1: Tenant-Scoped Resolution

Synchronization must accept or resolve a specific workspace and season.

Synchronization must not query for the only active installation.

### SYR-2: Tenant-Scoped Locks

Synchronization and import advisory locks must be derived from stable resource identifiers.

Global constant lock keys that serialize unrelated tenants must be removed or restricted to operator-wide tasks.

### SYR-3: Background Execution

Long-running provider synchronization should execute outside the request lifecycle before open SaaS registration.

The API should enqueue or create work and return a job or synchronization-run identifier.

The existing persisted-state polling behavior is a migration foundation.

### SYR-4: Idempotency

Repeated synchronization of unchanged provider data must preserve an equivalent canonical state.

### SYR-5: Failure Isolation

One workspace's synchronization failure must not block unrelated workspaces.

### SYR-6: Scheduling

Automatic synchronization must be workspace-aware and distribute provider requests over time.

The scheduler must avoid launching all workspaces simultaneously.

## Mobile and API Requirements

### MR-1: Responsive Registration

Registration and onboarding must work at a viewport width of 320 CSS pixels without page-level horizontal scrolling.

### MR-2: Responsive Analytics

Core analytics must remain usable on mobile web.

Touch targets should generally be at least 44 by 44 CSS pixels where practical.

### MR-3: Native-App-Compatible APIs

Authentication and API contracts must not assume that all clients are browser pages.

The future native application may require a different token transport, but authorization and tenant ownership must remain shared.

### MR-4: Desktop Stability

The SaaS conversion should not redesign the desktop analytics interface unless a change is required for tenant selection, registration, security, or provider support.

## Operational Requirements

### OR-1: Initial Capacity

The homelab deployment must support the initial target of 50 to 100 registered users.

Capacity testing must model:

- Concurrent logins
- Concurrent spectator reads
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
- Role authorization

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

- Whether administrator email is mandatory
- Email verification
- Password-reset delivery
- Reset-token expiration
- Reset-token single use
- Administrator account recovery when provider credentials are unavailable

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

## Acceptance Requirements

The SaaS foundation is not ready for implementation completion until automated tests prove:

1. Two workspaces may coexist.
2. Each workspace has one administrator.
3. Each workspace has at most one spectator identity.
4. Administrator login replaces the previous administrator session.
5. Spectator logins remain concurrent.
6. A duplicate provider league claim is rejected.
7. Concurrent duplicate claims produce exactly one winner.
8. The rejected claim leaves no partial tenant state.
9. Workspace A cannot read Workspace B's league.
10. Workspace A cannot read Workspace B's season.
11. Workspace A cannot trigger Workspace B's synchronization.
12. A spectator cannot use administrator endpoints.
13. A new hosted deployment initializes without migrating v1 installation data.
14. ESPN synchronization still passes its contract tests.
15. Registration works at 320 CSS pixels.
16. Backup restoration has been tested.

## Deferred Decisions

The system design must resolve:

- Login identifier and workspace discovery
- Mandatory email and verification
- Password-reset delivery
- Registration transaction boundaries
- Provisional-workspace expiration
- Background-job implementation
- Workspace-slug requirements
- Whether the physical `installations` table is renamed
- Whether tenant IDs are added directly to all high-volume tables
- Tenant-aware database defense beyond application queries
- Operator administration interface
- Terms, privacy, and acceptable-use workflows
- Yahoo OAuth client configuration
- Cloud migration architecture

## Traceability

Future architecture and implementation documents should reference requirement IDs from this document.

Examples:

```text
FR-12: Atomic duplicate-claim rejection
AR-5: Cross-tenant tests
SYR-2: Tenant-scoped locks
SER-4: Credential encryption

```

A requirement should be updated here when product behavior changes. Architecture documents should explain how the requirement is satisfied rather than redefining product behavior.
