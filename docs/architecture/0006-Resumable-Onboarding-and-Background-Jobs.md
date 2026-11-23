# 0006: Resumable Onboarding and Background Synchronization Jobs

## Status

Proposed

## Date

2026-11-18

## Context

Fantasy What-If v1.0.1 performs setup finalization and provider synchronization through synchronous HTTP requests.

The existing design includes recovery behavior for long-running requests:

- Nginx API timeouts are increased to 300 seconds.
- The frontend treats selected `502`, `504`, and network failures as ambiguous.
- The frontend polls persisted setup or synchronization state.
- Synchronization runs preserve success or failure status.
- Initial setup can recover when the request connection ends after the backend continues processing.

This design is effective for one self-hosted installation, but the SaaS version will support multiple independent League Workspaces.

Public SaaS onboarding introduces several additional requirements:

- Registration must be resumable.
- Email verification may occur in a different browser session.
- Provider validation may involve slow external services.
- League claiming must be atomic.
- Initial synchronization may take several minutes.
- Synchronization must not occupy an API worker for its full duration.
- One workspace's slow provider must not block unrelated workspaces.
- The scheduler must distribute synchronization work across providers and workspaces.
- Failed work must be observable and safely retryable.
- Duplicate league claims must not create partial tenant state.
- Deployment restarts must not lose accepted work.
- Background work must remain suitable for the initial homelab deployment.

Keeping an HTTP request open for provider synchronization would couple browser connectivity, reverse-proxy timeouts, API-worker capacity, and provider latency.

Keeping one PostgreSQL transaction open across provider network calls would increase lock duration and risk transaction failure.

A background job system is therefore required before public SaaS registration.

## Decision

Fantasy What-If will use persisted PostgreSQL-backed background jobs within the existing modular monolith.

The initial SaaS deployment will run separate process roles from the same backend codebase and container image:

```text
api
worker
scheduler
migration
```

The initial job system will use PostgreSQL as the authoritative queue and job-state store.

The first SaaS release will not introduce Redis, RabbitMQ, Kafka, Celery, Kubernetes, or a separate microservice solely for job execution.

This choice is appropriate for the initial target of approximately 50 to 100 registered users and preserves operational simplicity on the homelab.

## Goals

The background job design must provide:

- Durable accepted work
- Resumable onboarding
- Workspace isolation
- Per-season synchronization serialization
- Safe retries
- Persisted progress
- Persisted failure information
- Deployment-restart recovery
- Provider concurrency controls
- Scheduler jitter
- Operator visibility
- Idempotent execution
- Safe cancellation boundaries
- Compatibility with future cloud infrastructure

## Non-Goals

The first implementation does not require:

- Exactly-once delivery
- Distributed workflow orchestration
- Arbitrary user-defined jobs
- Cross-region workers
- Real-time event streaming
- Kafka-compatible event logs
- Redis-backed queues
- A general-purpose task framework
- Automatic horizontal scaling
- Workflow visualizations
- Microservice decomposition

The system will use at-least-once job delivery with idempotent handlers.

## Process Roles

### API Process

The API process is responsible for:

- Registration requests
- Authentication
- Email-verification requests
- Provider-validation requests
- Atomic league claims
- Job creation
- Job-status responses
- Authorization
- Analytics reads
- Administrator actions

The API process must not perform long provider synchronization inline after a job has been accepted.

### Worker Process

The worker process is responsible for:

- Claiming available jobs
- Executing provider operations
- Updating job progress
- Creating or updating synchronization runs
- Persisting canonical data
- Recording safe failures
- Retrying eligible failures
- Activating workspaces when onboarding completes

Multiple worker processes may be added later.

### Scheduler Process

The scheduler process is responsible for:

- Determining which workspace seasons require synchronization
- Creating scheduled synchronization jobs
- Applying schedule jitter
- Avoiding duplicate pending jobs
- Enforcing provider scheduling policies
- Creating maintenance jobs
- Recovering stale job leases when safe

The scheduler does not perform provider synchronization directly.

### Migration Process

The migration process remains responsible for:

