# Fantasy What-If

Fantasy What-If is a self-hosted fantasy football analytics application. It imports league data through provider adapters and calculates alternative views of team performance.

ESPN Fantasy Football is the supported provider in v1. The provider-neutral application architecture is designed to support additional providers in future releases.

## Analytics

Fantasy What-If currently provides:

- All-play standings
- League-average standings
- Schedule-swap comparisons
- Persisted optimal-lineup analysis
- Matchup-aware weekly optimal-lineup breakdowns
- Actual versus optimal record comparisons
- Players-out, players-in, and retained-starter analysis

## Access Model

Fantasy What-If uses a deliberately small account model:

- One administrator account
- At most one shared spectator account
- No fantasy-team-specific permissions
- No separate invitation or account for every fantasy manager

The administrator controls:

- Provider credentials
- League synchronization
- Season imports
- Spectator access
- Installation and maintenance actions

## Supported Deployments

### Recommended Self-Hosted Installation

The release stack uses prebuilt container images from GitHub Container Registry:

- `ghcr.io/henry-oo7/fantasy-what-if-backend`
- `ghcr.io/henry-oo7/fantasy-what-if-frontend`

Supported container platforms:

- `linux/amd64`
- `linux/arm64`

See [Installation](docs/INSTALLATION.md) for the complete deployment process.

### Contributor Development

Development runs from source with:

- PostgreSQL in Docker
- FastAPI and Uvicorn from the backend virtual environment
- Vite from the frontend source tree
- A separate development database and Docker Compose project

See [Development](docs/DEVELOPMENT.md) and [Contributing](CONTRIBUTING.md).

## Production Architecture

The production release stack contains the following services:

| Service | Purpose |
|---|---|
| `database` | PostgreSQL 18 persistent database |
| `migrate` | One-time Alembic migration job |
| `backend` | FastAPI application and analytics API |
| `frontend` | Unprivileged Nginx static frontend and reverse proxy |
| `sync-scheduler` | Dedicated scheduled synchronization process |

Only the frontend publishes a host port. PostgreSQL and the backend remain on the internal Docker network.

The default host binding is:

```text
127.0.0.1:8080
```

This binding is appropriate when Cloudflare Tunnel, Caddy, Traefik, or another reverse proxy running on the Docker host provides public ingress.

## Quick Start

### Requirements

- Docker Engine 24 or newer
- Docker Compose v2
- OpenSSL
- At least 2 GB of available memory
- At least 5 GB of available disk space
- An HTTPS hostname through Cloudflare Tunnel or another reverse proxy
- ESPN league credentials

### Download the Release Files

```bash
mkdir -p fantasy-what-if/scripts
cd fantasy-what-if

curl -LO https://raw.githubusercontent.com/henry-oo7/fantasy-what-if/main/compose.release.yaml

curl -LO https://raw.githubusercontent.com/henry-oo7/fantasy-what-if/main/.env.production.example

curl -L \
  https://raw.githubusercontent.com/henry-oo7/fantasy-what-if/main/scripts/generate-production-env.sh \
  -o scripts/generate-production-env.sh

chmod +x scripts/generate-production-env.sh
```

### Generate the Production Environment

```bash
scripts/generate-production-env.sh \
  .env.production.example \
  .env
```

The generator creates:

- A random application secret
- A Fernet provider-credential encryption key
- A random PostgreSQL password
- A private `.env` file with permissions set to `0600`

Do not commit, email, or publicly share the generated `.env` file.

### Configure the Public Address

Edit `.env` and replace the example hostname:

```text
APP_VERSION=v1
FRONTEND_ORIGIN=https://fantasy.example.com
TRUSTED_ORIGINS=https://fantasy.example.com
```

Both origin values must:

- Use HTTPS in production
- Exactly match the browser-visible origin
- Omit a trailing slash

### Start the Stack

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

