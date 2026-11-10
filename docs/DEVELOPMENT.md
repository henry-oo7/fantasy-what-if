# Development

Development and production are intentionally separate.

Development uses source code, a Vite development server, a local backend process, and a dedicated development PostgreSQL database.

Production uses immutable containers, a separate PostgreSQL volume, a production-only environment file, and no source mounts.

## Requirements

- Git
- Docker Engine
- Docker Compose v2
- Python 3.14
- `uv`
- Node.js 24
- npm

## Clone the Repository

```bash
git clone \
  https://github.com/henry-oo7/fantasy-what-if.git

cd fantasy-what-if
```

## Create the Development Environment

Copy the development template:

```bash
cp .env.example .env
```

Replace development secrets and database credentials before starting the application.

The development environment should include:

```text
APP_ENV=development
APP_DEBUG=true
FRONTEND_ORIGIN=http://localhost:5173
TRUSTED_ORIGINS=http://localhost:5173
FORWARDED_ALLOW_IPS=127.0.0.1,::1
SESSION_COOKIE_NAME=fantasy_session
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_SAMESITE=lax
```

Generate a Fernet provider key from the backend environment:

```bash
cd backend

uv run python -c \
  'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode("ascii"))'

cd ..
```

Store the result in:

```text
PROVIDER_CREDENTIAL_KEY=
```

## Start the Development Database

```bash
docker compose \
  --project-name fantasy-what-if \
  --file compose.yaml \
  --file compose.dev.yaml \
  up -d
```

Development Compose starts only PostgreSQL.

The database is published on:

```text
127.0.0.1:5433
```

The development database and volume are separate from production.

## Verify PostgreSQL

```bash
docker compose \
  --project-name fantasy-what-if \
  --file compose.yaml \
  --file compose.dev.yaml \
  ps
```

Expected state:

```text
database: healthy
```

## Backend Setup

```bash
cd backend
```

Install locked dependencies:

```bash
uv sync --frozen --dev
```

Apply migrations:

```bash
uv run alembic upgrade head
```

Start the backend:

```bash
uv run python -m app.scripts.run_api
```

The backend listens on:

```text
http://127.0.0.1:8000
```

Verify:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

## Frontend Setup

From a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

The current Vite configuration supports the code-server proxy base path:

```text
/proxy/5173/
```

The production build automatically uses `/` instead.

## Backend Checks

Run from `backend/`:

```bash
uv run ruff format --check app tests
uv run ruff check app tests
uv run pytest -q
uv run alembic heads
uv run alembic current
```

Apply formatting when needed:

```bash
uv run ruff format app tests
```

Apply safe automatic lint fixes when appropriate:

```bash
uv run ruff check app tests --fix
```

Always review automatic changes before committing.

## Frontend Checks

Run from `frontend/`:

```bash
npm run lint
npm test
npm run build
npm audit --audit-level=low
```

## Production Image Builds

Build the backend:

```bash
docker build \
  --file backend/Dockerfile \
  --tag fantasy-what-if-backend:local \
  backend
```

Build the frontend:

```bash
docker build \
  --file frontend/Dockerfile \
  --tag fantasy-what-if-frontend:local \
  frontend
```

Inspect runtime users:

```bash
docker image inspect \
  fantasy-what-if-backend:local \
  --format 'User={{.Config.User}}'

docker image inspect \
  fantasy-what-if-frontend:local \
  --format 'User={{.Config.User}}'
```

Expected users:

```text
Backend: fantasy
Frontend: nginx
```

## Multi-Platform Image Validation

The supported release platforms are:

- `linux/amd64`
- `linux/arm64`

Create a temporary Buildx builder:

```bash
docker buildx create \
  --name fantasy-multiplatform-validation \
  --driver docker-container \
  --use

docker buildx inspect --bootstrap
```

Validate the backend:

```bash
docker buildx build \
  --builder fantasy-multiplatform-validation \
  --platform linux/amd64 \
  --file backend/Dockerfile \
  --output type=oci,dest=/tmp/fantasy-backend-amd64.tar \
  backend

docker buildx build \
  --builder fantasy-multiplatform-validation \
  --platform linux/arm64 \
  --file backend/Dockerfile \
  --output type=oci,dest=/tmp/fantasy-backend-arm64.tar \
  backend
```

Validate the frontend:

