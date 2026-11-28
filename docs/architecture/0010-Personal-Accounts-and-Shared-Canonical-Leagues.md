# ADR 0010: Personal Accounts and Shared Canonical Leagues

- **Status:** Accepted
- **Date:** 2026-11-25
- **Supersedes:** ADR 0004 in full; the conflicting parts of ADR 0002, ADR 0005, ADR 0006, and ADR 0007 (see the supersession map)
- **Depends on:** ADR 0001, ADR 0003, ADR 0008, ADR 0009

## Context

ADRs 0004 through 0007 defined a League Workspace as the tenant and security boundary. One administrator controlled one workspace, one shared spectator account read it, a provider league could belong to exactly one workspace, and the first importer held that league exclusively.

That model was implemented through registration, onboarding, workspace-scoped authorization, shared spectator access, activation, and workspace deletion. It has these product limits:

- A person who belongs to several leagues needs several workspaces and several administrator accounts.
- Two people in the same provider league cannot both use the product. The second is rejected as a duplicate claim.
- The shared spectator password is a shared secret that cannot be revoked per person.
- Access to a league is decided by whoever imported it first, not by each person's own provider access.

Henry approved replacing that model. Every person holds an independent personal account. A provider league becomes a shared canonical data resource. Each user proves access to each league season on their own.

This ADR is a documentation decision. It changes no code, schema, or deployment. Implementation follows the branch sequence in `docs/roadmap/SAAS_MIGRATION_ROADMAP.md`.

## Decision

1. Every person creates an independent personal account with a display name, an email, and a password. Email is the sole login identity.
2. The target normal-user model has no administrator role, spectator role, League Workspace, workspace slug, workspace owner, shared spectator account, shared password, or exclusive first-importer control.
3. A provider league is a shared canonical data resource and is not owned by any user.
4. Authorization is persisted per user and per league season. Every user must independently prove access to every private league season.
5. Provider credentials belong to the user who supplied them and never prove another user's access.
6. Synchronization is owned by the league season, not by a workspace.
7. Canonical league data outlives individual access relations and is retained for 30 days after the last active access disappears.

## Invariants

These invariants govern all work after this ADR.

- A user account is independent. No other account administers it, shares its credentials, or controls its access.
- One account may connect multiple leagues across multiple providers.
- A canonical league has no owner. The first importer creates the record when absent and gains no authority beyond that user's own access.
- The first importer cannot remove another user, administer later users, or decide whether another authorized user keeps access.
- Existing database records, another user's access, cached league data, and another user's provider credentials never prove authorization for a new user.
- Possessing or guessing an internal League ID or Season ID never grants access.
- Every response about an inaccessible resource is uniform and does not reveal whether the canonical league or season exists.
- No provider network call runs inside an open database transaction.
- No synchronization job payload, synchronization status response, or API response discloses provider secrets, provider connection IDs, whose credentials are used, or another user's account details.
- Removing one user's connection never deletes shared canonical data while another user retains active access.
- Public registration remains closed until the account-model cutover is complete and validated.

## Data Ownership

| Data | Owner | Lifetime |
| --- | --- | --- |
| User, sessions, verification and reset records, preferences | The user | Removed with the account |
| Provider connection and encrypted credentials | The user who supplied them | Removed with the connection or the user |
| `user_league_season_access` row | The user and the season | Removed with the user, the season, or an explicit removal |
| Canonical league, season, teams, matchups, players, analytics | Nobody (shared) | Retained while any active access exists, then 30 days |
| Synchronization jobs and runs | The season | Follow the season |

Provider credentials are never attached to the canonical league. A provider connection references its owning user. It is never visible to another user, never used to prove another user's access, and removed with the owning user or connection.

Credentials must not be retained merely because orphaned canonical league data remains during retention. Removing the last access relation does not keep credentials alive.

## Canonical Identity

For ESPN, the canonical league identity is:

