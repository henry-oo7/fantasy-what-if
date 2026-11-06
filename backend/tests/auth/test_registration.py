from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.registration import (
    register_invited_user,
)
from app.auth.security import (
    generate_invitation_token,
    hash_token,
    verify_password,
)
from app.db.auth_models import (
    InvitationStatus,
    RegistrationInvite,
    User,
    UserRole,
    UserTeamMembership,
)
from app.db.models import League, Season, Team
from app.db.session import engine
from app.repositories.invitation_repository import (
    InviteExpiredError,
    InviteUnavailableError,
)


@pytest.fixture
def database_session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
    )

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def get_season_and_team(
    session: Session,
) -> tuple[Season, Team]:
    season = session.scalar(
        select(Season)
        .join(
            League,
            Season.league_id == League.id,
        )
        .where(
            League.espn_league_id == 529250970,
            Season.year == 2026,
        )
    )

    assert season is not None

    team = session.scalar(
        select(Team).where(
            Team.season_id == season.id,
            Team.espn_team_id == 1,
        )
    )

    assert team is not None

    return season, team


def create_invitation(
    session: Session,
    *,
    intended_display_name: str | None = None,
    status: InvitationStatus = InvitationStatus.ACTIVE,
    expired: bool = False,
) -> tuple[RegistrationInvite, str]:
    season, team = get_season_and_team(session)
    raw_token = generate_invitation_token()
    now = datetime.now(UTC)

    invitation = RegistrationInvite(
        token_hash=hash_token(raw_token),
        season_id=season.id,
        team_id=team.id,
        intended_display_name=intended_display_name,
        status=status,
        created_at=now,
        expires_at=(now - timedelta(hours=1) if expired else now + timedelta(hours=1)),
    )

    session.add(invitation)
    session.flush()

    return invitation, raw_token


def test_registration_creates_user_and_membership(
    database_session: Session,
) -> None:
    invitation, raw_token = create_invitation(database_session)

    result = register_invited_user(
        database_session,
        invitation_token=raw_token,
        username="new-member",
        display_name="New Member",
        email="new-member@example.com",
        password="correct horse battery staple",
    )

    assert result.user.id is not None
    assert result.user.username == "new-member"
    assert result.user.display_name == "New Member"
    assert result.user.role == UserRole.MEMBER
    assert verify_password(
        "correct horse battery staple",
        result.user.password_hash,
    )

    membership = database_session.scalar(
        select(UserTeamMembership).where(UserTeamMembership.user_id == result.user.id)
    )

    assert membership is not None
    assert membership.team_id == invitation.team_id
    assert membership.season_id == invitation.season_id

    assert invitation.status == InvitationStatus.USED
    assert invitation.used_by_user_id == result.user.id
    assert invitation.used_at is not None


def test_invitation_display_name_is_authoritative(
    database_session: Session,
) -> None:
    _, raw_token = create_invitation(
        database_session,
        intended_display_name="Invited League Mate",
    )

    result = register_invited_user(
        database_session,
        invitation_token=raw_token,
        username="invited-member",
        display_name="Different Name",
        password="correct horse battery staple",
    )

    assert result.user.display_name == "Invited League Mate"


def test_used_invitation_cannot_be_reused(
    database_session: Session,
) -> None:
    _, raw_token = create_invitation(database_session)

    register_invited_user(
        database_session,
        invitation_token=raw_token,
        username="first-member",
        display_name="First Member",
        password="correct horse battery staple",
    )

    with pytest.raises(
        InviteUnavailableError,
        match="no longer active",
    ):
        register_invited_user(
            database_session,
            invitation_token=raw_token,
            username="second-member",
            display_name="Second Member",
            password="correct horse battery staple",
        )


def test_expired_invitation_is_rejected(
    database_session: Session,
) -> None:
    _, raw_token = create_invitation(
        database_session,
        expired=True,
    )

    with pytest.raises(
        InviteExpiredError,
        match="expired",
    ):
        register_invited_user(
            database_session,
            invitation_token=raw_token,
            username="expired-member",
            display_name="Expired Member",
            password="correct horse battery staple",
        )

    user = database_session.scalar(select(User).where(User.username == "expired-member"))

    assert user is None


def test_registration_can_be_rolled_back_atomically(
    database_session: Session,
) -> None:
    invitation, raw_token = create_invitation(database_session)

    savepoint = database_session.begin_nested()

    result = register_invited_user(
        database_session,
        invitation_token=raw_token,
        username="rollback-member",
        display_name="Rollback Member",
        password="correct horse battery staple",
    )

    user_id = result.user.id

    savepoint.rollback()
    database_session.expire_all()

    user = database_session.get(User, user_id)

    assert user is None

    database_session.refresh(invitation)

    assert invitation.status == InvitationStatus.ACTIVE
    assert invitation.used_by_user_id is None
    assert invitation.used_at is None
