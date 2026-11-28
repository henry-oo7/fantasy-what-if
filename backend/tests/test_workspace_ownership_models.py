from sqlalchemy import Index

from app.db import model_registry  # noqa: F401
from app.db.auth_models import User
from app.db.platform_models import Installation


def index_by_name(
    model: type[User] | type[Installation],
) -> dict[str, Index]:
    return {index.name: index for index in model.__table__.indexes if index.name is not None}


def test_installation_has_workspace_slug_fields() -> None:
    table = Installation.__table__

    assert table.c.slug.nullable is True
    assert table.c.slug.type.length == 50
    assert table.c.normalized_slug.nullable is True
    assert table.c.normalized_slug.type.length == 50

    indexes = index_by_name(Installation)
    slug_index = indexes["uq_installations_normalized_slug"]

    assert slug_index.unique is True
    assert tuple(column.name for column in slug_index.columns) == ("normalized_slug",)


def test_user_has_workspace_ownership_fields() -> None:
    table = User.__table__

    assert table.c.workspace_id.nullable is True
    assert table.c.normalized_email.nullable is True
    assert table.c.normalized_email.type.length == 320
    assert table.c.email_verified_at.nullable is True

    foreign_key = next(iter(table.c.workspace_id.foreign_keys))

    assert foreign_key.target_fullname == "installations.id"
    assert foreign_key.ondelete == "CASCADE"
    assert foreign_key.constraint.name == "fk_users_workspace_id_installations"


def test_user_workspace_cardinality_indexes() -> None:
    indexes = index_by_name(User)

    admin_index = indexes["uq_users_one_admin_per_workspace"]
    spectator_index = indexes["uq_users_one_spectator_per_workspace"]

    assert admin_index.unique is True
    assert spectator_index.unique is True

    assert tuple(column.name for column in admin_index.columns) == ("workspace_id",)
    assert tuple(column.name for column in spectator_index.columns) == ("workspace_id",)

    admin_predicate = str(admin_index.dialect_options["postgresql"]["where"])
    spectator_predicate = str(spectator_index.dialect_options["postgresql"]["where"])

    assert "role = 'admin'::user_role" in admin_predicate
    assert "role = 'spectator'::user_role" in spectator_predicate


def test_normalized_email_is_globally_unique() -> None:
    indexes = index_by_name(User)
    email_index = indexes["uq_users_normalized_email"]

    assert email_index.unique is True
    assert tuple(column.name for column in email_index.columns) == ("normalized_email",)

    predicate = str(email_index.dialect_options["postgresql"]["where"])

    assert "role" not in predicate
    assert "normalized_email IS NOT NULL" in predicate
