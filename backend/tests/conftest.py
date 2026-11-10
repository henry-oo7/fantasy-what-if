from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.api.auth_dependencies import get_current_user
from app.db.auth_models import User, UserRole
from app.db.session import engine
from app.main import app


def create_test_authenticated_user() -> User:
    return User(
        id=999_999,
        username="authenticated-test-user",
        display_name="Authenticated Test User",
        email=None,
        password_hash="$argon2id$test-placeholder",
        role=UserRole.SPECTATOR,
        is_active=True,
    )


def _seed_canonical_integration_baseline() -> bool:
    """Seed deterministic integration data in an empty test database."""

    with engine.begin() as connection:
        database_name = connection.scalar(text("SELECT current_database()"))

        if not isinstance(database_name, str):
            raise RuntimeError("Could not determine the test database name.")

        if not database_name.endswith("_test"):
            return False

        installation_count = int(connection.scalar(text("SELECT count(*) FROM installations")) or 0)

        if installation_count:
            return False

        now = datetime.now(UTC)

        connection.execute(
            text(
                """
                INSERT INTO installations (
                    id,
                    application_name,
                    public_url,
                    timezone,
                    status,
                    setup_started_at,
                    setup_completed_at,
                    created_at,
                    updated_at
                )
                VALUES (
                    1,
                    'Fantasy What-If Test',
                    'https://fantasy.test.example',
                    'America/Chicago',
                    'active',
                    :now,
                    :now,
                    :now,
                    :now
                )
                """
            ),
            {
                "now": now,
            },
        )

        connection.execute(
            text(
                """
                INSERT INTO provider_connections (
                    id,
                    installation_id,
                    provider,
                    display_name,
                    encrypted_credentials,
                    credential_version,
                    is_active,
                    last_validated_at,
                    last_validation_error,
                    created_at,
                    updated_at
                )
                VALUES (
                    1,
                    1,
                    'espn',
                    'Synthetic ESPN test connection',
                    NULL,
                    1,
                    true,
                    :now,
                    NULL,
                    :now,
                    :now
                )
                """
            ),
            {
                "now": now,
            },
        )

        connection.execute(
            text(
                """
                INSERT INTO leagues (
                    id,
                    name,
                    installation_id,
                    provider,
                    provider_league_id,
                    sport,
                    logo_url,
                    created_at,
                    updated_at
                )
                VALUES (
                    1,
                    'Synthetic Test League',
                    1,
                    'espn',
                    '529250970',
                    'football',
                    NULL,
                    :now,
                    :now
                )
                """
            ),
            {
                "now": now,
            },
        )

        connection.execute(
            text(
                """
                INSERT INTO seasons (
                    id,
                    league_id,
                    year,
                    current_week,
                    regular_season_weeks,
                    last_synced_at,
                    provider_connection_id,
                    provider_season_id,
                    status,
                    team_count,
                    playoff_week_start,
                    playoff_week_end,
                    scoring_type,
                    supports_head_to_head,
                    supports_weekly_matchups,
                    supports_lineup_history,
                    supports_optimal_lineups,
                    supports_schedule_swap,
                    supports_all_play,
                    supports_league_average,
                    supports_team_logos,
                    configuration_snapshot,
                    activated_at,
                    completed_at,
                    archived_at,
                    created_at,
                    updated_at
                )
                VALUES (
                    1,
                    1,
                    2026,
                    9,
                    12,
                    :now,
                    1,
                    '2026',
                    'active',
                    12,
                    13,
                    16,
                    'head_to_head_points',
                    true,
                    true,
                    true,
                    true,
                    true,
                    true,
                    true,
                    false,
                    '{}'::jsonb,
                    :now,
                    NULL,
                    NULL,
                    :now,
                    :now
                )
                """
            ),
            {
                "now": now,
            },
        )

        team_rows = []

        for team_id in range(1, 13):
            team_rows.append(
                {
                    "id": team_id,
                    "season_id": 1,
                    "provider_team_id": str(team_id),
                    "name": f"Synthetic Team {team_id}",
                    "abbreviation": f"T{team_id:02d}",
                    "owner_display_name": (f"Synthetic Manager {team_id}"),
                    "wins": team_id % 7,
                    "losses": (12 - team_id) % 7,
                    "ties": 0,
                    "points_for": Decimal(f"{900 + team_id}.00"),
                    "points_against": Decimal(f"{850 + team_id}.00"),
                    "now": now,
                }
            )

        connection.execute(
            text(
                """
                INSERT INTO teams (
                    id,
                    season_id,
                    provider_team_id,
                    franchise_id,
                    name,
                    abbreviation,
                    owner_display_name,
                    logo_url,
                    is_active,
                    wins,
                    losses,
                    ties,
                    points_for,
                    points_against,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :season_id,
                    :provider_team_id,
                    NULL,
                    :name,
                    :abbreviation,
                    :owner_display_name,
                    NULL,
                    true,
                    :wins,
                    :losses,
                    :ties,
                    :points_for,
                    :points_against,
                    :now,
                    :now
                )
                """
            ),
            team_rows,
        )

        roster_rows = []

        for team_id in range(1, 13):
            roster_rows.append(
                {
                    "id": team_id,
                    "season_id": 1,
                    "team_id": team_id,
                    "week": 1,
                    "provider_player_id": (f"synthetic-player-{team_id}"),
                    "provider_position": "QB",
                    "player_name": (f"Synthetic Player {team_id}"),
                    "pro_team": "TST",
                    "default_position": "QB",
                    "actual_lineup_slot": "QB",
                    "eligible_lineup_slots": ('["QB", "SUPERFLEX", "BENCH", "IR"]'),
                    "points": Decimal(f"{10 + team_id}.00"),
                    "projected_points": Decimal("12.00"),
                    "now": now,
                }
            )

        connection.execute(
            text(
                """
                INSERT INTO player_week_scores (
                    id,
                    season_id,
                    team_id,
                    week,
                    provider_player_id,
                    provider_position,
                    player_name,
                    pro_team,
                    default_position,
                    actual_lineup_slot,
                    eligible_lineup_slots,
                    points,
                    projected_points,
                    was_on_roster,
                    is_starter,
                    is_bench,
                    is_injured_reserve,
                    raw_metadata,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :season_id,
                    :team_id,
                    :week,
                    :provider_player_id,
                    :provider_position,
                    :player_name,
                    :pro_team,
                    :default_position,
                    :actual_lineup_slot,
                    CAST(:eligible_lineup_slots AS json),
                    :points,
                    :projected_points,
                    true,
                    true,
                    false,
                    false,
                    '{"source": "synthetic-test"}'::json,
                    :now,
                    :now
                )
                """
            ),
            roster_rows,
        )

        connection.execute(
            text(
                """
                INSERT INTO sync_runs (
                    id,
                    season_id,
                    provider_connection_id,
                    provider,
                    provider_request_metadata,
                    analytics_rebuilt,
                    started_at,
                    completed_at,
                    status,
                    is_full_sync,
                    requested_from_week,
                    requested_through_week,
                    teams_upserted,
                    matchups_upserted,
                    player_week_scores_upserted,
                    safe_error_message
                )
                VALUES (
                    1,
                    1,
                    1,
                    'espn',
                    '{"source": "synthetic-test"}'::jsonb,
                    true,
                    :now,
                    :now,
                    'succeeded',
                    true,
                    1,
                    8,
                    12,
                    0,
                    12,
                    NULL
                )
                """
            ),
            {
                "now": now,
            },
        )

        connection.execute(
            text(
                """
                INSERT INTO users (
                    id,
                    username,
                    display_name,
                    email,
                    password_hash,
                    role,
                    is_active,
                    last_login_at,
                    created_at,
                    updated_at
                )
                VALUES
                    (
                        1,
                        'synthetic-admin',
                        'Synthetic Administrator',
                        NULL,
                        '$argon2id$synthetic-test-placeholder',
                        'admin',
                        true,
                        NULL,
                        :now,
                        :now
                    ),
                    (
                        2,
                        'synthetic-spectator',
                        'Synthetic Test Spectator',
                        NULL,
                        '$argon2id$synthetic-test-placeholder',
                        'spectator',
                        true,
                        NULL,
                        :now,
                        :now
                    )
                """
            ),
            {
                "now": now,
            },
        )

        connection.execute(
            text(
                """
                UPDATE installations
                SET
                    active_league_id = 1,
                    active_season_id = 1,
                    updated_at = :now
                WHERE id = 1
                """
            ),
            {
                "now": now,
            },
        )

        for sequence_name in (
            "installations_id_seq",
            "provider_connections_id_seq",
            "leagues_id_seq",
            "seasons_id_seq",
            "teams_id_seq",
            "player_week_scores_id_seq",
            "sync_runs_id_seq",
            "users_id_seq",
        ):
            connection.execute(
                text(
                    "SELECT setval("
                    ":sequence_name, "
                    "(SELECT max(id) FROM " + sequence_name.removesuffix("_id_seq") + "), "
                    "true"
                    ")"
                ),
                {
                    "sequence_name": sequence_name,
                },
            )

    return True


@pytest.fixture(
    scope="session",
    autouse=True,
)
def canonical_integration_baseline() -> Iterator[None]:
    """Prepare deterministic data for clean test databases."""

    _seed_canonical_integration_baseline()

    yield


@pytest.fixture(autouse=True)
def authenticated_fantasy_routes() -> Iterator[None]:
    app.dependency_overrides[get_current_user] = create_test_authenticated_user

    try:
        yield
    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
