# ADR 0003: Canonical Provider Adapter Contract

- **Status:** Accepted
- **Date:** 2026-11-08
- **Depends on:** ADR 0001 and ADR 0002
- **Initial implementation:** ESPN
- **Future implementations:** Sleeper and Yahoo

## Context

The preserved application connects to ESPN through the third-party `espn-api` Python package.

The current ESPN boundary is incomplete.

`app/espn/adapter.py` translates basic ESPN league, team, and matchup objects into domain snapshots, but the production synchronization service does not use those snapshots.

`app/services/espn_sync.py` currently reads raw ESPN objects directly:

```text
espn_league.current_week
espn_league.settings
espn_league.teams
espn_league.box_scores()
box_score.home_team
box_score.away_team
box_score.home_lineup
box_score.away_lineup
player.playerId
player.position
player.lineupSlot
player.eligibleSlots
```

As a result, the synchronization service performs too many responsibilities:

1. Provider connection
2. Provider response translation
3. Provider response validation
4. Week selection
5. Matchup validation
6. Roster translation
7. Database persistence
8. Transaction control
9. Sync-run state management

This coupling prevents the same synchronization service from supporting Sleeper or Yahoo.

## Decision

All fantasy providers will implement one provider-independent contract.

Provider adapters are responsible for translating raw provider responses into immutable canonical domain objects.

The generic synchronization service may consume canonical domain objects only.

Raw ESPN, Sleeper, and Yahoo objects must not reach:

- Repositories
- Analytics
- API routes
- Setup services
- Season services
- Generic synchronization orchestration

## Package Structure

Provider code will use this structure:

```text
app/providers/
├── __init__.py
├── base.py
├── errors.py
├── registry.py
├── espn/
│   ├── __init__.py
│   ├── adapter.py
│   ├── client.py
│   ├── protocols.py
│   └── mappings.py
├── sleeper/
│   ├── __init__.py
│   ├── adapter.py
│   ├── client.py
│   └── schemas.py
└── yahoo/
    ├── __init__.py
    ├── adapter.py
    ├── client.py
    ├── oauth.py
    └── schemas.py
```

The existing `app/espn` package remains temporarily as a compatibility layer while imports move to `app/providers/espn`.

## Canonical Identifiers

All provider identifiers are strings.

Examples include:

```text
provider_league_id
provider_season_id
provider_team_id
provider_player_id
provider_owner_id
provider_slot_code
provider_stat_code
```

Adapters must convert numeric provider identifiers to strings before constructing canonical domain objects.

Internal database primary keys remain integers.

Provider identifiers must never be used as database foreign keys.

## Canonical Domain Objects

Canonical provider domain objects use frozen, slotted dataclasses.

They must not import:

- SQLAlchemy models
- FastAPI types
- Provider SDK classes
- Repository classes
- Application settings

### Provider Credentials

```python
@dataclass(frozen=True, slots=True)
class ProviderCredentials:
    values: Mapping[str, str]
```

Credential values are provider-specific.

The contract does not expose credentials after a provider instance is created.

### Provider League Reference

```python
@dataclass(frozen=True, slots=True)
class ProviderLeagueReference:
    provider_league_id: str
    provider_season_id: str
    name: str
    sport: FantasySport
    season_year: int
    logo_url: str | None
```

This object supports setup-time league discovery without requiring a full synchronization.

### Provider League

```python
@dataclass(frozen=True, slots=True)
class ProviderLeague:
    provider_league_id: str
    provider_season_id: str
    name: str
    sport: FantasySport
    season_year: int
    current_week: int
    regular_season_weeks: int
    playoff_week_start: int | None
    playoff_week_end: int | None
    scoring_type: str | None
    logo_url: str | None
    teams: tuple[ProviderTeam, ...]
    lineup_slots: tuple[ProviderLineupSlot, ...]
    scoring_rules: tuple[ProviderScoringRule, ...]
    schedule_rules: ProviderScheduleRules
    capabilities: ProviderCapabilities
    metadata: Mapping[str, object]
```

### Provider Team

```python
@dataclass(frozen=True, slots=True)
class ProviderTeam:
    provider_team_id: str
    name: str
    abbreviation: str | None
    owner_display_name: str | None
    logo_url: str | None
    wins: int
    losses: int
    ties: int
    points_for: Decimal
    points_against: Decimal
    is_active: bool
    metadata: Mapping[str, object]
```

### Provider Matchup

```python
@dataclass(frozen=True, slots=True)
class ProviderMatchup:
    week: int
    home_provider_team_id: str
    away_provider_team_id: str | None
    home_score: Decimal | None
    away_score: Decimal | None
    is_complete: bool
    is_bye: bool
    metadata: Mapping[str, object]
```

