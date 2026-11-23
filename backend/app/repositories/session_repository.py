from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.auth.security import (
    generate_csrf_token,
    generate_session_token,
    hash_token,
)
from app.db.auth_models import (
    AuthSession,
    AuthSessionPurpose,
    SessionRevocationReason,
    User,
)


@dataclass(frozen=True, slots=True)
class CreatedSession:
    session: AuthSession
    session_token: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class ResolvedSession:
    session: AuthSession
    user: User


class SessionResolutionFailure(StrEnum):
    """Why a presented session token did not authenticate.

    ``INVALID`` covers every case that must stay indistinguishable to the
    client: missing, malformed, unknown, expired, otherwise revoked, or
    belonging to an inactive or missing user.
    """

    INVALID = "invalid"
    REPLACED = "replaced"


@dataclass(frozen=True, slots=True)
class SessionResolution:
    """Outcome of resolving one presented session token."""

    resolved: ResolvedSession | None
    failure: SessionResolutionFailure | None

    @classmethod
    def authenticated(cls, resolved: ResolvedSession) -> SessionResolution:
        return cls(resolved=resolved, failure=None)

    @classmethod
    def rejected(cls, failure: SessionResolutionFailure) -> SessionResolution:
        return cls(resolved=None, failure=failure)


class SessionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_session(
        self,
        *,
        user: User,
        lifetime_hours: int | None = None,
        lifetime: timedelta | None = None,
        purpose: AuthSessionPurpose = AuthSessionPurpose.STANDARD,
        ip_address: str | None = None,
        user_agent: str | None = None,
        now: datetime | None = None,
    ) -> CreatedSession:
        """Create one session with exactly one lifetime specification.

        ``lifetime`` supports sub-hour sessions such as the fixed 15-minute
        ``deletion_recovery`` session. Sessions are never renewed: the
        expiration chosen here is final.
        """

        if not user.is_active:
            raise ValueError("Inactive users cannot create sessions.")

        session_lifetime = _resolve_lifetime(
            lifetime_hours=lifetime_hours,
            lifetime=lifetime,
        )

        current_time = now or datetime.now(UTC)
        session_token = generate_session_token()
        csrf_token = generate_csrf_token()

        auth_session = AuthSession(
            user_id=user.id,
            session_token_hash=hash_token(session_token),
            csrf_token_hash=hash_token(csrf_token),
            created_at=current_time,
            expires_at=current_time + session_lifetime,
            last_seen_at=current_time,
            purpose=purpose,
            ip_address=_normalize_ip_address(ip_address),
            user_agent=_normalize_user_agent(user_agent),
        )

        self.session.add(auth_session)
        self.session.flush()

        return CreatedSession(
            session=auth_session,
            session_token=session_token,
            csrf_token=csrf_token,
        )

    def replace_user_sessions(
        self,
        *,
        user: User,
        lifetime_hours: int | None = None,
        lifetime: timedelta | None = None,
        purpose: AuthSessionPurpose = AuthSessionPurpose.STANDARD,
        ip_address: str | None = None,
        user_agent: str | None = None,
        now: datetime | None = None,
    ) -> CreatedSession:
        """Atomically make a new session the user's only active session.

        The user row is locked with ``SELECT ... FOR UPDATE`` before
        revocation so concurrent replacements serialize. Under READ
        COMMITTED, the revocation statement of a waiting transaction sees
        sessions committed by the transaction that held the lock, so at most
        one session remains active. Displaced sessions are revoked with the
        ``replaced`` reason.
        """

        self.session.execute(select(User.id).where(User.id == user.id).with_for_update())

        self.revoke_all_user_sessions(
            user_id=user.id,
            reason=SessionRevocationReason.REPLACED,
            now=now,
        )

        return self.create_session(
            user=user,
            lifetime_hours=lifetime_hours,
            lifetime=lifetime,
            purpose=purpose,
            ip_address=ip_address,
            user_agent=user_agent,
            now=now,
        )

    def resolve_session_state(
        self,
        raw_session_token: str,
        *,
        now: datetime | None = None,
        update_last_seen: bool = True,
    ) -> SessionResolution:
        """Resolve a token and classify why it failed, when it failed.

        ``REPLACED`` is reported only for a session explicitly revoked with
        the ``replaced`` reason that has not expired and whose user still
        exists and is active. Every other failure is ``INVALID``.
        """

        invalid = SessionResolution.rejected(SessionResolutionFailure.INVALID)

        try:
            token_hash = hash_token(raw_session_token)
        except ValueError:
            return invalid

        auth_session = self.session.scalar(
            select(AuthSession).where(AuthSession.session_token_hash == token_hash)
        )

        if auth_session is None:
            return invalid

        current_time = now or datetime.now(UTC)

        if auth_session.expires_at <= current_time:
            return invalid

        user = self.session.get(
            User,
            auth_session.user_id,
        )

        if user is None or not user.is_active:
            return invalid

        if auth_session.revoked_at is not None:
            if auth_session.revocation_reason is SessionRevocationReason.REPLACED:
                return SessionResolution.rejected(SessionResolutionFailure.REPLACED)

            return invalid

        if update_last_seen:
            auth_session.last_seen_at = current_time

        return SessionResolution.authenticated(
            ResolvedSession(
                session=auth_session,
                user=user,
            )
        )

    def resolve_session(
        self,
        raw_session_token: str,
        *,
        now: datetime | None = None,
        update_last_seen: bool = True,
    ) -> ResolvedSession | None:
        """Return the active session, or ``None`` for any failure."""

        return self.resolve_session_state(
            raw_session_token,
            now=now,
            update_last_seen=update_last_seen,
        ).resolved

    def revoke_session(
        self,
        auth_session: AuthSession,
        *,
        reason: SessionRevocationReason,
        now: datetime | None = None,
    ) -> bool:
        """Revoke one session if it is still active.

        The database enforces first-reason-wins: the conditional update
        matches only while ``revoked_at IS NULL``, so a session already
        revoked by a concurrent transaction keeps its original reason even
        when ``auth_session`` holds stale in-memory state.
        """

        result = self.session.execute(
            update(AuthSession)
            .where(
                AuthSession.id == auth_session.id,
                AuthSession.revoked_at.is_(None),
            )
            .values(
                revoked_at=now or datetime.now(UTC),
                revocation_reason=reason,
            )
            .execution_options(synchronize_session="fetch")
        )

        return bool(result.rowcount)

    def revoke_session_by_token(
        self,
        raw_session_token: str,
        *,
        reason: SessionRevocationReason,
        now: datetime | None = None,
    ) -> bool:
        resolved = self.resolve_session(
            raw_session_token,
            now=now,
            update_last_seen=False,
        )

        if resolved is None:
            return False

        return self.revoke_session(
            resolved.session,
            reason=reason,
            now=now,
        )

    def revoke_all_user_sessions(
        self,
        *,
        user_id: int,
        reason: SessionRevocationReason,
        now: datetime | None = None,
    ) -> int:
        """Revoke every active session of one user with an explicit reason.

        Already-revoked sessions keep their original timestamp and reason.
        """

        current_time = now or datetime.now(UTC)

        result = self.session.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(
                revoked_at=current_time,
                revocation_reason=reason,
            )
            .execution_options(synchronize_session="fetch")
        )

        return int(result.rowcount or 0)

    def revoke_all_workspace_sessions(
        self,
        *,
        workspace_id: int,
        reason: SessionRevocationReason,
        now: datetime | None = None,
    ) -> int:
        """Revoke every active session of every user in one workspace.

        Covers the administrator and the shared spectator, whatever the
        session purpose. Already-revoked sessions keep their original
        timestamp and reason.
        """

        current_time = now or datetime.now(UTC)
        workspace_user_ids = select(User.id).where(User.workspace_id == workspace_id)

        result = self.session.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id.in_(workspace_user_ids),
                AuthSession.revoked_at.is_(None),
            )
            .values(
                revoked_at=current_time,
                revocation_reason=reason,
            )
            .execution_options(synchronize_session="fetch")
        )

        return int(result.rowcount or 0)

    def delete_expired_sessions(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        current_time = now or datetime.now(UTC)

        result = self.session.execute(
            delete(AuthSession).where(AuthSession.expires_at <= current_time)
        )

        return int(result.rowcount or 0)


def _resolve_lifetime(
    *,
    lifetime_hours: int | None,
    lifetime: timedelta | None,
) -> timedelta:
    if (lifetime_hours is None) == (lifetime is None):
        raise ValueError("Specify exactly one of lifetime_hours or lifetime.")

    resolved = lifetime if lifetime is not None else timedelta(hours=lifetime_hours or 0)

    if resolved <= timedelta(0):
        raise ValueError("Session lifetime must be positive.")

    return resolved


def _normalize_ip_address(
    ip_address: str | None,
) -> str | None:
    if ip_address is None:
        return None

    normalized = ip_address.strip()

    if not normalized:
        return None

    return normalized[:45]


def _normalize_user_agent(
    user_agent: str | None,
) -> str | None:
    if user_agent is None:
        return None

    normalized = user_agent.strip()

    if not normalized:
        return None

    return normalized[:500]
