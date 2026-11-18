from dataclasses import dataclass
from typing import Annotated

from fastapi import (
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)

from app.auth.cookies import (
    get_csrf_cookie,
    get_session_token,
)
from app.auth.csrf import CSRF_HEADER_NAME
from app.auth.security import csrf_tokens_match
from app.core.settings import Settings, get_settings
from app.db.auth_models import AuthSession, User, UserRole
from app.db.session import SessionFactory
from app.repositories.session_repository import (
    ResolvedSession,
    SessionRepository,
)


@dataclass(frozen=True, slots=True)
class AuthenticationContext:
    user_id: int
    workspace_id: int
    role: UserRole
    session_id: int
    user: User
    auth_session: AuthSession


def get_authentication_context(
    request: Request,
    settings: Annotated[
        Settings,
        Depends(get_settings),
    ],
) -> AuthenticationContext:
    session_token = get_session_token(
        request,
        settings=settings,
    )

    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required.",
        )

    with SessionFactory() as database_session:
        repository = SessionRepository(database_session)

        resolved = repository.resolve_session(session_token)

        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication is required.",
            )

        context = build_authentication_context(resolved)

        database_session.commit()

        database_session.expunge(resolved.user)
        database_session.expunge(resolved.session)

        return context


def build_authentication_context(
    resolved: ResolvedSession,
) -> AuthenticationContext:
    workspace_id = resolved.user.workspace_id

    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required.",
        )

    return AuthenticationContext(
        user_id=resolved.user.id,
        workspace_id=workspace_id,
        role=resolved.user.role,
        session_id=resolved.session.id,
        user=resolved.user,
        auth_session=resolved.session,
    )


def get_current_user(
    context: Annotated[
        AuthenticationContext,
        Depends(get_authentication_context),
    ],
) -> User:
    return context.user


def get_current_workspace_id(
    user: Annotated[
        User,
        Depends(get_current_user),
    ],
) -> int:
    workspace_id = user.workspace_id

    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required.",
        )

    return workspace_id


def require_admin_user(
    user: Annotated[
        User,
        Depends(get_current_user),
    ],
) -> User:
    if user.role is not UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access is required.",
        )

    return user


def require_session_csrf(
    request: Request,
    context: Annotated[
        AuthenticationContext,
        Depends(get_authentication_context),
    ],
    settings: Annotated[
        Settings,
        Depends(get_settings),
    ],
    csrf_header: Annotated[
        str | None,
        Header(alias=CSRF_HEADER_NAME),
    ] = None,
) -> AuthenticationContext:
    csrf_cookie = get_csrf_cookie(
        request,
        settings=settings,
    )

    if not csrf_tokens_match(
        csrf_cookie,
        csrf_header,
        context.auth_session.csrf_token_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed.",
        )

    return context