```bash
docker buildx build \
  --builder fantasy-multiplatform-validation \
  --platform linux/amd64 \
  --file frontend/Dockerfile \
  --output type=oci,dest=/tmp/fantasy-frontend-amd64.tar \
  frontend

docker buildx build \
  --builder fantasy-multiplatform-validation \
  --platform linux/arm64 \
  --file frontend/Dockerfile \
  --output type=oci,dest=/tmp/fantasy-frontend-arm64.tar \
  frontend
```

Remove temporary resources:

```bash
docker buildx use default

docker buildx rm \
  fantasy-multiplatform-validation

rm -f \
  /tmp/fantasy-backend-amd64.tar \
  /tmp/fantasy-backend-arm64.tar \
  /tmp/fantasy-frontend-amd64.tar \
  /tmp/fantasy-frontend-arm64.tar
```

## Database Migrations

Create migrations only from `backend/`:

```bash
uv run alembic revision \
  --autogenerate \
  -m "describe the schema change"
```

Review generated migrations manually.

Generated migrations are starting points, not automatically trusted production code.

## Required Migration Validation

Every migration change should be tested with this lifecycle:

1. Create an empty disposable PostgreSQL database.
2. Run `alembic upgrade head`.
3. Compare PostgreSQL tables with SQLAlchemy metadata.
4. Run `alembic downgrade base`.
5. Verify that no application tables remain.
6. Verify that no PostgreSQL enum types remain.
7. Run `alembic upgrade head` again.
8. Run the full backend test suite.
9. Verify the normal development database remains unchanged.

PostgreSQL enum types created by a migration must be removed by the owning downgrade after all dependent columns and tables are removed.

Never experiment with migration downgrade commands against the production database.

## Application Architecture

Provider-specific behavior belongs in:

```text
backend/app/providers/
```

Provider data must be mapped into canonical application models before analytics use it.

Analytics, API routes, and frontend contracts should use:

- Internal league IDs
- Internal season IDs
- Internal team IDs
- Canonical matchup models
- Canonical player-week models
- Canonical lineup rules
- Canonical provider identifiers only at synchronization boundaries

Avoid adding ESPN-specific assumptions to generic analytics code.

## Authentication Architecture

Authentication responsibilities are separated across:

```text
backend/app/auth/
backend/app/api/auth.py
backend/app/api/auth_dependencies.py
backend/app/api/security.py
backend/app/repositories/
```

Security-sensitive requirements include:

- Generic invalid-credential responses
- CSRF validation
- Trusted-origin validation
- Secure production cookies
- Hashed session tokens
- HMAC-protected throttle identifiers
- Persistent login throttling
- Administrator authorization checks
- Secret redaction from validation and exception responses

Do not log passwords, ESPN cookies, CSRF tokens, session tokens, application secrets, or provider encryption keys.

## Frontend Architecture

The frontend uses:

- React Router for browser routes
- TanStack Query for server state
- A same-origin `/api` path
- Cookie-based authentication
- CSRF tokens for state-changing requests
- A production Nginx reverse proxy

The production frontend must continue to support:

```text
/
/setup
/login
/admin
/all-play
/league-average
/schedule-swap
/optimal-lineups
/optimal-lineups/:teamId
```

Nginx must return `index.html` for browser routes while preserving `/api/` and `/health` proxy behavior.

## Testing Principles

Tests should:

- Use isolated database transactions or explicit cleanup
- Avoid leaving persistent login-throttle rows
- Avoid real provider credentials
- Avoid real ESPN cookies
- Verify generic authentication failures
- Verify authorization independently from response headers
- Verify migration and schema assumptions
- Cover provider-specific mappings at provider boundaries
- Cover canonical analytics behavior independently

## Git Workflow

Create a focused branch from `main`:

```bash
git checkout main
git pull --ff-only
git checkout -b feature/descriptive-name
```

Commit coherent milestones:

```bash
git add <files>
git diff --cached --check
git commit -m "type: concise description"
```

Push:

```bash
git push -u origin feature/descriptive-name
```

Open a pull request to `main`.

## Documentation

Architecture decisions are stored under:

```text
docs/architecture/
```

Update documentation when a change affects:

- Installation
- Upgrade
- Backup
- Restore
- Rollback
- Security
- Migration behavior
- Provider contracts
- Container topology
- Public API contracts