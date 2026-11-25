import pytest

from app.services.synchronization_job_contract import (
    PROHIBITED_SYNCHRONIZATION_PAYLOAD_NAMES,
    SEASON_SYNC_PAYLOAD_VERSION,
    SYNCHRONIZATION_JOB_PAYLOAD_VERSION,
    InvalidSeasonSyncPayloadError,
    build_season_sync_payload,
    season_sync_idempotency_key,
    synchronization_payload_contains_prohibited_names,
    validate_season_sync_payload,
)


def test_legacy_payload_version_is_one() -> None:
    assert SYNCHRONIZATION_JOB_PAYLOAD_VERSION == 1


@pytest.mark.parametrize(
    "field",
    sorted(PROHIBITED_SYNCHRONIZATION_PAYLOAD_NAMES),
)
def test_detects_prohibited_payload_names_case_insensitively(
    field: str,
) -> None:
    assert synchronization_payload_contains_prohibited_names(
        {
            "season_id": 31,
            field.upper(): "secret-value",
        }
    )


def test_season_sync_builder_output_is_exact() -> None:
    payload = build_season_sync_payload(31)

    assert SEASON_SYNC_PAYLOAD_VERSION == 1
    assert payload == {"version": 1, "season_id": 31}
    assert not synchronization_payload_contains_prohibited_names(payload)
    assert validate_season_sync_payload(payload, payload_version=1) == 31


@pytest.mark.parametrize(
    ("season_id", "key"),
    [
        (1, "league-season-sync:1"),
        (31, "league-season-sync:31"),
        (9999999999, "league-season-sync:9999999999"),
    ],
)
def test_season_sync_idempotency_key_format(season_id: int, key: str) -> None:
    assert season_sync_idempotency_key(season_id) == key


@pytest.mark.parametrize("season_id", [0, -1, True, "1", None, 1.0])
def test_season_sync_builder_rejects_invalid_season(season_id: object) -> None:
    with pytest.raises(InvalidSeasonSyncPayloadError, match="positive integer"):
        build_season_sync_payload(season_id)  # type: ignore[arg-type]

    with pytest.raises(InvalidSeasonSyncPayloadError):
        season_sync_idempotency_key(season_id)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "extra",
    [
        "workspace_id",
        "user_id",
        "provider_connection_id",
        "league_id",
        "full_sync",
        "weeks",
        "trigger",
        "credentials",
    ],
)
def test_season_sync_validator_rejects_extra_keys(extra: str) -> None:
    with pytest.raises(InvalidSeasonSyncPayloadError):
        validate_season_sync_payload({"version": 1, "season_id": 31, extra: 1}, payload_version=1)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": 1},
        {"season_id": 31},
        {"version": 2, "season_id": 31},
        {"version": True, "season_id": 31},
        {"version": 1.0, "season_id": 31},
        {"version": "1", "season_id": 31},
        {"version": 1, "season_id": "1"},
        {"version": 1, "season_id": 0},
        {"version": 1, "season_id": -5},
        {"version": 1, "season_id": True},
        {"version": 1, "season_id": None},
        {"version": 1, "season_id": 1.0},
    ],
)
def test_season_sync_validator_rejects_invalid_payloads(payload: dict) -> None:
    with pytest.raises(InvalidSeasonSyncPayloadError):
        validate_season_sync_payload(payload, payload_version=1)


@pytest.mark.parametrize("payload_version", [0, 2, "1"])
def test_season_sync_validator_rejects_other_payload_versions(payload_version: object) -> None:
    with pytest.raises(InvalidSeasonSyncPayloadError):
        validate_season_sync_payload(
            {"version": 1, "season_id": 31},
            payload_version=payload_version,  # type: ignore[arg-type]
        )