- Applying Alembic migrations
- Exiting successfully before application processes become ready
- Preventing API or worker startup against an unsupported schema

## PostgreSQL Job Model

The target job model is represented conceptually as:

```text
BackgroundJob
├── id
├── workspace_id
├── job_type
├── status
├── priority
├── idempotency_key
├── payload
├── progress
├── attempt_count
├── maximum_attempts
├── available_at
├── claimed_at
├── lease_expires_at
├── claimed_by
├── started_at
├── completed_at
├── failed_at
├── canceled_at
├── safe_error_code
├── safe_error_message
├── created_at
└── updated_at
```

The exact names may change during implementation, but the behavioral contract must remain.

## Job Types

Initial background job types include:

```text
initial_workspace_sync
incremental_season_sync
full_season_sync
historical_season_import
provider_credential_validation
provider_token_refresh
provisional_workspace_cleanup
expired_session_cleanup
expired_token_cleanup
workspace_deletion
```

Not every job type must be implemented in the first migration.

The first required executable jobs are:

```text
initial_workspace_sync
incremental_season_sync
full_season_sync
historical_season_import
```

Yahoo token refresh becomes required when Yahoo integration is implemented.

## Job Status

Initial job statuses are:

```text
pending
running
succeeded
failed
retry_scheduled
canceled
```

A job may transition only through documented state changes.

Allowed transitions include:

```text
pending
    -> running
    -> succeeded

pending
    -> running
    -> failed

pending
    -> running
    -> retry_scheduled
    -> running

pending
    -> canceled

retry_scheduled
    -> canceled
```

A succeeded job is terminal.

A failed job is terminal unless an explicit operator or application action creates a new job.

A canceled job is terminal.

The job system must reject invalid status transitions.

## Workspace Ownership

Every tenant-owned job must have a non-null `workspace_id`.

A worker must verify workspace ownership before executing the job.

Job payloads may contain internal resource identifiers such as:

```text
season_id
league_id
provider_connection_id
sync_run_id
```

These identifiers do not establish authorization.

Before execution, the job handler must verify that every referenced resource belongs to the job's workspace.

A malformed or cross-workspace job must fail safely without accessing provider credentials or modifying canonical data.

## Job Payloads

Job payloads use versioned JSON.

Conceptual example:

```json
{
  "version": 1,
  "season_id": 42,
  "full_sync": false,
  "trigger": "administrator"
}
```

Payloads must contain only data needed to execute the job.

Payloads must not contain:

- Plaintext provider credentials
- Encrypted provider credentials
- Passwords
- Session tokens
- OAuth access tokens
- OAuth refresh tokens
- Authorization headers
- Email-verification tokens
- Password-reset tokens

Workers resolve encrypted credentials from the workspace-owned provider connection at execution time.

## Idempotency

Background delivery is at least once.

Every job handler must be idempotent or protected by an idempotency boundary.

The job model must support an optional idempotency key.

Representative keys include:

```text
initial-sync:{workspace_id}:{season_id}
incremental-sync:{season_id}:{provider_revision}
full-sync:{season_id}:{requested_at_bucket}
historical-import:{workspace_id}:{league_id}:{year}
cleanup-provisional:{workspace_id}
```

The database should prevent multiple active jobs with the same idempotency key when duplication would be unsafe.

The exact active-status partial unique index will be finalized during schema design.

Repeated execution must not create duplicate:

- Provider claims
- Leagues
- Seasons
- Teams
- Matchups
- Player-week scores
- Synchronization runs representing the same attempt
- Workspace activations

## Job Claiming

Workers will claim jobs using PostgreSQL row locking.

Conceptual query pattern:

```sql
SELECT id
FROM background_jobs
WHERE status IN ('pending', 'retry_scheduled')
  AND available_at <= NOW()
ORDER BY priority DESC, available_at, id
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

The worker then:

1. Marks the job `running`.
2. Records the worker identity.
3. Sets the claim and lease timestamps.
4. Commits the claim.
5. Executes the job outside the claim transaction.
6. Updates progress and terminal status in short transactions.

`FOR UPDATE SKIP LOCKED` allows multiple workers to claim different jobs without serializing the entire queue.

## Job Leases

Running jobs must have a lease expiration.

The lease allows recovery when a worker process exits unexpectedly.

A worker should renew the lease while executing long work.

A job with an expired lease is not automatically assumed safe to rerun.

Lease recovery must inspect:

- Job type
- Synchronization-run status
- Provider-operation idempotency
- Workspace state
- Season lock state
- Last progress timestamp
- Existing canonical transaction state

Because canonical persistence is transactional, a worker crash during an uncommitted persistence transaction should leave no partial canonical update.

A worker crash after a successful commit but before marking the job succeeded must be recoverable through idempotent reconciliation.

## Synchronization Runs and Jobs

`BackgroundJob` and `SyncRun` serve different purposes.

### Background Job

The background job represents queue execution:

- When work is available
- Which worker claimed the work
- Attempt count
- Retry timing
- Execution status
- Safe job failure

### Synchronization Run

The synchronization run represents the provider synchronization domain event:

- Provider
- Workspace and season
- Requested synchronization mode
- Requested weeks
- Imported counts
- Provider request metadata
- Canonical synchronization result
- Safe synchronization error

A synchronization job should reference or create a synchronization run.

The frontend may use a combined response, but the two concepts should remain separate.

## Synchronization Serialization

Only one synchronization may execute for the same season at a time.

Workers must acquire a season-scoped PostgreSQL advisory transaction lock before canonical persistence or other unsafe overlapping work.

The lock key must be derived from stable internal resource identifiers.

Conceptual scope:

```text
synchronization
season_id
```

The lock must not serialize unrelated workspaces or seasons.

A separate workspace-and-league lock may protect historical season import.

Provider-level concurrency limits are separate from season-level serialization.

## Provider Concurrency

Each provider may require different concurrency controls.

Initial configurable limits should support:

```text
ESPN:
Low concurrency because the integration relies on an unofficial service.

Sleeper:
Moderate concurrency, subject to verified public API behavior.

Yahoo:
OAuth-aware concurrency and rate limits based on verified API documentation.
```

Exact numerical limits must not be invented.

Before public launch, each implemented provider must document:

- Verified rate-limit behavior
- Recommended concurrent requests
- Retryable responses
- Required backoff
- Authentication-failure handling

Until verified, the initial worker should use conservative provider concurrency.

For the first homelab deployment, one synchronization job per provider at a time is an acceptable safe starting point.

The design must allow provider-specific concurrency to increase without changing domain handlers.

## Retry Policy

Not every failure is retryable.

### Retryable Failures

Potential retry candidates include:

- Provider timeout
- Temporary DNS failure
- Temporary connection failure
- HTTP `429`
- Selected HTTP `500`, `502`, `503`, and `504` responses
- Worker shutdown before execution completes
- Temporary database connectivity loss before canonical commit

### Non-Retryable Failures

Non-retryable failures include:

- Invalid provider credentials
- Revoked provider authorization
- Provider league not found
- Workspace ownership mismatch
- Duplicate provider league claim
- Unsupported provider capability
- Invalid canonical data
- Invalid job payload
- Suspended workspace
- Deleted workspace
- Failed schema validation

The provider error hierarchy remains the source for retry classification.

## Retry Timing

Retryable jobs use exponential backoff with jitter.

Conceptual sequence:

```text
Attempt 1:
Immediate

Attempt 2:
Approximately 30 seconds

Attempt 3:
Approximately 2 minutes

Attempt 4:
Approximately 10 minutes

Attempt 5:
Approximately 30 minutes
```

The exact function must be configurable.

Jitter must prevent many jobs from retrying simultaneously after a provider outage.

The first implementation should default to no more than five attempts for automatic synchronization unless provider-specific behavior justifies a different limit.

Initial onboarding failures requiring corrected credentials must not repeatedly retry without user action.

## Progress Reporting

Jobs must expose safe persisted progress.

Conceptual progress payload:

```json
{
  "stage": "persisting_matchups",
  "completed_units": 36,
  "total_units": 72,
  "message": "Importing league matchups."
}
```

Progress must not include:

- Provider secrets
- Raw private responses
- Internal stack traces
- Another workspace's identifiers
- Personally sensitive provider-account information

Progress is advisory.

The terminal job status and canonical transaction outcome are authoritative.

## Initial Onboarding State Machine

The onboarding state machine is:

```text
registration_pending
    -> email_verification_pending
    -> provider_setup_pending
    -> league_claim_pending
    -> initial_sync_pending
    -> initial_sync_running
    -> spectator_setup_pending
    -> active
