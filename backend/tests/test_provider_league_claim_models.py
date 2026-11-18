from sqlalchemy import CheckConstraint, UniqueConstraint

from app.db import model_registry  # noqa: F401
from app.db.platform_models import ProviderLeagueClaim


def test_provider_league_claim_fields() -> None:
    table = ProviderLeagueClaim.__table__

    assert table.c.workspace_id.nullable is False
    assert table.c.league_id.nullable is True
    assert table.c.provider.nullable is False
    assert table.c.provider_league_id.nullable is False
    assert table.c.provider_league_id.type.length == 255
    assert table.c.normalized_provider_league_id.nullable is False
    assert table.c.normalized_provider_league_id.type.length == 255


def test_provider_league_claim_foreign_keys() -> None:
    table = ProviderLeagueClaim.__table__

    workspace_key = next(iter(table.c.workspace_id.foreign_keys))
    league_key = next(iter(table.c.league_id.foreign_keys))

    assert workspace_key.target_fullname == "installations.id"
    assert workspace_key.ondelete == "CASCADE"
    assert workspace_key.constraint.name == ("fk_provider_league_claims_workspace_id_installations")

    assert league_key.target_fullname == "leagues.id"
    assert league_key.ondelete == "CASCADE"
    assert league_key.constraint.name == "fk_provider_league_claims_league_id_leagues"


def test_provider_league_claim_uniqueness() -> None:
    constraints = {
        constraint.name: constraint
        for constraint in (ProviderLeagueClaim.__table__.constraints)
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert tuple(
        column.name for column in constraints["uq_provider_league_claims_workspace_id"].columns
    ) == ("workspace_id",)

    assert tuple(
        column.name for column in constraints["uq_provider_league_claims_league_id"].columns
    ) == ("league_id",)

    assert tuple(
        column.name for column in constraints["uq_provider_league_claims_provider_identity"].columns
    ) == (
        "provider",
        "normalized_provider_league_id",
    )


def test_provider_league_claim_normalization_checks() -> None:
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in (ProviderLeagueClaim.__table__.constraints)
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert (
        "provider_league_id = "
        "btrim(provider_league_id)" in checks["ck_provider_league_claims_trimmed_id"]
    )
    assert (
        "normalized_provider_league_id = "
        "btrim(provider_league_id)" in checks["ck_provider_league_claims_normalized_id"]
    )
