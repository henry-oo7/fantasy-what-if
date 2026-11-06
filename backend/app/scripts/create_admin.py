from getpass import getpass

from app.auth.security import (
    InvalidPasswordError,
    InvalidUsernameError,
)
from app.db.auth_models import UserRole
from app.db.session import SessionFactory
from app.repositories.auth_repository import (
    AuthenticationRepository,
    DuplicateEmailError,
    DuplicateUsernameError,
)


def prompt_required(
    label: str,
) -> str:
    while True:
        value = input(label).strip()

        if value:
            return value

        print("A value is required.")


def prompt_optional(
    label: str,
) -> str | None:
    value = input(label).strip()

    return value or None


def prompt_password() -> str:
    while True:
        password = getpass("Password: ")
        confirmation = getpass("Confirm password: ")

        if password != confirmation:
            print("Passwords do not match.")
            continue

        return password


def main() -> None:
    print("=== CREATE ADMINISTRATOR ===")
    print("The password will not be displayed or written to shell history.")
    print()

    username = prompt_required("Username: ")
    display_name = prompt_required("Display name: ")
    email = prompt_optional("Email address, optional: ")
    password = prompt_password()

    try:
        with SessionFactory() as session:
            repository = AuthenticationRepository(session)

            user = repository.create_user(
                username=username,
                display_name=display_name,
                email=email,
                password=password,
                role=UserRole.ADMIN,
            )

            session.commit()

            print()
            print("=== ADMINISTRATOR CREATED ===")
            print(f"User ID: {user.id}")
            print(f"Username: {user.username}")
            print(f"Display name: {user.display_name}")
            print(f"Email configured: {user.email is not None}")
            print(f"Role: {user.role.value}")
            print(f"Active: {user.is_active}")
            print(f"Password hash stored: {user.password_hash.startswith('$argon2')}")
            print("Password and hash intentionally hidden.")

    except (
        DuplicateUsernameError,
        DuplicateEmailError,
        InvalidUsernameError,
        InvalidPasswordError,
        ValueError,
    ) as exc:
        raise SystemExit(f"Unable to create administrator: {exc}") from None


if __name__ == "__main__":
    main()
