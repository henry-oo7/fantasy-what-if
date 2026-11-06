from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Request,
    Response,
    status,
)
from sqlalchemy import select

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
    log_in_user,
)
from app.auth.cookies import (
    clear_authentication_cookies,
    clear_pre_auth_csrf_cookie,
    set_authentication_cookies,
    set_pre_auth_csrf_cookie,
)
from app.auth.csrf import (
    generate_pre_auth_csrf_token,
)
from app.auth.registration import register_invited_user
from app.auth.security import (
    InvalidPasswordError,
    InvalidUsernameError,
)
from app.core.settings import Settings, get_settings
from app.db.auth_models import UserTeamMembership
from app.db.models import Season, Team
from app.db.session import SessionFactory
from app.repositories.auth_repository import (
    DuplicateEmailError,
    DuplicateTeamMembershipError,
    DuplicateUsernameError,
)
from app.repositories.invitation_repository import (
    InvitationRepository,
    InviteExpiredError,
    InviteNotFoundError,
    InviteUnavailableError,
)
from app.schemas.auth import (
    AuthenticatedUserResponse,
    AuthenticationResponse,
    CsrfTokenResponse,
    InviteValidationResponse,
    LoginRequest,
    LogoutResponse,
    RegistrationRequest,
)

router = APIRouter(
    prefix="/api/auth",
    tags=["authentication"],
)


@router.get("/csrf")
def issue_pre_auth_csrf_token(
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

    return CsrfTokenResponse(csrf_token=csrf_token)


@router.get("/invitations/{token}")
def validate_registration_invitation(
    token: Annotated[
        str,
        Path(min_length=20, max_length=500),
    ],
) -> InviteValidationResponse:
    try:
        with SessionFactory() as session:
            repository = InvitationRepository(session)
            validated = repository.validate_invitation(token)

            return InviteValidationResponse(
                valid=True,
                team_id=validated.team.espn_team_id,
                team_name=validated.team.name,
                season=validated.season.year,
                intended_display_name=(validated.invitation.intended_display_name),
                expires_at=(validated.invitation.expires_at.isoformat()),
            )

    except (
        InviteNotFoundError,
        InviteExpiredError,
        InviteUnavailableError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
)
def register_member(
    payload: RegistrationRequest,
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
    try:
        with SessionFactory() as session:
            registration = register_invited_user(
                session,
                invitation_token=(payload.invitation_token),
                username=payload.username,
                display_name=payload.display_name,
                email=(str(payload.email) if payload.email is not None else None),
                password=payload.password,
            )

            login = log_in_user(
                session,
                username=registration.user.username,
                password=payload.password,
                session_lifetime_hours=(settings.session_lifetime_hours),
                ip_address=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )

            session.commit()

            user_response = AuthenticatedUserResponse(
                id=login.user.id,
                username=login.user.username,
                display_name=(login.user.display_name),
                email=login.user.email,
                role=login.user.role.value,
                is_active=login.user.is_active,
                team_id=registration.team_id,
                team_name=registration.team_name,
                season=registration.season_year,
            )

            set_authentication_cookies(
                response,
                settings=settings,
                session_token=login.session_token,
                csrf_token=login.csrf_token,
            )
            clear_pre_auth_csrf_cookie(
                response,
                settings=settings,
            )

            return AuthenticationResponse(
                user=user_response,
                csrf_token=login.csrf_token,
            )

    except (
        InviteNotFoundError,
        InviteExpiredError,
        InviteUnavailableError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except (
        DuplicateUsernameError,
        DuplicateEmailError,
        DuplicateTeamMembershipError,
        InvalidUsernameError,
        InvalidPasswordError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


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
    try:
        with SessionFactory() as session:
            result = log_in_user(
                session,
                username=payload.username,
                password=payload.password,
                session_lifetime_hours=(settings.session_lifetime_hours),
                ip_address=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )

            user_response = _build_user_response(
                session,
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

    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        ) from exc
    except InactiveAccountError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is inactive.",
        ) from exc


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
    with SessionFactory() as session:
        return _build_user_response(
            session,
            user_id=context.user.id,
            username=context.user.username,
            display_name=context.user.display_name,
            email=context.user.email,
            role=context.user.role.value,
            is_active=context.user.is_active,
        )


def _build_user_response(
    session,
    *,
    user_id: int,
    username: str,
    display_name: str,
    email: str | None,
    role: str,
    is_active: bool,
) -> AuthenticatedUserResponse:
    row = session.execute(
        select(
            UserTeamMembership,
            Team,
            Season,
        )
        .join(
            Team,
            UserTeamMembership.team_id == Team.id,
        )
        .join(
            Season,
            UserTeamMembership.season_id == Season.id,
        )
        .where(UserTeamMembership.user_id == user_id)
        .order_by(Season.year.desc())
        .limit(1)
    ).first()

    if row is None:
        return AuthenticatedUserResponse(
            id=user_id,
            username=username,
            display_name=display_name,
            email=email,
            role=role,
            is_active=is_active,
        )

    _, team, season = row

    return AuthenticatedUserResponse(
        id=user_id,
        username=username,
        display_name=display_name,
        email=email,
        role=role,
        is_active=is_active,
        team_id=team.espn_team_id,
        team_name=team.name,
        season=season.year,
    )


def _client_ip(
    request: Request,
) -> str | None:
    if request.client is None:
        return None

    return request.client.host
