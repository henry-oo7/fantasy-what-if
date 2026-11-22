from datetime import UTC, datetime, timedelta

from sqlalchemy import Index

from app.db import model_registry  # noqa: F401
from app.db.auth_models import (
    EmailVerificationToken,
    PasswordResetToken,
    RegistrationRateLimit,
)
from app.db.platform_enums import (
    OnboardingStage,
    RegistrationMode,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation


def index_by_name(model) -> dict[str, Index]:
    return {index.name: index for index in model.__table__.indexes if index.name is not None}


def test_registration_modes_are_stable() -> None:
    assert tuple(mode.value for mode in RegistrationMode) == (
        "open",
        "closed",
        "maintenance",
    )


def test_workspace_registration_statuses_are_stable() -> None:
    assert tuple(status.value for status in WorkspaceRegistrationStatus) == (
        "provisional",
        "active",
        "suspended",
        "deletion_pending",
        "deleted",
    )


def test_onboarding_stages_are_stable() -> None:
    assert tuple(stage.value for stage in OnboardingStage) == (
        "registration_pending",
        "email_verification_pending",
        "provider_setup_pending",
        "league_claim_pending",
        "initial_sync_pending",
        "initial_sync_running",
        "spectator_setup_pending",
        "complete",
        "provider_validation_failed",
        "league_claim_conflict",
        "initial_sync_failed",
    )


def test_installation_has_separate_registration_state() -> None:
    table = Installation.__table__

    assert table.c.registration_status.nullable is True
    assert table.c.onboarding_stage.nullable is True

    indexes = index_by_name(Installation)

    assert "ix_installations_registration_status" in indexes
    assert "ix_installations_onboarding_stage" in indexes


def test_legacy_setup_needs_no_registration_state() -> None:
    installation = Installation(
        application_name="Legacy setup",
        timezone="America/Chicago",
    )

    assert installation.registration_status is None
    assert installation.onboarding_stage is None


def test_provisional_workspace_can_be_constructed() -> None:
    installation = Installation(
        application_name="New workspace",
        timezone="America/Chicago",
        registration_status=(WorkspaceRegistrationStatus.PROVISIONAL),
        onboarding_stage=(OnboardingStage.EMAIL_VERIFICATION_PENDING),
    )

    assert installation.registration_status is (WorkspaceRegistrationStatus.PROVISIONAL)
    assert installation.onboarding_stage is (OnboardingStage.EMAIL_VERIFICATION_PENDING)


def test_email_verification_token_contract() -> None:
    now = datetime.now(UTC)
    token = EmailVerificationToken(
        user_id=1,
        token_hash="a" * 64,
        expires_at=now + timedelta(hours=24),
    )
    table = EmailVerificationToken.__table__

    assert token.token_hash == "a" * 64
    assert table.c.token_hash.type.length == 64
    assert table.c.token_hash.nullable is False
    assert table.c.expires_at.nullable is False
    assert table.c.used_at.nullable is True

    foreign_key = next(iter(table.c.user_id.foreign_keys))

    assert foreign_key.target_fullname == "users.id"
    assert foreign_key.ondelete == "CASCADE"
    assert foreign_key.constraint.name == ("fk_email_verification_tokens_user_id_users")


def test_password_reset_token_contract() -> None:
    now = datetime.now(UTC)
    token = PasswordResetToken(
        user_id=1,
        token_hash="b" * 64,
        expires_at=now + timedelta(minutes=30),
    )
    table = PasswordResetToken.__table__

    assert token.token_hash == "b" * 64
    assert table.c.token_hash.type.length == 64
    assert table.c.token_hash.nullable is False
    assert table.c.expires_at.nullable is False
    assert table.c.used_at.nullable is True

    foreign_key = next(iter(table.c.user_id.foreign_keys))

    assert foreign_key.target_fullname == "users.id"
    assert foreign_key.ondelete == "CASCADE"
    assert foreign_key.constraint.name == ("fk_password_reset_tokens_user_id_users")


def test_registration_rate_limit_contract() -> None:
    table = RegistrationRateLimit.__table__

    assert table.c.action.type.length == 50
    assert table.c.action.nullable is False
    assert table.c.identity_hash.type.length == 64
    assert table.c.identity_hash.nullable is False
    assert table.c.request_count.nullable is False
    assert table.c.request_count.server_default.arg == "0"
    assert table.c.window_started_at.nullable is False
    assert table.c.last_request_at.nullable is False

    constraints = {
        constraint.name: constraint
        for constraint in table.constraints
        if constraint.name is not None
    }

    uniqueness = constraints["uq_registration_rate_limits_action_identity"]

    assert tuple(column.name for column in uniqueness.columns) == (
        "action",
        "identity_hash",
    )

    indexes = index_by_name(RegistrationRateLimit)

    assert "ix_registration_rate_limits_last_request_at" in indexes