### Provider Player Week

```python
@dataclass(frozen=True, slots=True)
class ProviderPlayerWeek:
    provider_player_id: str
    player_name: str
    pro_team: str | None
    provider_position: str
    canonical_position: str
    actual_lineup_slot: str
    eligible_lineup_slots: tuple[str, ...]
    points: Decimal
    projected_points: Decimal | None
    is_starter: bool
    is_bench: bool
    is_injured_reserve: bool
    is_on_roster: bool
    metadata: Mapping[str, object]
```

### Provider Weekly Roster

```python
@dataclass(frozen=True, slots=True)
class ProviderWeeklyRoster:
    provider_team_id: str
    week: int
    players: tuple[ProviderPlayerWeek, ...]
```

### Provider Lineup Slot

```python
@dataclass(frozen=True, slots=True)
class ProviderLineupSlot:
    provider_slot_code: str
    canonical_slot_code: str
    display_name: str
    order: int
    starter_count: int
    is_starter: bool
    is_bench: bool
    is_injured_reserve: bool
    eligible_positions: tuple[str, ...]
    metadata: Mapping[str, object]
```

### Provider Scoring Rule

```python
@dataclass(frozen=True, slots=True)
class ProviderScoringRule:
    provider_stat_code: str
    canonical_stat_code: str | None
    display_name: str
    points_per_unit: Decimal
    metadata: Mapping[str, object]
```

### Provider Schedule Rules

```python
@dataclass(frozen=True, slots=True)
class ProviderScheduleRules:
    regular_season_weeks: int
    playoff_week_start: int | None
    playoff_week_end: int | None
    has_divisions: bool
    has_median_game: bool
    has_doubleheaders: bool
    allows_ties: bool
    metadata: Mapping[str, object]
```

### Provider Capabilities

```python
@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    supports_head_to_head: bool
    supports_weekly_matchups: bool
    supports_lineup_history: bool
    supports_optimal_lineups: bool
    supports_schedule_swap: bool
    supports_all_play: bool
    supports_league_average: bool
    supports_team_logos: bool
```

## Provider Protocol

Every provider adapter implements:

```python
class FantasyProvider(Protocol):
    provider: FantasyProviderName

    def validate_configuration(
        self,
    ) -> ProviderValidationResult:
        ...

    def discover_leagues(
        self,
    ) -> tuple[ProviderLeagueReference, ...]:
        ...

    def fetch_league(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
    ) -> ProviderLeague:
        ...

    def fetch_matchups(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
        weeks: tuple[int, ...],
    ) -> tuple[ProviderMatchup, ...]:
        ...

    def fetch_weekly_rosters(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
        weeks: tuple[int, ...],
    ) -> tuple[ProviderWeeklyRoster, ...]:
        ...
```

The first implementation may use synchronous methods because the existing ESPN library is synchronous.

The protocol may gain asynchronous variants later without changing the canonical domain objects.

## Required and Optional Capabilities

A provider does not need to support every analytics feature.

The adapter must report season capabilities honestly.

### Required for Initial Synchronization

- League identity
- Season identity
- Season year
- Current week
- Regular-season length
- Team identities
- Team names
- Team scores or records when available

### Required for All-Play

- Completed weekly team scores

### Required for League Average

- Completed weekly team scores

### Required for Schedule Swap

- Completed head-to-head matchups
- Stable provider team identities
- Weekly scores

### Required for Optimal Lineups

- Weekly roster membership
- Actual starter slots
- Player fantasy points
- Provider player identity
- Player position
- Eligible lineup slots
- Season lineup-slot definitions

If required source data is missing, the corresponding capability must be `false`.

The application must not fabricate provider capabilities.

## Discovery and Validation

Provider setup uses two distinct operations.

### Validation

`validate_configuration()` confirms:

- Required credentials are present
- Credentials can authenticate when authentication is required
- The provider endpoint is reachable
- The provider response is structurally usable

Validation must not persist league data.

### Discovery

`discover_leagues()` returns leagues visible to the configured provider identity.

Discovery must not perform a full season synchronization.

For ESPN Version 1, discovery may be limited to validating an explicitly entered league ID and season because the unofficial API may not provide stable account-wide discovery.

## Synchronization Contract

The generic synchronization orchestrator receives:

```text
Internal season ID
Full or incremental synchronization mode
Optional explicit week range
```

The orchestrator:

1. Loads the season and provider connection.
2. Acquires a lock using the internal season ID.
3. Instantiates the registered provider.
4. Fetches canonical league metadata.
5. Determines the requested matchup and roster weeks.
6. Fetches canonical matchups.
7. Fetches canonical weekly rosters.
8. Validates canonical data consistency.
9. Persists canonical records.
10. Rebuilds supported analytics.
11. Completes the synchronization run.
12. Commits transactionally.