```

Recoverable failure states include:

```text
provider_validation_failed
league_claim_conflict
initial_sync_failed
```

The exact storage representation may use one enum plus supporting timestamps or separate onboarding records.

## Email Verification and Onboarding

Email verification is not a background provider job.

Email delivery may be queued later, but verification state remains part of registration.

After email verification succeeds:

- The administrator may authenticate into onboarding.
- The workspace remains provisional.
- The administrator may configure the provider.
- The workspace must not expose analytics until initial synchronization succeeds.

## Provider Validation

Provider validation may involve network calls.

For the first SaaS implementation, short validation may remain synchronous if the operation has a strict timeout and does not perform canonical synchronization.

Provider validation must:

- Use a bounded timeout
- Avoid writing canonical league data
- Return safe errors
- Avoid persisting plaintext credentials
- Avoid holding a database transaction during the network call

If provider validation proves too slow for reliable HTTP execution, it may become a background job without changing league-claim semantics.

### Implemented ESPN Validation Step

`POST /api/onboarding/provider/validate` implements provider selection and ESPN league-access validation for verified provisional administrators. The workspace comes only from the authenticated session.

- Allowed from `provider_setup_pending` and `provider_validation_failed`. Every other stage returns `onboarding_stage_invalid`; suspended and other non-provisional workspaces return `workspace_suspended` or `workspace_inactive`.
- The administrator need not be the ESPN league manager, commissioner, or a team owner.
- An anonymous check runs first. `credentials_required` leaves the stage unchanged so the administrator can submit `SWID` and `espn_s2`.
- Success atomically stores the single current provider connection with the validated target, under a workspace row lock, and moves the stage to `league_claim_pending`. It never creates a League, Season, provider league claim, or background job, and never activates the workspace.
- An authentication failure or an unusable target moves the stage to the recoverable `provider_validation_failed`. A transient ESPN outage leaves the stage unchanged. No failure replaces a previously validated target or credentials.
- Attempts are limited per workspace and per normalized request source before any ESPN request.

### Implemented League Claim Step

`GET /api/onboarding/provider/target` returns only the safe validated target: provider, League ID, initial season and access mode. It never returns credentials, ciphertext, key versions or usernames. `POST /api/onboarding/provider/claim` takes no ownership fields and is allowed only at `league_claim_pending`. It runs one transaction:

1. Lock the workspace row and the active provider connection, and require a complete validated target.
2. Insert the claim inside a savepoint. `uq_provider_league_claims_provider_identity` (provider plus normalized League ID, independent of season) is the concurrency authority.
3. Success advances to `initial_sync_pending` and commits. The workspace stays provisional, and no League, Season, synchronization job or spectator access is created.
4. A provider-identity conflict rolls back only the savepoint, deletes the losing provisional workspace in the same transaction (the database cascades users, sessions, account tokens, provider connections, claims, leagues, seasons and jobs), commits, clears authentication cookies, and returns `409 provider_league_already_claimed`.

Repeated confirmation from the winning workspace is serialized by the workspace lock and returns `onboarding_stage_invalid` without creating another claim. Initial synchronization is the next onboarding step, followed by spectator setup and activation.

## Atomic League Claim

The provider league claim remains a short database transaction.

The claim transaction must not include the full provider synchronization.

The target sequence is:

1. Validate provider access outside a long database transaction.
2. Normalize the provider league identity.
3. Open a short transaction.
4. Lock the provisional workspace when required.
5. Insert the exclusive provider league claim.
6. Persist the encrypted provider connection.
7. Persist the minimal canonical league and season identity needed for synchronization.
8. Create the initial synchronization job.
9. Commit.
10. Return accepted onboarding state.

The uniqueness constraint on provider identity is the concurrency authority.

## Duplicate Claim Outcome

If league-claim insertion conflicts:

1. Roll back the claim transaction.
2. Do not create a synchronization job.
3. Do not retain the provider credentials.
4. Do not create canonical league or season records.
5. Revoke the provisional onboarding session.
6. Remove the provisional administrator and workspace through the same transaction or deterministic compensation.
7. Return the safe `409 Conflict` response.

Recommended error code:

```text
provider_league_already_claimed
```

Recommended message:

> This fantasy league is already connected to Fantasy What-If. Contact the league owner and ask for the workspace's spectator account if you need viewing access.

The existing workspace slug must not be disclosed.

## Initial Synchronization

The initial synchronization job must:

1. Verify the provisional workspace.
2. Verify the provider league claim.
3. Verify the workspace-owned provider connection.
4. Decrypt credentials only for provider construction.
5. Fetch canonical provider data.
6. Validate canonical consistency.
7. Persist canonical data transactionally.
8. Persist provider capabilities and rules.
9. Rebuild required analytics.
10. Mark the synchronization run succeeded.
11. Move onboarding to spectator setup.
12. Mark the job succeeded.

The workspace must not become active merely because a job was queued or started.

## Initial Synchronization Failure

If initial synchronization fails:

- The job records a safe failure.
- The synchronization run records a safe failure.
- The workspace remains provisional.
- The provider league claim remains held.
- Canonical transaction changes roll back.
- The administrator can review safe recovery guidance.
- Retry is offered only when safe.
- Credential correction is available for authentication failures.
- Another workspace cannot claim the held provider league.

The claim remains held because provider validation and claim creation succeeded for the provisional owner.

Claim release is governed by cleanup and deletion policy, not automatic synchronization failure.

## Workspace Activation

The workspace becomes active only after:

- Administrator email is verified.
- Provider league claim exists.
- Provider connection is valid.
- Initial synchronization succeeded.
- Canonical league exists.
- Active season exists.
- Required canonical settings exist.
- Shared spectator setup is complete when spectator setup is mandatory.

Activation must be idempotent.

The activation transaction must verify all invariants before updating workspace status.

## Spectator Setup

The initial product flow should require spectator-password configuration before activation.

This preserves the product promise that every active workspace has:

```text
One administrator
One shared spectator
```

If spectator setup is later made optional, ADR 0004 and the SaaS requirements must be updated.

Spectator setup is a short database transaction and does not require a background job.

## Onboarding Status API

The frontend must read persisted onboarding status.

Conceptual response:

```json
{
  "workspace_status": "synchronizing",
  "onboarding_stage": "initial_sync_running",
  "job": {
    "id": 184,
    "status": "running",
    "progress": {
      "stage": "fetching_matchups",
      "message": "Loading league matchups."
    }
  },
  "safe_error": null,
  "next_action": "wait"
}
```

The response must contain only data belonging to the authenticated provisional workspace.

## Job Status API

A job-status route must be workspace-scoped.

Conceptual route:

```text
GET /api/jobs/{job_id}
```

The API must verify:

```text
job.workspace_id == authenticated_context.workspace_id
```

An authenticated user requesting another workspace's job should receive a safe not-found response rather than confirmation that the job exists.

## Frontend Polling

The frontend may poll persisted job or onboarding state.

Initial recommended polling behavior:

```text
First minute:
Every 2 seconds

