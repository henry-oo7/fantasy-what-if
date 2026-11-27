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
from app.db.auth_models import AuthSession, AuthSessionPurpose, User, UserRole
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

SESSION_RESTRICTED_CODE = "session_restricted"
SESSION_RESTRICTED_DETAIL = "This session cannot access that resource."

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

    @property
    def session_purpose(self) -> AuthSessionPurpose:
        return self.auth_session.purpose


def resolve_session_authentication_context(
    request: Request,
    settings: Annotated[
        Settings,
        Depends(get_settings),
    ],
) -> AuthenticationContext:
    """Authenticate the presented session whatever its purpose.

    Only the FR-20 deletion-recovery allowlist may depend on this
    directly: ``GET /api/auth/me``, ``POST /api/auth/logout``, and the
    workspace-deletion status and cancellation routes. Every other route
    uses ``get_authentication_context``, which denies restricted sessions.
    """

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


def get_authentication_context(
    context: Annotated[
        AuthenticationContext,
        Depends(resolve_session_authentication_context),
    ],
) -> AuthenticationContext:
    """Authenticate a standard session; deny every restricted purpose.

    This is the single default-deny rule for ``deletion_recovery``
    sessions: every authenticated dependency (current user, workspace,
    administrator, active workspace, and session CSRF) builds on it.
    """

    if context.session_purpose is not AuthSessionPurpose.STANDARD:
        raise WorkspaceAccessError(
            detail=SESSION_RESTRICTED_DETAIL,
            code=SESSION_RESTRICTED_CODE,
        )

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


def get_personal_authentication_context(
    context: Annotated[
        AuthenticationContext,
        Depends(get_authentication_context),
    ],
) -> AuthenticationContext:
    """Standard session of an active, email-verified user, for personal League routes.

    Role and workspace are not consulted: per-Season access rows authorize
    personal reads. Unverified accounts (every spectator) are refused before
    any League Season is looked up, so the refusal reveals nothing about one.
    """

    if context.user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification is required.",
        )

    return context


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


def _validate_session_csrf(
    request: Request,
    context: AuthenticationContext,
    settings: Settings,
    csrf_header: str | None,
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
    """Session CSRF for a standard session (restricted purposes denied)."""

    return _validate_session_csrf(request, context, settings, csrf_header)


def require_any_purpose_session_csrf(
    request: Request,
    context: Annotated[
        AuthenticationContext,
        Depends(resolve_session_authentication_context),
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
    """Session CSRF for the deletion-recovery allowlist only."""

    return _validate_session_csrf(request, context, settings, csrf_header)


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
