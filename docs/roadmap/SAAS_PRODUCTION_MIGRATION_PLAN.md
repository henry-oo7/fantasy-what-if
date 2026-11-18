# Fantasy What-If SaaS Production Migration Plan

## Document Status

- **Status:** Draft
- **Source release:** v1.0.1
- **Source commit:** `1dbb5c4`
- **Source migration head:** `5e32a7bc419d`
- **Target:** First multi-tenant League Workspace
- **Migration strategy:** Additive, staged, validated, and forward-recoverable
- **Public registration during migration:** Disabled

## Purpose

This document defines how the existing Fantasy What-If v1.0.1 production installation will become the first League Workspace without losing canonical fantasy data, provider credentials, analytics, account security, or synchronization history.

The migration must not enable additional workspaces until the original production workspace has been migrated, validated, synchronized, backed up, and smoke tested.

## Governing Decisions

This plan follows:

- ADR 0004: League Workspace multi-tenancy
- ADR 0005: SaaS identity and session policy
- ADR 0006: Background jobs and resumable onboarding
- ADR 0007: Provider identity and credential strategy
- ADR 0008: Homelab SaaS deployment and recovery
- `SAAS_TARGET_ARCHITECTURE.md`
- `SAAS_MIGRATION_ROADMAP.md`

## Migration Goals

The migration must preserve:

- Existing installation ID
- Administrator identity
- Shared spectator identity
- Password hashes
- Provider connection
- Encrypted ESPN credentials
- Canonical league
- Imported seasons
- Franchises
- Teams
- Matchups
- Player-week scores
- Lineup-slot definitions
- Scoring rules
- Schedule rules
- Playoff settings
- Administrator tiebreaker fallback
- Persisted analytics
- Synchronization runs
- Active league selection
- Active season selection
- Safe audit history that exists at migration time

The migration must establish:

- Explicit user-to-workspace ownership
- One administrator per workspace
- One shared spectator per workspace
- A globally unique workspace slug
- A globally unique normalized administrator email
- One exclusive ESPN provider league claim
- Workspace-aware authentication context
- Workspace-scoped provider resolution
- Workspace-scoped synchronization
- Tenant-aware test fixtures
- A safe foundation for background jobs and registration

## Non-Goals

The first production migration will not:

- Enable public registration
- Add Sleeper
- Add Yahoo
- Rename the physical `installations` table
- Remove the existing `username` column
- Introduce individual spectator accounts
- Introduce multiple administrators
- Enable paid subscriptions
- Add native-app authentication
- Move production to the cloud
- Delete compatibility fields
- Perform tenant deletion

## Safety Principles

### Backup Before Mutation

No production tenancy migration may run until:

1. A current PostgreSQL backup succeeds.
2. The backup is copied off-host.
3. Backup integrity verification succeeds.
4. Required provider credential encryption keys are recoverable.
5. The current production version and migration revision are recorded.

### Rehearse Before Production

Every schema migration must first run against a restored production-like v1.0.1 database.

The rehearsal must verify:

- Pre-migration audit
- Migration duration
- Lock duration
- Post-migration validation
- Credential decryption
- Administrator login
- Spectator login
- Synchronization
- Analytics
- Recovery procedure

### Add Before Removing

The migration sequence is:

1. Add nullable structures.
2. Backfill existing data.
3. Validate the backfill.
4. Add indexes and constraints.
5. Switch application reads and writes.
6. Preserve compatibility behavior.
7. Remove obsolete behavior in later releases.

### Fail Closed

If ownership or credential invariants cannot be proven:

- Stop the migration.
- Do not enable public registration.
- Do not guess the intended workspace.
- Do not silently delete inconsistent data.
- Restore or forward-repair according to the documented failure point.

## Required Production Facts

Before implementation, record:

```text
Production application version
Production backend image digest
Production frontend image digest
Current Alembic revision
Installation ID
Administrator user ID
Spectator user ID
Provider connection ID
Canonical league ID
Active season ID
ESPN provider league ID
Credential-key version
Imported season count
Latest successful synchronization-run ID
PostgreSQL database size
Current backup timestamp
```

Secret values must not be printed.

## Pre-Migration Audit

A read-only audit command must validate the following.

### Installation

- Exactly one installation exists.
- Installation status is valid.
- Active league is set.
- Active season is set.
- Setup completion time exists.

### Users

- Exactly one administrator exists.
- No more than one spectator exists.
- Administrator email exists.
- Administrator email is syntactically valid.
- Administrator password hash is present.
- Spectator password hash is present when the spectator exists.
- No unexpected roles exist.

### Sessions

- Every session references an existing user.
- Active sessions reference active users.
- No session token is exposed by the audit.
- Existing sessions can be revoked safely if ownership backfill is uncertain.

### Provider Connection