The provider adapter does not write to PostgreSQL.

Repositories do not call provider APIs.

## Incremental Synchronization

The initial incremental policy remains:

```text
Last completed week
Previous completed week
```

For example, if the provider reports current week 9, the incremental roster refresh requests weeks 7 and 8.

A full synchronization requests every completed regular-season week.

The generic week-selection service owns this policy.

The provider adapter receives the final requested week tuple and does not decide synchronization scope.

## Matchup Validation

The generic synchronization layer validates:

- Every provider team ID belongs to the synchronized season.
- A team does not appear more than once in a weekly head-to-head schedule unless the captured schedule rules permit doubleheaders.
- Completed weeks contain expected matchup data.
- Bye records do not reference an away team.
- Non-bye records reference two different teams.
- Provider week numbers are positive.
- Completed matchup scores are present.
- Incomplete matchup scores may be absent.

Provider-specific response-shape checks remain inside the adapter.

## Roster Validation

The generic synchronization layer validates:

- Every roster references a known provider team ID.
- Every requested team-week appears no more than once.
- Every player appears no more than once in one team-week roster.
- Player IDs are nonempty strings.
- Actual lineup slots are nonempty.
- Eligible lineup slots are normalized strings.
- Fantasy points are finite decimal values.
- Starter, bench, and injured-reserve classifications do not conflict.

## Canonical Position Mapping

Provider-specific positions are preserved.

Adapters also map recognized provider positions to canonical position codes.

Examples include:

```text
QB
RB
WR
TE
K
DST
DL
LB
DB
IDP_FLEX
```

Unknown positions are not silently discarded.

An unknown position uses a stable provider-derived canonical code and records the original value in metadata.

## Canonical Slot Mapping

Provider lineup slots are mapped to stable canonical codes where possible.

Examples include:

```text
QB
RB
WR
TE
FLEX
SUPERFLEX
K
DST
DL
LB
DB
IDP_FLEX
BENCH
IR
```

Provider slot codes remain stored alongside canonical codes.

The provider adapter owns provider-code translation.

The generic synchronization service owns persistence.

## Decimal Handling

Canonical score and point values use `Decimal`.

Adapters must not expose binary floating-point values in canonical domain objects.

Provider floating-point values are converted using their string representation:

```python
Decimal(str(provider_value))
```

This preserves database-compatible decimal behavior and avoids unnecessary floating-point artifacts.

## Metadata

Provider metadata uses read-only mappings.

Metadata may preserve useful provider-specific fields that are not yet part of the canonical model.

Metadata must not contain:

- Authentication cookies
- OAuth access tokens
- OAuth refresh tokens
- Client secrets
- Session identifiers
- Raw authorization headers
- Personally sensitive provider account data

## Provider Registry

The provider registry maps provider names to factories.

Conceptual interface:

```python
ProviderFactory = Callable[
    [ProviderConfiguration],
    FantasyProvider,
]
```

Registered names are:

```text
espn
sleeper
yahoo
```

Requesting an unregistered provider raises `ProviderNotSupportedError`.

The registry must not import every provider eagerly when doing so would require optional dependencies or credentials.

## Provider Errors

Provider errors use a canonical hierarchy:

```text
ProviderError
├── ProviderConfigurationError
├── ProviderAuthenticationError
├── ProviderAuthorizationError
├── ProviderNotFoundError
├── ProviderRateLimitError
├── ProviderUnavailableError
├── ProviderResponseError
└── ProviderCapabilityError
```

### Safe Error Behavior

Provider exceptions must:

- Preserve a safe user-facing message
- Preserve the original exception through exception chaining
- Avoid credentials and tokens
- Avoid raw provider response bodies unless sanitized
- Identify the provider
- Identify the operation
- Include safe retry guidance when relevant

### Retry Behavior

Version 1 does not automatically retry every provider failure.

Retry candidates include:

- Temporary provider unavailability
- Timeouts
- HTTP 429 responses
- Selected HTTP 5xx responses

Authentication and validation failures are not retried automatically.

## Credential Handling

The provider contract receives decrypted credentials only during provider construction.

Credentials must not be stored on canonical domain objects.

### ESPN Transition

During the transition, ESPN may read:

```text
ESPN_LEAGUE_ID
ESPN_SEASON
ESPN_S2
ESPN_SWID
```

These values may come from application settings.

After credential encryption is implemented, the ESPN provider will read decrypted credentials from `provider_connections`.

The canonical provider interface must remain unchanged during that transition.

### Sleeper

The initial read-only Sleeper integration may operate without credentials.

### Yahoo

