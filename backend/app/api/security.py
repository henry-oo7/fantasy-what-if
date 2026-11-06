from typing import Annotated

from fastapi import (
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)

from app.auth.cookies import (
    get_pre_auth_csrf_cookie,
)
from app.auth.csrf import (
    CSRF_HEADER_NAME,
    validate_double_submit_csrf,
)
from app.core.settings import (
    Settings,
    get_settings,
)


def validate_request_origin(
    request: Request,
    settings: Annotated[
        Settings,
        Depends(get_settings),
    ],
) -> None:
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")

    if origin is not None:
        normalized_origin = origin.rstrip("/")

        if normalized_origin not in settings.trusted_origin_list:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Request origin is not allowed.",
            )

        return

    if referer is not None:
        if any(
            referer.startswith(origin + "/") or referer == origin
            for origin in settings.trusted_origin_list
        ):
            return

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Request origin is not allowed.",
        )

    if settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Request origin is required.",
        )


def require_pre_auth_csrf(
    request: Request,
    settings: Annotated[
        Settings,
        Depends(get_settings),
    ],
    csrf_header: Annotated[
        str | None,
        Header(
            alias=CSRF_HEADER_NAME,
        ),
    ] = None,
) -> None:
    cookie_token = get_pre_auth_csrf_cookie(request)

    if not validate_double_submit_csrf(
        cookie_token=cookie_token,
        header_token=csrf_header,
        settings=settings,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed.",
        )
