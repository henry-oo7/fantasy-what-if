# Installation

This guide installs Fantasy What-If using prebuilt container images from GitHub Container Registry.

## Requirements

- Docker Engine 24 or newer
- Docker Compose v2
- OpenSSL
- At least 2 GB of available memory
- At least 5 GB of available disk space
- An HTTPS hostname
- ESPN league credentials

Supported container platforms:

- `linux/amd64`
- `linux/arm64`

## Create the Installation Directory

```bash
mkdir -p fantasy-what-if/scripts
cd fantasy-what-if
```

## Download the Release Files

Download the release Compose file and production environment template:

```bash
curl -LO \
  https://raw.githubusercontent.com/henry-oo7/fantasy-what-if/main/compose.release.yaml

curl -LO \
  https://raw.githubusercontent.com/henry-oo7/fantasy-what-if/main/.env.production.example
```

Download the production environment generator:

```bash
curl -L \
  https://raw.githubusercontent.com/henry-oo7/fantasy-what-if/main/scripts/generate-production-env.sh \
  -o scripts/generate-production-env.sh

chmod +x scripts/generate-production-env.sh
```

For a stable production installation, use the files attached to a tagged GitHub release whenever available.

## Generate Production Secrets

Run:

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

Do not commit, email, or publicly share `.env`.

The generated provider encryption key is required to decrypt stored provider credentials. Include `.env` in an encrypted backup plan.

## Configure the Application Version

Edit `.env` and select the desired release:

```text
APP_VERSION=1
```

Available tag styles include:

```text
APP_VERSION=1.0.0
APP_VERSION=1.0
APP_VERSION=1
APP_VERSION=latest
```

For dependable production deployments, prefer a complete version such as:

```text
APP_VERSION=1.0.0
```

A complete version makes rollback predictable because the deployment does not move automatically when a newer image is published.

## Configure the Public Address

Edit these values in `.env`:

```text
FRONTEND_ORIGIN=https://fantasy.example.com
TRUSTED_ORIGINS=https://fantasy.example.com
```

Both values must:

- Use HTTPS in production
- Exactly match the browser-visible origin
- Omit a trailing slash
- Include a nonstandard port if the browser-visible URL uses one

Example with a nonstandard port:

```text
FRONTEND_ORIGIN=https://fantasy.example.com:8443
TRUSTED_ORIGINS=https://fantasy.example.com:8443
```

## Configure Host Ingress

The default binding is:

```text
APP_BIND_ADDRESS=127.0.0.1
APP_PORT=8080
```

This publishes the frontend only to the Docker host.

PostgreSQL and the backend do not publish host ports.

### Cloudflare Tunnel

Configure the Cloudflare Tunnel public hostname to use this origin service:

```text
http://127.0.0.1:8080
```

Cloudflare provides HTTPS to the browser.

Do not point Cloudflare Tunnel directly at the backend container. The frontend Nginx container is the supported ingress and the only trusted forwarding proxy.

### Caddy

Example Caddy configuration:

```caddyfile
fantasy.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

### Traefik

If Traefik runs on the Docker host rather than inside the Fantasy What-If network, proxy to:

```text
http://127.0.0.1:8080
```

If Traefik is attached directly to the application Docker network, review the trusted-proxy configuration carefully before changing the supported topology.

### Direct LAN Access

For direct LAN access without a host reverse proxy, change:

```text
APP_BIND_ADDRESS=0.0.0.0
```

Production authentication cookies require HTTPS. Plain HTTP production access prevents secure authentication cookies from working correctly.

## Docker Network Configuration

The default production subnet is:

```text
PRODUCTION_SUBNET=172.31.100.0/24
```

The frontend receives this fixed internal address:

```text
FRONTEND_PROXY_IP=172.31.100.10
```

The backend trusts only that address for forwarded client headers:

```text
FORWARDED_ALLOW_IPS=172.31.100.10
```

If the subnet conflicts with an existing Docker or LAN network, change all three values together.

Example:

```text
PRODUCTION_SUBNET=172.31.110.0/24
FRONTEND_PROXY_IP=172.31.110.10
FORWARDED_ALLOW_IPS=172.31.110.10
```

`FRONTEND_PROXY_IP` and `FORWARDED_ALLOW_IPS` must match.

Never configure:

```text
FORWARDED_ALLOW_IPS=*
```

## Review the Remaining Production Settings

Recommended production values:

```text
APP_ENV=production
APP_DEBUG=false
APP_LOG_LEVEL=INFO

