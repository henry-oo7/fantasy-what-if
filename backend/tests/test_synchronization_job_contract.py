import pytest

from app.services.synchronization_job_contract import (
    PROHIBITED_SYNCHRONIZATION_PAYLOAD_NAMES,
    SYNCHRONIZATION_JOB_PAYLOAD_VERSION,
    build_synchronization_job_payload,
    synchronization_payload_contains_prohibited_names,
)


def test_builds_version_one_secret_free_payload() -> None:
    payload = build_synchronization_job_payload(
        season_id=31,
        full_sync=False,
        trigger="administrator",
    )

    assert SYNCHRONIZATION_JOB_PAYLOAD_VERSION == 1
    assert payload == {
        "season_id": 31,
        "full_sync": False,
        "trigger": "administrator",
    }
    assert not synchronization_payload_contains_prohibited_names(payload)


@pytest.mark.parametrize("season_id", [True, 0, -1, "31"])
def test_rejects_invalid_season_identifier(season_id: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        build_synchronization_job_payload(
            season_id=season_id,  # type: ignore[arg-type]
            full_sync=False,
            trigger="administrator",
        )


@pytest.mark.parametrize("full_sync", [0, 1, "false", None])
def test_rejects_non_boolean_synchronization_mode(
    full_sync: object,
) -> None:
    with pytest.raises(ValueError, match="must be a boolean"):
        build_synchronization_job_payload(
            season_id=31,
            full_sync=full_sync,  # type: ignore[arg-type]
            trigger="administrator",
        )


@pytest.mark.parametrize("trigger", ["", "   "])
def test_rejects_empty_trigger(trigger: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        build_synchronization_job_payload(
            season_id=31,
            full_sync=False,
            trigger=trigger,
        )


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