Yahoo requires OAuth client credentials plus access and refresh tokens.

Yahoo token refresh remains inside the Yahoo provider package.

## Idempotency

Repeated synchronization of unchanged provider data must produce the same canonical database state.

Adapters must produce stable ordering where practical:

- Teams ordered by provider team ID
- Matchups ordered by week and provider team IDs
- Rosters ordered by week and provider team ID
- Players ordered by provider player ID
- Rules ordered by provider rule code

Repositories use canonical unique keys to upsert records.

## ESPN Transition Plan

The ESPN transition occurs incrementally.

### Stage 1: Canonical Provider Domain Objects

Add provider-independent domain dataclasses and tests.

No production synchronization behavior changes.

### Stage 2: ESPN Canonical Adapter

Move raw ESPN interpretation into `app/providers/espn`.

The adapter produces:

- Canonical league
- Canonical teams
- Canonical matchups
- Canonical weekly rosters
- Canonical lineup slots
- Canonical scoring rules
- Canonical schedule rules
- Canonical capabilities

### Stage 3: Generic Synchronization Service

Add:

```text
app/services/provider_sync.py
```

The generic service consumes the `FantasyProvider` protocol.

The existing `synchronize_espn()` function remains temporarily as a compatibility wrapper.

### Stage 4: Entry-Point Migration

Update:

- Admin synchronization dependency
- Scheduled synchronization
- CLI synchronization command
- Verification scripts
- Inspection scripts

### Stage 5: Compatibility Cleanup

Remove direct raw ESPN access from generic services.

The following search must return no matches outside the ESPN provider package and provider-specific diagnostic scripts:

```text
espn_league
box_score
playerId
eligibleSlots
```

A representative verification command is:

```bash
grep -R -nE 'espn_league|box_score|playerId|eligibleSlots' app scripts
```

## Repository Transition

During the transition, repositories may accept both legacy ESPN fields and canonical provider fields.

New code must prefer:

```text
provider_league_id
provider_season_id
provider_team_id
provider_player_id
```

Legacy columns remain populated until the deprecated-field cleanup migration.

Repositories must not accept raw provider SDK objects.

## Testing Strategy

Provider tests use deterministic test doubles or sanitized captured fixtures.

Tests must not require live provider access.

### Contract Tests

Every provider must pass the same contract suite for supported operations.

Examples include:

- Provider identifiers are strings.
- Domain objects are immutable.
- Decimal values are finite.
- Team IDs referenced by matchups exist.
- Team IDs referenced by rosters exist.
- Requested weeks are respected.
- Credentials are absent from domain metadata.
- Unsupported capabilities are reported accurately.

### ESPN Adapter Tests

ESPN tests use protocol-compatible fake objects representing:

- League settings
- Teams
- Matchups
- Bye weeks
- Players
- Starter and bench slots
- Injured-reserve slots
- Projected points
- Missing optional values
- Duplicate team or player conditions
- Empty completed weeks

### Integration Tests

Generic synchronization integration tests use a fake provider and a PostgreSQL test database.

These tests verify:

- Full synchronization
- Incremental synchronization
- Idempotent repeated synchronization
- Transaction rollback
- Sync-run failure state
- Season isolation
- Capability persistence
- Rule persistence
- Matchup and roster persistence
- Analytics rebuild invocation

## Consequences

### Positive

- ESPN, Sleeper, and Yahoo share one synchronization architecture.
- Provider SDK objects remain isolated.
- Generic synchronization can be tested without network access.
- Provider identifiers are consistently strings.
- Dynamic rules can be imported per season.
- Provider capabilities control analytics availability.
- Existing ESPN behavior can transition incrementally.

### Negative

- The ESPN adapter becomes larger before the old synchronization service can be removed.
- Compatibility wrappers temporarily duplicate entry points.
- Provider DTOs add translation work.
- Yahoo authentication remains significantly more complex than ESPN or Sleeper.
- Captured provider fixtures require maintenance when providers change.

## Version 1 Boundaries

Version 1 provider integrations are read-only.

The provider contract does not support:

- Draft operations
- Lineup changes
- Waiver claims
- Trades
- League-setting changes
- Chat or message posting
- Provider-side account management

## Follow-Up Work

After this ADR is accepted:

1. Add canonical provider domain dataclasses.
2. Add canonical provider error classes.
3. Add the `FantasyProvider` protocol.
4. Add the provider registry.
5. Add contract-level unit tests.
6. Move ESPN protocols into `app/providers/espn`.
7. Add the ESPN canonical adapter.
8. Add generic provider synchronization.
9. Retain `synchronize_espn()` as a compatibility wrapper.
10. Migrate admin, scheduler, and CLI entry points.