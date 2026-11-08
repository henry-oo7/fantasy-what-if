"""Canonical ESPN provider integration."""

from app.providers.espn.mappings import (
    ESPN_LINEUP_SLOT_MAP,
    ESPN_POSITION_MAP,
    canonical_lineup_slot,
    canonical_position,
    is_bench_slot,
    is_injured_reserve_slot,
    is_starter_slot,
    normalize_eligible_slots,
    normalize_espn_code,
)

__all__ = [
    "ESPN_LINEUP_SLOT_MAP",
    "ESPN_POSITION_MAP",
    "canonical_lineup_slot",
    "canonical_position",
    "is_bench_slot",
    "is_injured_reserve_slot",
    "is_starter_slot",
    "normalize_eligible_slots",
    "normalize_espn_code",
]
