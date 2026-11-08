"""Canonical ESPN provider integration."""

from app.providers.espn.client import (
    EspnConfiguration,
    create_espn_client,
    credentials_from_values,
    parse_espn_configuration,
)
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
from app.providers.espn.provider import (
    EspnProvider,
    create_espn_provider,
    register_espn_provider,
)

__all__ = [
    "ESPN_LINEUP_SLOT_MAP",
    "ESPN_POSITION_MAP",
    "EspnConfiguration",
    "EspnProvider",
    "canonical_lineup_slot",
    "canonical_position",
    "create_espn_client",
    "create_espn_provider",
    "credentials_from_values",
    "is_bench_slot",
    "is_injured_reserve_slot",
    "is_starter_slot",
    "normalize_eligible_slots",
    "normalize_espn_code",
    "parse_espn_configuration",
    "register_espn_provider",
]
