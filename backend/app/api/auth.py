import contextlib
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse

from app.api.auth_dependencies import (
    AuthenticationContext,
    get_authentication_context,
    require_session_csrf,
)
from app.api.registration_dependencies import (
    get_transactional_email_sender,
)
from app.api.security import (
    require_pre_auth_csrf,
    validate_request_origin,
)
from app.auth.authentication import (
    InactiveAccountError,
    InvalidCredentialsError,
    LoginThrottledError,
    log_in_administrator_by_email,
    log_in_user,
    log_in_workspace_spectator,
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
from app.auth.security import InvalidPasswordError
from app.core.settings import Settings, get_settings
from app.db.auth_models import SessionRevocationReason, UserRole
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation
from app.db.session import SessionFactory
from app.notifications.transactional_email import (
    TransactionalEmailDeliveryError,
    TransactionalEmailSender,
)
from app.repositories.auth_repository import (
    AuthenticationRepository,
    normalize_email,
)
from app.repositories.registration_rate_limit_repository import (
    RegistrationRateLimitRepository,
)
from app.schemas.auth import (
    AdministratorAuthenticatedUserResponse,
    AdministratorAuthenticationResponse,
    AdministratorLoginRequest,
    AuthenticatedUserResponse,
    AuthenticationResponse,
    CsrfTokenResponse,
    LoginRequest,
    LogoutResponse,
    PasswordResetCompletionRequest,
    PasswordResetCompletionResponse,
    PasswordResetRequest,
    PasswordResetRequestResponse,
    SpectatorAuthenticatedUserResponse,
    SpectatorAuthenticationResponse,
    SpectatorLoginRequest,
)
from app.services.password_recovery import (
    PasswordRecoveryError,
    PasswordRecoveryStateError,
    issue_password_reset_token,
    reset_administrator_password,
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


@router.post("/admin/login")
def administrator_login(
    payload: AdministratorLoginRequest,
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
) -> AdministratorAuthenticationResponse:
    """Authenticate an administrator by normalized email."""

    with SessionFactory() as session:
        try:
            result = log_in_administrator_by_email(
                session,
                email=payload.email,
                password=payload.password,
                session_lifetime_hours=(settings.session_lifetime_hours),
                ip_address=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                throttle_secret=(settings.app_secret_key.get_secret_value()),
                maximum_attempts=5,
                attempt_window_minutes=(settings.login_attempt_window_minutes),
                lockout_minutes=(settings.login_lockout_minutes),
            )
        except LoginThrottledError as exc:
            session.commit()
            raise HTTPException(
                status_code=(status.HTTP_429_TOO_MANY_REQUESTS),
                detail="Invalid email or password.",
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
                detail="Invalid email or password.",
            ) from exc

        if result.user.workspace_id is None or result.user.email is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password.",
            )

        user_response = AdministratorAuthenticatedUserResponse(
            id=result.user.id,
            workspace_id=result.user.workspace_id,
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

    return AdministratorAuthenticationResponse(
        user=user_response,
        csrf_token=result.csrf_token,
    )


@router.post("/spectator/login")
def spectator_login(
    payload: SpectatorLoginRequest,
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
) -> SpectatorAuthenticationResponse:
    """Create an additional workspace spectator session."""

    with SessionFactory() as session:
        try:
            result = log_in_workspace_spectator(
                session,
                workspace_slug=payload.workspace_slug,
                password=payload.password,
                session_lifetime_hours=(settings.session_lifetime_hours),
                browser_id=(
                    get_login_browser_id(request)
                    or ensure_login_browser_cookie(
                        request,
                        response,
                        settings=settings,
                    )
                ),
                ip_address=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                throttle_secret=(settings.app_secret_key.get_secret_value()),
                maximum_attempts=5,
                attempt_window_minutes=(settings.login_attempt_window_minutes),
                lockout_minutes=(settings.login_lockout_minutes),
            )
        except LoginThrottledError as exc:
            session.commit()

            raise HTTPException(
                status_code=(status.HTTP_429_TOO_MANY_REQUESTS),
                detail=("The workspace or spectator credentials are incorrect."),
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
                detail=("The workspace or spectator credentials are incorrect."),
            ) from exc

        if result.user.workspace_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=("The workspace or spectator credentials are incorrect."),
            )

        workspace = session.get(
            Installation,
            result.user.workspace_id,
        )

        if workspace is None or workspace.slug is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=("The workspace or spectator credentials are incorrect."),
            )

        user_response = SpectatorAuthenticatedUserResponse(
            id=result.user.id,
            workspace_id=result.user.workspace_id,
            workspace_slug=workspace.slug,
            display_name=result.user.display_name,
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

    return SpectatorAuthenticationResponse(
        user=user_response,
        csrf_token=result.csrf_token,
    )


@router.post(
    "/password-reset/request",
    response_model=PasswordResetRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_administrator_password_reset(
    payload: PasswordResetRequest,
    settings: Annotated[
        Settings,
        Depends(get_settings),
    ],
    email_sender: Annotated[
        TransactionalEmailSender,
        Depends(get_transactional_email_sender),
    ],
    _: Annotated[
        None,
        Depends(validate_request_origin),
    ],
    __: Annotated[
        None,
        Depends(require_pre_auth_csrf),
    ],
) -> PasswordResetRequestResponse | JSONResponse:
    """Issue a reset token without disclosing account eligibility."""

    generic_response = PasswordResetRequestResponse(
        detail=(
            "If an eligible administrator account exists for "
            "that email address, password reset instructions "
            "have been sent."
        ),
        code="password_reset_request_accepted",
    )

    normalized_email = normalize_email(payload.email)
    delivery: tuple[str, str, str] | None = None

    with SessionFactory() as session:
        limit = RegistrationRateLimitRepository(
            session,
            secret=settings.app_secret_key.get_secret_value(),
        ).record_request(
            action="password_reset_request",
            identity=normalized_email,
            maximum_requests=(settings.password_reset_maximum_requests),
            window_minutes=(settings.password_reset_window_minutes),
        )

        if not limit.allowed:
            session.commit()

            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={
                    "Retry-After": str(limit.retry_after_seconds),
                },
                content={
                    "detail": ("Password reset is temporarily unavailable. Try again later."),
                    "code": "password_reset_throttled",
                    "retryable": True,
                },
            )

        administrator = AuthenticationRepository(session).get_administrator_by_email(
            normalized_email
        )

        if (
            administrator is not None
            and administrator.role is UserRole.ADMIN
            and administrator.is_active
            and administrator.email is not None
            and administrator.email_verified_at is not None
            and administrator.workspace_id is not None
        ):
            workspace = session.get(
                Installation,
                administrator.workspace_id,
            )

            active_workspace = (
                workspace is not None
                and workspace.status is InstallationStatus.ACTIVE
                and workspace.registration_status
                in (
                    None,
                    WorkspaceRegistrationStatus.ACTIVE,
                )
            )
            resumable_onboarding = (
                workspace is not None
                and workspace.status is not InstallationStatus.ACTIVE
                and workspace.registration_status is WorkspaceRegistrationStatus.PROVISIONAL
                and workspace.onboarding_stage
                not in (
                    None,
                    OnboardingStage.REGISTRATION_PENDING,
                    OnboardingStage.EMAIL_VERIFICATION_PENDING,
                )
            )

            if active_workspace or resumable_onboarding:
                issued = issue_password_reset_token(
                    session,
                    user=administrator,
                    lifetime_minutes=(settings.password_reset_lifetime_minutes),
                )
                delivery = (
                    administrator.email,
                    administrator.display_name,
                    issued.raw_token,
                )

        session.commit()

    if delivery is not None:
        with contextlib.suppress(TransactionalEmailDeliveryError):
            email_sender.send_password_reset(
                recipient_email=delivery[0],
                recipient_display_name=delivery[1],
                password_reset_token=delivery[2],
            )

    return generic_response


@router.post(
    "/password-reset/complete",
    response_model=PasswordResetCompletionResponse,
)
def complete_administrator_password_reset(
    payload: PasswordResetCompletionRequest,
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
) -> PasswordResetCompletionResponse | JSONResponse:
    """Replace the password and revoke every administrator session."""

    del settings

    try:
        with SessionFactory() as session:
            completed = reset_administrator_password(
                session,
                raw_token=payload.token,
                new_password=payload.new_password,
            )
            revoked_sessions = completed.revoked_sessions
            session.commit()
    except PasswordRecoveryError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "detail": ("The password reset link is invalid or expired."),
                "code": "invalid_password_reset",
                "retryable": False,
            },
        )
    except PasswordRecoveryStateError:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": "Password reset is unavailable.",
                "code": "password_reset_unavailable",
                "retryable": False,
            },
        )
    except InvalidPasswordError as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "detail": str(exc),
                "code": "invalid_password",
                "retryable": False,
            },
        )

    return PasswordResetCompletionResponse(
        detail=("Your password has been reset. Sign in again with your administrator email."),
        code="password_reset_completed",
        revoked_sessions=revoked_sessions,
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
            repository.revoke_session(
                stored_session,
                reason=SessionRevocationReason.LOGOUT,
            )
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
