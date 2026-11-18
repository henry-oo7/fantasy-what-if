# Fantasy What-If SaaS Vision

## Document Status

- Status: Draft
- Baseline release: v1.0.1
- Target: Hosted multi-tenant SaaS
- Initial hosting environment: Henry's homelab
- Initial capacity target: 50 to 100 registered users
- Initial supported sport: Fantasy football

## Product Vision

Fantasy What-If will become a hosted fantasy analytics service that users can join without operating their own server.

A league owner will be able to:

1. Register an administrator account.
2. Create a League Workspace.
3. Connect a supported fantasy provider.
4. Claim a fantasy league.
5. Synchronize canonical league data.
6. Create the workspace's shared spectator account.
7. Use Fantasy What-If analytics from desktop or mobile browsers.

The hosted service will preserve the existing analytics experience while replacing the single self-hosted installation lifecycle with tenant-aware registration, onboarding, authorization, synchronization, and data ownership.

## Product Principles

### Hosted by Default

End users must not need Docker, PostgreSQL, a virtual machine, a domain, or server administration knowledge.

The service operator owns deployment, upgrades, backups, observability, security patches, and database migrations.

### Provider-Neutral Canonical Data

ESPN, Sleeper, and Yahoo data must be converted into stable canonical domain contracts.

Provider-specific identifiers, credentials, vocabulary, API behavior, and unsupported capabilities remain within provider integration boundaries.

Analytics should consume canonical data rather than provider response objects.

### Historical Data over Speculative Scores

What-if standings may change game outcomes, but Fantasy What-If will not invent future fantasy-point totals.

Projected standings and brackets will use:

- Synchronized completed results
- User-selected matchup outcomes
- Provider-defined standings rules
- Points already scored
- Deterministic documented fallbacks

ESPN, Sleeper, Yahoo, or third-party player projections will not be treated as historical results.

### Secure Tenant Isolation

Every League Workspace is an independent security and data-ownership boundary.

A user authenticated to one workspace must not be able to read, modify, synchronize, enumerate, or infer another workspace's data.

Tenant isolation must be enforced in backend queries and database ownership relationships, not only by frontend routing.

### Preserve the Simple Account Model

Each League Workspace will initially have:

- Exactly one administrator identity
- Exactly one shared spectator identity
- Multiple concurrent spectator sessions
- At most one active administrator session

A successful administrator login must revoke or replace any existing administrator session for the same workspace.

Spectator login must allow multiple viewers to use the shared spectator credentials concurrently.

### Mobile-Web First, Native-App Ready

The existing desktop interface is acceptable for the initial SaaS conversion.

Mobile web must be polished for common workflows, including:

- Registration
- Login
- Provider connection
- League setup
- Season selection
- Standings
- Matchup editing
- Playoff Predictor
- Administrative synchronization

API contracts and authentication boundaries must remain suitable for a future native mobile application.

## League Workspace

The target customer boundary is called a League Workspace.

A League Workspace owns:

- Administrator identity
- Shared spectator identity
- Provider connections
- Claimed provider leagues
- Imported seasons
- Teams and franchises
- Matchups
- Rosters and player-week scores
- League settings
- Analytics results
- Synchronization history
- Workspace preferences
- Audit and security events

The existing Installation model is the migration source for this concept.

Whether the installations table is renamed will be decided during detailed system design. Avoiding a physical table rename may reduce migration risk while still exposing League Workspace terminology in product and API contracts.

## League Claiming and Exclusivity

A provider league may belong to only one League Workspace at a time.

The canonical claim identity is:

- Provider
- Provider league ID

Examples:

- ESPN plus ESPN league ID
- Sleeper plus Sleeper league ID
- Yahoo plus Yahoo league key

Provider league IDs are not assumed to be globally unique across different providers.

### Atomic Claim Requirement

League claiming must be protected by a database unique constraint on normalized provider identity.

Application-level availability checks are not sufficient because two registrations may run concurrently.

Registration and initial setup must use a transaction that includes the claim and all dependent records.

If a claim conflicts, the transaction must roll back completely.

A failed claim must not leave behind:

- A partial League Workspace
- An administrator account
- A provider connection
- Encrypted provider credentials
- A league
- A season
- Teams
- Matchups
- Synchronization records
- An active session

### Existing League Experience

When a registrant attempts to claim a league that already belongs to another workspace, the API must return a safe conflict response.

Recommended response category:

- HTTP 409 Conflict
- Stable application error code: provider_league_already_claimed

Recommended user-facing message:

> This fantasy league is already connected to Fantasy What-If. Contact the league owner and ask for the workspace's spectator account if you need viewing access.

The response must not reveal:

- Administrator username
- Administrator email
- Administrator display name
- Spectator username
- Workspace membership
- Session activity
- Provider credentials
- Internal workspace IDs

The conflict must not disclose whether a particular person owns the league.

## Registration and Onboarding

The initial SaaS onboarding sequence should be:

1. Create administrator credentials.
2. Verify required registration controls.
3. Create a provisional League Workspace.
4. Select a fantasy provider.
5. Enter or authorize provider configuration.
6. Validate the provider connection.
7. Discover or identify the provider league.
8. Claim the provider league atomically.
9. Run the initial canonical synchronization.
10. Create or configure the shared spectator account.
11. Activate the League Workspace.
12. Enter the application.

The exact transaction boundary will be decided during system design.

The preferred user experience is resumable onboarding, but the provider league claim and workspace activation must remain strongly consistent.

Registration may evolve from the existing setup sequence, but the current security protections must not be weakened.

## Provider Roadmap

### ESPN

Before broader SaaS registration, ESPN support must be hardened for multiple independent workspaces.

Required areas include:

- Private league credential handling
- Credential encryption and rotation
- Provider league identity normalization
- Schedule accuracy
- Historical season import
- Provider settings serialization
- Rate-limit and retry behavior
- Safe provider error messages
- Per-workspace synchronization
- Per-season advisory locking
- Capability detection
- Contract fixtures from sanitized provider data
- Regression tests for ESPN API behavior

### Sleeper

Sleeper should be the second fully implemented provider because its public API and provider identifiers are generally suitable for server-side integration.

Implementation must still use the canonical provider contracts rather than adding Sleeper behavior directly to analytics.

Required investigation includes:

- User and league discovery
- League identity
- Season chains
- Rosters
- Matchups
- Scoring settings
- Lineup slots
- Playoff settings
- Historical data availability
- API limits and reliability
- Authentication requirements

### Yahoo Fantasy Sports

Yahoo requires separate OAuth and token-lifecycle planning.

Required investigation includes:

- Yahoo OAuth authorization
- Refresh-token storage
- Token revocation
- Yahoo league keys
- User-consent scopes
- Season and game identifiers
- Rate limits
- Provider capability differences
- Reauthorization workflows
- Secure deletion of Yahoo credentials

Yahoo must not be represented as implemented until authorization, synchronization, token refresh, and error recovery work end to end.

## Initial Homelab SaaS Scope

The initial hosted deployment should support approximately 50 to 100 registered users without premature service decomposition.

The preferred application shape remains:

- React frontend
- FastAPI backend
- PostgreSQL
- Background synchronization process
- Reverse proxy
- Containerized deployment
- Automated migrations
- Encrypted provider credentials
- Central logs and health checks

A modular monolith is appropriate for this stage.

Background work may be separated operationally from HTTP handling without splitting the domain into microservices.

## Homelab Operational Requirements

Before public registration, the hosted service requires:

- Automated PostgreSQL backups
- Tested restore procedures
- Off-host backup copies
- TLS termination
- Secure secret management
- Rate limiting
- Registration abuse controls
- Login throttling
- Health monitoring
- Disk-capacity monitoring
- Database connection monitoring
- Synchronization queue monitoring
- Structured application logs
- Audit events
- Deployment rollback procedures
- Migration rollback or forward-recovery procedures
- Maintenance-mode capability

A home internet outage or hardware failure must be treated as a known availability limitation during the initial phase.

## SaaS Security Requirements

The current protections must be retained and adapted for tenant-aware operation:

- Argon2 password hashing
- Hashed session tokens
- CSRF protection
- Trusted-origin validation
- Login throttling
- Session revocation
- Encrypted provider credentials
- Safe error messages
- Administrator authorization
- Spectator read-only authorization
- Secure cookie configuration
- No provider-secret exposure in API responses or logs

Additional SaaS requirements include:

- Tenant-scoped authorization on every protected resource
- One active administrator session per workspace
- Concurrent spectator sessions
- Atomic provider league claims
- Registration throttling
- Email ownership strategy
- Password reset strategy
- Audit logging
- Account and workspace deletion
- Credential deletion and revocation
- Backup-retention policy
- Privacy policy
- Terms of service
- Abuse response process

## Data and Migration Direction

The migration from v1.0.1 should be additive and staged.

The existing production installation should become the first League Workspace.

Existing data must be preserved, including:

- Users
- Sessions where safely migratable
- Provider credentials
- League identity
- Seasons
- Teams
- Matchups
- Player-week scores
- Analytics results
- Synchronization history
- Playoff settings
- Administrator fallback settings

Likely migration work includes:

- Explicit workspace ownership for users
- Workspace-aware session authorization
- Tenant-scoped uniqueness constraints
- Provider identity claim constraints
- Removal of global single-installation queries
- Workspace-scoped provider resolution
- Workspace-scoped advisory locks
- Workspace-scoped setup state
- Workspace-scoped spectator management
- Workspace-scoped synchronization
- Workspace-aware API routes and dependencies
- Safer serialized provider metadata

No destructive migration should be applied until production data has been backed up and a restore has been tested.

## Explicit Non-Goals for the First SaaS Release

The first hosted SaaS release does not require:

- Paid subscriptions
- Multiple administrators per workspace
- Individual spectator accounts
- Team-specific invitations
- Social login
- Native iOS or Android applications
- Microservices
- Kubernetes
- Multi-region deployment
- Automatic cloud migration
- Support for fantasy sports other than football
- Sharing one administrator identity across multiple workspaces

These features may be reconsidered after the tenant model and provider integrations are stable.

## Initial Success Criteria

The first SaaS milestone is successful when:

1. A new user can register without server administration.
2. Registration creates an isolated League Workspace.
3. The user can connect one supported provider league.
4. A duplicate provider league claim is rejected atomically.
5. The workspace administrator can finish onboarding.
6. The workspace can create one shared spectator account.
7. Multiple spectator sessions can be active concurrently.
8. Only one administrator session can remain active.
9. No cross-workspace data access is possible.
10. Synchronization jobs are isolated by workspace and season.
11. Existing v1.0.1 production data migrates without loss.
12. ESPN synchronization remains stable.
13. Sleeper and Yahoo can be added through provider contracts.
14. Core mobile-web workflows are usable without horizontal overflow.
15. The homelab deployment can operate safely for the initial user target.

## Next Documents

This vision is the product baseline for:

- SAAS_REQUIREMENTS.md
- MULTI_TENANCY_AND_DATA_OWNERSHIP.md
- PROVIDER_DATA_STRATEGY.md
- HOMELAB_SAAS_DEPLOYMENT.md
- SAAS_TARGET_ARCHITECTURE.md
- SAAS_MIGRATION_ROADMAP.md

Detailed implementation must not begin until the target ownership model, registration transaction, authorization model, and production migration sequence are documented and reviewed.