- Exactly one intended active provider connection exists.
- Provider is ESPN.
- Connection belongs to the existing installation.
- Credential-key version is known.
- Encrypted credentials are present.
- Credentials can be decrypted without printing their values.
- The connection matches the active season.

### League and Season

- The active league belongs to the installation.
- The league has a valid ESPN provider league ID.
- Every season belongs to the league.
- Every season year is unique within the league.
- The active season belongs to the active league.
- Every season's provider connection belongs to the installation.

### Canonical Ownership

- Every team belongs to a valid season.
- Every matchup belongs to a valid season.
- Every matchup team belongs to the same season.
- Every player-week score belongs to the expected season and team.
- Every rule belongs to a valid season.
- Every synchronization run belongs to valid workspace-owned resources.

### Provider Claim

- The normalized ESPN league ID can be derived.
- No duplicate provider identity exists.
- Identity normalization version `1` is applicable.

## Migration Releases

The migration should occur over multiple deployable releases.

## Release A: Ownership Preparation

### Schema Changes

Add to the workspace or existing `installations` table:

```text
slug
normalized_slug
```

Add to `users`:

```text
workspace_id
normalized_email
email_verified_at
```

During the initial migration, `workspace_id` may begin nullable.

### Backfill

- Assign the existing installation ID to every valid existing user.
- Normalize the administrator email.
- Mark the existing administrator email verified only through an explicit migration decision.
- Assign the approved production workspace slug.
- Preserve all existing IDs.

The production workspace slug must be selected before this migration is authored.

### Validation

Verify:

- Every user has the expected workspace.
- Exactly one administrator belongs to the workspace.
- Zero or one spectator belongs to the workspace.
- The normalized administrator email is nonempty.
- The workspace slug is valid and unique.

### Constraints

After validation:

- Make `users.workspace_id` non-null.
- Add foreign key to `installations.id`.
- Add one-administrator partial unique index.
- Add one-spectator partial unique index.
- Add unique normalized workspace-slug constraint.
- Add unique normalized administrator-email constraint.
- Add supporting indexes.

### Application Behavior

Release A may continue using the existing login and setup behavior while writing and validating workspace ownership.

Public registration remains disabled.

## Release B: Workspace-Aware Authentication

### Application Changes

Add authenticated workspace context:

```text
user_id
workspace_id
role
session_id
```

Implement:

- Workspace-aware administrator authorization
- Workspace-aware spectator authorization
- Administrator session replacement
- Concurrent spectator sessions
- Workspace suspension checks
- Safe cross-workspace denial

### Login Compatibility

During Release B:

- Existing administrator username login may remain temporarily available.
- Email login is added and validated.
- Spectator workspace-slug login is added.
- Existing spectator username login remains only as a documented compatibility path if necessary.

Compatibility paths must not bypass workspace ownership.

### Session Policy

If existing sessions cannot be assigned safely to a workspace, revoke all sessions during deployment.

Forced reauthentication is preferable to ambiguous authorization.

## Release C: Provider League Claim

### Schema

Create `provider_league_claims` with:

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

### Backfill

Insert the existing ESPN claim:

```text
provider = espn
normalized_provider_league_id = normalized existing ESPN league ID
original_provider_league_id = existing stored league ID
identity_version = 1
status = active
workspace_id = existing installation ID
```

### Constraint

Enforce active uniqueness by:

```text
provider
normalized_provider_league_id
```

### Validation

Verify:

- Exactly one active claim exists for the workspace.
- The claim matches the canonical league.
- The claim matches the provider connection.
- The claim matches every relevant season.
- The claim prevents a duplicate insertion.
- The same textual identifier remains permissible for another provider.

## Release D: Workspace-Scoped Data Access

Replace global resolution in:

- Setup status
- Provider connection setup
- Provider adapter resolution
- Active season resolution
- Synchronization status
- Historical season import
- Spectator management
- Standings tiebreaker configuration
- Dashboard
- All analytics
- Playoff Predictor

Every protected read must derive or verify workspace ownership.

Public registration remains disabled.

## Release E: Background Jobs

Add:

- Background-job table
- Job repository
- Job status transitions
- Idempotency keys
- Job leases
- Worker process
- Job progress
- Safe errors
- Retry classification

Migrate in order:

1. Administrator synchronization
2. Full synchronization
3. Historical season import
4. Initial workspace synchronization

The existing production workspace must complete queued synchronization successfully before onboarding work begins.

## Release F: Provisional Registration

Add:

- Registration mode
- Provisional workspace lifecycle
- Email-verification tokens
- Password-reset tokens
- Resumable onboarding
- Provider validation
- Atomic claim creation
- Initial-sync job creation
- Spectator setup
- Workspace activation
- Provisional cleanup

Production registration remains:

```text
closed
```

until the private beta gate is passed.

