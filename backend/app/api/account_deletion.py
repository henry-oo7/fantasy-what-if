"""Personal account deletion route (ADR 0010, FR-17).

A standard personal session only: restricted purposes are denied by
``require_session_csrf``, and legacy workspace users, spectators and
unverified accounts get the same single refusal as every other state.
There is no GET, no status route and no recovery route.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from app.api.auth_dependencies import AuthenticationContext, require_session_csrf
from app.api.destructive_throttle import record_destructive_attempt
from app.api.security import validate_request_origin
from app.auth.cookies import clear_authentication_cookies
from app.core.client_ip import resolve_client_ip
from app.core.settings import Settings, get_settings
from app.db.session import SessionFactory
from app.notifications.recipient_encryption import TransactionalEmailRecipientCipher
from app.schemas.account_deletion import AccountDeletedResponse, AccountDeletionRequest
from app.services.account_deletion import (
    ACCOUNT_DELETION_ATTEMPT_WINDOW_MINUTES,
    ACCOUNT_DELETION_MAXIMUM_ATTEMPTS,
    ACCOUNT_DELETION_REQUEST_ACTION,
    AccountDeletionConfirmationError,
    AccountDeletionError,
    AccountDeletionNoticeUnavailableError,
    AccountDeletionPasswordError,
    delete_personal_account,
)

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api/account", tags=["account"])


def _refusal(status_code: int, detail: str, code: str, *, retryable: bool) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "code": code, "retryable": retryable},
    )


@router.post("/deletion", response_model=AccountDeletedResponse)
def delete_account(
    payload: AccountDeletionRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[AuthenticationContext, Depends(require_session_csrf)],
    _: Annotated[None, Depends(validate_request_origin)],
) -> JSONResponse:
    """Delete the signed-in personal account immediately after password and phrase checks."""

    throttled = record_destructive_attempt(
        settings,
        action=ACCOUNT_DELETION_REQUEST_ACTION,
        user_id=context.user_id,
        source=resolve_client_ip(request),
        maximum_requests=ACCOUNT_DELETION_MAXIMUM_ATTEMPTS,
        window_minutes=ACCOUNT_DELETION_ATTEMPT_WINDOW_MINUTES,
        code="account_deletion_throttled",
    )

    if throttled is not None:
        return throttled

    try:
        with SessionFactory() as session:
            delete_personal_account(
                session,
                user_id=context.user_id,
                auth_session_id=context.session_id,
                current_password=payload.current_password,
                confirmation=payload.confirmation,
                recipient_cipher_factory=lambda: TransactionalEmailRecipientCipher(
                    settings.transactional_email_keyring()
                ),
            )
            session.commit()
    except AccountDeletionPasswordError as exc:
        return _refusal(
            status.HTTP_400_BAD_REQUEST,
            "The current password is incorrect.",
            exc.code,
            retryable=True,
        )
    except AccountDeletionConfirmationError as exc:
        return _refusal(
            status.HTTP_400_BAD_REQUEST,
            "Type the confirmation phrase exactly as shown.",
            exc.code,
            retryable=True,
        )
    except AccountDeletionNoticeUnavailableError as exc:
        return _refusal(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Account deletion could not be completed. Nothing was deleted. Try again later.",
            exc.code,
            retryable=True,
        )
    except AccountDeletionError as exc:
        return _refusal(
            status.HTTP_409_CONFLICT,
            "Account deletion is unavailable.",
            exc.code,
            retryable=False,
        )

    response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content=AccountDeletedResponse(
            detail="Your account was deleted. You have been signed out."
        ).model_dump(mode="json"),
    )
    clear_authentication_cookies(response, settings=settings)

    return response
