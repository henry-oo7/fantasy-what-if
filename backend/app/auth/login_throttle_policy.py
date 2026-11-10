"""Role-aware login throttling policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.auth.login_identity import (
    client_bucket_hash,
)
from app.auth.security import hmac_identifier
from app.db.auth_models import UserRole


class LoginThrottlePolicyKind(StrEnum):
    """Supported login throttle strategies."""

    ADMIN_ACCOUNT = "admin_account"
    CLIENT = "client"


@dataclass(frozen=True, slots=True)
class LoginThrottlePolicy:
    """Resolved throttle policy for one login attempt."""

    kind: LoginThrottlePolicyKind
    bucket_username: str
    bucket_source: str
    delay_schedule_seconds: tuple[
        tuple[int, int],
        ...,
    ]

    def delay_for_failure(
        self,
        failed_attempts: int,
    ) -> int:
        """Return the throttle delay for a failure count."""

        delay = 0

        for threshold, seconds in self.delay_schedule_seconds:
            if failed_attempts >= threshold:
                delay = seconds

        return delay


ADMIN_DELAY_SCHEDULE_SECONDS = (
    (3, 15),
    (4, 60),
    (5, 15 * 60),
)

CLIENT_DELAY_SCHEDULE_SECONDS = (
    (3, 30),
    (4, 2 * 60),
    (5, 15 * 60),
)

SOURCE_SPRAY_MAXIMUM_FAILURES = 20
SOURCE_SPRAY_LOCKOUT_SECONDS = 30 * 60


def resolve_login_throttle_policy(
    *,
    normalized_username: str,
    user_role: UserRole | None,
    browser_id: str,
    source_address: str | None,
    secret: str,
) -> LoginThrottlePolicy:
    """Resolve a role-appropriate privacy-preserving policy."""

    if user_role is UserRole.ADMIN:
        account_bucket = hmac_identifier(
            normalized_username,
            secret=secret,
            namespace="admin-login-account",
        )

        return LoginThrottlePolicy(
            kind=(LoginThrottlePolicyKind.ADMIN_ACCOUNT),
            bucket_username=account_bucket,
            bucket_source="account-wide",
            delay_schedule_seconds=(ADMIN_DELAY_SCHEDULE_SECONDS),
        )

    client_bucket = client_bucket_hash(
        browser_id=browser_id,
        source_address=source_address,
        secret=secret,
    )

    username_bucket = hmac_identifier(
        normalized_username,
        secret=secret,
        namespace="client-login-username",
    )

    return LoginThrottlePolicy(
        kind=LoginThrottlePolicyKind.CLIENT,
        bucket_username=username_bucket,
        bucket_source=client_bucket,
        delay_schedule_seconds=(CLIENT_DELAY_SCHEDULE_SECONDS),
    )


def resolve_source_spray_policy(
    *,
    source_address: str | None,
    secret: str,
) -> LoginThrottlePolicy:
    """Resolve the network-wide password-spraying bucket."""

    from app.auth.login_identity import (
        network_prefix_hash,
    )

    network_bucket = network_prefix_hash(
        source_address,
        secret=secret,
    )

    return LoginThrottlePolicy(
        kind=LoginThrottlePolicyKind.CLIENT,
        bucket_username=hmac_identifier(
            "network-wide-login-spray",
            secret=secret,
            namespace="login-source-policy",
        ),
        bucket_source=network_bucket,
        delay_schedule_seconds=(
            (
                SOURCE_SPRAY_MAXIMUM_FAILURES,
                SOURCE_SPRAY_LOCKOUT_SECONDS,
            ),
        ),
    )