After first minute:
Every 5 seconds

After five minutes:
Every 10 seconds
```

The frontend should stop polling when:

- The job succeeds
- The job fails
- The job is canceled
- The user leaves the workflow
- The session expires
- The workspace is suspended

The frontend must not treat a temporary polling failure as proof that the job failed.

## Future Server-Sent Updates

Server-Sent Events or WebSockets are not required for the first SaaS release.

The persisted job model allows a future event-delivery mechanism without changing job execution semantics.

Polling is acceptable for the initial 50 to 100 user target.

## Scheduled Synchronization

The scheduler must create workspace-aware synchronization jobs.

The scheduler must:

- Select active workspaces and eligible seasons
- Skip suspended or deletion-pending workspaces
- Skip seasons with active synchronization
- Avoid duplicate active jobs
- Add schedule jitter
- Respect provider concurrency policy
- Record the trigger as scheduled
- Avoid launching every workspace simultaneously

A scheduled job must be indistinguishable from an administrator-triggered job at the canonical synchronization-handler boundary except for trigger metadata and authorization origin.

## Administrator-Triggered Synchronization

An administrator request should:

1. Verify administrator authorization.
2. Resolve the workspace and season.
3. Check for an existing active job.
4. Create a synchronization job or return the existing job.
5. Return `202 Accepted`.
6. Include a safe job-status reference.

Conceptual response:

```json
{
  "job_id": 184,
  "status": "pending",
  "existing": false
}
```

If equivalent work is already pending or running, the API may return:

```json
{
  "job_id": 179,
  "status": "running",
  "existing": true
}
```

Creating duplicate active synchronization jobs for the same season should be avoided.

## Historical Season Import

Historical season import becomes a background job.

The API request must:

1. Verify administrator authorization.
2. Resolve the workspace's claimed league.
3. Validate the requested year.
4. Confirm that the season is not already imported.
5. Create an idempotent historical-import job.
6. Return `202 Accepted`.

The import job must recheck all invariants at execution time.

## Cancellation

Not every job can be canceled safely after execution starts.

### Safe Cancellation

A pending or retry-scheduled job may be canceled before a worker claims the job.

### Cooperative Cancellation

A running job may observe a cancellation request between safe stages.

The worker must not interrupt a canonical persistence transaction in a way that leaves partial committed state.

The initial implementation may support only pending-job cancellation.

Administrator-triggered cancellation is deferred unless a clear user need appears.

Operator cancellation must be available for incident response.

## Worker Shutdown

Workers must handle graceful shutdown.

On shutdown:

1. Stop claiming new jobs.
2. Allow the active safe stage to complete when practical.
3. Update or renew the lease while completing.
4. Exit before the deployment grace period ends.
5. Leave the job recoverable if the worker cannot complete.

A hard termination must rely on lease recovery and idempotency.

## Deployment Behavior

The release stack should include:

```text
database
migrate
backend-api
backend-worker
sync-scheduler
frontend
```

The API, worker, and scheduler may use the same backend image with different commands.

Example conceptual commands:

```text
python -m app.scripts.run_api
python -m app.scripts.run_worker
python -m app.scripts.sync_scheduler
```

Exact entry points will be defined during implementation.

## Homelab Resource Controls

The first homelab deployment must use conservative resource limits.

The design should support configuration for:

- Worker count
- Worker polling interval
- Job-lease duration
- Provider concurrency
- Maximum automatic attempts
- Scheduler batch size
- Database pool size
- Job-history retention
- Progress-update frequency

One worker process is an acceptable initial configuration.

A second worker should be added only after tests show that database and provider concurrency remain safe.

## Queue Backpressure

The queue must remain observable under provider outages or registration bursts.

Backpressure controls include:

- Provider concurrency limits
- Job priority
- Retry delays
- Scheduler batch limits
- Duplicate-job prevention
- Registration throttling
- Operator maintenance mode

Initial priority order should favor:

1. Initial workspace synchronization
2. Administrator-triggered synchronization
3. Historical import
4. Scheduled synchronization
5. Cleanup and maintenance work

Exact priority values remain implementation details.

## Job Retention

Succeeded and failed jobs should remain available long enough for support, debugging, and audit correlation.

Recommended initial retention:

```text
Succeeded jobs:
30 days

