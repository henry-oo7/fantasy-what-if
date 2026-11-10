# Fantasy What-If

Fantasy What-If is a self-hosted fantasy football analytics application. It imports league data through provider adapters and calculates alternative views of team performance.

ESPN is the currently supported provider. Analytics use canonical internal league, season, team, matchup, player-week, and lineup-rule models so future providers can reuse the same backend and frontend contracts.

## Current analytics

- All-play standings
- League-average standings
- Schedule-swap comparisons
- Persisted optimal-lineup analysis
- Matchup-aware weekly optimal-lineup breakdowns
- Actual versus optimal record comparisons
- Players-out, players-in, and retained starter movement analysis

## Access model

- One administrator account
- At most one shared spectator account
- No fantasy-team ownership permissions
- No separate account or invitation for each league member

## Lineup compatibility

Optimal-lineup calculations import starting-slot counts from the configured league instead of assuming one fixed application lineup.

The v1 implementation supports:

- Variable total starting-lineup sizes
- Variable counts of QB, RB, WR, TE, K, and DST
- Multiple standard FLEX slots
- Multiple QB starters when reported by the provider
- Leagues without kicker or team defense
- Repeated configured starter slots
- Provider-imported bench and injured-reserve counts
- Variable-length actual and optimal lineup rendering in the frontend

The implementation has been live-tested against:

- A 9-starter league with one FLEX slot
- A 10-starter league with two FLEX slots

Both configurations use the same provider-rule persistence, optimizer, API, and frontend path.

## Known lineup limitations and post-v1 TODO

The v1 implementation targets common redraft formats. Uncommon restricted FLEX, IDP, and custom-slot formats require additional validation.

Planned work:

- Preserve distinct eligibility for `RB/WR`, `WR/TE`, and `RB/WR/TE` slots instead of treating every variant as unrestricted FLEX.
- Add complete Superflex tests, including repeated Superflex slots.
- Add complete IDP tests for DL, LB, DB, and IDP FLEX configurations.
- Validate unusual combinations such as two quarterbacks plus one linebacker.
- Explicitly reject or configure unknown custom provider slots.
- Replace the legacy default-lineup fallback with an explicit installation consistency state.
- Introduce one reusable season-lineup configuration service for all future lineup-dependent features.
- Add a compatibility matrix for standard, no-kicker, no-defense, Superflex, restricted FLEX, IDP, and custom-slot formats.

Leagues using restricted FLEX, IDP, or custom provider slots should verify optimal-lineup results before relying on them.

## Provider architecture

After provider data is imported:

- Analytics use canonical internal models.
- API routes use internal database IDs.
- The frontend uses provider-neutral response contracts.
- Provider-specific IDs remain synchronization details.
- Original provider vocabulary may be retained for debugging and source preservation.

Sleeper and Yahoo remain future provider targets.

## Persisted optimal lineups

Optimal-lineup results are calculated during synchronization and persisted by season, team, and week. Ordinary analytics requests read persisted results instead of recalculating every team-week.

A full synchronization rebuilds all selected completed weeks. An incremental synchronization rebuilds only affected weeks.

## Technology

### Backend

- Python
- FastAPI
- SQLAlchemy
- Alembic
- PostgreSQL
- uv
- Ruff
- Pytest

### Frontend

- React
- TypeScript
- Vite
- TanStack Query
- Oxlint
- Vitest

## Development checks

Backend:

```bash
cd backend
uv run ruff format --check app tests
uv run ruff check app tests
uv run pytest -q
```

Frontend:

```bash
cd frontend
npm run lint
npm run build
npm test
```

## Project status

Fantasy What-If is under active development and has not completed its formal pre-production audit.

Remaining production work includes:

- Docker and Docker Compose audit
- Dedicated single-instance synchronization scheduler
- Migration startup ordering
- Security configuration review
- Backup and restore documentation
- Existing-installation upgrade testing
- Clean-volume deployment testing
- Public installation and troubleshooting documentation

Architecture decisions and canonical provider contracts are stored in `docs/architecture/`.
