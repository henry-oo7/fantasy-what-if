"""Public personal-account registration endpoints."""

import ipaddress
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse

from app.api.security import (
    require_pre_auth_csrf,
    validate_request_origin,
)
from app.auth.cookies import (
    clear_pre_auth_csrf_cookie,
    set_authentication_cookies,
)
from app.auth.identity import can_log_in_by_email, classify_identity
from app.auth.security import (
    InvalidPasswordError,
)
from app.core.client_ip import resolve_client_ip
from app.core.settings import Settings, get_settings
from app.db.platform_enums import (
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation
from app.db.session import SessionFactory
from app.repositories.auth_repository import (
    AuthenticationRepository,
    normalize_email,
)
from app.repositories.registration_rate_limit_repository import (
    RegistrationRateLimitRepository,
)
from app.repositories.session_repository import (
    SessionRepository,
)
from app.schemas.registration import (
    EmailVerificationRequest,
    EmailVerificationResponse,
    RegistrationRequest,
    RegistrationResponse,
    VerificationResendRequest,
    VerificationResendResponse,
)
from app.services.account_navigation import resolve_navigation_state
from app.services.account_registration import (
    DuplicateAccountEmailError,
    InvalidDisplayNameError,
    RegistrationUnavailableError,
    register_personal_account,
)
from app.services.email_verification import (
    EmailVerificationError,
    EmailVerificationStateError,
)
from app.services.email_verification import (
    verify_email as redeem_email_verification,
)
from app.services.identity_email import queue_verification_resend

router = APIRouter(
    prefix="/api/registration",
    tags=["registration"],
)


@router.post(
    "",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "description": "Registration submissions are throttled.",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Registration is unavailable.",
        },
    },
)
def register_account(
    payload: RegistrationRequest,
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
) -> RegistrationResponse | JSONResponse:
    """Create the personal account and queue verification atomically.

    Success means registration was accepted and verification delivery was
    queued. No token is created and no provider is called here.
    """

    with SessionFactory() as session:
        limit = RegistrationRateLimitRepository(
            session,
            secret=settings.app_secret_key.get_secret_value(),
        ).record_request(
            action="registration_submission",
            identity=normalize_request_source(_client_ip(request)),
            maximum_requests=(settings.registration_submission_maximum_requests),
            window_minutes=(settings.registration_submission_window_minutes),
        )
        session.commit()

    if not limit.allowed:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={
                "Retry-After": str(limit.retry_after_seconds),
            },
            content={
                "detail": ("Registration is temporarily unavailable. Try again later."),
                "code": "registration_throttled",
                "retryable": True,
            },
        )

    try:
        with SessionFactory() as session:
            register_personal_account(
                session,
                registration_mode=settings.registration_mode,
                display_name=payload.display_name,
                email=payload.email,
                password=payload.password,
            )
            session.commit()

    except DuplicateAccountEmailError:
        # Enumeration protection: an existing email receives the same
        # external result as a new registration, and no state is created.
        return RegistrationResponse()
    except RegistrationUnavailableError:
        return _application_error(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail="Registration is unavailable.",
            code="registration_unavailable",
            retryable=False,
        )
    except (
        InvalidDisplayNameError,
        InvalidPasswordError,
    ) as exc:
        return _application_error(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(exc),
            code="invalid_registration",
            retryable=False,
        )

    return RegistrationResponse()


def normalize_request_source(
    source: str | None,
) -> str:
    """Return a stable throttle identity for one request source."""

    if source is None or not source.strip():
        return "unknown-source"

    try:
        return ipaddress.ip_address(source.strip()).compressed
    except ValueError:
        return source.strip().casefold()


@router.post(
    "/verify-email",
    response_model=EmailVerificationResponse,
)
def verify_email(
    payload: EmailVerificationRequest,
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
) -> EmailVerificationResponse | JSONResponse:
    """Verify one account's email and create its session."""

    try:
        with SessionFactory() as session:
            user = redeem_email_verification(
                session,
                raw_token=payload.token,
            )

            if user.email is None:
                raise EmailVerificationStateError("The verification identity is incomplete.")

            identity = classify_identity(user)

            if identity is None:
                raise EmailVerificationStateError("The verification identity is incomplete.")

            created = SessionRepository(session).create_session(
                user=user,
                lifetime_hours=settings.session_lifetime_hours,
                ip_address=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )

            result = EmailVerificationResponse(
                display_name=user.display_name,
                email=user.email,
                navigation_state=resolve_navigation_state(
                    session,
                    user=user,
                    identity=identity,
                    purpose=created.session.purpose,
                ),
                csrf_token=created.csrf_token,
            )

            session.commit()

    except EmailVerificationError:
        return _application_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("The email verification link is invalid or expired."),
            code="invalid_email_verification",
            retryable=False,
        )
    except EmailVerificationStateError:
        return _application_error(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email verification is unavailable.",
            code="email_verification_unavailable",
            retryable=False,
        )

    set_authentication_cookies(
        response,
        settings=settings,
        session_token=created.session_token,
        csrf_token=created.csrf_token,
    )
    clear_pre_auth_csrf_cookie(
        response,
        settings=settings,
    )

    return result


@router.post(
    "/resend-verification",
    response_model=VerificationResendResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def resend_verification(
    payload: VerificationResendRequest,
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
) -> VerificationResendResponse | JSONResponse:
    """Queue verification for an eligible account without disclosing it.

    No token is created and no provider is called. Earlier verification
    tokens stay valid until the worker's newer delivery is accepted.
    """

    generic_response = VerificationResendResponse(
        detail=(
            "If an eligible account exists for "
            "that email address, verification instructions "
            "will be sent. Check your email for the next step."
        ),
        code="verification_resend_accepted",
    )

    normalized_email = normalize_email(payload.email)

    with SessionFactory() as session:
        limit = RegistrationRateLimitRepository(
            session,
            secret=settings.app_secret_key.get_secret_value(),
        ).record_request(
            action="verification_resend",
            identity=normalized_email,
            maximum_requests=(settings.verification_resend_maximum_requests),
            window_minutes=(settings.verification_resend_window_minutes),
        )

        if not limit.allowed:
            session.commit()

            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={
                    "Retry-After": str(limit.retry_after_seconds),
                },
                content={
                    "detail": ("Verification resend is temporarily unavailable. Try again later."),
                    "code": "verification_resend_throttled",
                    "retryable": True,
                },
            )

        user = AuthenticationRepository(session).get_user_by_login_email(normalized_email)

        if (
            user is not None
            and can_log_in_by_email(user)
            and user.is_active
            and user.email_verified_at is None
            and user.email is not None
            and _verification_workspace_allows(session, user)
        ):
            queue_verification_resend(session, user=user)

        session.commit()

    return generic_response


def _verification_workspace_allows(session, user) -> bool:
    """A personal account has no workspace to gate verification.

    A legacy administrator may resend only while its workspace still awaits
    email verification.
    """

    if user.workspace_id is None:
        return True

    workspace = session.get(Installation, user.workspace_id)

    return (
        workspace is not None
        and workspace.registration_status is WorkspaceRegistrationStatus.PROVISIONAL
        and workspace.onboarding_stage is OnboardingStage.EMAIL_VERIFICATION_PENDING
    )


def _client_ip(
    request: Request,
) -> str | None:
    return resolve_client_ip(request)


def _application_error(
    *,
    status_code: int,
    detail: str,
    code: str,
    retryable: bool,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": detail,
            "code": code,
            "retryable": retryable,
        },
    )
