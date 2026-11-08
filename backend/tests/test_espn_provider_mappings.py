import pytest

from app.providers.espn.mappings import (
    canonical_lineup_slot,
    canonical_position,
    is_bench_slot,
    is_injured_reserve_slot,
    is_starter_slot,
    normalize_eligible_slots,
    normalize_espn_code,
)


@pytest.mark.parametrize(
    ("provider_position", "expected"),
    [
        ("QB", "QB"),
        ("rb", "RB"),
        ("WR", "WR"),
        ("TE", "TE"),
        ("K", "K"),
        ("D/ST", "DST"),
        ("DE", "DL"),
        ("DT", "DL"),
        ("CB", "DB"),
        ("S", "DB"),
    ],
)
def test_known_positions_are_mapped(
    provider_position: str,
    expected: str,
) -> None:
    assert canonical_position(provider_position) == expected


def test_unknown_position_is_preserved() -> None:
    assert canonical_position("ATH") == "ESPN_ATH"


@pytest.mark.parametrize(
    ("provider_slot", "expected"),
    [
        ("QB", "QB"),
        ("RB/WR/TE", "FLEX"),
        ("OP", "SUPERFLEX"),
        ("QB/RB/WR/TE", "SUPERFLEX"),
        ("D/ST", "DST"),
        ("DP", "IDP_FLEX"),
        ("BE", "BENCH"),
        ("BN", "BENCH"),
        ("IR", "IR"),
    ],
)
def test_known_lineup_slots_are_mapped(
    provider_slot: str,
    expected: str,
) -> None:
    assert canonical_lineup_slot(provider_slot) == expected


def test_unknown_lineup_slot_is_preserved() -> None:
    assert canonical_lineup_slot("CUSTOM/FLEX") == "ESPN_CUSTOM_FLEX"


def test_eligible_slots_are_normalized_and_deduplicated() -> None:
    assert normalize_eligible_slots(
        [
            "WR",
            " WR ",
            "Rookie",
            "Rookie",
            "RB/WR/TE",
            "BE",
        ]
    ) == (
        "WR",
        "Rookie",
        "RB/WR/TE",
        "BE",
    )


def test_empty_eligible_slots_return_empty_tuple() -> None:
    assert normalize_eligible_slots(None) == ()
    assert normalize_eligible_slots([]) == ()


def test_lineup_classification_helpers() -> None:
    assert is_starter_slot("QB") is True
    assert is_starter_slot("RB/WR/TE") is True

    assert is_bench_slot("BE") is True
    assert is_bench_slot("BN") is True
    assert is_starter_slot("BE") is False

    assert is_injured_reserve_slot("IR") is True
    assert is_starter_slot("IR") is False


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
    ],
)
def test_empty_espn_codes_are_rejected(
    value: str,
) -> None:
    with pytest.raises(ValueError):
        normalize_espn_code(value)
