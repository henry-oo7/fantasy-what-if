# ADR 0001: Portable Multi-Provider Fantasy Platform

- **Status:** Accepted
- **Date:** 2026-11-08
- **Decision owners:** Project maintainers
- **Starting release:** `v0.1.0-personal-espn`
- **Architecture branch:** `feat/portable-platform`

## Context

Fantasy What-If began as a private application for one ESPN fantasy football league and one season.

The preserved application supports:

- One ESPN league configured through environment variables
- One implicitly selected season
- ESPN-specific numeric identifiers
- Invitation-based member registration
- Administrator and member accounts
- Team-bound user memberships
- All-Play standings
- League Average standings
- Schedule Swap analysis
- Optimal Lineup analysis
- PostgreSQL persistence
- FastAPI and React
- Docker Compose deployment
- Scheduled ESPN synchronization

The application is now intended to become a public, self-hosted fantasy football analytics platform that can support different providers, league configurations, and historical seasons.

The current personal application is preserved by:

- Git tag: `v0.1.0-personal-espn`
- PostgreSQL backup created before this refactor
- Main branch retained as the preserved personal release
- Portable work isolated on `feat/portable-platform`

## Decision

Fantasy What-If will become a portable, provider-independent, self-hosted fantasy football analytics platform.

Version 1 will support:

- One installation
- One logical fantasy league
- Unlimited historical seasons
- Exactly one active season at a time
- One administrator account
- One shared viewer account
- Fantasy football only
- Sleeper, ESPN, and Yahoo provider architecture
- Season-specific teams, schedules, rules, and analytics
- Browser-based first-run setup
- Docker Compose installation
- Provider-independent canonical data
- Precomputed analytics stored in PostgreSQL

## Product Hierarchy

The canonical application hierarchy is:

```text
Installation
└── League
    ├── Season
    │   ├── Provider connection
    │   ├── Season teams
    │   ├── Roster and lineup rules
    │   ├── Scoring rules
    │   ├── Matchups
    │   ├── Weekly rosters
    │   ├── Player-week scores
    │   ├── Synchronization history
    │   └── Cached analytics
    └── Additional historical seasons
```

The first public release will allow only one logical league per installation. The database architecture should avoid assumptions that would make multiple leagues impossible later.

## Season Workspaces

Each season is an independent analytics workspace.

A season owns its own:

1. Provider and provider connection
2. Provider league identifier
3. Provider season identifier
4. Teams
5. Managers or owners
6. Team names and logos
7. Roster positions
8. Lineup slot eligibility
9. Scoring rules
10. Schedule rules
11. Matchups
12. Weekly rosters
13. Player-week scores
14. Synchronization runs
15. Analytics capabilities
16. Cached analytics

Changes to one season must not mutate another season.

For example, if 2027 adds a second FLEX slot, removes kickers, changes passing touchdowns from four points to six points, or expands from 12 teams to 14 teams, the stored 2026 configuration and analytics remain unchanged.

## Season Statuses

Seasons use these lifecycle states:

```text
draft
active
completed
archived
```

### Draft

A season exists but has not completed setup and its initial synchronization.

### Active

The active season is the installation default and is eligible for scheduled synchronization.

Only one season may be active at a time.

### Completed

A completed season remains fully browseable but no longer synchronizes automatically.

An administrator may perform a deliberate correction synchronization.

### Archived

An archived season remains available through season history but is visually separated from current seasons.

Normal application workflows do not delete historical seasons.

## New-Season Workflow

An administrator creates a new season through:

```text
Administration → Seasons → Add season
```

The wizard will:

1. Select or configure a provider connection.
2. Discover the provider league and season.
3. Import teams, owners, rules, schedule settings, and season length.
4. Compare the imported configuration with the previous season.
5. Display rule and membership changes.
6. Allow the administrator to reconcile team continuity.
7. Perform the initial synchronization.
8. Calculate supported analytics.
9. Mark the previous active season as completed.
10. Activate the new season transactionally.

If setup or synchronization fails, the previous season remains active.

## Historical Navigation

