# ESPN Multi-Workspace Live-Validation Runbook

## Purpose

This runbook validates two authorized ESPN League Workspaces against the live ESPN provider after automated multi-workspace isolation tests have passed.

The live test confirms that:

- each workspace owns a different ESPN league identity
- each workspace uses its own encrypted credential envelope
- synchronization is queued through PostgreSQL-backed background jobs
- the background worker resolves credentials at execution time
- canonical teams, matchups, and synchronization runs remain workspace-scoped
- one workspace cannot read another workspace's job through the API
- provider failures remain isolated and expose only safe errors
- no credential values appear in job payloads or ordinary logs

This runbook supplements automated tests. It does not replace them.

## Authorization Requirements

Use only ESPN leagues that the operator owns or has explicit authorization to test.

Do not:

- search for public `espn_s2` or `SWID` values
- reuse another person's ESPN session credentials
- paste credentials into issues, pull requests, chat, screenshots, or logs
- add credentials to source-controlled files
- place credentials in background-job payloads
- capture raw, unsanitized ESPN payloads in the repository

If a second authorized ESPN league is unavailable, stop after the readiness checks and retain the automated two-workspace validation as the current isolation evidence.

## Preconditions

Before starting, confirm:

- the application is running the intended commit
- database migrations are at the current Alembic head
- the API, worker, scheduler, frontend, and PostgreSQL services are healthy
- exactly one background worker is being used for this controlled test
- both workspaces have distinct provider league claims
- both workspaces have active seasons
- both provider connections contain encrypted stored credentials
- the provider credential key is available to the API and worker
- a verified database backup exists
- the operator can sign in as the administrator of each workspace
- production registration remains closed

Do not use shared ESPN environment fallback as evidence that SaaS credential isolation works. Each tested workspace must resolve workspace-owned stored credentials.

## Environment Selection

Prefer the development environment for the first live test.

Development and production must remain separate. Do not point development processes at the production PostgreSQL database.

Record:

| Field | Value |
| --- | --- |
| Environment | |
| Git commit | |
| Application version | |
| Test date and UTC time | |
| Operator | |
| First workspace ID | |
| First league ID | |
| First season ID | |
| Second workspace ID | |
| Second league ID | |
| Second season ID | |
| ESPN library version | |

Do not record cookies, credential envelopes, decrypted credential fields, or credential-key material.

## 1. Verify Repository and Migration State

From the repository root:

```bash
git status --short --branch
git log -1 --oneline

cd backend
uv run alembic heads
uv run alembic current
uv run alembic check
```

Expected result:

- the intended branch or release commit is checked out
- the worktree is clean
- `alembic heads` reports one head
- `alembic current` matches that head
- `alembic check` reports no new upgrade operations

Return to the repository root before continuing.

## 2. Create a Verified Backup

For the supported production Compose deployment:

```bash
scripts/backup.sh backups/before-espn-multi-workspace-smoke.dump
```

Verify the archive and checksum according to `docs/OPERATIONS.md`.

Do not continue against production unless the backup completes successfully.

For development, use the established development backup procedure or a disposable database restored from an approved baseline.

## 3. Verify Process Health

For the release Compose stack:

```bash
docker compose \
  --env-file .env \
  -f compose.release.yaml \
  ps
```

Expected services include:

- `database`
- `migrate`, completed successfully
- `backend`
- `backend-worker`
- `sync-scheduler`
- `frontend`

Check the local health endpoint:

```bash
curl --fail --silent --show-error \
  http://127.0.0.1:8080/health
```

Inspect worker and scheduler startup logs:

```bash
docker compose \
  --env-file .env \
  -f compose.release.yaml \
  logs \
  --tail=200 \
  backend-worker \
  sync-scheduler
```

Do not continue if:

- the worker is disabled
- migrations failed
- the worker cannot connect to PostgreSQL
- the worker cannot load the provider credential key
- repeated lease recovery or database errors are present

## 4. Verify Each Workspace Provider Read-Only

Run provider verification separately for each workspace.

Release Compose example for the first workspace:

