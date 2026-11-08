from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.security import (
    hash_password,
    normalize_username,
)
from app.db.auth_models import (
    User,
    UserRole,
)


class DuplicateUsernameError(ValueError):
    pass


class DuplicateEmailError(ValueError):
    pass


class AuthenticationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_user_by_id(
        self,
        user_id: int,
    ) -> User | None:
        return self.session.get(User, user_id)

    def get_user_by_username(
        self,
        username: str,
    ) -> User | None:
        normalized_username = normalize_username(username)

        return self.session.scalar(select(User).where(User.username == normalized_username))

    def get_user_by_email(
        self,
        email: str,
    ) -> User | None:
        normalized_email = normalize_email(email)

        return self.session.scalar(select(User).where(func.lower(User.email) == normalized_email))

    def username_exists(
        self,
        username: str,
    ) -> bool:
        normalized_username = normalize_username(username)

        return (
            self.session.scalar(select(User.id).where(User.username == normalized_username))
            is not None
        )

    def email_exists(
        self,
        email: str,
    ) -> bool:
        normalized_email = normalize_email(email)

        return (
            self.session.scalar(select(User.id).where(func.lower(User.email) == normalized_email))
            is not None
        )

    def create_user(
        self,
        *,
        username: str,
        display_name: str,
        password: str,
        email: str | None = None,
        role: UserRole = UserRole.SPECTATOR,
        is_active: bool = True,
    ) -> User:
        normalized_username = normalize_username(username)
        normalized_display_name = normalize_display_name(display_name)
        normalized_email = normalize_email(email) if email is not None else None

        if self.username_exists(normalized_username):
            raise DuplicateUsernameError("That username is already registered.")

        if normalized_email is not None and self.email_exists(normalized_email):
            raise DuplicateEmailError("That email address is already registered.")

        user = User(
            username=normalized_username,
            display_name=normalized_display_name,
            email=normalized_email,
            password_hash=hash_password(password),
            role=role,
            is_active=is_active,
        )

        self.session.add(user)
        self.session.flush()

        return user

    def record_successful_login(
        self,
        user: User,
    ) -> None:
        user.last_login_at = datetime.now(UTC)

    def deactivate_user(
        self,
        user: User,
    ) -> None:
        user.is_active = False


def normalize_email(
    email: str,
) -> str:
    normalized = email.strip().casefold()

    if not normalized:
        raise ValueError("Email address cannot be empty.")

    return normalized


def normalize_display_name(
    display_name: str,
) -> str:
    normalized = " ".join(display_name.strip().split())

    if not normalized:
        raise ValueError("Display name cannot be empty.")

    if len(normalized) > 100:
        raise ValueError("Display name cannot exceed 100 characters.")

    return normalized