Failed jobs:
90 days

Canceled jobs:
30 days
```

Synchronization runs may have a longer retention policy because synchronization history is part of the product and operational record.

Deleting old jobs must not delete canonical data or required audit events.

## Safe Errors

Background jobs store only safe error information suitable for authenticated users.

Conceptual fields:

```text
safe_error_code
safe_error_message
retryable
```

Detailed stack traces belong only in protected operator logs.

Logs must include correlation identifiers such as:

```text
job_id
workspace_id
season_id
sync_run_id
provider
operation
```

Logs must not include secrets.

## Operator Controls

The service operator must be able to:

- View queue depth
- View running jobs
- View failed jobs
- View stale leases
- Retry eligible jobs
- Cancel pending jobs
- Pause synchronization job creation
- Disable registration
- Suspend a workspace
- Inspect safe failure metadata
- Correlate jobs with synchronization runs

The first operator interface may be command-line based.

A public or tenant administrator must not gain access to global queue information.

## Observability

Required job metrics include:

- Pending job count
- Running job count
- Retry-scheduled count
- Failed job count
- Oldest pending-job age
- Job execution duration
- Job wait duration
- Jobs completed by type
- Jobs failed by type
- Provider failure counts
- Lease-expiration count
- Retry count
- Scheduler-created job count

Metrics must avoid tenant-sensitive labels such as email or workspace slug.

Internal numeric workspace IDs may be acceptable in protected logs but should not become unbounded metric labels.

## Failure Isolation

One workspace's failed or slow job must not:

- Prevent other workspaces from logging in
- Block unrelated analytics reads
- Hold a global synchronization lock
- Consume every worker indefinitely
- Reveal its failure to another workspace
- Corrupt another workspace's canonical data

Provider-wide backoff may temporarily delay work for the same provider when rate limiting or outage conditions are detected.

## Database Availability

PostgreSQL is both the canonical data store and initial job queue.

This creates a dependency: jobs cannot be claimed when PostgreSQL is unavailable.

That dependency is acceptable because canonical synchronization cannot commit without PostgreSQL.

Workers must:

- Back off during database failure
- Avoid tight reconnect loops
- Preserve accepted jobs already committed
- Resume processing after database recovery

## Migration from Synchronous Synchronization

The migration should proceed incrementally.

### Phase 1: Introduce Job Persistence

- Add background-job table.
- Add status and transition tests.
- Add idempotency-key support.
- Add workspace ownership.
- Add job-status repository.

### Phase 2: Add Worker Process

- Add job claiming with `SKIP LOCKED`.
- Add leases.
- Add graceful shutdown.
- Add safe failure persistence.
- Execute test-only jobs.

### Phase 3: Move Administrator Synchronization

- Change administrator synchronization to enqueue work.
- Return `202 Accepted`.
- Poll persisted job and synchronization status.
- Remove reliance on one long HTTP response.

### Phase 4: Move Historical Import

- Enqueue historical imports.
- Add idempotent year-based keys.
- Preserve inventory refresh behavior.

### Phase 5: Move Initial Onboarding Synchronization

- Claim league atomically.
- Enqueue initial synchronization.
- Drive onboarding from persisted state.
- Activate only after successful synchronization and spectator setup.

### Phase 6: Replace Legacy Timeout Recovery

- Retain safe network-failure handling for ordinary API calls.
- Remove long-request assumptions from synchronization UX.
- Keep persisted-state recovery as the normal design rather than an exception path.

## Testing Requirements

### Queue Tests

- Pending jobs can be claimed.
- Locked jobs are skipped by another worker.
- Priority and availability ordering are respected.
- Terminal jobs cannot be reclaimed.
- Invalid transitions are rejected.
- Duplicate active idempotency keys are rejected.
- Expired leases are detected.

### Workspace Isolation Tests

- A worker rejects a mismatched workspace and season.
- Workspace A cannot read Workspace B's job.
- Workspace A cannot cancel Workspace B's job.
- A spectator cannot enqueue administrator jobs.
- Global queue counts are not exposed to tenant users.

### Retry Tests

- Retryable provider failures schedule a retry.
- Non-retryable failures become terminal.
- Retry delay increases with attempts.
- Jitter stays within configured bounds.
- Maximum attempts are enforced.
- Credential failures do not loop automatically.

### Onboarding Tests

- Email verification precedes provider claiming.
- Claim success creates exactly one initial-sync job.
- Duplicate claim creates no job.
- Initial-sync success advances onboarding.
- Initial-sync failure preserves resumable state.
- Workspace activation requires all invariants.
- Duplicate activation is idempotent.
- Abandoned provisional registration is cleaned safely.

### Worker Recovery Tests

- Worker termination leaves the job recoverable.
- A committed canonical sync is not duplicated after lease recovery.
- An uncommitted canonical sync rolls back.
- Graceful shutdown stops new claims.
- Multiple workers claim different jobs.

### Scheduler Tests

- Suspended workspaces are skipped.
- Deletion-pending workspaces are skipped.
- Existing active jobs prevent duplicates.
- Scheduling jitter is applied.
- Unrelated workspaces receive independent jobs.
- Provider concurrency policy is respected.

### Migration Tests

- Existing synchronous synchronization results remain valid.
- Existing `SyncRun` data is preserved.
- v1.0.1 production data can enqueue a workspace-scoped synchronization.
- Existing scheduler behavior migrates without global installation resolution.

## Security Consequences

### Positive

- API workers are not held by provider latency.
- Accepted work survives browser disconnects.
- Tenant ownership is explicit on jobs.
- Provider credentials are resolved only at execution time.
- Jobs expose safe progress rather than raw provider data.
- One tenant's failures are isolated.
- Queue operations are auditable.
- The design supports later cloud workers.

### Negative

- PostgreSQL becomes both canonical store and queue.
- Workers and leases introduce more operational states.
- At-least-once delivery requires idempotent handlers.
- Onboarding state becomes more complex.
- Operators need queue observability and recovery tools.
- Job cleanup and retention require maintenance.

## Rejected Alternatives

### Continue Long HTTP Synchronization Requests

Rejected because public SaaS synchronization cannot depend on browser connectivity, reverse-proxy timeouts, or one API worker remaining occupied.

### Hold One Transaction Across Provider Calls

Rejected because external latency would extend database locks and create unsafe failure boundaries.

### Redis and Celery Immediately

Deferred because Redis and Celery add infrastructure, monitoring, deployment, and recovery complexity before the initial workload justifies the additional components.

### RabbitMQ

Deferred because durable broker operation is disproportionate to the initial 50 to 100 user target.

### Kafka

Rejected because the product does not require a distributed event-streaming platform.

### In-Memory Queue

Rejected because jobs would be lost during process restart, deployment, or host failure.

### Cron Calling Synchronization Directly

Rejected because direct cron execution lacks tenant-aware queueing, deduplication, progress, retry state, and operator visibility.

## Cloud Migration

The PostgreSQL-backed job contract must not prevent future migration to:

- AWS SQS
- Amazon RDS for PostgreSQL
- Azure Service Bus
- Azure Database for PostgreSQL
- Google Cloud Tasks
- Google Cloud SQL
- Redis-based queues
- RabbitMQ

Job handlers should depend on application job contracts rather than PostgreSQL SQL details.

The queue repository may be replaced later while retaining:

- Job types
- Payload versions
- Idempotency keys
- Workspace ownership
- Progress semantics
- Retry classification
- Synchronization-run relationships

## Follow-Up Work

After this ADR is accepted:

1. Design the background-job schema.
2. Define valid status transitions.
3. Define worker lease duration and heartbeat behavior.
4. Add job repository contract tests.
5. Add workspace-scoped idempotency keys.
6. Add worker process entry point.
7. Move administrator synchronization to queued execution.
8. Move historical import to queued execution.
9. Replace initial setup finalization with resumable onboarding.
10. Add queue metrics and operator inspection commands.
11. Update production Compose with the worker role.
12. Load-test one-worker and two-worker configurations.

## Requirement Traceability

This decision supports:

- `FR-14`: Resumable onboarding
- `FR-15`: Initial synchronization
- `FR-16`: Workspace activation
- `FR-17`: Workspace-scoped season import
- `PR-7`: Provider rate protection
- `DR-5`: Audit events
- `SYR-1`: Tenant-scoped resolution
- `SYR-2`: Tenant-scoped locks
- `SYR-3`: Background execution
- `SYR-4`: Idempotency
- `SYR-5`: Failure isolation
- `SYR-6`: Scheduling
- `OR-1`: Initial capacity
- `OR-3`: Observability
- `OR-4`: Maintenance mode
- `SER-5`: Secret logging