"""Shared synthetic fixtures for transactional-email worker and token tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.db import model_registry  # noqa: F401
from app.db.auth_models import User, UserRole
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation
from app.db.session import engine
from app.notifications.email_provider import (
    EmailAcceptance,
    EmailProviderError,
    OutgoingEmail,
)

SYNTHETIC_API_KEY = "re_synthetic_worker_key_0123456789"
PUBLIC_BASE_URL = "https://fantasy.synthetic.invalid"
SENDER = "notifications@synthetic.invalid"


class TrackedSession(Session):
    """Session that counts how many are open, to prove no transaction spans HTTP."""

    open_count = 0

    def __enter__(self) -> TrackedSession:
        TrackedSession.open_count += 1
        return super().__enter__()

    def __exit__(self, *args: object) -> None:
        TrackedSession.open_count -= 1
        super().__exit__(*args)


def isolated_factory() -> Iterator[sessionmaker[Session]]:
    """Sessions share one connection; commits release savepoints and the
    outer transaction is always rolled back."""

    connection = engine.connect()
    transaction = connection.begin()
    TrackedSession.open_count = 0
    factory = sessionmaker(
        bind=connection,
        class_=TrackedSession,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield factory
    finally:
        if transaction.is_active:
            transaction.rollback()

        connection.close()


def email_settings(
    *,
    enabled: bool = True,
    keys: str | None = None,
    **overrides: Any,
) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "app_debug": True,
        "app_secret_key": SecretStr("x" * 48),
        "frontend_origin": "http://localhost:5173",
        "trusted_origins": "http://localhost:5173",
        "session_cookie_name": "fantasy_session",
        "session_cookie_secure": False,
        "database_url": "postgresql+psycopg://user:password@database:5432/unused",
        "transactional_email_enabled": enabled,
        "transactional_email_provider": "resend",
        "transactional_email_from_address": SENDER,
        "transactional_email_from_name": "Fantasy What-If",
        "transactional_email_public_base_url": PUBLIC_BASE_URL,
        "transactional_email_api_key": SecretStr(SYNTHETIC_API_KEY),
        "transactional_email_keys": SecretStr(keys) if keys else None,
        "transactional_email_current_key_version": 1,
        "email_verification_lifetime_hours": 24,
        "password_reset_lifetime_minutes": 30,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def generated_keyring_json() -> str:
    return json.dumps({"1": Fernet.generate_key().decode("ascii")})


def make_workspace(session: Session, *, provisional: bool = False) -> Installation:
    slug = f"mail-{uuid4().hex[:12]}"
    workspace = Installation(
        application_name="Email Worker",
        slug=slug,
        normalized_slug=slug,
        status=InstallationStatus.ACTIVE,
        registration_status=(
            WorkspaceRegistrationStatus.PROVISIONAL
            if provisional
            else WorkspaceRegistrationStatus.ACTIVE
        ),
        onboarding_stage=(
            OnboardingStage.EMAIL_VERIFICATION_PENDING if provisional else OnboardingStage.COMPLETE
        ),
    )
    session.add(workspace)
    session.flush()
    return workspace


def make_admin(
    session: Session,
    workspace: Installation,
    *,
    verified: bool = False,
) -> User:
    local = f"owner-{uuid4().hex[:10]}"
    address = f"{local}@synthetic.invalid"
    user = User(
        username=f"admin-{uuid4().hex[:12]}",
        display_name="Synthetic Owner",
        workspace_id=workspace.id,
        email=address,
        normalized_email=address,
        email_verified_at=datetime.now(UTC) - timedelta(days=1) if verified else None,
        password_hash="$argon2id$synthetic",
        role=UserRole.ADMIN,
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


Outcome = EmailAcceptance | EmailProviderError | Callable[[OutgoingEmail], EmailAcceptance]


@dataclass
class FakeTransport:
    """Scripted transport; every call is recorded in memory only."""

    outcomes: list[Outcome] = field(default_factory=list)
    sent: list[OutgoingEmail] = field(default_factory=list)
    closed: int = 0

    def send(self, message: OutgoingEmail) -> EmailAcceptance:
        self.sent.append(message)

        if not self.outcomes:
            raise AssertionError("FakeTransport received an unscripted send.")

        outcome = self.outcomes.pop(0)

        if isinstance(outcome, EmailProviderError):
            raise outcome

        if isinstance(outcome, EmailAcceptance):
            return outcome

        return outcome(message)

    def close(self) -> None:
        self.closed += 1

    def factory(self, _settings: Settings) -> FakeTransport:
        return self


def token_from(message: OutgoingEmail) -> str:
    marker = "?token="
    start = message.text_body.index(marker) + len(marker)
    return message.text_body[start:].split()[0]