SESSION_COOKIE_NAME=__Host-fantasy_session
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=lax
SESSION_LIFETIME_HOURS=168

POSTGRES_DB=fantasy_what_if
POSTGRES_USER=fantasy_app
DATABASE_ECHO=false
```

The secure `__Host-` cookie name requires:

- HTTPS
- The `Secure` cookie flag
- No cookie domain
- A cookie path of `/`

The application configures these requirements automatically when the production settings are valid.

## Configure Scheduled Synchronization

Default schedule:

```text
SYNC_SCHEDULE_TIMEZONE=America/Chicago
SYNC_SCHEDULE_WEEKDAY=2
SYNC_SCHEDULE_HOUR=6
SYNC_SCHEDULE_MINUTE=0
SYNC_SCHEDULER_POLL_SECONDS=30
```

This runs the normal scheduled synchronization on Wednesday at 6:00 AM Central Time.

Python weekday values are:

| Day | Value |
|---|---:|
| Monday | `0` |
| Tuesday | `1` |
| Wednesday | `2` |
| Thursday | `3` |
| Friday | `4` |
| Saturday | `5` |
| Sunday | `6` |

The release Compose file enables the scheduler only in the dedicated `sync-scheduler` container.

## Validate the Compose Configuration

Run:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  config --quiet
```

A successful command produces no error output.

To inspect the resolved configuration:

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  config
```

Be careful when sharing this output because resolved Compose output can contain environment values.

## Pull the Images

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  pull
```

The release stack uses:

```text
ghcr.io/henry-oo7/fantasy-what-if-backend
ghcr.io/henry-oo7/fantasy-what-if-frontend
postgres:18-bookworm
```

## Start the Stack

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  up -d
```

Startup ordering is:

1. PostgreSQL starts and becomes healthy.
2. The migration job runs `alembic upgrade head`.
3. The backend and scheduler start after migration success.
4. The frontend starts after the backend becomes healthy.

## Verify Service Health

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

Check the local health endpoint:

```bash
curl http://127.0.0.1:8080/health
```

Expected response:

```json
{"status":"ok"}
```

Check the public endpoint:

```bash
curl https://fantasy.example.com/health
```

## Complete First-Run Setup

Open the configured HTTPS address in a browser.

The setup flow will:

1. Create the administrator account.
2. Configure the ESPN provider connection.
3. Validate the provider credentials.
4. Import the league and current season.
5. Import league-specific lineup and schedule rules.
6. Create the optional shared spectator account.
7. Finalize the installation.
8. Calculate and persist optimal-lineup results.

Provider credentials are encrypted before database storage.

## Verify the Scheduler

```bash
docker compose \
  --env-file .env \
  --file compose.release.yaml \
  logs --tail=100 sync-scheduler
```

The log should report:

- The configured timezone
- The configured weekday and time
- The next scheduled synchronization

## Installation Files to Preserve

Back up all of the following:

- `.env`
- PostgreSQL dump files
- PostgreSQL dump checksum files
- `compose.release.yaml`
- The currently deployed `APP_VERSION`
- Any reverse-proxy or Cloudflare Tunnel configuration

The following `.env` values are especially important:

- `APP_SECRET_KEY`
- `PROVIDER_CREDENTIAL_KEY`
- `POSTGRES_PASSWORD`

Losing `PROVIDER_CREDENTIAL_KEY` prevents recovery of encrypted provider credentials from the database.

## Next Steps

After installation:

1. Create a baseline backup.
2. Save `.env` in encrypted storage.
3. Record the deployed `APP_VERSION`.
4. Verify the synchronization schedule.
5. Review docs/OPERATIONS.md.
6. Test the restore procedure before depending on the installation.