```bash
docker compose \
  --env-file .env \
  -f compose.release.yaml \
  exec \
  backend \
  python -m app.scripts.verify_provider \
  --workspace-id FIRST_WORKSPACE_ID
```

Release Compose example for the second workspace:

```bash
docker compose \
  --env-file .env \
  -f compose.release.yaml \
  exec \
  backend \
  python -m app.scripts.verify_provider \
  --workspace-id SECOND_WORKSPACE_ID
```

Development examples from `backend/`:

```bash
uv run python -m app.scripts.verify_provider \
  --workspace-id FIRST_WORKSPACE_ID
```

```bash
uv run python -m app.scripts.verify_provider \
  --workspace-id SECOND_WORKSPACE_ID
```

For both workspaces, confirm:

- provider is `espn`
- credential source indicates stored workspace credentials
- the internal season ID belongs to the expected workspace
- the returned provider league ID is the authorized league
- the two provider league IDs are different
- provider validation succeeds
- no database rows are written
- no credential values are displayed

Stop if either workspace resolves `environment_fallback`. That result does not prove tenant-specific credential isolation.

## 5. Record the Pre-Synchronization Baseline

Using a protected PostgreSQL operator session, record only non-secret metadata.

The query must not select `encrypted_credentials`.

Replace `FIRST_WORKSPACE_ID` and `SECOND_WORKSPACE_ID` with numeric internal workspace IDs before execution.

```sql
SELECT
    i.id AS workspace_id,
    i.active_league_id,
    i.active_season_id,
    l.provider,
    l.provider_league_id,
    s.provider_season_id,
    s.provider_connection_id,
    pc.credential_version,
    pc.encrypted_credentials IS NOT NULL AS has_encrypted_credentials
FROM installations AS i
JOIN leagues AS l
  ON l.id = i.active_league_id
 AND l.installation_id = i.id
JOIN seasons AS s
  ON s.id = i.active_season_id
 AND s.league_id = l.id
JOIN provider_connections AS pc
  ON pc.id = s.provider_connection_id
 AND pc.installation_id = i.id
WHERE i.id IN (
    FIRST_WORKSPACE_ID,
    SECOND_WORKSPACE_ID
)
ORDER BY i.id;
```

Confirm:

- both workspaces are returned
- league and season ownership joins succeed
- provider connection ownership joins succeed
- provider league IDs differ
- provider connection IDs differ
- both rows report encrypted credentials
- credential versions are recognized by the configured vault

Verify exclusive claims:

```sql
SELECT
    workspace_id,
    league_id,
    provider,
    normalized_provider_league_id,
    original_provider_league_id
FROM provider_league_claims
WHERE workspace_id IN (
    FIRST_WORKSPACE_ID,
    SECOND_WORKSPACE_ID
)
ORDER BY workspace_id;
```

Confirm that each workspace has exactly one expected claim and the normalized ESPN league identities differ.

## 6. Enqueue the First Workspace Synchronization

Sign in through the application as the first workspace administrator.

Use the administration page to request an incremental synchronization.

The supported API operation is:

```text
POST /api/admin/sync
```

with:

```json
{
  "full_sync": false
}
```

The browser session supplies the required authentication and CSRF protections.

Record the returned:

- job ID
- initial status
- `existing` value

Expected response:

- HTTP `202 Accepted`
- status is `pending` or another active status
- `existing` is `false`, unless an equivalent active job already exists

Do not create background-job rows directly with SQL.

## 7. Enqueue the Second Workspace Synchronization

Sign out completely, then sign in as the second workspace administrator.

Request the same incremental synchronization from the administration page.

Record the second job ID and initial status.

Confirm:

- the second job ID differs from the first job ID
- the second job belongs to the second workspace
- no credential value appears in either job payload
- the two jobs can be processed by the same worker

## 8. Observe Worker Execution

Inspect worker logs without raising the application log level beyond what is required:

```bash
docker compose \
  --env-file .env \
  -f compose.release.yaml \
  logs \
  --tail=300 \
  backend-worker
```

Confirm:

- both jobs are claimed
- each job reaches a terminal state
- no raw ESPN cookie, `SWID`, encrypted credential envelope, worker stack trace, or provider authorization value appears
- one job does not block or overwrite the other workspace
- lease and heartbeat processing remain healthy

Do not copy unrestricted production logs into the repository.

If diagnostic output must be retained, redact it and verify that it contains no secret, personal, or infrastructure-sensitive values.

## 9. Verify Job Ownership and Terminal State

Use a protected PostgreSQL operator session.

Replace `FIRST_JOB_ID` and `SECOND_JOB_ID` with the numeric job IDs returned by the administrator API.

```sql
SELECT
    id,
    workspace_id,
    job_type,
    status,
    payload_version,
    payload,
    attempt_count,
    maximum_attempts,
    safe_error_code,
    safe_error_message,
    retryable,
    started_at,
    completed_at,
    failed_at
FROM background_jobs
WHERE id IN (
    FIRST_JOB_ID,
    SECOND_JOB_ID
)
ORDER BY id;
```

Confirm:

- each job has the correct workspace ID
- both use synchronization payload version `1`
- payloads contain only non-secret synchronization metadata
- payloads do not contain credential or authorization fields
- successful jobs reach `succeeded`
- failed jobs contain only normalized safe errors
- worker identity and lease metadata are not exposed through the administrator API

Expected payload shape:

```json
{
  "season_id": 1,
  "full_sync": false,
  "trigger": "administrator"
}
```

The actual season ID will differ by workspace.

## 10. Verify Workspace-Scoped API Reads

While signed in as the first workspace administrator, open:

```text
GET /api/admin/jobs/FIRST_JOB_ID
```

Confirm the first job is visible.

Attempt to open:

```text
GET /api/admin/jobs/SECOND_JOB_ID
```

Confirm the other workspace's job produces the same safe absence behavior as an unknown job.

Repeat the inverse test while signed in as the second workspace administrator.

Do not expect or record another workspace's:

- job status
- progress
- safe error
- IDs beyond those already known to the authorized operator
- worker identity
- provider connection metadata

## 11. Verify Canonical Isolation

Verify team ownership through each season and league:

```sql
SELECT
    i.id AS workspace_id,
    l.id AS league_id,
    s.id AS season_id,
    t.provider_team_id,
    t.name
FROM installations AS i
JOIN leagues AS l
  ON l.installation_id = i.id
JOIN seasons AS s
  ON s.league_id = l.id
JOIN teams AS t
  ON t.season_id = s.id
WHERE i.id IN (
    FIRST_WORKSPACE_ID,
    SECOND_WORKSPACE_ID
)
ORDER BY
    i.id,
    t.provider_team_id;
```

Verify matchup ownership:

```sql
SELECT
    i.id AS workspace_id,
    s.id AS season_id,
    m.week,
    m.home_team_id,
    m.away_team_id
FROM installations AS i
JOIN leagues AS l
  ON l.installation_id = i.id
JOIN seasons AS s
  ON s.league_id = l.id
JOIN matchups AS m
  ON m.season_id = s.id
WHERE i.id IN (
    FIRST_WORKSPACE_ID,
    SECOND_WORKSPACE_ID
)
ORDER BY
    i.id,
    m.week,
    m.id;
```

Confirm:

- each workspace has only its expected ESPN teams
- no team from one league appears in the other season
- matchup team references remain within the owning season
- synchronization does not change the other workspace's active league or active season

## 12. Verify Synchronization Runs

```sql
SELECT
    sr.id,
    l.installation_id AS workspace_id,
    sr.season_id,
    sr.provider_connection_id,
    sr.provider,
    sr.status,
    sr.is_full_sync,
    sr.teams_upserted,
    sr.matchups_upserted,
    sr.player_week_scores_upserted,
    sr.safe_error_message,
    sr.started_at,
    sr.completed_at
FROM sync_runs AS sr
JOIN seasons AS s
  ON s.id = sr.season_id
JOIN leagues AS l
  ON l.id = s.league_id
WHERE l.installation_id IN (
    FIRST_WORKSPACE_ID,
    SECOND_WORKSPACE_ID
)
ORDER BY sr.id DESC;
```

