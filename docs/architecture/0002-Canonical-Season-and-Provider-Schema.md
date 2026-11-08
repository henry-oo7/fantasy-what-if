# ADR 0002: Canonical Season and Provider Schema

- **Status:** Accepted
- **Date:** 2026-11-08
- **Depends on:** ADR 0001
- **Starting database revision:** `12c9cf9bbb5a`
- **Migration strategy:** Additive transition with ESPN backfill

## Context

The preserved application stores one ESPN league and one season using provider-specific columns:

```text
leagues.espn_league_id
teams.espn_team_id
player_week_scores.espn_player_id
```

The active season is selected through environment variables:

```text
ESPN_LEAGUE_ID
ESPN_SEASON
```

API routes implicitly use that configured league and season.

The portable platform requires:

1. Provider-independent identifiers
2. Multiple historical seasons
3. One active season at a time
4. Season-specific providers
5. Season-specific teams and rules
6. Dynamic lineup slots
7. Dynamic scoring settings
8. Optional franchise continuity
9. Persisted analytics
10. Safe migration of existing ESPN data

The first canonical migration must preserve all existing data and continue supporting the existing application while the repositories and synchronization services are refactored.

## Decision

The canonical schema will be introduced additively.

Existing ESPN-specific columns and authentication tables remain during the transition. New canonical columns are added and backfilled before application code begins depending on them.

No table or column is removed in the first canonical migration.

## Existing Tables

The current database contains:

```text
alembic_version
auth_sessions
leagues
matchups
player_week_scores
registration_invites
seasons
sync_runs
teams
user_team_memberships
users
```

The existing 2026 data includes:

```text
1 league
1 season
12 teams
72 matchups
1,579 player-week scores
2 users
1 team membership
4 registration invitations
12 authentication sessions
```

## New Enumerations

### Fantasy Provider

```text
espn
sleeper
yahoo
```

Database enum name:

```text
fantasy_provider
```

### Installation Status

```text
setup_required
setup_in_progress
active
```

Database enum name:

```text
installation_status
```

### Season Status

```text
draft
active
completed
archived
```

Database enum name:

```text
season_status
```

### Sport

Version 1 supports only:

```text
football
```

Database enum name:

```text
fantasy_sport
```

The explicit sport field prevents football assumptions from being hidden inside otherwise provider-independent records.

## Installations

Create:

```text
installations
```

Columns:

```text
id INTEGER PRIMARY KEY
application_name VARCHAR(120) NOT NULL
public_url VARCHAR(500) NULL
timezone VARCHAR(100) NOT NULL
status installation_status NOT NULL
active_league_id INTEGER NULL
active_season_id INTEGER NULL
setup_started_at TIMESTAMPTZ NULL
setup_completed_at TIMESTAMPTZ NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Version 1 supports exactly one installation row.

This is enforced initially through application logic. A singleton database constraint may be added after the setup workflow is stable.

`active_league_id` and `active_season_id` are added after their target tables exist.

The active season reference is a convenience pointer. The authoritative season state remains `seasons.status`.

## Provider Connections

Create:

```text
provider_connections
```

Columns:

```text
id INTEGER PRIMARY KEY
installation_id INTEGER NOT NULL
provider fantasy_provider NOT NULL
display_name VARCHAR(120) NOT NULL
encrypted_credentials TEXT NULL
credential_version INTEGER NOT NULL DEFAULT 1
is_active BOOLEAN NOT NULL DEFAULT TRUE
last_validated_at TIMESTAMPTZ NULL
last_validation_error TEXT NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Foreign key:

```text
installation_id
→ installations.id
ON DELETE CASCADE
```

A provider connection stores connection-level credentials.

Provider league identifiers and provider season identifiers do not belong in this table because one connection may discover more than one league or season.

`encrypted_credentials` contains encrypted JSON. Credential encryption is defined in a separate ADR.

Sleeper may use a provider connection without stored credentials.

## Canonical League Changes

Retain the existing `leagues` table.

Add:

```text
installation_id INTEGER NULL
provider fantasy_provider NULL
provider_league_id VARCHAR(255) NULL
sport fantasy_sport NULL
logo_url VARCHAR(1000) NULL
```

Foreign key:

```text
installation_id
→ installations.id
ON DELETE CASCADE
```

Transition behavior:

1. Add columns as nullable.
2. Create the installation row.
3. Backfill the existing league:
   - `provider = espn`
   - `provider_league_id = CAST(espn_league_id AS VARCHAR)`
   - `sport = football`
   - `installation_id = existing installation`
4. Validate the backfill.
5. Make canonical fields non-null in a later migration.
6. Keep `espn_league_id` until ESPN-specific repositories are retired.

Canonical uniqueness:

```sql
UNIQUE (
    installation_id,
    provider,
    provider_league_id
)
```

The existing unique ESPN index remains temporarily.

## Canonical Season Changes

Retain the existing `seasons` table.

Add:

```text
provider_connection_id INTEGER NULL
provider_season_id VARCHAR(255) NULL
status season_status NULL
team_count INTEGER NULL
playoff_week_start INTEGER NULL
playoff_week_end INTEGER NULL
scoring_type VARCHAR(100) NULL
supports_head_to_head BOOLEAN NOT NULL DEFAULT FALSE
supports_weekly_matchups BOOLEAN NOT NULL DEFAULT FALSE
supports_lineup_history BOOLEAN NOT NULL DEFAULT FALSE
supports_optimal_lineups BOOLEAN NOT NULL DEFAULT FALSE
supports_schedule_swap BOOLEAN NOT NULL DEFAULT FALSE
supports_all_play BOOLEAN NOT NULL DEFAULT FALSE
supports_league_average BOOLEAN NOT NULL DEFAULT FALSE
supports_team_logos BOOLEAN NOT NULL DEFAULT FALSE
configuration_snapshot JSONB NULL
activated_at TIMESTAMPTZ NULL
completed_at TIMESTAMPTZ NULL
archived_at TIMESTAMPTZ NULL
```

Foreign key:

```text
provider_connection_id
→ provider_connections.id
ON DELETE RESTRICT
```

`provider_season_id` is a string because provider season identifiers must not be assumed to be integers.

The existing `year` remains the canonical display year.

Existing 2026 backfill:

```text
provider_season_id = "2026"
status = active
team_count = 12
supports_head_to_head = true
supports_weekly_matchups = true
supports_lineup_history = true
supports_optimal_lineups = true
supports_schedule_swap = true
supports_all_play = true
supports_league_average = true
supports_team_logos = false
activated_at = existing season created_at
```

`configuration_snapshot` stores provider-independent summary metadata used for season comparisons. It does not replace normalized lineup or scoring rule tables.

The existing uniqueness constraint remains:

```sql
UNIQUE (league_id, year)
```

Additional canonical uniqueness:

```sql
UNIQUE (
    league_id,
    provider_connection_id,
    provider_season_id
)
```

## One Active Season

Only one active season may exist for one logical league.

PostgreSQL enforces this with a partial unique index:

```sql
CREATE UNIQUE INDEX uq_seasons_one_active_per_league
ON seasons (league_id)
WHERE status = 'active';
```

Activation must occur transactionally:

1. Lock the league.
2. Validate the draft season.
3. Complete the previously active season.
4. Activate the new season.
5. Update `installations.active_season_id`.
6. Commit.

If any step fails, the previous season remains active.

## Franchises

Create:

```text
franchises
```

Columns:

