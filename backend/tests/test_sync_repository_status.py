from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db import model_registry  # noqa: F401
from app.db.platform_enums import FantasyProvider
from app.repositories.sync_repository import (
    get_sync_status,
)


def test_status_uses_canonical_provider_identity() -> None:
    engine = create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
    )

    with Session(
        engine,
        autoflush=False,
        expire_on_commit=False,
    ) as session:
        stored = get_sync_status(
            session,
            season_id=1,
        )

    assert stored is not None
    assert stored.provider is FantasyProvider.ESPN
    assert stored.season_id == 1
    assert stored.provider_league_id == "529250970"
    assert stored.provider_season_id == "2026"
    assert stored.last_synced_at is not None
    assert stored.latest_run is not None
    assert stored.latest_run.id == 49


def test_missing_season_status_returns_none() -> None:
    engine = create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
    )

    with Session(
        engine,
        autoflush=False,
        expire_on_commit=False,
    ) as session:
        stored = get_sync_status(
            session,
            season_id=999_999,
        )

    assert stored is None


def test_invalid_season_id_is_rejected() -> None:
    engine = create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
    )

    with Session(
        engine,
        autoflush=False,
        expire_on_commit=False,
    ) as session:
        try:
            get_sync_status(
                session,
                season_id=0,
            )
        except ValueError as exc:
            assert str(exc) == "season_id must be positive."
        else:
            raise AssertionError("Expected invalid season ID rejection.")


def test_datetime_import_remains_timezone_aware() -> None:
    value = datetime.now(UTC)

    assert value.tzinfo is not None
