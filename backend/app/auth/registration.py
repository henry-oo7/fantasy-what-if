from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.auth_models import User
from app.repositories.auth_repository import (
    AuthenticationRepository,
    create_team_membership,
)
from app.repositories.invitation_repository import (
    InvitationRepository,
)


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    user: User
    team_id: int
    team_name: str
    season_year: int


def register_invited_user(
    session: Session,
    *,
    invitation_token: str,
    username: str,
    display_name: str,
    password: str,
    email: str | None = None,
) -> RegistrationResult:
    invitation_repository = InvitationRepository(session)
    authentication_repository = AuthenticationRepository(session)

    validated = invitation_repository.validate_invitation(invitation_token)

    effective_display_name = validated.invitation.intended_display_name or display_name

    user = authentication_repository.create_user(
        username=username,
        display_name=effective_display_name,
        email=email,
        password=password,
    )

    create_team_membership(
        session,
        user=user,
        season_id=validated.season.id,
        team_id=validated.team.id,
    )

    invitation_repository.mark_invitation_used(
        validated.invitation,
        user=user,
    )

    session.flush()

    return RegistrationResult(
        user=user,
        team_id=validated.team.espn_team_id,
        team_name=validated.team.name,
        season_year=validated.season.year,
    )
