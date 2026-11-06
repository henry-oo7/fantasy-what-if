from fastapi import Request, Response

from app.core.settings import Settings


def csrf_cookie_name(
    settings: Settings,
) -> str:
    if settings.is_production:
        return "__Host-fantasy_csrf"

    return settings.session_cookie_name + "_csrf"


def set_authentication_cookies(
    response: Response,
    *,
    settings: Settings,
    session_token: str,
    csrf_token: str,
) -> None:
    max_age = settings.session_lifetime_hours * 60 * 60

    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )

    response.set_cookie(
        key=csrf_cookie_name(settings),
        value=csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def clear_authentication_cookies(
    response: Response,
    *,
    settings: Settings,
) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )

    response.delete_cookie(
        key=csrf_cookie_name(settings),
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def get_session_token(
    request: Request,
    *,
    settings: Settings,
) -> str | None:
    return request.cookies.get(settings.session_cookie_name)


def get_csrf_cookie(
    request: Request,
    *,
    settings: Settings,
) -> str | None:
    return request.cookies.get(csrf_cookie_name(settings))


def set_pre_auth_csrf_cookie(
    response: Response,
    *,
    settings: Settings,
    csrf_token: str,
) -> None:
    response.set_cookie(
        key="fantasy_preauth_csrf",
        value=csrf_token,
        max_age=60 * 60,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def clear_pre_auth_csrf_cookie(
    response: Response,
    *,
    settings: Settings,
) -> None:
    response.delete_cookie(
        key="fantasy_preauth_csrf",
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def get_pre_auth_csrf_cookie(
    request: Request,
) -> str | None:
    return request.cookies.get("fantasy_preauth_csrf")