### Verify Service Health

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  ps --all
```

Expected service state:

| Service | Expected state |
|---|---|
| `database` | Healthy |
| `migrate` | Exited with code 0 |
| `backend` | Healthy |
| `frontend` | Healthy |
| `sync-scheduler` | Running |

Check the public health endpoint:

```bash
curl https://fantasy.example.com/health
```

Expected response:

```json
{"status":"ok"}
```

Open the configured HTTPS address and complete the browser setup flow.

See [Installation](docs/INSTALLATION.md) for detailed ingress, network, and troubleshooting instructions.

## First-Run Setup

The browser setup flow will:

1. Create the administrator account.
2. Configure the ESPN provider connection.
3. Validate and import the league.
4. Create the optional shared spectator account.
5. Finalize the installation.
6. Calculate and persist optimal-lineup results.

Provider credentials are encrypted before database storage.

## Cloudflare Tunnel

The default release configuration publishes the frontend on:

```text
http://127.0.0.1:8080
```

Configure the Cloudflare Tunnel public hostname to use that address as its origin service.

Do not point Cloudflare Tunnel directly at the backend container. The frontend Nginx container is the supported application ingress and the only trusted forwarding proxy.

## Other Reverse Proxies

### Caddy

```caddyfile
fantasy.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

### Direct LAN Binding

For direct LAN access without a reverse proxy, change:

```text
APP_BIND_ADDRESS=0.0.0.0
```

Production authentication cookies require HTTPS. Plain HTTP production access prevents secure cookies from working correctly.

## Docker Network Configuration

The default production Docker subnet is:

```text
172.31.100.0/24
```

The frontend uses this fixed internal address:

```text
172.31.100.10
```

The backend trusts only that address for forwarded client headers.

If the default subnet conflicts with another Docker or LAN network, change all three values together:

```text
PRODUCTION_SUBNET=172.31.110.0/24
FRONTEND_PROXY_IP=172.31.110.10
FORWARDED_ALLOW_IPS=172.31.110.10
```

`FRONTEND_PROXY_IP` and `FORWARDED_ALLOW_IPS` must match.

Never use the following production setting:

```text
FORWARDED_ALLOW_IPS=*
```

## Operations

Production procedures are documented in [Operations](docs/OPERATIONS.md).

The operations guide covers:

- Service status
- Container logs
- PostgreSQL backup
- PostgreSQL restore
- Version upgrades
- Application rollback
- Database rollback
- Scheduler validation
- Container health
- Common troubleshooting

### Backup

Download the backup script if it is not already present:

```bash
curl -L \
  https://raw.githubusercontent.com/henry-oo7/fantasy-what-if/main/scripts/backup.sh \
  -o scripts/backup.sh

chmod +x scripts/backup.sh
```

Create a backup:

```bash
scripts/backup.sh
```

A backup includes:

- A PostgreSQL custom-format dump
- A SHA-256 checksum file
- File permissions set to `0600`

Create a named pre-upgrade backup:

```bash
scripts/backup.sh backups/before-upgrade.dump
```

Back up `.env` separately using encrypted storage.

The PostgreSQL password and provider encryption key in `.env` are required to operate and restore the installation.

### Restore

Download the restore script if it is not already present:

```bash
curl -L \
  https://raw.githubusercontent.com/henry-oo7/fantasy-what-if/main/scripts/restore.sh \
  -o scripts/restore.sh

chmod +x scripts/restore.sh
```

A restore replaces the current Fantasy What-If database.

```bash
scripts/restore.sh \
  --backup backups/before-upgrade.dump \
  --confirm-database-replacement
```

The restore script:

1. Validates the backup archive.
2. Validates the SHA-256 checksum when present.
3. Stops application database clients.
4. Keeps PostgreSQL running.
5. Recreates the application database.
6. Restores the selected backup.
7. Applies migrations newer than the backup.
8. Restarts the application.

If restoration fails, application services remain stopped so the failure can be investigated safely.

### Upgrade

Create a verified backup before upgrading:

```bash
scripts/backup.sh backups/before-upgrade.dump
```

Change the version in `.env`:

```text
APP_VERSION=v1.1.0
```

Pull the new images:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  pull
```

Start the updated stack:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  up -d
```

The migration job completes before the backend starts.

### Rollback

Application rollback and database rollback are separate operations.

If the newer release did not apply an incompatible database migration, set `APP_VERSION` to the previous version:

```text
APP_VERSION=v1.0.0
```

