import argparse

from sqlalchemy import select

from app.core.settings import get_settings
from app.db.auth_models import User, UserRole
from app.db.models import League, Season, Team
from app.db.session import SessionFactory
from app.repositories.invitation_repository import (
    AdminRequiredError,
    InvitationRepository,
    TeamNotFoundError,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Create a one-time registration invitation for an ESPN fantasy team.")
    )

    parser.add_argument(
        "--admin",
        required=True,
        help="Username of the administrator creating the invite.",
    )
    parser.add_argument(
        "--team-id",
        type=int,
        help=("ESPN fantasy team ID. Omit to list available teams."),
    )
    parser.add_argument(
        "--display-name",
        help="Optional intended display name for the invitee.",
    )

    return parser.parse_args()


def list_teams(
    *,
    espn_league_id: int,
    season_year: int,
) -> None:
    with SessionFactory() as session:
        rows = (
            session.execute(
                select(Team)
                .join(
                    Season,
                    Team.season_id == Season.id,
                )
                .join(
                    League,
                    Season.league_id == League.id,
                )
                .where(
                    League.espn_league_id == espn_league_id,
                    Season.year == season_year,
                )
                .order_by(Team.espn_team_id)
            )
            .scalars()
            .all()
        )

    print("=== AVAILABLE TEAMS ===")

    for team in rows:
        print(f"{team.espn_team_id:>2}: {team.name}")


def main() -> None:
    arguments = parse_arguments()
    settings = get_settings()

    if arguments.team_id is None:
        list_teams(
            espn_league_id=settings.espn_league_id,
            season_year=settings.espn_season,
        )
        print()
        print("Run the command again with --team-id <number>.")
        return

    try:
        with SessionFactory() as session:
            admin = session.scalar(
                select(User).where(User.username == arguments.admin.strip().casefold())
            )

            if admin is None:
                raise AdminRequiredError("Administrator account was not found.")

            if admin.role != UserRole.ADMIN:
                raise AdminRequiredError("The selected account is not an administrator.")

            repository = InvitationRepository(session)

            created = repository.create_invitation(
                admin_user=admin,
                espn_league_id=settings.espn_league_id,
                season_year=settings.espn_season,
                espn_team_id=arguments.team_id,
                lifetime_hours=(settings.invite_lifetime_hours),
                intended_display_name=(arguments.display_name),
            )

            session.commit()

            registration_url = (
                settings.frontend_app_url.rstrip("/") + "/register?invite=" + created.raw_token
            )

            print("=== REGISTRATION INVITATION CREATED ===")
            print(f"Invitation ID: {created.invitation.id}")
            print(f"Team: {created.team_name}")
            print(f"Season: {created.season_year}")
            print(f"Intended display name: {created.invitation.intended_display_name}")
            print(f"Expires at: {created.invitation.expires_at.isoformat()}")
            print()
            print("Registration URL:")
            print(registration_url)
            print()
            print("The token is shown only now. Send it privately to the league mate.")
            print("Only the token hash is stored in PostgreSQL.")

    except (
        AdminRequiredError,
        TeamNotFoundError,
        ValueError,
    ) as exc:
        raise SystemExit(f"Unable to create invitation: {exc}") from None


if __name__ == "__main__":
    main()