```text
provider = espn
normalized provider league ID
```

ESPN league IDs continue using the established normalization: the value is trimmed, validated as decimal digits only, and stripped of leading zeroes. The season identity is the canonical league plus the season year.

This ADR does not define identity rules for Sleeper or Yahoo. Their provider-contract branches must establish identity, authentication, privacy, and season behavior from evidence.

## Conceptual Schema

This is the target direction, not a migration specification. The schema branch owns exact types, constraints, and migration order.

### users

- `id`
- `display_name`
- `email`
- `normalized_email`
- `password_hash`
- `email_verified_at`
- account status
- timestamps

Final retirement removes `workspace_id`, `role`, `username`, and the administrator and spectator uniqueness constraints.

### user_preferences

- `user_id`
- `default_league_season_id`

The application verifies that the user retains access to the selected season. The preference is cleared when the access relation disappears.

### provider_connections

Ownership is user-based. Conceptual fields:

- `user_id`
- `provider`
- encrypted credentials
- credential version
- schema version
- stable account fingerprint stored as an HMAC and never as plaintext
- connection status
- last validation time
- last safe failure code

### leagues

- No workspace ownership.
- Unique on provider and normalized provider league ID.
- `orphaned_at`.

### seasons

- Unique on league and season year.
- Synchronization lifecycle fields: `sync_state`, `last_successful_sync_at`, `last_sync_attempt_at`, `last_sync_failure_code`, `data_version`, and the last-observed access mode.
- Sync states: `never_synced`, `queued`, `syncing`, `fresh`, `stale`, `failed`.

### user_league_season_access

One row per user and season.

- `user_id`
- `season_id`
- `provider_connection_id` (nullable for public proof)
- `proof_mode`
- `verified_at`
- `last_reverified_at`
- `status`: `active`, `reverification_required`, `revoked`
- `created_at`
- `updated_at`

Uniqueness is enforced on user and season. The interface groups seasons under a canonical league, but each season requires its own access proof. Importing a new season requires fresh proof.

## Import Workflow

The target endpoint may be `POST /api/league-imports`. The exact path may be refined, but the behavior is approved.

### T0: short committed operation

- Authenticate the verified user.
- Require CSRF protection.
- Persist throttle state.

### Provider-proof phase: no open database transaction

- Perform the provider request.
- Use only anonymous access where applicable, or credentials supplied by the requesting user.
- Verify the returned provider target and season.
- Return a uniform failure that does not reveal whether the canonical league already exists.
- Persist no League, Season, access, or connection state after failed proof.

### T1: atomic transaction after successful proof

1. Upsert the requesting user's provider connection when credentials were supplied.
2. Insert or retrieve the canonical league through database uniqueness.
3. Coordinate safely with orphan purging.
4. Clear `orphaned_at`.
5. Insert or retrieve the season.
6. Insert or reactivate the user-season access row.
7. Queue one league-season synchronization job if needed.
8. Set the user's default season when absent.
9. Commit atomically.

### Expected behavior

- A first import creates the canonical records.
- A repeat import by the same user is idempotent.
- A second authorized user reuses the existing records.
- Simultaneous imports create one canonical league, one canonical season, one access row per user, and at most one active synchronization job.
- A failure rolls back only the requesting user's transaction. Another user's canonical records and access are never deleted.

## Authorization

Private import must:

1. Authenticate and verify the Fantasy What-If user.
2. Require CSRF protection.
3. Perform provider verification without an open database transaction.
4. Use only anonymous access where applicable, or credentials supplied by the requesting user.
5. Verify the returned provider target and season.
6. Return uniform failures that do not reveal whether the canonical league already exists.
7. Create or reactivate access only after successful proof.