Then pull and restart:

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

If the release changed the database incompatibly:

1. Set `APP_VERSION` to the previous version.
2. Pull the previous images.
3. Restore the backup created before the upgrade.
4. Verify service health.

```bash
scripts/restore.sh \
  --backup backups/before-upgrade.dump \
  --confirm-database-replacement
```

Doon unless release-specific instructions explicitly require it.

## Scheduled Synchronization

The default synchronization schedule is:

- Wednesday
- 6:00 AM
- `America/Chicago`

The scheduler runs as a dedicated container. The scheduler is separate from the web API so API restarts do not create overlapping scheduler instances.

The schedule can be changed in `.env`:

```text
SYNC_SCHEDULE_TIMEZONE=America/Chicago
SYNC_SCHEDULE_WEEKDAY=2
SYNC_SCHEDULE_HOUR=6
SYNC_SCHEDULE_MINUTE=0
SYNC_SCHEDULER_POLL_SECONDS=30
```

Python weekday values use:

| Day | Value |
|---|---:|
| Monday | `0` |
| Tuesday | `1` |
| Wednesday | `2` |
| Thursday | `3` |
| Friday | `4` |
| Saturday | `5` |
| Sunday | `6` |

Confirm scheduler operation:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs --tail=200 sync-scheduler
```

## Security

Fantasy What-If includes the following security controls:

- Argon2id password hashing
- PostgreSQL-backed login throttling
- HMAC-protected throttle identifiers
- Hashed session tokens
- Hashed CSRF tokens
- Pre-authentication and authenticated CSRF protection
- Trusted-origin validation
- Administrator route protection
- Secure production cookies
- Restricted trusted-proxy handling
- Content Security Policy
- Frame-embedding denial
- MIME-sniffing prevention
- Referrer restrictions
- Permissions Policy
- HTTP Strict Transport Security in production
- Sensitive-response cache prevention
- Validation-value redaction
- Generic unexpected-error responses
- Read-only application containers
- Unprivileged backend and frontend users
- Internal-only PostgreSQL and backend networking
- SHA-256 backup validation
- Destructive restore safeguards

Report security concerns according to the [Security Policy](SECURITY.md).

Do not open a public GitHub issue for a suspected vulnerability.

## Authentication Security

The login throttling system protects:

- Administrator credentials
- Shared spectator credentials
- Unknown usernames
- Browser-specific failure patterns
- IPv4 `/24` network ranges
- IPv6 `/64` network ranges
- Broad password-spray attempts

Throttle records do not store raw usernames, browser identifiers, or client IP addresses.

Normal authenticated analytics requests do not perform login-throttle queries. Throttling work occurs during authentication attempts.

## Provider and Lineup Compatibility

The v1 optimizer imports lineup-slot counts and eligibility from the configured league instead of assuming one fixed lineup.

Validated configurations include:

- A 9-starter league with one FLEX slot
- A 10-starter league with two FLEX slots
- Variable total starting-lineup sizes
- Variable QB, RB, WR, TE, K, and DST counts
- Multiple standard FLEX slots
- Multiple quarterback starters when reported by the provider
- Leagues without kicker
- Leagues without team defense
- Repeated configured starter slots
- Provider-imported bench and injured-reserve counts
- Variable-length actual and optimal lineup rendering

## Known v1 Limitations

The v1 optimizer targets common redraft formats.

The following configurations require additional post-v1 validation or implementation:

- Restricted `RB/WR` FLEX eligibility
- Restricted `WR/TE` FLEX eligibility
- Complete Superflex coverage
- Repeated Superflex slots
- IDP positions such as DL, LB, and DB
- IDP FLEX
- Unknown custom provider slots
- Unusual combinations such as multiple quarterbacks plus defensive players

Leagues using restricted FLEX, IDP, Superflex, or custom provider slots should verify optimal-lineup results before relying on them.

## Provider Architecture

After provider data is imported:

- Analytics use canonical internal models.
- API routes use internal database IDs.
- The frontend uses provider-neutral response contracts.
- Provider-specific IDs remain synchronization details.
- Original provider vocabulary may be retained for debugging and source preservation.

ESPN Fantasy Football is supported in v1.

Future provider targets include:

- Sleeper
- Yahoo Fantasy Sports

## Persisted Optimal Lineups

Optimal-lineup results are calculated during synchronization and persisted by season, team, and week.

Ordinary analytics requests read persisted results instead of recalculating every team-week.

A full synchronization rebuilds all selected completed weeks. An incremental synchronization rebuilds only the affected weeks.

This design keeps analytics pages responsive while ensuring synchronization remains the authoritative calculation boundary.

## Development

Development and production environments are intentionally separate.

Development uses:

- The `fantasy-what-if` Docker Compose project
- A development PostgreSQL database
- PostgreSQL published only on `127.0.0.1:5433`
- The backend running from the local Python environment
- The frontend running from the local Vite development server

Production uses:

- Immutable backend and frontend images
- A separate Docker Compose project
- A separate PostgreSQL volume
- A production-only `.env`
- No source-code volume mounts
- No host-published backend or database ports

See docs/DEVELOPMENT.md for complete setup instructions.

## Backend Development Checks

```bash
cd backend