Confirm that the tested runs:

- reference the expected season
- reference that season's workspace-owned provider connection
- report provider `espn`
- have independent statuses and counts
- do not contain credential values in provider request metadata or safe errors

## 13. Optional Controlled Failure Isolation

Perform this step only in development and only when credential replacement or another reversible controlled credential operation is available.

Do not directly corrupt production ciphertext.

The intended test is:

1. Preserve a valid first-workspace credential envelope.
2. Introduce invalid authorized test credentials for the second workspace through the supported credential workflow.
3. Enqueue synchronization for both workspaces.
4. Confirm the first job succeeds.
5. Confirm the second job fails with:
   - safe error code `authentication`
   - safe message `The fantasy provider credentials were rejected.`
   - `retryable = false`
6. Confirm the first workspace's canonical data and synchronization run remain committed.
7. Restore the second workspace's valid credentials through the supported workflow.
8. Revalidate the second provider connection.

Skip this test until supported credential replacement exists if executing it would require unsafe direct database manipulation.

## 14. Analytics Smoke Test

For each workspace, verify through the browser:

- dashboard loads
- standings correspond to the expected league
- current matchup data belongs to the expected league
- all-play analytics load
- league-average analytics load
- optimal-lineup data loads when supported
- Playoff Predictor uses the workspace's own season
- no team name or matchup from the other workspace appears

Record only sanitized result summaries.

## 15. Success Criteria

The live smoke test passes only if:

- both authorized ESPN leagues validate using stored workspace credentials
- both claims are exclusive and distinct
- two workspace-owned jobs are enqueued
- the worker executes both jobs
- each job can be read only by its owning workspace
- each synchronization run references the correct provider connection
- canonical teams and matchups remain isolated
- no credential fields appear in job payloads
- no credential values appear in API responses or inspected logs
- one workspace's failure does not mutate or roll back another workspace's success
- analytics display only the signed-in workspace's data

## 16. Stop Conditions

Stop immediately if:

- a workspace resolves shared environment credentials
- provider league identities unexpectedly match
- a provider connection belongs to another workspace
- a job payload contains credential material
- one workspace can retrieve another workspace's job
- canonical rows cross workspace boundaries
- logs expose credential or authorization values
- a worker lease repeatedly expires during ordinary synchronization
- synchronization changes another workspace's active league or season
- the operator cannot prove authorization for either ESPN league

Preserve the database and logs for protected investigation, but do not publish sensitive evidence.

## 17. Evidence Record

Record a sanitized summary:

| Check | First workspace | Second workspace |
| --- | --- | --- |
| Stored credential source | | |
| Provider league ID verified | | |
| Exclusive claim verified | | |
| Job ID | | |
| Job terminal status | | |
| Attempt count | | |
| Sync-run ID | | |
| Provider connection ID matched | | |
| Team isolation verified | | |
| Matchup isolation verified | | |
| Cross-workspace job read denied | | |
| Analytics smoke test passed | | |
| Secrets absent from inspected logs | | |

Do not include:

- ESPN cookies
- `SWID`
- encrypted credential envelopes
- provider encryption keys
- session cookies
- CSRF values
- database passwords
- raw provider payloads
- worker identity values

## 18. Cleanup

After a development smoke test:

- restore any deliberately invalid test credential through the supported workflow
- cancel or resolve only test-created nonterminal jobs
- retain valid canonical data if the test was intended to refresh the development leagues
- remove temporary sanitized local evidence that is no longer needed
- confirm the worker and scheduler remain healthy
- confirm the repository worktree remains clean

Do not delete provider claims or canonical data merely to make a failed test appear clean.

## Deferred Live Test

If a second authorized ESPN league is unavailable:

- do not obtain random credentials
- do not use an unapproved league
- retain the automated multi-workspace ESPN validation as the current evidence
- mark the live two-workspace smoke test as blocked by test-league availability
- continue with credential hardening, sanitized fixtures, provider contract tests, concurrency controls, and provider-outage behavior
