from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.auth.security import (
    generate_csrf_token,
    generate_session_token,
    hash_token,
)
from app.db.auth_models import AuthSession, User


@dataclass(frozen=True, slots=True)
class CreatedSession:
    session: AuthSession
    session_token: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class ResolvedSession:
    session: AuthSession
    user: User


class SessionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_session(
        self,
        *,
        user: User,
        lifetime_hours: int,
        ip_address: str | None = None,
        user_agent: str | None = None,
        now: datetime | None = None,
    ) -> CreatedSession:
        if not user.is_active:
            raise ValueError("Inactive users cannot create sessions.")

        current_time = now or datetime.now(UTC)
        session_token = generate_session_token()
        csrf_token = generate_csrf_token()

        auth_session = AuthSession(
            user_id=user.id,
            session_token_hash=hash_token(session_token),
            csrf_token_hash=hash_token(csrf_token),
            created_at=current_time,
            expires_at=current_time + timedelta(hours=lifetime_hours),
            last_seen_at=current_time,
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

    def resolve_session(
        self,
        raw_session_token: str,
        *,
        now: datetime | None = None,
        update_last_seen: bool = True,
    ) -> ResolvedSession | None:
        try:
            token_hash = hash_token(raw_session_token)
        except ValueError:
            return None

        auth_session = self.session.scalar(
            select(AuthSession).where(AuthSession.session_token_hash == token_hash)
        )

        if auth_session is None:
            return None

        current_time = now or datetime.now(UTC)

        if auth_session.revoked_at is not None:
            return None

        if auth_session.expires_at <= current_time:
            return None

        user = self.session.get(
            User,
            auth_session.user_id,
        )

        if user is None or not user.is_active:
            return None

        if update_last_seen:
            auth_session.last_seen_at = current_time

        return ResolvedSession(
            session=auth_session,
            user=user,
        )

    def revoke_session(
        self,
        auth_session: AuthSession,
        *,
        now: datetime | None = None,
    ) -> None:
        if auth_session.revoked_at is None:
            auth_session.revoked_at = now or datetime.now(UTC)

    def revoke_session_by_token(
        self,
        raw_session_token: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        resolved = self.resolve_session(
            raw_session_token,
            now=now,
            update_last_seen=False,
        )

        if resolved is None:
            return False

        self.revoke_session(
            resolved.session,
            now=now,
        )

        return True

    def revoke_all_user_sessions(
        self,
        *,
        user_id: int,
        now: datetime | None = None,
    ) -> int:
        current_time = now or datetime.now(UTC)

        result = self.session.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=current_time)
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
