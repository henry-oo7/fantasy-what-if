# Contributing

Thank you for contributing to Fantasy What-If.

## License

Fantasy What-If is licensed under:

```text
AGPL-3.0-only
```

By submitting a contribution, you agree that the contribution may be distributed under the project license.

## Contribution Workflow

1. Fork the repository.
2. Create a focused branch from `main`.
3. Make a coherent change.
4. Add or update tests.
5. Run all applicable checks.
6. Update affected documentation.
7. Open a pull request to `main`.

Example:

```bash
git checkout main
git pull --ff-only
git checkout -b fix/descriptive-name
```

## Pull Request Expectations

A pull request should describe:

- The problem being solved
- The implementation approach
- Tests added or modified
- Database migration impact
- Security impact
- Container or deployment impact
- Upgrade or rollback impact
- Documentation changes
- Screenshots for visible frontend changes

Keep pull requests focused. Separate unrelated cleanup, features, schema changes, and design changes when practical.

## Backend Quality Gates

Run from `backend/`:

```bash
uv run ruff format --check app tests
uv run ruff check app tests
uv run pytest -q
uv run alembic heads
uv run alembic current
```

## Frontend Quality Gates

Run from `frontend/`:

```bash
npm run lint
npm test
npm run build
npm audit --audit-level=low
```

## Container Quality Gates

Build both images:

```bash
docker build \
  --file backend/Dockerfile \
  --tag fantasy-what-if-backend:local \
  backend

docker build \
  --file frontend/Dockerfile \
  --tag fantasy-what-if-frontend:local \
  frontend
```

Pull requests that affect container behavior should verify:

- Backend health
- Frontend health
- `/health`
- `/api/setup/status`
- `/api/auth/csrf`
- SPA route fallback
- Read-only root filesystems
- Unprivileged runtime users
- Internal-only backend and database networking

## Database Changes

Every schema change requires an Alembic migration.

Migrations must include a usable downgrade unless irreversible behavior is clearly documented.

PostgreSQL enum types created by a migration must be removed by the owning downgrade after dependent columns and tables are removed.

Migration pull requests should verify:

1. Upgrade from an empty database to head.
2. Upgrade from the previous release state to head.
3. Downgrade a disposable database to base.
4. Remove application-owned PostgreSQL enum types.
5. Re-upgrade to head.
6. Match SQLAlchemy metadata with the database schema.
7. Preserve existing data when the migration promises preservation.

Never test destructive migrations against a production database.

## Security Requirements

Do not commit:

- `.env`
- `.env.prod`
- ESPN cookies
- `SWID`
- Passwords
- Application secrets
- Provider encryption keys
- PostgreSQL dumps
- Private keys
- Session tokens
- CSRF tokens
- Real user data

Authentication failures must remain generic.

Secret values must not be returned through:

- Validation responses
- Unexpected-error responses
- Application logs
- Test output
- GitHub Actions output

Security-sensitive changes should include tests for both allowed and denied behavior.

## Authentication Changes

Authentication changes must preserve:

- Argon2id password hashing
- Hashed session tokens
- Hashed CSRF tokens
- Trusted-origin checks
- Pre-authentication CSRF protection
- Authenticated CSRF protection
- Secure production cookie requirements
- Persistent login throttling
- Administrator authorization
- Shared spectator access rules
- Generic invalid-credential responses

Do not weaken security tests to accept an incorrect authorization outcome.

Tests for security headers may accept multiple valid route outcomes only when authorization is tested independently elsewhere.

## Provider Architecture

New provider work should implement the provider contracts and map provider data into canonical application models.

Do not add provider-specific identifiers to generic analytics APIs unless a documented provider-neutral alternative is impossible.

Provider adapters should own:

- Provider authentication
- Provider request behavior
- Provider response parsing
- Provider vocabulary mapping
- Provider identifier normalization
- Provider-specific error translation

Canonical application code should own:

- Internal league models
- Internal season models
- Internal team models
- Matchups
- Player-week scores
- Lineup rules
- Schedule rules
- Analytics
- API response contracts

## Frontend Contributions

Frontend changes should follow the existing production design system:

- Mobile-first layout
- Deep football green
- White and neutral grays
- Barlow body font
- Barlow Condensed sports headings
- Flat surfaces
- Minimal corner rounding
- No gradients
- No glow effects
- No drop shadows
- No lift or scaling hover effects
- Color and borders for hierarchy
- Desktop sidebar
- Mobile sliding navigation drawer

Visible changes should include screenshots for desktop and mobile widths.

## Documentation Changes

Update documentation when a change affects:

- Installation
- Production configuration
- Environment variables
- Backup
- Restore
- Upgrade
- Rollback
- Scheduler behavior
- Provider credentials
- Security boundaries
- Network topology
- Container images
- Public API behavior

## Commit Messages

Use concise commit messages with a meaningful prefix.

Examples:

```text
feat: add provider synchronization status
fix: reject invalid lineup slot mappings
test: cover spectator authorization
docs: document production rollback
ci: publish multi-platform images
chore: remove unused dependency
```

## Reporting Security Issues

Do not submit security vulnerabilities through public pull requests or public GitHub issues.

Follow SECURITY.md.