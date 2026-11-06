import pytest
from sqlalchemy.orm import Session

from app.auth.security import verify_password
from app.db.auth_models import UserRole
from app.db.session import engine
from app.repositories.auth_repository import (
    AuthenticationRepository,
    DuplicateEmailError,
    DuplicateUsernameError,
)


@pytest.fixture
def database_session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
    )

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def test_create_user_stores_normalized_values(
    database_session: Session,
) -> None:
    repository = AuthenticationRepository(database_session)

    user = repository.create_user(
        username="  Test.Admin  ",
        display_name="  Test   Administrator  ",
        email="  TEST.ADMIN@EXAMPLE.COM  ",
        password="correct horse battery staple",
        role=UserRole.ADMIN,
    )

    assert user.id is not None
    assert user.username == "test.admin"
    assert user.display_name == "Test Administrator"
    assert user.email == "test.admin@example.com"
    assert user.role == UserRole.ADMIN
    assert user.is_active is True
    assert user.password_hash.startswith("$argon2")
    assert verify_password(
        "correct horse battery staple",
        user.password_hash,
    )


def test_get_user_by_username_is_normalized(
    database_session: Session,
) -> None:
    repository = AuthenticationRepository(database_session)

    created = repository.create_user(
        username="lookup-user",
        display_name="Lookup User",
        password="correct horse battery staple",
    )

    found = repository.get_user_by_username("  LOOKUP-USER  ")

    assert found is not None
    assert found.id == created.id


def test_duplicate_username_is_rejected(
    database_session: Session,
) -> None:
    repository = AuthenticationRepository(database_session)

    repository.create_user(
        username="duplicate-user",
        display_name="First User",
        password="correct horse battery staple",
    )

    with pytest.raises(
        DuplicateUsernameError,
        match="already registered",
    ):
        repository.create_user(
            username="DUPLICATE-USER",
            display_name="Second User",
            password="correct horse battery staple",
        )


def test_duplicate_email_is_rejected(
    database_session: Session,
) -> None:
    repository = AuthenticationRepository(database_session)

    repository.create_user(
        username="first-email-user",
        display_name="First User",
        email="shared@example.com",
        password="correct horse battery staple",
    )

    with pytest.raises(
        DuplicateEmailError,
        match="already registered",
    ):
        repository.create_user(
            username="second-email-user",
            display_name="Second User",
            email="SHARED@EXAMPLE.COM",
            password="correct horse battery staple",
        )


def test_user_can_be_deactivated(
    database_session: Session,
) -> None:
    repository = AuthenticationRepository(database_session)

    user = repository.create_user(
        username="inactive-user",
        display_name="Inactive User",
        password="correct horse battery staple",
    )

    repository.deactivate_user(user)

    assert user.is_active is False