Authenticated frontend routes include explicit season context:

```text
/seasons/:seasonId
/seasons/:seasonId/all-play
/seasons/:seasonId/league-average
/seasons/:seasonId/schedule-swap
/seasons/:seasonId/optimal-lineups
/seasons/:seasonId/optimal-lineups/:teamId
```

The season selector appears in the league identity area of the desktop sidebar and mobile navigation drawer.

URLs, browser history, refresh behavior, and shared links therefore preserve the selected season.

Frontend query keys must include season context:

```text
["season", seasonId, "league"]
["season", seasonId, "all-play"]
["season", seasonId, "league-average"]
["season", seasonId, "schedule-swap", firstTeamId, secondTeamId]
["season", seasonId, "optimal-lineups"]
["season", seasonId, "optimal-lineups", teamId]
```

Season data must never be cached under season-independent query keys.

## Authentication Model

Version 1 uses two accounts per installation.

### Administrator Account

The administrator can:

- Configure the installation
- Configure providers
- Manage seasons
- Synchronize league data
- Change synchronization schedules
- Manage shared viewer access
- Rotate passwords
- View diagnostics
- Export and back up data

The administrator may also be associated with a fantasy team for dashboard defaults and analytics context.

The administrator role and team association are independent.

### Shared Viewer Account

League members share one read-only viewer account.

The viewer can:

- Browse active and historical seasons
- Select teams for display
- View all supported analytics
- Maintain multiple concurrent sessions

The viewer cannot:

- Change installation settings
- View provider credentials
- Trigger synchronization
- Manage seasons
- Change accounts
- Modify stored fantasy data

### Retired Authentication Behavior

The portable platform will retire:

- Public registration
- Registration invitations
- Individual league-member accounts
- Required user-to-team memberships
- Personalized behavior that assumes one account per person

The migration will stop using invitation behavior before removing invitation tables.

Server-side sessions, Argon2id password hashing, CSRF protection, session revocation, and role authorization remain.

## First-Run Setup

A fresh database leaves the installation in an incomplete state.

Before setup is complete, application access redirects to `/setup`.

The setup wizard includes:

1. Installation name, public URL, and time zone
2. Administrator account creation
3. Shared viewer account creation
4. Provider selection and credentials
5. League and season discovery
6. Configuration preview
7. Initial synchronization
8. Analytics generation
9. Setup completion

The wizard must be resumable after interruption.

League configuration and provider credentials belong in PostgreSQL, not ordinary deployment environment variables.

Infrastructure secrets remain in `.env`.

## Provider Architecture

Analytics and repositories must not consume raw provider objects.

Provider data flows through:

```text
Provider API
→ Provider-specific response validation
→ Provider adapter
→ Canonical domain objects
→ Canonical PostgreSQL tables
→ Cached analytics
→ FastAPI
→ React
```

### Provider Contract

Each provider implements a common interface that supports the capabilities required by the provider and league.

Expected operations include:

```text
validate_configuration
discover_leagues
fetch_league
fetch_season
fetch_teams
fetch_matchups
fetch_weekly_rosters
fetch_players
fetch_roster_rules
fetch_scoring_rules
```

Provider packages use this structure:

```text
app/providers/
├── base.py
├── registry.py
├── sleeper/
├── espn/
└── yahoo/
```

### Provider Order

Implementation order is:

1. ESPN adapter extracted from the existing integration
2. Sleeper provider
3. Yahoo provider

ESPN is extracted first to prove the abstraction without losing existing behavior.

Sleeper is the first new provider because its read-only API exposes league, roster, settings, user, and matchup information without an OAuth flow.

Yahoo follows after the canonical model and setup wizard are stable because Yahoo requires OAuth application credentials and token management.

### ESPN Support Level

ESPN support is labeled experimental.

The connector relies on an unofficial, undocumented fantasy API. Private leagues require ESPN cookies.

The setup UI and documentation must clearly disclose that ESPN changes can break the connector without an application release.

### Yahoo Support Level

Yahoo uses OAuth 2.0.