Every read of league-season data requires *readable access* for the requesting user and season: an `active` or `reverification_required` row. A `reverification_required` row keeps the relationship, because that state records an ambiguous provider outcome rather than lost access. It serves prior successful data with a bounded reverification indicator; without such data the user sees only the season summary until synchronization or proof succeeds. It never exposes the credential owner, provider connection, raw provider failure, or another user. Missing access, `revoked` access, and a nonexistent season all return the same response.

*Active access* means `status = active`. Synchronization credential eligibility, orphan retention, purge decisions, and the other lifecycle rules in this ADR count only active access; `reverification_required` and `revoked` never count.

### Reverification

Private access is reverified periodically, initially every seven days.

Do not automatically revoke access because of:

- expired credentials
- HTTP 401 by itself
- timeout
- provider outage
- rate limiting
- generic authentication failure
- ambiguous ESPN 403 or 404

Those outcomes mark the provider connection invalid or set the access row to `reverification_required`, as appropriate.

Revoke access only when the provider supplies definitive evidence that the user no longer has access. Where ESPN cannot distinguish removed membership from an unavailable or nonexistent target, use `reverification_required` rather than automatic revocation.

If a previously public season becomes private, anonymous proof is no longer sufficient. Continued access requires private proof.

## Synchronization

Workspace-centered synchronization is replaced by league-season synchronization.

- The active-job idempotency key is `league-season-sync:{season_id}`.
- Only one active synchronization job may exist for a season.
- Jobs contain no secrets and reveal no credential-owner identity.
- Public seasons synchronize anonymously where the provider supports it.
- A private season may use any currently valid eligible provider connection, selected by joining active user-season access to active user-owned provider connections. The credential owner must hold active access to that season and the connection must remain valid.

Approved flow:

1. **Short transaction.** Load the season. Select and decrypt an eligible connection when the season is private. Commit and close.
2. **Provider network request** with no database transaction open.
3. **Short persistence transaction.** Acquire the season advisory transaction lock. Compare `data_version` when needed. Persist replacement canonical data. Rebuild derived analytics. Update sync state and the `SyncRun`. Commit.

The existing background-job leases, retries, active idempotency uniqueness, and season advisory locking may be reused.

### Behavior when a user imports an existing season

| Sync state | Behavior |
| --- | --- |
| `fresh` | Reuse immediately. |
| `stale` | Show existing data and queue one refresh. |
| `queued` or `syncing` | Allow access to prior successful data if it exists. Otherwise show an importing state. |
| `failed` | Show prior successful data with a warning and permit a safe retry. Otherwise show a failed-import state and permit a safe retry. |

No synchronization-status response discloses whose credentials are used, provider connection IDs, provider secrets, or another user's account details.

## Lifecycle

### Removing a league connection

Removing a league from an account removes that user's access to every connected season of that league. It removes or deactivates only that user's access rows. It must not delete canonical data while another user retains active access, remove another user's provider connection, or interrupt another authorized user.

### Account deletion

Account deletion removes the user, sessions, verification and reset records, preferences, user-owned provider connections, user-to-league-season access, and other personal account data. It must not delete shared league data while another user retains active access.

Personal account deletion is confirmed and immediate. There is no recovery state and no personal grace period; the 30-day window below applies to orphaned canonical league data only. A durable completion notice that outlives the user (an unscoped delivery with an encrypted recipient, created in the deletion transaction) is the only personal data retained, until it is sent or fails.

### Orphan retention

When the final active access relation disappears:

1. Mark the canonical league orphaned and set `orphaned_at`.
2. Retain the canonical league data for 30 days.
3. If the same league is re-imported during retention, revive the same record and clear `orphaned_at`.
4. After 30 days, a purge job may lock the league and delete it only after confirming zero active access relationships.

### Sessions

Multiple concurrent sessions per personal account are supported. Password reset and consequential account-security actions revoke all sessions. The one-active-administrator-session restriction is superseded.

## Security