```text
id INTEGER PRIMARY KEY
league_id INTEGER NOT NULL
display_name VARCHAR(255) NOT NULL
logo_url VARCHAR(1000) NULL
is_active BOOLEAN NOT NULL DEFAULT TRUE
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Foreign key:

```text
league_id
→ leagues.id
ON DELETE CASCADE
```

A franchise represents optional cross-season continuity.

A franchise does not own season results directly.

## Canonical Team Changes

Retain the existing `teams` table.

The table continues to represent one team in one season.

Add:

```text
franchise_id INTEGER NULL
provider_team_id VARCHAR(255) NULL
abbreviation VARCHAR(50) NULL
owner_display_name VARCHAR(255) NULL
logo_url VARCHAR(1000) NULL
is_active BOOLEAN NOT NULL DEFAULT TRUE
```

Foreign key:

```text
franchise_id
→ franchises.id
ON DELETE SET NULL
```

Existing ESPN backfill:

```text
provider_team_id = CAST(espn_team_id AS VARCHAR)
is_active = true
```

Canonical uniqueness:

```sql
UNIQUE (
    season_id,
    provider_team_id
)
```

The existing ESPN uniqueness constraint remains temporarily:

```sql
UNIQUE (
    season_id,
    espn_team_id
)
```

Franchise association is optional. Season analytics must not require a franchise mapping.

## Players

Do not introduce a global canonical player identity in the first schema migration.

Provider player IDs cannot be reliably matched across providers without a separate identity-resolution project.

The initial provider-independent transition keeps player-week records self-contained.

Add to `player_week_scores`:

```text
provider_player_id VARCHAR(255) NULL
provider_position VARCHAR(100) NULL
```

Existing ESPN backfill:

```text
provider_player_id = CAST(espn_player_id AS VARCHAR)
provider_position = default_position
```

Replace the provider-independent uniqueness target with:

```sql
UNIQUE (
    season_id,
    week,
    team_id,
    provider_player_id
)
```

The existing ESPN uniqueness constraint remains temporarily.

A future ADR may introduce:

```text
players
player_provider_ids
```

This should occur only when cross-provider player identity is needed.

## Dynamic Lineup Slot Definitions

Create:

```text
lineup_slot_definitions
```

Columns:

```text
id INTEGER PRIMARY KEY
season_id INTEGER NOT NULL
provider_slot_code VARCHAR(100) NOT NULL
canonical_slot_code VARCHAR(100) NOT NULL
display_name VARCHAR(120) NOT NULL
slot_order INTEGER NOT NULL
starter_count INTEGER NOT NULL DEFAULT 0
is_starter BOOLEAN NOT NULL DEFAULT FALSE
is_bench BOOLEAN NOT NULL DEFAULT FALSE
is_injured_reserve BOOLEAN NOT NULL DEFAULT FALSE
metadata JSONB NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Foreign key:

```text
season_id
→ seasons.id
ON DELETE CASCADE
```

Unique constraint:

```sql
UNIQUE (
    season_id,
    provider_slot_code
)
```

Check constraints:

```sql
starter_count >= 0
slot_order >= 0
```

Starter slot definitions may represent multiple slots through `starter_count`.

Example:

```text
provider_slot_code = RB
canonical_slot_code = RB
starter_count = 2
is_starter = true
```

## Lineup Slot Eligibility

Create:

```text
lineup_slot_eligibility
```

Columns:

```text
id INTEGER PRIMARY KEY
lineup_slot_definition_id INTEGER NOT NULL
canonical_position VARCHAR(100) NOT NULL
created_at TIMESTAMPTZ NOT NULL
```

Foreign key:

```text
lineup_slot_definition_id
→ lineup_slot_definitions.id
ON DELETE CASCADE
```

Unique constraint:

```sql
UNIQUE (
    lineup_slot_definition_id,
    canonical_position
)
```

Examples:

```text
FLEX → RB
FLEX → WR
FLEX → TE
SUPERFLEX → QB
SUPERFLEX → RB
SUPERFLEX → WR
SUPERFLEX → TE
```

## Dynamic Scoring Rules

Create:

```text
scoring_rules
```

Columns:

```text
id INTEGER PRIMARY KEY
season_id INTEGER NOT NULL
provider_stat_code VARCHAR(150) NOT NULL
canonical_stat_code VARCHAR(150) NULL
display_name VARCHAR(200) NOT NULL
points_per_unit NUMERIC(12, 6) NOT NULL
metadata JSONB NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Foreign key:

```text
season_id
→ seasons.id
ON DELETE CASCADE
```

Unique constraint:

```sql
UNIQUE (
    season_id,
    provider_stat_code
)
```

Provider-calculated weekly points remain authoritative in Version 1.

Scoring rules support:

- Configuration display
- Season comparison
- Provider validation
- Future custom analytics

## Schedule Rules

Create:

```text
schedule_rules
```

Columns:

```text
id INTEGER PRIMARY KEY
season_id INTEGER NOT NULL
regular_season_weeks INTEGER NOT NULL
playoff_week_start INTEGER NULL
playoff_week_end INTEGER NULL
has_divisions BOOLEAN NOT NULL DEFAULT FALSE
has_median_game BOOLEAN NOT NULL DEFAULT FALSE
has_doubleheaders BOOLEAN NOT NULL DEFAULT FALSE
allows_ties BOOLEAN NOT NULL DEFAULT TRUE
metadata JSONB NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Foreign key:

```text
season_id
→ seasons.id
ON DELETE CASCADE
```

Unique constraint:

```sql
UNIQUE (season_id)
```

`seasons.regular_season_weeks` remains temporarily for compatibility.

A later migration may remove the duplicated season column after all services use `schedule_rules`.

## Synchronization Changes

Add to `sync_runs`:

```text
provider_connection_id INTEGER NULL
provider fantasy_provider NULL
provider_request_metadata JSONB NULL
analytics_rebuilt BOOLEAN NOT NULL DEFAULT FALSE
```

Foreign key:

```text
provider_connection_id
→ provider_connections.id
ON DELETE SET NULL
```

Existing ESPN sync runs are backfilled with:

```text
provider = espn
provider_connection_id = existing ESPN connection
analytics_rebuilt = false
```

## Authentication Transition

The first canonical migration does not remove or change:

```text
users
auth_sessions
user_team_memberships
registration_invites
```

The current `member` and `admin` roles remain temporarily.

The viewer role is introduced in a separate authentication migration after setup and account-transition behavior are defined.

The existing administrator remains valid throughout the schema transition.

## Installation Backfill

The first canonical migration creates one installation row for the existing application.

Preferred backfill values:

```text
application_name = "Fantasy What-If"
public_url = existing FRONTEND_APP_URL value when available
timezone = existing SYNC_SCHEDULE_TIMEZONE value
status = active
setup_completed_at = migration execution time
```

Application environment values are not directly available inside a portable Alembic migration.

Therefore, deterministic migration defaults are:

```text
application_name = "Fantasy What-If"
public_url = NULL
timezone = "America/Chicago"
status = active
setup_completed_at = now()
```

The administrator may update these values later through installation settings.

## Provider Connection Backfill

Create one provider connection for the existing ESPN application:

```text
provider = espn
display_name = "Migrated ESPN connection"
encrypted_credentials = NULL
credential_version = 1
is_active = true
```

Existing ESPN credentials remain in environment variables during the transition.

Credentials are not copied into PostgreSQL until credential encryption is implemented.

The provider adapter may temporarily fall back to environment-based ESPN credentials.

## Existing League Backfill

For the existing league:

```text
installation_id = migrated installation
provider = espn
provider_league_id = CAST(espn_league_id AS VARCHAR)
sport = football
```

## Existing Season Backfill

For the existing 2026 season:

```text
provider_connection_id = migrated ESPN connection
provider_season_id = CAST(year AS VARCHAR)
status = active
team_count = count of season teams
```

Capabilities are set from known existing data:

```text
supports_head_to_head = true
supports_weekly_matchups = true
supports_lineup_history = true
supports_optimal_lineups = true
supports_schedule_swap = true
supports_all_play = true
supports_league_average = true
supports_team_logos = false
```

## Existing Team Backfill

For each existing team:

```text
provider_team_id = CAST(espn_team_id AS VARCHAR)
is_active = true
```

No automatic franchise rows are created in the first migration.

Franchise reconciliation belongs to the season-management workflow.

## Existing Player-Week Backfill

For each existing player-week score:

