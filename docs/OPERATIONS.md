# Operations

All examples assume commands are run from the Fantasy What-If installation directory.

## Standard Compose Command

The release stack uses:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml
```

The examples below show the complete command for clarity.

## Service Status

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  ps --all
```

Expected state:

| Service | Expected state |
|---|---|
| `database` | Healthy |
| `migrate` | Exited with code 0 |
| `backend` | Healthy |
| `frontend` | Healthy |
| `sync-scheduler` | Running |

The `migrate` service is expected to exit after completing migrations.

## Logs

### All Services

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs --tail=200
```

Follow logs continuously:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs --follow
```

### Backend

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs --tail=200 backend
```

### Frontend

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs --tail=200 frontend
```

### PostgreSQL

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs --tail=200 database
```

### Migration Job

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs migrate
```

### Scheduler

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs --tail=200 sync-scheduler
```

## Health Checks

### Local Health Endpoint

```bash
curl http://127.0.0.1:8080/health
```

Expected response:

```json
{"status":"ok"}
```

### Public Health Endpoint

```bash
curl https://fantasy.example.com/health
```

### Setup Status

```bash
curl https://fantasy.example.com/api/setup/status
```

Do not use the setup-status response as a substitute for authenticated administrative checks.

## Backup

Download the backup script if it is not already present:

```bash
mkdir -p scripts

curl -L \
  https://raw.githubusercontent.com/henry-oo7/fantasy-what-if/main/scripts/backup.sh \
  -o scripts/backup.sh

chmod +x scripts/backup.sh
```

Create a timestamped backup:

```bash
scripts/backup.sh
```

The default backup directory is:

```text
backups/
```

Create a named backup:

```bash
scripts/backup.sh backups/before-upgrade.dump
```

The script creates:

- A PostgreSQL custom-format dump
- A SHA-256 checksum file
- File permissions set to `0600`

Example output:

```text
backups/before-upgrade.dump
backups/before-upgrade.dump.sha256
```

Verify the checksum manually:

```bash
cd backups
sha256sum --check before-upgrade.dump.sha256
```

Expected result:

```text
before-upgrade.dump: OK
```

Back up `.env` separately using encrypted storage.

The database dump does not replace the need to preserve:

- `APP_SECRET_KEY`
- `PROVIDER_CREDENTIAL_KEY`
- `POSTGRES_PASSWORD`
- The deployed `APP_VERSION`

## Restore

A restore replaces the entire application database.

Download the restore script if necessary:

```bash
mkdir -p scripts

curl -L \
  https://raw.githubusercontent.com/henry-oo7/fantasy-what-if/main/scripts/restore.sh \
  -o scripts/restore.sh

chmod +x scripts/restore.sh
```

Run:

```bash
scripts/restore.sh \
  --backup backups/before-upgrade.dump \
  --confirm-database-replacement
```

The confirmation flag is mandatory.

The restore script:

1. Verifies the backup exists.
2. Verifies the backup is not empty.
3. Validates the PostgreSQL archive.
4. Validates the SHA-256 checksum when present.
5. Stops the frontend, backend, and scheduler.
6. Keeps PostgreSQL available.
7. Recreates the application database.
8. Restores the selected backup.
9. Runs `alembic upgrade head`.
10. Restarts the backend, scheduler, and frontend.

If restoration fails, the application services remain stopped.

Inspect PostgreSQL logs before retrying:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs --tail=300 database
```

## Upgrade

### 1. Record the Current Version

```bash
grep '^APP_VERSION=' .env
```

### 2. Create a Backup

```bash
scripts/backup.sh backups/before-upgrade.dump
```

Verify:

```bash
cd backups
sha256sum --check before-upgrade.dump.sha256
cd ..
```

### 3. Change the Version

Edit `.env`:

```text
APP_VERSION=1.1.0
```

### 4. Pull the New Images

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  pull
```

### 5. Start the Updated Stack

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  up -d
```

The migration job runs before the backend starts.

### 6. Verify the Upgrade

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  ps --all
```

Check:

```bash
curl https://fantasy.example.com/health
```

Review logs:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs --tail=200
```

Perform a browser smoke test:

1. Sign in as administrator.
2. Confirm the active league and season.
3. Open each analytics page.
4. Confirm stored optimal-lineup results.
5. Review the administration page.
6. Confirm scheduler logs.

## Rollback

Application rollback and database rollback are different operations.

## Application-Only Rollback

Use this when the newer application version remains compatible with the current database.

Set the prior image version in `.env`:

```text
APP_VERSION=1.0.0
```

Pull and restart:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  pull

docker compose \
  --env-file .env \
  --file compose.release.yaml \
  up -d
