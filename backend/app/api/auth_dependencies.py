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
from app.db.platform_models import Installation
from app.db.session import SessionFactory
from app.repositories.session_repository import (
    ResolvedSession,
    SessionRepository,
    SessionResolutionFailure,
)
from app.services.workspace_lifecycle import (
    WorkspaceAccess,
    resolve_workspace_access,
)

SESSION_REPLACED_CODE = "session_replaced"
SESSION_REPLACED_DETAIL = (
    "This administrator account was signed in from another device. Sign in again to continue."
)


class SessionReplacedError(RuntimeError):
    """The presented administrator session was replaced by a newer login.

    The response discloses only the stable code and safe message, never
    the replacing device, address, user agent, browser, or location.
    """

    def __init__(self) -> None:
        self.detail = SESSION_REPLACED_DETAIL
        self.code = SESSION_REPLACED_CODE
        super().__init__(SESSION_REPLACED_DETAIL)


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

        resolution = repository.resolve_session_state(session_token)
        resolved = resolution.resolved

        if resolution.failure is SessionResolutionFailure.REPLACED:
            raise SessionReplacedError()

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


class WorkspaceAccessError(RuntimeError):
    """Authenticated workspace access is not permitted."""

    def __init__(
        self,
        *,
        detail: str,
        code: str,
    ) -> None:
        self.detail = detail
        self.code = code
        super().__init__(detail)


def require_active_workspace(
    user: Annotated[
        User,
        Depends(get_current_user),
    ],
) -> int:
    """Require an active workspace for ordinary application access."""

    workspace_id = user.workspace_id

    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required.",
        )

    with SessionFactory() as session:
        workspace = session.get(
            Installation,
            workspace_id,
        )

    access = resolve_workspace_access(workspace, user.role)

    if access is WorkspaceAccess.SUSPENDED:
        raise WorkspaceAccessError(
            detail="Workspace access is unavailable.",
            code="workspace_suspended",
        )

    if workspace is None or access is not WorkspaceAccess.ACTIVE:
        raise WorkspaceAccessError(
            detail="Workspace access is unavailable.",
            code="workspace_inactive",
        )

    return workspace.id