## Migration Commands and Automation

Every production migration release should include commands for:

```text
Pre-migration audit
Database backup
Compose validation
Image pull
Migration execution
Migration log inspection
Database revision verification
Post-migration audit
Health verification
Authentication smoke test
Synchronization smoke test
Analytics smoke test
Rollback or forward-recovery
```

Commands must not print secrets.

## Production Deployment Sequence

For each migration release:

1. Announce or begin the maintenance window.
2. Set registration to `closed`.
3. Disable synchronization job creation when required.
4. Allow or cancel active work according to the release plan.
5. Record the current application and schema versions.
6. Run the pre-migration audit.
7. Create and verify the backup.
8. Copy the backup off-host.
9. Validate the Compose configuration.
10. Pull versioned images.
11. Run the migration container.
12. Inspect migration logs.
13. Verify the expected Alembic head.
14. Start or recreate application services.
15. Verify API readiness.
16. Verify worker readiness when applicable.
17. Verify scheduler readiness.
18. Run the post-migration audit.
19. Test administrator authentication.
20. Test spectator authentication.
21. Run one controlled synchronization.
22. Verify canonical data and analytics.
23. Review logs and metrics.
24. Reenable permitted work.
25. End the maintenance window.

## Post-Migration Validation

Post-migration validation must verify:

### Ownership

- Every user has one workspace.
- Every provider connection belongs to one workspace.
- Every league belongs to one workspace.
- Every season resolves to one workspace.
- Every active job belongs to one workspace.
- Every tenant audit event has the correct workspace when applicable.

### Cardinality

- Exactly one administrator exists for the migrated workspace.
- No more than one spectator exists.
- Only one active administrator session remains after a replacement login.
- Multiple spectator sessions may remain active.

### Provider

- The ESPN claim exists.
- Stored credentials decrypt.
- Provider validation succeeds.
- Incremental synchronization succeeds.
- Future matchup schedules remain correct.
- Imported playoff settings remain present.

### Analytics

Verify representative results for:

- Dashboard
- Current standings
- All-Play
- League Average
- Schedule Swap
- Optimal Lineups
- Playoff Predictor
- Historical season selection

The validation should compare known counts or representative values from before migration.

## Data Comparison

Before migration, capture safe baseline values such as:

```text
League count
Season count
Franchise count
Team count by season
Matchup count by season
Player-week score count by season
Synchronization-run count
Latest successful sync timestamp
Schedule-rule count
Lineup-slot definition count
Scoring-rule count
Persisted analytics row counts
```

After migration, compare those values.

Differences must be explained.

No unexplained loss is acceptable.

## Credential Validation

Credential migration validation must:

1. Load the migrated provider connection.
2. Decrypt the envelope.
3. Verify expected credential-field names.
4. Avoid printing secret values.
5. Construct the ESPN adapter.
6. Validate access.
7. Perform a controlled synchronization.
8. Confirm the envelope remains decryptable afterward.

## Migration Failure Points

### Audit Failure Before Migration

Response:

- Stop.
- Make no schema changes.
- Investigate inconsistent production state.
- Correct the inconsistency through a separate reviewed change.

### Backup Failure

Response:

- Stop.
- Do not run migrations.
- Repair backup or off-host transfer.
- Verify backup integrity before continuing.

### Migration Container Failure

Response:

- Keep upgraded application services stopped.
- Inspect the Alembic transaction state.
- Determine whether PostgreSQL rolled back the migration.
- Use forward repair or restore based on the migration runbook.

### Backfill Validation Failure

Response:

- Do not add non-null or unique constraints.
- Preserve the additive nullable schema.
- Correct data through a reviewed repair.
- Rerun validation.

### Credential Decryption Failure

Response:

- Stop provider-related activation.
- Preserve the encrypted envelope.
- Verify key versions and deployment secrets.
- Do not overwrite credentials.
- Restore missing keys or recapture credentials through a controlled workflow.

### Authentication Failure

Response:

- Keep public registration closed.
- Revoke ambiguous sessions.
- Verify workspace ownership and normalized email.
- Prefer forced reauthentication over bypassing new checks.

### Synchronization Failure

Response:

- Preserve existing canonical data.
- Inspect the safe provider error.
- Verify claim and provider ownership.
- Retry only if the error is classified as retryable.
- Do not disable tenant scoping to make synchronization work.

### Analytics Regression

Response:

- Keep registration closed.
- Compare pre- and post-migration row counts.
- Verify workspace-scoped queries.
- Restore the previous application version if the schema remains backward compatible.
- Use forward repair when schema rollback is unsafe.

## Rollback Model

Rollback depends on the migration stage.

### Before Schema Mutation

No rollback is required.

### Additive Nullable Schema Only

The previous application may usually run against the additive schema if no incompatible behavior was introduced.