A self-hosted administrator must provide approved Yahoo application credentials unless a future hosted OAuth broker is created.

Yahoo client secrets, access tokens, and refresh tokens are encrypted at rest.

### Provider Credentials

Provider credentials are stored in `provider_connections`.

Sensitive values are encrypted before storage.

The encryption key is an infrastructure secret supplied through the deployment environment.

The application must never:

- Return stored provider secrets to the frontend
- Print provider secrets in logs
- Include provider secrets in database exports as plaintext
- Include provider secrets in diagnostics
- Commit provider secrets to Git

## Canonical Identifiers

Provider identifiers are strings.

The canonical model must not assume that provider league, team, player, user, or season identifiers are integers.

Provider-specific identifiers are stored separately from internal database primary keys.

Examples include:

```text
provider_league_id VARCHAR
provider_season_id VARCHAR
provider_team_id VARCHAR
provider_player_id VARCHAR
```

Internal relationships use internal integer primary keys.

## Teams and Franchise Continuity

A provider team is a season-specific record.

Team identity cannot be assumed to persist across seasons.

Optional franchise continuity connects season teams for historical analysis:

```text
franchises
season_teams
```

An administrator confirms or changes suggested mappings when a new season is created.

This permits future features such as:

- Franchise career records
- Historical manager performance
- Team-name history
- Cross-season lineup efficiency
- Year-over-year comparisons

Franchise mapping does not affect season-specific analytics.

## Dynamic Lineup Rules

Lineup slots belong to a season.

The application stores:

```text
lineup_slot_definitions
lineup_slot_eligibility
```

The rules must support configurations including:

- Standard FLEX
- Multiple FLEX slots
- Superflex
- Two-quarterback leagues
- No kicker
- Multiple kickers
- Defensive teams
- IDP positions
- Multiple bench and injured-reserve slots
- Provider-specific slot names

The optimal-lineup engine must use stored lineup slot definitions and eligibility rather than fixed ESPN constants.

## Dynamic Scoring Rules

Scoring settings belong to a season.

The application stores provider and canonical scoring rule names, point values, and provider-specific metadata.

Provider-calculated fantasy points remain the primary source when available.

Version 1 will not attempt to reproduce every provider score from raw NFL statistics.

Stored scoring rules support:

- Configuration display
- Validation
- Season comparison
- Analytics explanation
- Future custom scoring calculations

## Analytics Capability Model

Not every provider or league configuration supports every analytics feature.

Each season records capabilities such as:

```text
supports_head_to_head
supports_weekly_matchups
supports_lineup_history
supports_optimal_lineups
supports_schedule_swap
supports_all_play
supports_league_average
supports_team_logos
```

The frontend displays navigation only for capabilities supported by the selected season.

### All-Play

Requires completed weekly team scores.

### League Average

Requires completed weekly team scores.

### Schedule Swap

Requires head-to-head matchups, stable weekly schedules, and team scores.

Version 1 Schedule Swap analyzes regular-season head-to-head scoring periods.

Provider median games are not treated as scheduled opponents.

### Optimal Lineups

Requires:

- Weekly roster membership
- Actual starters
- Player fantasy points
- Season lineup slots
- Position eligibility

The optimizer uses a dynamic player-to-slot assignment model.

Players are never added from waivers for historical calculations.

Injured-reserve treatment follows the captured season rules.

If no legal player is available for a position, the slot remains explicitly unfilled.

## Analytics Persistence

Analytics are precomputed after successful synchronization and stored in PostgreSQL.

Normal GET requests must not run expensive season-wide analytics.

The initial priority is persistent Optimal Lineup results.

Expected tables include:

```text
optimal_lineup_week_results
optimal_lineup_team_summaries
```

Cache rows contain:

- Season
- Team
- Week
- Algorithm version
- Source synchronization run
- Calculation timestamp
- Actual points
- Optimal points
- Missed points
- Efficiency
- Actual lineup
- Optimal lineup
- Recommended substitutions
- Unfilled slots

If required analytics have not been calculated, the API returns a clear unavailable response rather than silently recalculating during the request.

