from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)

from app.api.auth_dependencies import (
    AuthenticationContext,
    get_authentication_context,
    require_session_csrf,
)
from app.api.security import (
    require_pre_auth_csrf,
    validate_request_origin,
)
from app.auth.authentication import (
    InactiveAccountError,
    InvalidCredentialsError,
    LoginThrottledError,
    log_in_user,
)
from app.auth.cookies import (
    clear_authentication_cookies,
    clear_pre_auth_csrf_cookie,
    ensure_login_browser_cookie,
    get_login_browser_id,
    set_authentication_cookies,
    set_pre_auth_csrf_cookie,
)
from app.auth.csrf import (
    generate_pre_auth_csrf_token,
)
from app.core.settings import Settings, get_settings
from app.db.session import SessionFactory
from app.schemas.auth import (
    AuthenticatedUserResponse,
    AuthenticationResponse,
    CsrfTokenResponse,
    LoginRequest,
    LogoutResponse,
)

router = APIRouter(
    prefix="/api/auth",
    tags=["authentication"],
)


@router.get("/csrf")
def issue_pre_auth_csrf_token(
    request: Request,
    response: Response,
    settings: Annotated[
        Settings,
        Depends(get_settings),
    ],
) -> CsrfTokenResponse:
    csrf_token = generate_pre_auth_csrf_token(settings)

    set_pre_auth_csrf_cookie(
        response,
        settings=settings,
        csrf_token=csrf_token,
    )
    ensure_login_browser_cookie(
        request,
        response,
        settings=settings,
    )

    return CsrfTokenResponse(csrf_token=csrf_token)


@router.post("/login")
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    settings: Annotated[
        Settings,
        Depends(get_settings),
    ],
    _: Annotated[
        None,
        Depends(validate_request_origin),
    ],
    __: Annotated[
        None,
        Depends(require_pre_auth_csrf),
    ],
) -> AuthenticationResponse:
    with SessionFactory() as session:
        try:
            result = log_in_user(
                session,
                username=payload.username,
                password=payload.password,
                session_lifetime_hours=(settings.session_lifetime_hours),
                ip_address=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                throttle_secret=(settings.app_secret_key.get_secret_value()),
                browser_id=(
                    get_login_browser_id(request)
                    or ensure_login_browser_cookie(
                        request,
                        response,
                        settings=settings,
                    )
                ),
                maximum_attempts=5,
                attempt_window_minutes=(settings.login_attempt_window_minutes),
                lockout_minutes=(settings.login_lockout_minutes),
            )
        except LoginThrottledError as exc:
            session.commit()

            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Invalid username or password.",
                headers={
                    "Retry-After": str(exc.retry_after_seconds),
                },
            ) from exc
        except (
            InvalidCredentialsError,
            InactiveAccountError,
        ) as exc:
            session.commit()

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password.",
            ) from exc

        user_response = _build_user_response(
            user_id=result.user.id,
            username=result.user.username,
            display_name=result.user.display_name,
            email=result.user.email,
            role=result.user.role.value,
            is_active=result.user.is_active,
        )

        session.commit()

        set_authentication_cookies(
            response,
            settings=settings,
            session_token=result.session_token,
            csrf_token=result.csrf_token,
        )
        clear_pre_auth_csrf_cookie(
            response,
            settings=settings,
        )

        return AuthenticationResponse(
            user=user_response,
            csrf_token=result.csrf_token,
        )


@router.post("/logout")
def logout(
    response: Response,
    settings: Annotated[
        Settings,
        Depends(get_settings),
    ],
    context: Annotated[
        AuthenticationContext,
        Depends(require_session_csrf),
    ],
    _: Annotated[
        None,
        Depends(validate_request_origin),
    ],
) -> LogoutResponse:
    with SessionFactory() as session:
        stored_session = session.get(
            type(context.auth_session),
            context.auth_session.id,
        )

        if stored_session is not None:
            from app.repositories.session_repository import (
                SessionRepository,
            )

            repository = SessionRepository(session)
            repository.revoke_session(stored_session)
            session.commit()

    clear_authentication_cookies(
        response,
        settings=settings,
    )

    return LogoutResponse(logged_out=True)


@router.get("/me")
def read_current_user(
    context: Annotated[
        AuthenticationContext,
        Depends(get_authentication_context),
    ],
) -> AuthenticatedUserResponse:
    return _build_user_response(
        user_id=context.user.id,
        username=context.user.username,
        display_name=context.user.display_name,
        email=context.user.email,
        role=context.user.role.value,
        is_active=context.user.is_active,
    )


def _build_user_response(
    *,
    user_id: int,
    username: str,
    display_name: str,
    email: str | None,
    role: str,
    is_active: bool,
) -> AuthenticatedUserResponse:
    """Return role-based account identity without team ownership."""

    return AuthenticatedUserResponse(
        id=user_id,
        username=username,
        display_name=display_name,
        email=email,
        role=role,
        is_active=is_active,
    )


def _client_ip(
    request: Request,
) -> str | None:
    if request.client is None:
        return None

    return request.client.host