```text
provider_player_id = CAST(espn_player_id AS VARCHAR)
provider_position = default_position
```

The existing JSON `eligible_lineup_slots` remains temporarily.

The dynamic slot tables are populated by a later ESPN rule-import service because current player rows alone cannot reliably determine starter slot counts.

## Data Isolation Rules

All season-owned queries must include an explicit internal `season_id`.

The following entities are season-owned:

```text
teams
matchups
player_week_scores
lineup_slot_definitions
lineup_slot_eligibility through slot definitions
scoring_rules
schedule_rules
sync_runs
cached analytics
```

Repositories must not choose a season from environment settings after the season-aware repository migration is complete.

Provider identifiers must never be used as database foreign keys.

## Deletion Behavior

Deletion rules are:

```text
installation → leagues CASCADE
installation → provider connections CASCADE
league → seasons CASCADE
league → franchises CASCADE
season → teams CASCADE
season → matchups CASCADE
season → player-week scores CASCADE
season → lineup rules CASCADE
season → scoring rules CASCADE
season → schedule rules CASCADE
season → analytics caches CASCADE
franchise → season team association SET NULL
provider connection → season RESTRICT
provider connection → sync run SET NULL
```

Historical seasons are not deleted through ordinary application workflows.

Database cascade behavior protects referential integrity if an administrator later performs an explicit destructive reset.

## Migration Stages

### Migration A: Canonical Foundation

Add:

```text
installations
provider_connections
new enums
canonical league columns
canonical season columns
canonical team columns
canonical player-week columns
franchises
lineup_slot_definitions
lineup_slot_eligibility
scoring_rules
schedule_rules
sync-run provider columns
```

Backfill existing ESPN data.

Keep new replacement columns nullable during backfill.

### Migration B: Canonical Constraints

After application and migration tests prove the backfill:

1. Make required canonical columns non-null.
2. Add canonical unique constraints.
3. Add the partial active-season index.
4. Validate foreign keys.
5. Preserve ESPN-specific columns.

### Migration C: Authentication Transition

1. Add the viewer role.
2. Add installation account settings.
3. Stop using registration invitations.
4. Stop requiring user-team memberships.
5. Preserve old tables temporarily.

### Migration D: Deprecated Field Cleanup

Only after all application code and compatibility tests stop using legacy fields:

1. Remove `leagues.espn_league_id`.
2. Remove `teams.espn_team_id`.
3. Remove `player_week_scores.espn_player_id`.
4. Remove obsolete ESPN-only unique constraints.
5. Remove invitation tables.
6. Remove obsolete environment configuration.

Deprecated-field cleanup must not occur in the same release as the canonical foundation.

## Testing Requirements

The canonical foundation requires tests for:

1. A clean database migration from no tables to head.
2. An upgrade from revision `12c9cf9bbb5a`.
3. Existing ESPN identifier backfill.
4. Installation singleton creation.
5. ESPN provider connection creation.
6. Existing season activation.
7. Existing team identifier backfill.
8. Existing player identifier backfill.
9. One active season per league.
10. Multiple historical seasons with different rules.
11. Provider identifier strings containing nonnumeric characters.
12. Cascade behavior for season-owned data.
13. Restricted deletion of provider connections used by seasons.
14. Franchise deletion setting season references to null.
15. Downgrade behavior where supported.
16. Existing application model import compatibility.
17. Existing 2026 analytics remaining available.

## Consequences

### Positive

- Existing ESPN data migrates without deletion.
- Historical seasons can retain independent rules.
- Provider identifiers support ESPN, Sleeper, and Yahoo.
- Provider credentials are separated from league identity.
- Dynamic lineup and scoring rules become possible.
- The application can transition incrementally.
- Authentication can change later without blocking the canonical model.

### Negative

- Legacy and canonical columns coexist temporarily.
- Repositories must support a controlled transition period.
- Some rule tables cannot be fully backfilled until ESPN rule import is implemented.
- The first migration adds several nullable compatibility columns.
- Temporary duplication increases schema complexity.