Historical raw canonical data remains sufficient to rebuild analytics after an algorithm change.

## Synchronization

The generic synchronization pipeline is:

```text
Acquire season advisory lock
→ Load provider connection
→ Instantiate provider adapter
→ Validate credentials
→ Fetch provider data
→ Validate provider responses
→ Convert to canonical domain objects
→ Upsert season data
→ Determine season capabilities
→ Rebuild analytics
→ Commit transaction
→ Record synchronization result
```

Only the active season synchronizes automatically.

Completed and archived seasons require deliberate administrator synchronization.

Failed synchronization must preserve the previous successful data and analytics.

## Portable Deployment

The target installation workflow is:

```bash
git clone <repository>
cd fantasy-what-if
cp .env.example .env
docker compose up -d
```

The administrator then opens:

```text
http://server-address:8080/setup
```

Infrastructure environment variables include only values such as:

```text
APP_SECRET_KEY
CREDENTIAL_ENCRYPTION_KEY
POSTGRES_PASSWORD
PUBLIC_PORT
```

Provider settings, league IDs, seasons, scheduler settings, and user accounts are managed through the application.

## Visual Design

The portable frontend keeps the established mobile-first flat football design system:

- Deep football green
- White and neutral gray
- Barlow body typography
- Barlow Condensed headings and scores
- Desktop sidebar
- Mobile sliding navigation drawer
- Minimal rectangular corner rounding
- No drop shadows
- No gradients
- No neon or glow effects
- No scaling or lift effects
- Horizontal sliding transitions
- Balanced information density
- Analytics-focused layouts

## Migration Strategy

The portable refactor uses incremental migrations.

Planned sequence:

1. Add installation and provider models.
2. Add canonical league and season fields.
3. Add season lifecycle state.
4. Add provider-independent string identifiers.
5. Add dynamic lineup and scoring rule tables.
6. Backfill existing ESPN data into canonical fields.
7. Make repositories and analytics explicitly season-aware.
8. Add season-aware API routes.
9. Add season-aware frontend routes and query keys.
10. Extract ESPN behind the provider contract.
11. Add administrator and viewer account behavior.
12. Stop using registration invitations.
13. Build first-run setup.
14. Build new-season setup and configuration comparison.
15. Add Sleeper.
16. Generalize and persist analytics.
17. Add Yahoo OAuth.
18. Remove obsolete ESPN-only and invitation fields after transition.

No migration should remove old data in the same release that introduces its replacement.

## Version 1 Exclusions

Version 1 will not include:

- Multiple active leagues per installation
- Multiple active seasons
- Multiple administrators
- Individual league-member accounts
- Fantasy sports other than football
- Provider write operations
- Draft management
- Waiver management
- Trade management
- Live scoring
- Native mobile applications
- Hosted SaaS operation
- Historical algorithm-version comparison
- Automatic destructive historical-season deletion

These exclusions reduce implementation risk while preserving an architecture that can evolve later.

## Consequences

### Positive

- Historical seasons remain stable and browseable.
- Provider changes do not reach analytics directly.
- League rules can change safely between seasons.
- Expensive analytics become fast read operations.
- Self-hosted setup no longer depends on shell-based league configuration.
- The access model fits a read-only league analytics application.
- Sleeper, ESPN, and Yahoo can coexist behind one canonical model.
- The project becomes suitable for public distribution.

### Negative

- The schema and synchronization pipeline become significantly more complex.
- ESPN remains operationally fragile.
- Yahoo introduces OAuth approval and token-management requirements.
- Shared viewer accounts provide limited individual auditing.
- Season and franchise reconciliation requires administrator input.
- Existing routes, query keys, and tests require broad refactoring.
- The migration must temporarily support old and new models together.

## Follow-Up Decisions

Separate architecture decisions should be created for:

1. Provider credential encryption
2. Canonical provider adapter contracts
3. Season and franchise schema
4. Shared viewer authentication behavior
5. Analytics cache invalidation and algorithm versioning
6. Yahoo OAuth deployment options
7. Backup and restore format