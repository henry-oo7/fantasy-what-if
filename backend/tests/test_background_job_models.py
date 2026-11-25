from sqlalchemy import CheckConstraint, Index

from app.db import model_registry  # noqa: F401
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
)
from app.db.platform_models import BackgroundJob


def indexes_by_name() -> dict[str, Index]:
    return {
        index.name: index for index in BackgroundJob.__table__.indexes if index.name is not None
    }


def checks_by_name() -> dict[str, CheckConstraint]:
    return {
        constraint.name: constraint
        for constraint in BackgroundJob.__table__.constraints
        if (
            isinstance(
                constraint,
                CheckConstraint,
            )
            and constraint.name is not None
        )
    }


def test_background_job_enum_values_are_stable() -> None:
    assert tuple(status.value for status in BackgroundJobStatus) == (
        "pending",
        "running",
        "retry_scheduled",
        "succeeded",
        "failed",
        "canceled",
    )

    assert tuple(job_type.value for job_type in BackgroundJobType) == (
        "initial_workspace_sync",
        "incremental_season_sync",
        "full_season_sync",
        "historical_season_import",
        "provider_credential_validation",
        "provider_token_refresh",
        "provisional_workspace_cleanup",
        "expired_session_cleanup",
        "expired_token_cleanup",
        "workspace_deletion",
        "transactional_email_delivery",
        "league_season_sync",
    )


def test_background_job_workspace_ownership() -> None:
    table = BackgroundJob.__table__

    # Nullable only for unscoped transactional-email jobs; a CHECK guards it.
    assert table.c.workspace_id.nullable is True

    foreign_key = next(iter(table.c.workspace_id.foreign_keys))

    assert foreign_key.target_fullname == "installations.id"
    assert foreign_key.ondelete == "CASCADE"
    assert foreign_key.constraint.name == ("fk_background_jobs_workspace_id_installations")


def test_background_job_payload_and_attempt_fields() -> None:
    table = BackgroundJob.__table__

    assert table.c.payload.nullable is False
    assert table.c.progress.nullable is True
    assert table.c.payload_version.nullable is False
    assert table.c.attempt_count.nullable is False
    assert table.c.maximum_attempts.nullable is False

    assert table.c.payload_version.server_default.arg == "1"
    assert table.c.attempt_count.server_default.arg == "0"
    assert table.c.maximum_attempts.server_default.arg == "5"


def test_background_job_active_idempotency_index() -> None:
    index = indexes_by_name()["uq_background_jobs_active_idempotency_key"]

    assert index.unique is True
    assert tuple(column.name for column in index.columns) == ("idempotency_key",)

    predicate = str(index.dialect_options["postgresql"]["where"])

    assert "idempotency_key IS NOT NULL" in predicate
    assert "'pending'::background_job_status" in predicate
    assert "'running'::background_job_status" in predicate
    assert "'retry_scheduled'::background_job_status" in predicate
    assert "'succeeded'" not in predicate
    assert "'failed'" not in predicate
    assert "'canceled'" not in predicate


def test_background_job_queue_order_index() -> None:
    index = indexes_by_name()["ix_background_jobs_queue_order"]

    expressions = tuple(str(expression) for expression in index.expressions)

    assert expressions == (
        "background_jobs.status",
        "priority DESC",
        "background_jobs.available_at",
        "background_jobs.id",
    )


def test_background_job_state_checks_exist() -> None:
    checks = checks_by_name()

    assert {
        "ck_background_jobs_payload_version_positive",
        "ck_background_jobs_attempt_count_nonnegative",
        "ck_background_jobs_maximum_attempts_positive",
        "ck_background_jobs_attempt_limit",
        "ck_background_jobs_idempotency_key_trimmed",
        "ck_background_jobs_idempotency_key_nonempty",
        "ck_background_jobs_running_claim",
        "ck_background_jobs_succeeded_timestamp",
        "ck_background_jobs_failed_timestamp",
        "ck_background_jobs_canceled_timestamp",
    }.issubset(checks)
