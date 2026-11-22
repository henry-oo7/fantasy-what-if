"""Public administrator registration endpoints."""

import contextlib
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

from app.api.registration_dependencies import (
    get_transactional_email_sender,
)
from app.api.security import (
    require_pre_auth_csrf,
    validate_request_origin,
)
from app.auth.cookies import (
    clear_pre_auth_csrf_cookie,
    set_authentication_cookies,
)
from app.auth.security import (
    InvalidPasswordError,
)
from app.core.settings import Settings, get_settings
from app.db.auth_models import UserRole
from app.db.platform_enums import (
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
from app.repositories.session_repository import (
    SessionRepository,
)
from app.schemas.registration import (
    AdministratorRegistrationRequest,
    AdministratorRegistrationResponse,
    EmailVerificationRequest,
    EmailVerificationResponse,
    VerificationResendRequest,
    VerificationResendResponse,
)
from app.services.administrator_registration import (
    DuplicateAdministratorEmailError,
    InvalidAdministratorDisplayNameError,
    InvalidWorkspaceNameError,
    InvalidWorkspaceSlugError,
    RegistrationConflictError,
    RegistrationUnavailableError,
    normalize_workspace_slug,
    register_provisional_administrator,
)
from app.services.email_verification import (
    EmailVerificationError,
    EmailVerificationStateError,
    issue_email_verification_token,
    verify_administrator_email,
)

router = APIRouter(
    prefix="/api/registration",
    tags=["registration"],
)


@router.post(
    "",
    response_model=AdministratorRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Workspace slug conflict.",
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "description": "Registration submissions are throttled.",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Registration or email is unavailable.",
        },
    },
)
def register_administrator(
    payload: AdministratorRegistrationRequest,
    request: Request,
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
) -> AdministratorRegistrationResponse | JSONResponse:
    """Create provisional identity, commit, then deliver verification."""

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
            result = register_provisional_administrator(
                session,
                registration_mode=settings.registration_mode,
                workspace_name=payload.workspace_name,
                workspace_slug=payload.workspace_slug,
                administrator_display_name=(payload.display_name),
                administrator_email=payload.email,
                administrator_password=payload.password,
                verification_lifetime_hours=(settings.email_verification_lifetime_hours),
            )

            recipient_email = result.administrator.email
            recipient_display_name = result.administrator.display_name
            verification_token = result.verification.raw_token
            workspace_slug = result.workspace.slug

            if recipient_email is None or workspace_slug is None:
                raise RuntimeError("Registration persisted invalid identity state.")

            response = _accepted_registration_response(workspace_slug)

            session.commit()

    except DuplicateAdministratorEmailError:
        # Enumeration protection: an existing administrator email receives
        # the same external result as a new registration, and no state is
        # created. Informational email to the owner is deferred.
        return _accepted_registration_response(normalize_workspace_slug(payload.workspace_slug))
    except RegistrationUnavailableError:
        return _application_error(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail="Registration is unavailable.",
            code="registration_unavailable",
            retryable=False,
        )
    except RegistrationConflictError as exc:
        return _application_error(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            code="registration_conflict",
            retryable=False,
        )
    except (
        InvalidWorkspaceNameError,
        InvalidWorkspaceSlugError,
        InvalidAdministratorDisplayNameError,
        InvalidPasswordError,
    ) as exc:
        return _application_error(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(exc),
            code="invalid_registration",
            retryable=False,
        )

    try:
        email_sender.send_email_verification(
            recipient_email=recipient_email,
            recipient_display_name=(recipient_display_name),
            verification_token=verification_token,
        )
    except TransactionalEmailDeliveryError:
        return _application_error(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=(
                "Registration was saved, but the verification "
                "email could not be delivered. Request another "
                "verification email to continue."
            ),
            code="email_delivery_unavailable",
            retryable=True,
        )

    return response


def _accepted_registration_response(
    workspace_slug: str,
) -> AdministratorRegistrationResponse:
    return AdministratorRegistrationResponse(
        workspace_slug=workspace_slug,
        registration_status=(WorkspaceRegistrationStatus.PROVISIONAL.value),
        onboarding_stage=(OnboardingStage.EMAIL_VERIFICATION_PENDING.value),
        verification_required=True,
    )


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
    """Verify one administrator and create the onboarding session."""

    try:
        with SessionFactory() as session:
            administrator = verify_administrator_email(
                session,
                raw_token=payload.token,
            )

            if administrator.workspace_id is None or administrator.email is None:
                raise EmailVerificationStateError("The verification identity is incomplete.")

            workspace = session.get(
                Installation,
                administrator.workspace_id,
            )

            if (
                workspace is None
                or workspace.slug is None
                or workspace.registration_status is None
                or workspace.onboarding_stage is None
            ):
                raise EmailVerificationStateError("The verification workspace is incomplete.")

            created = SessionRepository(session).replace_user_sessions(
                user=administrator,
                lifetime_hours=settings.session_lifetime_hours,
                ip_address=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )

            result = EmailVerificationResponse(
                workspace_slug=workspace.slug,
                display_name=administrator.display_name,
                email=administrator.email,
                role="admin",
                registration_status=(workspace.registration_status.value),
                onboarding_stage=workspace.onboarding_stage.value,
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
) -> VerificationResendResponse | JSONResponse:
    """Replace an eligible token without disclosing account existence."""

    generic_response = VerificationResendResponse(
        detail=(
            "If an eligible administrator account exists for "
            "that email address, verification instructions "
            "have been sent."
        ),
        code="verification_resend_accepted",
    )

    normalized_email = normalize_email(payload.email)
    delivery: tuple[str, str, str] | None = None

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

        administrator = AuthenticationRepository(session).get_administrator_by_email(
            normalized_email
        )

        if (
            administrator is not None
            and administrator.role is UserRole.ADMIN
            and administrator.email_verified_at is None
            and administrator.email is not None
            and administrator.workspace_id is not None
        ):
            workspace = session.get(
                Installation,
                administrator.workspace_id,
            )

            if (
                workspace is not None
                and workspace.registration_status is WorkspaceRegistrationStatus.PROVISIONAL
                and workspace.onboarding_stage is OnboardingStage.EMAIL_VERIFICATION_PENDING
            ):
                issued = issue_email_verification_token(
                    session,
                    user=administrator,
                    lifetime_hours=(settings.email_verification_lifetime_hours),
                )
                delivery = (
                    administrator.email,
                    administrator.display_name,
                    issued.raw_token,
                )

        session.commit()

    if delivery is not None:
        with contextlib.suppress(TransactionalEmailDeliveryError):
            email_sender.send_email_verification(
                recipient_email=delivery[0],
                recipient_display_name=delivery[1],
                verification_token=delivery[2],
            )

    return generic_response


def _client_ip(
    request: Request,
) -> str | None:
    if request.client is None:
        return None

    return request.client.host


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