- Credential encryption continues to use the versioned `PROVIDER_CREDENTIAL_KEYS` keyring. Account fingerprints use an HMAC and are never stored as plaintext.
- CSRF protection applies to the import endpoint and every other cookie-authenticated mutation.
- Throttle state is persisted before the provider request so that provider-proof attempts cannot be used for unlimited credential testing.
- Error responses, logs, jobs, and fixtures contain no provider credentials, raw provider responses, or credential-owner identity.
- Access decisions never depend on global or "first row" resolution.
- Negative tests are required for cross-user access whenever user-league behavior changes.
- Yahoo OAuth callback exposure is a later consequential network and deployment review (see unresolved questions).

## Frontend Consequences

Registration fields become display name, email, password, and confirm password. Workspace registration fields and "Create workspace" language are removed.

After verification, a user with no accessible leagues is routed to "Import your first league". A user with one or more accessible leagues is routed to the personal league dashboard.

Target capabilities:

- connected-leagues list
- league and season switcher
- import another league
- default season preference
- empty-account state
- import state and synchronization state
- credential-reconnection messaging visible only to the credential owner
- query-cache isolation by season

Retire or rewrite:

- administrator and spectator split login
- workspace slug login
- workspace registration fields
- league-claim onboarding
- shared spectator setup
- the activation step
- the league-already-connected page
- the workspace-unavailable page
- workspace deletion and recovery
- the admin route and admin page
- implicit single-current-league assumptions

The existing football design system is preserved unless a later visual decision supersedes it.

## Provider Support

The intended public-launch providers are ESPN, Sleeper, and Yahoo.

| Provider | Status |
| --- | --- |
| ESPN | Implemented |
| Sleeper | Not implemented |
| Yahoo | Not implemented |

Sleeper and Yahoo must not be described as functional until their provider branches are merged and validated.

## Player and Scoring Boundary

This direction is recorded now. It does not block the personal-account cutover.

### Canonical player identity

Canonical player identity eventually includes `nfl_players`, `provider_player_mappings`, and unresolved provider mappings. NFL team must not be immutable player identity. Historical affiliation belongs in season-aware or week-aware data.

### Global raw statistics

Raw weekly NFL statistics should eventually be stored once, keyed by canonical player, NFL season, week, and statistics source.

### League-specific records

These remain season-specific: fantasy-team ownership, roster membership, starter and bench state, lineup slots, matchups, waivers, transactions, keeper state, schedule, and provider-applied fantasy points.

### Scoring

Scoring configuration belongs to the league season. Retain conceptually the raw provider scoring payload, a normalized scoring representation, a normalizer version, and a deterministic scoring-profile fingerprint. Two identical normalized scoring configurations reuse one scoring profile.

Long-term calculated points are versioned by canonical player, NFL season, week, scoring-profile fingerprint, and calculator version.

### Launch boundary

The launch continues to use ESPN provider-supplied fantasy points. Canonical player and scoring boundaries follow the core account cutover. Independent NFL-statistics ingestion is not launch-critical for the account pivot. Source selection, licensing, corrections, player matching, defensive scoring, unusual bonuses, and rounding remain a separately estimated project.

## Migration Implications

- The hosted deployment is new. No v1 data is migrated, and this ADR adds no legacy-data preservation requirement.
- The existing workspace-owned schema, workspace-scoped authorization, claim table, shared spectator setup, activation gate, and FR-20 workspace deletion are the implemented baseline that the branch sequence replaces.
- Schema work is additive first. The canonical access schema lands before any behavior changes. The workspace schema is retired only after the account-only identity cutover and the frontend switcher are validated.
- Current staging validated the retired workspace model. It is not acceptance evidence for the personal-account model.
- Public registration remains closed during the entire cutover.
- A sync-fetch-outside-transaction fix precedes the schema work so that the new synchronization flow starts from the correct transaction boundary.

## Supersession Map