uv run ruff format --check app tests
uv run ruff check app tests
uv run pytest -q
uv run alembic heads
uv run alembic current
```

## Frontend Development Checks

```bash
cd frontend

npm run lint
npm test
npm run build
npm audit --audit-level=low
```

## Container Images

The GitHub Actions workflow builds and publishes:

```text
ghcr.io/henry-oo7/fantasy-what-if-backend
ghcr.io/henry-oo7/fantasy-what-if-frontend
```

Published image platforms:

- `linux/amd64`
- `linux/arm64`

Version tags include:

- `1.0.0`
- `1.0`
- `1`
- `latest`

Images include:

- OCI source metadata
- OCI revision metadata
- OCI version metadata
- Build provenance
- Software bill of materials attestations

Pull requests build and validate the images without publishing them. Semantic-version Git tags publish the images.

## Quality Gates

The completed preproduction audit includes:

- 562 backend tests
- 25 frontend tests
- Backend formatting and lint validation
- Frontend lint and TypeScript build validation
- Fresh database migration validation
- Full migration downgrade and re-upgrade validation
- SQLAlchemy metadata-to-PostgreSQL schema comparison
- First-run installation lifecycle testing
- Authentication and authorization matrix testing
- Dependency vulnerability scanning
- Static security analysis
- Git-history secret scanning
- Backend production image testing
- Frontend production image testing
- `linux/amd64` backend and frontend image builds
- `linux/arm64` backend and frontend image builds
- Full isolated-stack smoke testing
- Migration idempotency testing
- Container restart testing
- PostgreSQL volume-persistence testing
- PostgreSQL backup rehearsal
- Destructive PostgreSQL restore rehearsal
- Post-restore application health verification

## Technology

### Backend

- Python 3.14
- FastAPI
- SQLAlchemy
- Alembic
- PostgreSQL 18
- `uv`
- Ruff
- Pytest

### Frontend

- React 19
- TypeScript
- Vite
- TanStack Query
- Oxlint
- Vitest
- Nginx

### Distribution

- Docker
- Docker Compose
- GitHub Container Registry
- GitHub Actions
- Docker Buildx
- OCI provenance attestations
- OCI SBOM attestations

## Repository Documentation

- docs/INSTALLATION.md
- docs/OPERATIONS.md
- docs/DEVELOPMENT.md
- [Contributing](CONTRIBUTING.md)
- [Security Policy](SECURITY.md)
- docs/architecture/

## Contributing

Contributions are welcome.

Before opening a pull request:

1. Read [Contributing](CONTRIBUTING.md).
2. Create a focused branch from `main`.
3. Add or update tests.
4. Run all applicable backend and frontend checks.
5. Document migration, security, deployment, and upgrade impact.
6. Include screenshots for visible frontend changes.

By submitting a contribution, the contributor agrees that the contribution may be distributed under the project license.

## License

Fantasy What-If is licensed under the GNU Affero General Public License version 3 only.

SPDX identifier:

```text
AGPL-3.0-only
```

Copyright © 2026 Henry Huynh.

See LICENSE.