```

Verify service health.

## Application and Database Rollback

Use this when the newer release applied an incompatible database migration.

1. Set `APP_VERSION` to the prior version.
2. Pull the prior images.
3. Restore the pre-upgrade database backup.
4. Verify service health.

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  pull

scripts/restore.sh \
  --backup backups/before-upgrade.dump \
  --confirm-database-replacement
```

Do not run Alembic downgrade commands directly against production unless release-specific instructions explicitly require it.

## Restart

Restart all running services:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  restart
```

Restart one service:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  restart backend
```

PostgreSQL data remains in the named Docker volume.

## Stop Without Deleting Data

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  down
```

The PostgreSQL volume remains available.

Start again:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  up -d
```

## Destructive Removal

The following command removes containers, the network, and the PostgreSQL volume:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  down --volumes
```

Do not use `--volumes` unless:

- A verified backup exists
- Permanent database deletion is intended
- The installation is being rebuilt from scratch

## Scheduler

Default schedule:

- Wednesday
- 6:00 AM
- `America/Chicago`

Check scheduler startup and next-run calculation:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs sync-scheduler
```

Expected log information includes:

- Configured timezone
- Configured weekday
- Configured time
- Poll interval
- Next scheduled synchronization

## Manual Synchronization

Use the administration page for supported manual synchronization actions.

Do not start a second scheduler container or run overlapping synchronization commands unless the application documentation explicitly permits it.

The synchronization lock prevents overlapping supported synchronization operations, but operational procedures should still avoid unnecessary concurrency.

## Database Volume

List the release volume:

```bash
docker volume ls | grep fantasy
```

Inspect the volume:

```bash
docker volume inspect \
  fantasy-what-if_fantasy_what_if_postgres_data
```

The exact volume name can differ when a custom Compose project name is used.

## Image Information

List running image references:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  images
```

Inspect a backend image:

```bash
docker image inspect \
  ghcr.io/henry-oo7/fantasy-what-if-backend:1.0.0
```

## Common Troubleshooting

### Migration Container Exited Nonzero

Inspect:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs migrate
```

Do not repeatedly recreate the database.

Preserve the PostgreSQL volume and inspect the migration error.

### Backend Is Unhealthy

Inspect:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs backend
```

Check:

- Production settings
- Database connectivity
- Migration success
- `FRONTEND_ORIGIN`
- `TRUSTED_ORIGINS`
- `APP_SECRET_KEY`
- `PROVIDER_CREDENTIAL_KEY`
- PostgreSQL credentials

### Frontend Is Unhealthy

Inspect:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs frontend
```

Confirm that port `8080` is not already in use:

```bash
ss -ltnp | grep ':8080'
```

Change the host port if needed:

```text
APP_PORT=8081
```

### Login Cookies Do Not Persist

Confirm:

```text
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_NAME=__Host-fantasy_session
```

Also confirm:

- The public URL uses HTTPS
- The browser origin exactly matches `FRONTEND_ORIGIN`
- Cloudflare or the reverse proxy sends requests through the frontend
- The browser is not using an old HTTP bookmark

### Requests Report an Invalid Origin

Set both values to the exact browser-visible HTTPS origin:

```text
FRONTEND_ORIGIN=https://fantasy.example.com
TRUSTED_ORIGINS=https://fantasy.example.com
```

Do not include a trailing slash.

### Docker Subnet Conflict

Change:

```text
PRODUCTION_SUBNET=172.31.110.0/24
FRONTEND_PROXY_IP=172.31.110.10
FORWARDED_ALLOW_IPS=172.31.110.10
```

Remove and recreate the application network:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  down

docker compose \
  --env-file .env \
  --file compose.release.yaml \
  up -d
```

Do not add `--volumes`.

### GHCR Image Pull Fails

Confirm the requested tag exists:

```bash
grep '^APP_VERSION=' .env
```

Try:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  pull
```

Public GHCR packages should not require authentication. If a newly published package is private, change the package visibility to public in the GitHub package settings.

### Provider Synchronization Fails

Review backend and scheduler logs:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs --tail=300 backend sync-scheduler
```

Provider responses can fail because of:

- Expired ESPN credentials
- League privacy changes
- Incorrect league ID
- Incorrect season
- Provider availability
- Provider-side format changes

Update provider credentials through the supported administration or setup flow.

## Routine Maintenance Checklist

Monthly:

- Verify backups
- Verify backup checksums
- Review free disk space
- Review container health
- Review scheduler logs
- Install supported application updates
- Install supported Docker host security updates

Before every upgrade:

- Record `APP_VERSION`
- Create a database backup
- Verify the checksum
- Preserve `.env`
- Review release notes
- Confirm available disk space