| Prior document | Effect of ADR 0010 |
| --- | --- |
| ADR 0004, League Workspace Multi-Tenancy | Superseded in full. |
| ADR 0002, Canonical Season and Provider Schema | Superseded for installation and workspace ownership, the one-active-season rule, the authentication transition, and tenant isolation and deletion. Canonical season, team, player, scoring, and lineup schema direction remains. |
| ADR 0005, SaaS Identity, Registration, and Sessions | Superseded for administrator and spectator roles, workspace slug, shared spectator identity, tenant-scoped login, one-administrator-session replacement, workspace-scoped constraints, and workspace-oriented registration. Email verification, password recovery, throttling, enumeration protection, and password requirements remain, applied to personal accounts. |
| ADR 0006, Resumable Onboarding and Background Jobs | Superseded for workspace ownership of jobs, claim-oriented onboarding, spectator setup, workspace activation, administrator-triggered workspace synchronization, and the workspace deletion job. The PostgreSQL job model, leases, retries, and idempotency mechanics remain. |
| ADR 0007, SaaS Provider Data and Credential Strategy | Superseded for the exclusive claim identity, workspace-owned provider connections, workspace credential resolution, and tenant isolation. Provider package boundaries, credential envelope and keyring, ESPN identity normalization (as restated here), and provider-contract rules remain. |

The superseded ADRs remain as historical records. Their text is not rewritten to describe the new model.

## Consequences

### Positive

- One person can use the product across many leagues without extra accounts.
- Several members of one league can each use the product. There is no duplicate-claim rejection.
- No shared spectator secret exists.
- Access follows each person's own provider access.
- Provider data is stored once per league season.

### Negative

- Authorization becomes per-user, per-season. Every league-data read requires an access check, and every league-data test needs cross-user negative cases.
- Sharing canonical data raises the cost of authorization mistakes. A flaw can expose one league's data to a user without access.
- Private synchronization can run on another member's valid credentials. This requires strict secret and identity non-disclosure.
- Reverification adds provider load and ambiguous-outcome handling.
- The cutover touches identity, schema, jobs, deletion, and frontend. It requires a long staged sequence.
- Implemented workspace behavior, tests, and documentation must be retired deliberately.

## Rejected Alternatives

### Keep the workspace model and add invitations

Rejected. It keeps a shared owner, a first-importer authority, and duplicate-claim rejection, and it still requires one administrator per league.

### Per-user copies of league data

Rejected. It duplicates synchronization work and provider load, and it lets copies diverge.

### Treat an existing canonical record as proof of access

Rejected. Existence of data, another user's access, or a guessed internal ID cannot establish that a new user may read a private league.

### Revoke access on any provider authentication failure

Rejected. Expired credentials, outages, rate limits, and ambiguous ESPN 403 or 404 responses are not evidence that membership ended.

### Attach provider credentials to the canonical league

Rejected. Credentials belong to the user who supplied them and must not outlive that user's connection.

### Immediate global NFL-statistics pipeline

Rejected as a prerequisite. It is a separately estimated project and does not block the account pivot.

## Unresolved Provider-Specific Questions

- **ESPN removed membership:** Which responses, if any, give definitive evidence that a user no longer has access? Until evidence exists, ambiguous 403 and 404 responses use `reverification_required`.
- **ESPN identity edge cases:** How an all-zero league ID normalizes, and whether the stripped form needs an identity-version record, must be settled in the schema branch. ADR 0007 describes preserving the digit sequence, and this ADR adopts leading-zero stripping.
- **ESPN account fingerprint:** Which stable credential-derived value feeds the HMAC fingerprint?
- **Sleeper:** Identity, authentication model, privacy of leagues, and season chain behavior are unknown and require evidence.
- **Yahoo:** Identity, OAuth scopes, token refresh, and season behavior are unknown. OAuth callback exposure is a later consequential network and deployment review.
- **Public-to-private transitions:** How each provider signals that a previously public season became private.
- **Independent NFL statistics:** Source selection, licensing, corrections, player matching, defensive scoring, unusual bonuses, and rounding.
