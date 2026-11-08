"""Canonical mappings for ESPN positions and lineup slots."""

from __future__ import annotations

ESPN_POSITION_MAP: dict[str, str] = {
    "QB": "QB",
    "RB": "RB",
    "WR": "WR",
    "TE": "TE",
    "K": "K",
    "D/ST": "DST",
    "DST": "DST",
    "DL": "DL",
    "DE": "DL",
    "DT": "DL",
    "LB": "LB",
    "DB": "DB",
    "CB": "DB",
    "S": "DB",
    "P": "P",
    "HC": "HC",
}

ESPN_LINEUP_SLOT_MAP: dict[str, str] = {
    "QB": "QB",
    "RB": "RB",
    "WR": "WR",
    "TE": "TE",
    "RB/WR": "FLEX",
    "WR/TE": "FLEX",
    "RB/WR/TE": "FLEX",
    "OP": "SUPERFLEX",
    "QB/RB/WR/TE": "SUPERFLEX",
    "K": "K",
    "D/ST": "DST",
    "DST": "DST",
    "DL": "DL",
    "LB": "LB",
    "DB": "DB",
    "DP": "IDP_FLEX",
    "BE": "BENCH",
    "BN": "BENCH",
    "IR": "IR",
}


def normalize_espn_code(value: object) -> str:
    """Normalize one ESPN position or lineup-slot code."""

    normalized = str(value).strip().upper()

    if not normalized:
        raise ValueError("ESPN code must not be empty.")

    return normalized


def canonical_position(
    provider_position: object,
) -> str:
    """Map an ESPN position to a stable canonical code."""

    normalized = normalize_espn_code(provider_position)

    mapped = ESPN_POSITION_MAP.get(normalized)

    if mapped is not None:
        return mapped

    return f"ESPN_{normalized.replace('/', '_')}"


def canonical_lineup_slot(
    provider_slot: object,
) -> str:
    """Map an ESPN lineup slot to a stable canonical code."""

    normalized = normalize_espn_code(provider_slot)

    mapped = ESPN_LINEUP_SLOT_MAP.get(normalized)

    if mapped is not None:
        return mapped

    return f"ESPN_{normalized.replace('/', '_')}"


def normalize_eligible_slots(
    slots: object,
) -> tuple[str, ...]:
    """Normalize and deduplicate ESPN eligible lineup slots."""

    if not slots:
        return ()

    normalized_slots: list[str] = []
    seen_slots: set[str] = set()

    for slot in slots:
        normalized = str(slot).strip()

        if not normalized:
            continue

        if normalized in seen_slots:
            continue

        seen_slots.add(normalized)
        normalized_slots.append(normalized)

    return tuple(normalized_slots)


def is_bench_slot(provider_slot: object) -> bool:
    """Return whether an ESPN lineup slot is a bench slot."""

    return canonical_lineup_slot(provider_slot) == "BENCH"


def is_injured_reserve_slot(
    provider_slot: object,
) -> bool:
    """Return whether an ESPN lineup slot is injured reserve."""

    return canonical_lineup_slot(provider_slot) == "IR"


def is_starter_slot(provider_slot: object) -> bool:
    """Return whether an ESPN lineup slot is a starter slot."""

    return canonical_lineup_slot(provider_slot) not in {
        "BENCH",
        "IR",
    }
