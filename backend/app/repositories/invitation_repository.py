from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import (
    generate_invitation_token,
    hash_token,
)
from app.db.auth_models import (
    InvitationStatus,
    RegistrationInvite,
    User,
    UserRole,
)
from app.db.models import League, Season, Team


class InviteNotFoundError(ValueError):
    pass


class InviteExpiredError(ValueError):
    pass


class InviteUnavailableError(ValueError):
    pass


class TeamNotFoundError(ValueError):
    pass


class AdminRequiredError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CreatedInvitation:
    invitation: RegistrationInvite
    raw_token: str
    team_name: str
    season_year: int


@dataclass(frozen=True, slots=True)
class ValidatedInvitation:
    invitation: RegistrationInvite
    team: Team
    season: Season


class InvitationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_invitation(
        self,
        *,
        admin_user: User,
        espn_league_id: int,
        season_year: int,
        espn_team_id: int,
        lifetime_hours: int,
        intended_display_name: str | None = None,
    ) -> CreatedInvitation:
        if admin_user.role != UserRole.ADMIN or not admin_user.is_active:
            raise AdminRequiredError("An active administrator is required.")

        season = self.session.scalar(
            select(Season)
            .join(
                League,
                Season.league_id == League.id,
            )
            .where(
                League.espn_league_id == espn_league_id,
                Season.year == season_year,
            )
        )

        if season is None:
            raise TeamNotFoundError("The requested league season was not found.")

        team = self.session.scalar(
            select(Team).where(
                Team.season_id == season.id,
                Team.espn_team_id == espn_team_id,
            )
        )

        if team is None:
            raise TeamNotFoundError(f"Team {espn_team_id} was not found for season {season_year}.")

        normalized_display_name = (
            " ".join(intended_display_name.strip().split()) if intended_display_name else None
        )

        raw_token = generate_invitation_token()
        now = datetime.now(UTC)

        invitation = RegistrationInvite(
            token_hash=hash_token(raw_token),
            season_id=season.id,
            team_id=team.id,
            created_by_user_id=admin_user.id,
            intended_display_name=normalized_display_name,
            status=InvitationStatus.ACTIVE,
            created_at=now,
            expires_at=now + timedelta(hours=lifetime_hours),
        )

        self.session.add(invitation)
        self.session.flush()

        return CreatedInvitation(
            invitation=invitation,
            raw_token=raw_token,
            team_name=team.name,
            season_year=season.year,
        )

    def validate_invitation(
        self,
        raw_token: str,
        *,
        now: datetime | None = None,
    ) -> ValidatedInvitation:
        token_hash = hash_token(raw_token)

        invitation = self.session.scalar(
            select(RegistrationInvite).where(RegistrationInvite.token_hash == token_hash)
        )

        if invitation is None:
            raise InviteNotFoundError("Registration invitation was not found.")

        current_time = now or datetime.now(UTC)

        if invitation.status != InvitationStatus.ACTIVE:
            raise InviteUnavailableError("Registration invitation is no longer active.")

        if invitation.expires_at <= current_time:
            raise InviteExpiredError("Registration invitation has expired.")

        team = self.session.get(
            Team,
            invitation.team_id,
        )
        season = self.session.get(
            Season,
            invitation.season_id,
        )

        if team is None or season is None:
            raise InviteUnavailableError(
                "Registration invitation references unavailable league data."
            )

        return ValidatedInvitation(
            invitation=invitation,
            team=team,
            season=season,
        )

    def mark_invitation_used(
        self,
        invitation: RegistrationInvite,
        *,
        user: User,
    ) -> None:
        if invitation.status != InvitationStatus.ACTIVE:
            raise InviteUnavailableError("Registration invitation is no longer active.")

        invitation.status = InvitationStatus.USED
        invitation.used_at = datetime.now(UTC)
        invitation.used_by_user_id = user.id

    def revoke_invitation(
        self,
        invitation: RegistrationInvite,
    ) -> None:
        if invitation.status != InvitationStatus.ACTIVE:
            raise InviteUnavailableError("Only active invitations can be revoked.")

        invitation.status = InvitationStatus.REVOKED
        invitation.revoked_at = datetime.now(UTC)

    def get_invitation_by_id(
        self,
        invitation_id: int,
    ) -> RegistrationInvite | None:
        return self.session.get(
            RegistrationInvite,
            invitation_id,
        )

    def list_active_invitations(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[RegistrationInvite, ...]:
        current_time = now or datetime.now(UTC)

        invitations = self.session.scalars(
            select(RegistrationInvite)
            .where(
                RegistrationInvite.status == InvitationStatus.ACTIVE,
                RegistrationInvite.expires_at > current_time,
            )
            .order_by(
                RegistrationInvite.expires_at,
                RegistrationInvite.id,
            )
        ).all()

        return tuple(invitations)