Prefer application rollback while retaining unused additive columns.

### After Ownership Constraints

The previous application may not understand workspace ownership but may still function if its global assumptions remain true.

Compatibility must be tested before production deployment.

Do not assume backward compatibility.

### After Background Jobs Replace Synchronous Operations

Rolling back the application may strand pending jobs.

The rollback plan must define:

- Whether the previous version ignores the job table safely
- Whether active jobs must be canceled
- Whether the worker must remain on the newer version
- Whether synchronization should remain disabled

### After Public Registration

Restoring a pre-SaaS backup would lose newly registered workspaces.

After public registration begins, recovery must favor:

- Forward repair
- Point-in-time recovery when available
- Restoring the latest compatible backup
- Reconciling accepted registrations and provider claims

## Forward-Recovery Preference

For additive tenancy migrations, forward recovery is preferred when:

- Canonical data remains intact.
- Ownership fields can be repaired.
- Constraints have not deleted data.
- Provider credentials remain encrypted and available.
- Application images can be corrected quickly.

Database restoration is preferred when:

- Canonical data was destructively altered.
- Ownership cannot be reconstructed safely.
- Credential envelopes were corrupted.
- Migration changes cannot be understood reliably.
- Cross-tenant writes occurred.

## Suspension During Migration

If a workspace is suspended during or after migration:

- Revoke all administrator and spectator sessions.
- Reject new login.
- Block analytics, including read-only access.
- Stop synchronization and imports.
- Preserve workspace data.
- Preserve the provider claim.
- Return only a generic suspension message.

Suspension never releases a provider claim.

## Deletion During Migration

Workspace deletion should remain unavailable until the full deletion workflow is implemented and tested.

The approved deletion policy is:

- Enter `deletion_pending`.
- Revoke all sessions.
- Block workspace activity.
- Retain the provider claim for 30 days.
- Allow verified cancellation during the grace period.
- Delete tenant-owned data through a background job after the grace period.
- Release the active claim only after deletion completes.
- Retain only permitted audit history.
- Allow encrypted backup remnants to expire under the backup-retention policy.

## Public Registration Gate

Public registration must remain closed throughout the migration.

Registration may open only after:

- Two-workspace isolation tests pass.
- Duplicate provider claims are race-safe.
- Administrator email verification works.
- Administrator password recovery works.
- Background synchronization works.
- ESPN is hardened for workspace-owned credentials.
- Off-host backup succeeds.
- Restore testing succeeds.
- Queue and database monitoring are active.
- Incident-response documentation exists.
- Private beta succeeds.

## Required Migration Tests

Automated migration tests must cover:

- Empty database to target head
- Clean integration fixture to target head
- Production-like v1.0.1 database to target head
- Existing administrator without email
- Duplicate administrators
- Duplicate spectators
- Missing provider connection
- Undecryptable provider credentials
- Missing active league
- Missing active season
- Active season owned by the wrong league
- Duplicate provider claim
- Existing valid sessions
- Existing revoked sessions
- Multiple imported seasons
- Existing schedule-rule fallback
- Existing background synchronization history

## Manual Smoke Tests

After each production migration release:

### Administrator

- Sign in.
- Confirm only one session remains active after a second login.
- Open Administration.
- View synchronization status.
- Trigger or inspect synchronization when permitted.
- View historical seasons.
- Verify analytics.

### Spectator

- Sign in using the applicable compatibility or workspace-slug flow.
- Open a second spectator session.
- Confirm both spectator sessions remain active.
- Verify read-only analytics.
- Confirm administrator routes remain unavailable.

### Provider

- Validate ESPN connection.
- Run incremental synchronization.
- Confirm a successful synchronization run.
- Confirm matchup and roster counts.
- Confirm no secrets appear in logs.

## Production Evidence

Each migration release should preserve evidence of:

- Release tag
- Commit
- Image digests
- Workflow ID
- Backup ID
- Backup verification
- Migration logs
- Pre-migration audit
- Post-migration audit
- Health results
- Smoke-test results
- Known limitations
- Recovery decision

Evidence must not contain secrets.

## Completion Criteria

The original v1.0.1 installation is successfully migrated when:

1. The existing installation operates as one League Workspace.
2. Existing administrator access works through the target identity model.
3. Existing spectator access works through the target identity model.
4. Administrator session replacement works.
5. Concurrent spectator sessions work.
6. Every user has explicit workspace ownership.
7. The ESPN provider claim exists.
8. The ESPN connection uses workspace-owned encrypted credentials.
9. Every protected query is workspace scoped.
10. Synchronization is workspace and season scoped.
11. Queued synchronization succeeds.
12. Existing canonical data remains intact.
13. Existing analytics remain consistent.
14. Backups and restoration are proven.
15. Public registration remains controlled until launch gates pass.