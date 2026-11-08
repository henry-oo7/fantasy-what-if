from collections.abc import Iterator

import pytest

from app.api.auth_dependencies import get_current_user
from app.db.auth_models import User, UserRole
from app.main import app


def create_test_authenticated_user() -> User:
    return User(
        id=999_999,
        username="authenticated-test-user",
        display_name="Authenticated Test User",
        email=None,
        password_hash="$argon2id$test-placeholder",
        role=UserRole.SPECTATOR,
        is_active=True,
    )


@pytest.fixture(autouse=True)
def authenticated_fantasy_routes() -> Iterator[None]:
    app.dependency_overrides[get_current_user] = create_test_authenticated_user

    try:
        yield
    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
