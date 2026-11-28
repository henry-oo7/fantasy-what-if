"""Critical personal "Remove League from my account" behavior (ADR 0010).

Real PostgreSQL (``fantasy_what_if_test``) only; providers are the in-process
``FakeEspn`` transport and every user, credential and League is synthetic. The
race is ordered by a hook that runs while the League row lock is held and by
lock observation in ``pg_stat_activity``, never by sleeps.
"""

# ruff: noqa: F811  (pytest fixtures are imported and then requested by parameter name)

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, select, update

from app.db.auth_models import AuthSession
from app.db.league_access_models import UserLeagueSeasonAccess, UserPreference
from app.db.models import League, Season
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    FantasyProvider,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
)
from app.db.platform_models import (
    BackgroundJob,
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
    TransactionalEmailDelivery,
)
from app.db.session import SessionFactory
from app.repositories.league_access_repository import LeagueAccessRepository
from app.services.access_reverification import enqueue_access_reverification, reverify_access
from app.services.league_removal import (
    LeagueRemovalConfirmationError,
    LeagueRemovalUnavailableError,
    remove_league_access,
)
from tests.support.league_import import (  # noqa: F401
    OTHER_S2,
    Caller,
    FakeEspn,
    espn,
    forbid_live_network,
    import_state,
    league_id,
    preference,
    registry,
    run_import,
    unique_league_id,
    user_connections,
)
from tests.support.provider_sync_fixtures import worker_settings
from tests.test_account_deletion_critical import (
    FLAGGED,
    Pause,
    Worker,
    count_for_user,
    import_league_for,
    league_row,
    person,
    private_espn,
    seasons_of,
    set_orphaned,
    set_status,
    user_present,
    wait_for_lock_waiter,
)

SECOND_YEAR = 2023


@pytest.fixture(autouse=True)
def residue() -> Iterator[None]:
    with SessionFactory() as session:
        keep_jobs = set(session.scalars(select(BackgroundJob.id)))
        keep_deliveries = set(session.scalars(select(TransactionalEmailDelivery.id)))

    yield

    with SessionFactory() as session:
        session.execute(delete(BackgroundJob).where(BackgroundJob.id.not_in(keep_jobs)))
        session.execute(
            delete(TransactionalEmailDelivery).where(
                TransactionalEmailDelivery.id.not_in(keep_deliveries)
            )
        )
        session.commit()


def remove(
    caller: Caller, league: str, *, confirmation: str | None = None, session_id: int | None = None
) -> None:
    name = confirmation if confirmation is not None else league_name(league)

    with SessionFactory() as session:
        remove_league_access(
            session,
            user_id=caller.user_id,
            auth_session_id=session_id or caller.session_id,
            provider=FantasyProvider.ESPN,
            provider_league_id=league,
            confirmation=name,
        )
        session.commit()


def league_name(league: str) -> str:
    return league_row(league).name


def access_rows(user_id: int) -> list[tuple[int, UserSeasonAccessStatus]]:
    with SessionFactory() as session:
        return [
            (row.season_id, row.status)
            for row in session.scalars(
                select(UserLeagueSeasonAccess)
                .where(UserLeagueSeasonAccess.user_id == user_id)
                .order_by(UserLeagueSeasonAccess.season_id)
            )
        ]


def import_two_seasons(caller: Caller, league: str, fake: FakeEspn, secret: str | None = None):
    import_league_for(caller, league, fake, secret=secret)
    _import_year(caller, league, fake, secret)


def _import_year(caller: Caller, league: str, fake: FakeEspn, secret: str | None) -> None:
    from tests.support.league_import import SYNTHETIC_S2, private

    run_import(
        caller,
        league,
        fake,
        private_access=private(secret or SYNTHETIC_S2),
        season_year=SECOND_YEAR,
    )


def fingerprint(user_id: int) -> dict[str, Any]:
    with SessionFactory() as session:
        return {
            "sessions": sorted(
                session.scalars(select(AuthSession.id).where(AuthSession.user_id == user_id))
            ),
            "preference": preference(user_id),
            "connections": [
                (row.id, row.encrypted_credentials) for row in user_connections(user_id)
            ],
            "access": access_rows(user_id),
            "jobs": sorted(
                (job.id, job.status)
                for job in session.scalars(
                    select(BackgroundJob).where(
                        BackgroundJob.job_type == BackgroundJobType.ACCESS_REVERIFICATION,
                        BackgroundJob.payload["user_id"].as_integer() == user_id,
                    )
                )
            ),
        }


def claim_league(registry, league: str) -> None:
    pk = league_row(league).id

    with SessionFactory() as session:
        workspace = Installation(application_name="Claim", slug=f"removal-{league}")
        session.add(workspace)
        session.flush()
        registry.workspaces.append(workspace.id)
        session.add(
            ProviderLeagueClaim(
                workspace_id=workspace.id,
                league_id=pk,
                provider=FantasyProvider.ESPN,
                provider_league_id=league,
                normalized_provider_league_id=league,
            )
        )
        session.execute(update(League).where(League.id == pk).values(installation_id=workspace.id))
        session.commit()


# --------------------------------------------------------------------------


def test_removing_one_of_two_users_removes_every_season_and_touches_nobody_else(
    registry, league_id
):
    caller, other = person(registry), person(registry)
    fake = private_espn()
    import_two_seasons(caller, league_id, fake)
    import_two_seasons(other, league_id, fake, secret=OTHER_S2)
    state = import_state(league_id)
    assert (state.leagues, state.seasons, state.access) == (1, 2, 4)
    before = fingerprint(other.user_id)

    remove(caller, league_id)

    after = import_state(league_id)
    assert (after.leagues, after.seasons, after.access) == (1, 2, 2)
    assert access_rows(caller.user_id) == [] and user_connections(caller.user_id) == []
    assert fingerprint(other.user_id) == before
    assert league_row(league_id).orphaned_at is None
    assert user_present(caller.user_id) and count_for_user(AuthSession, caller.user_id) == 1
    # Shared synchronization continues through the remaining user's own authority.
    with SessionFactory() as session:
        eligible = LeagueAccessRepository(session).list_eligible_access_for_season(
            season_id=seasons_of(league_row(league_id).id)[0]
        )
    assert {row.user_id for row in eligible} == {other.user_id}


def test_removing_the_final_active_user_orphans_but_keeps_canonical_data(registry, league_id):
    caller = person(registry)
    import_two_seasons(caller, league_id, private_espn())
    league = league_row(league_id)
    before = seasons_of(league.id)

    remove(caller, league_id)

    after = league_row(league_id)
    assert after.id == league.id and after.orphaned_at is not None
    assert seasons_of(league.id) == before and len(before) == 2


def test_a_valid_workspace_claim_keeps_the_league_unorphaned(registry, league_id):
    caller = person(registry)
    import_league_for(caller, league_id, private_espn())
    claim_league(registry, league_id)

    remove(caller, league_id)

    assert league_row(league_id).orphaned_at is None
    assert access_rows(caller.user_id) == []


def test_reverification_required_access_counts_as_not_active_for_orphaning(registry, league_id):
    caller, other = person(registry), person(registry)
    fake = private_espn()
    import_league_for(caller, league_id, fake)
    import_league_for(other, league_id, fake, secret=OTHER_S2)
    set_status(other.user_id, FLAGGED)  # readable, but not active

    remove(caller, league_id)

    assert league_row(league_id).orphaned_at is not None
    assert access_rows(other.user_id)[0][1] is FLAGGED  # the other user's row is untouched

    # A flagged-only user may remove too, and the League is orphaned by the same rule.
    set_orphaned(league_row(league_id).id, None)
    remove(other, league_id)
    assert league_row(league_id).orphaned_at is not None


def test_default_season_preference_is_cleared_only_when_it_pointed_at_the_removed_league(
    registry, league_id
):
    caller = person(registry)
    second_league = unique_league_id()
    registry.league_ids.append(second_league)
    fake = private_espn()
    import_league_for(caller, league_id, fake)
    import_league_for(caller, second_league, fake)
    first_season = seasons_of(league_row(league_id).id)[0]
    second_season = seasons_of(league_row(second_league).id)[0]
    assert preference(caller.user_id) == first_season

    remove(caller, second_league)  # not the default's League
    assert preference(caller.user_id) == first_season

    with SessionFactory() as session:  # point the default at the League being removed
        session.execute(
            update(UserPreference)
            .where(UserPreference.user_id == caller.user_id)
            .values(default_league_season_id=None)
        )
        session.commit()
    import_league_for(caller, second_league, fake)
    with SessionFactory() as session:
        session.execute(
            update(UserPreference)
            .where(UserPreference.user_id == caller.user_id)
            .values(default_league_season_id=seasons_of(league_row(second_league).id)[0])
        )
        session.commit()

    remove(caller, second_league)

    assert preference(caller.user_id) is None
    assert access_rows(caller.user_id) == [(first_season, UserSeasonAccessStatus.ACTIVE)]
    assert second_season  # the League and its Seasons still exist
    assert league_row(second_league) is not None


def test_connections_still_referenced_survive_and_unused_ones_are_deleted(registry, league_id):
    caller = person(registry)
    other_league = unique_league_id()
    registry.league_ids.append(other_league)
    fake = private_espn()
    import_league_for(caller, league_id, fake)
    import_league_for(caller, other_league, fake)
    [removed_connection] = [
        row.id for row in user_connections(caller.user_id) if row.provider_league_id == league_id
    ]
    [kept_connection] = [
        row.id for row in user_connections(caller.user_id) if row.provider_league_id == other_league
    ]
    other_season = seasons_of(league_row(other_league).id)[0]

    with SessionFactory() as session:  # another access of the same user still uses the first one
        session.execute(
            update(UserLeagueSeasonAccess)
            .where(
                UserLeagueSeasonAccess.user_id == caller.user_id,
                UserLeagueSeasonAccess.season_id == other_season,
            )
            .values(
                provider_connection_id=removed_connection,
                proof_mode=UserSeasonProofMode.PRIVATE_CREDENTIALS,
            )
        )
        session.commit()

    remove(caller, league_id)
    assert {row.id for row in user_connections(caller.user_id)} == {
        removed_connection,
        kept_connection,
    }

    remove(caller, other_league)
    assert user_connections(caller.user_id) == []  # both are unused now


def test_removal_cancels_this_users_queued_reverification_and_a_stale_worker_restores_nothing(
    registry, league_id
):
    caller, other = person(registry), person(registry)
    fake = private_espn()
    import_league_for(caller, league_id, fake)
    import_league_for(other, league_id, fake, secret=OTHER_S2)
    season_id = seasons_of(league_row(league_id).id)[0]

    with SessionFactory() as session:
        enqueue_access_reverification(session, user_id=caller.user_id, season_id=season_id)
        enqueue_access_reverification(session, user_id=other.user_id, season_id=season_id)
        session.commit()
    other_jobs_before = fingerprint(other.user_id)["jobs"]

    remove(caller, league_id)

    with SessionFactory() as session:
        statuses = {
            job.payload["user_id"]: job.status
            for job in session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.job_type == BackgroundJobType.ACCESS_REVERIFICATION,
                    BackgroundJob.payload["season_id"].as_integer() == season_id,
                )
            )
        }
    assert statuses[caller.user_id] is BackgroundJobStatus.CANCELED
    assert fingerprint(other.user_id)["jobs"] == other_jobs_before

    # A worker that had already started cannot restore or mutate the removed access.
    reverify_access(
        session_factory=SessionFactory,
        user_id=caller.user_id,
        season_id=season_id,
        settings=worker_settings(),
        http_get=fake,
    )
    assert access_rows(caller.user_id) == [] and user_connections(caller.user_id) == []


def test_removal_versus_import_of_the_same_league_keeps_one_league_and_season(
    registry, league_id, monkeypatch
):
    caller, importer = person(registry), person(registry)
    import_league_for(caller, league_id, private_espn())
    league = league_row(league_id)
    pause = Pause("remover")
    original = LeagueAccessRepository.mark_league_orphaned

    def paused(self, **kwargs):
        result = original(self, **kwargs)  # League locked, orphan mark written, uncommitted
        pause()
        return result

    monkeypatch.setattr(LeagueAccessRepository, "mark_league_orphaned", paused)
    remover = Worker("remover", lambda: remove(caller, league_id))
    remover.start()
    pause.wait_reached()
    importing = Worker("importer", lambda: run_import(importer, league_id, FakeEspn(mode="public")))
    importing.start()
    wait_for_lock_waiter()  # the import is blocked on the League lock
    pause.release.set()
    remover.finish()
    importing.finish()

    state = import_state(league_id)
    assert (state.leagues, state.seasons, state.access) == (1, 1, 1)
    revived = league_row(league_id)
    assert revived.id == league.id and revived.orphaned_at is None
    assert access_rows(caller.user_id) == []
    assert access_rows(importer.user_id) != []


def test_failure_mid_transaction_rolls_everything_back(registry, league_id, monkeypatch):
    caller = person(registry)
    import_league_for(caller, league_id, private_espn())
    season_id = seasons_of(league_row(league_id).id)[0]
    with SessionFactory() as session:
        enqueue_access_reverification(session, user_id=caller.user_id, season_id=season_id)
        session.commit()
    before = fingerprint(caller.user_id)

    def fail(self, **kwargs):
        raise RuntimeError("synthetic failure after access and credentials were deleted")

    monkeypatch.setattr(LeagueAccessRepository, "mark_league_orphaned", fail)

    with pytest.raises(RuntimeError):
        remove(caller, league_id)

    assert fingerprint(caller.user_id) == before
    assert league_row(league_id).orphaned_at is None


def test_every_ineligible_or_inaccessible_request_is_one_identical_refusal(registry, league_id):
    owner, stranger = person(registry), person(registry)
    spectator = person(registry)
    import_league_for(owner, league_id, private_espn())
    league = league_row(league_id)
    with SessionFactory() as session:  # an unverified account
        from app.db.auth_models import User

        session.execute(
            update(User).where(User.id == spectator.user_id).values(email_verified_at=None)
        )
        session.commit()
    before = (access_rows(owner.user_id), fingerprint(owner.user_id))

    def refusal(caller: Caller, league_identity: str, **kwargs: Any) -> str:
        with pytest.raises(LeagueRemovalUnavailableError) as caught:
            remove(caller, league_identity, confirmation=league.name, **kwargs)
        return str(caught.value)

    messages = {
        refusal(stranger, league_id),  # a League the caller cannot access
        refusal(owner, "999999999999"),  # a League that does not exist
        refusal(owner, "not-a-number"),  # an invalid identifier
        refusal(spectator, league_id),  # an unverified caller
        refusal(owner, league_id, session_id=stranger.session_id),  # another user's session
    }

    assert len(messages) == 1 and str(league.id) not in messages.copy().pop()
    assert (access_rows(owner.user_id), fingerprint(owner.user_id)) == before
    assert league_row(league_id).orphaned_at is None


@pytest.mark.parametrize("confirmation", ["wrong name", "x", "  "])
def test_wrong_confirmation_changes_nothing(registry, league_id, confirmation):
    caller = person(registry)
    import_league_for(caller, league_id, private_espn())
    before = fingerprint(caller.user_id)

    with pytest.raises(LeagueRemovalConfirmationError):
        remove(caller, league_id, confirmation=confirmation)

    assert fingerprint(caller.user_id) == before


def test_confirmation_ignores_case_and_extra_spaces(registry, league_id):
    caller = person(registry)
    import_league_for(caller, league_id, private_espn())

    remove(caller, league_id, confirmation=f"  {league_name(league_id).upper()}  ")

    assert access_rows(caller.user_id) == []


def test_removed_credentials_never_authorize_a_later_import_by_anyone_else(registry, league_id):
    removed, newcomer = person(registry), person(registry)
    import_league_for(removed, league_id, private_espn())
    remove(removed, league_id)

    # The newcomer must prove access themselves; the removed user's secret proves nothing.
    with pytest.raises(Exception) as rejected:
        run_import(
            newcomer,
            league_id,
            FakeEspn(mode="private", valid_s2={OTHER_S2}),
            private_access=_private_access("synthetic-import-espn-s2-wrong-0003"),
        )
    assert rejected.value is not None
    assert access_rows(newcomer.user_id) == []


def _private_access(secret: str):
    from tests.support.league_import import private

    return private(secret)


def test_a_season_pointer_to_a_removed_credential_is_cleared(registry, league_id):
    caller = person(registry)
    import_league_for(caller, league_id, private_espn())
    season_id = seasons_of(league_row(league_id).id)[0]
    [connection] = [row.id for row in user_connections(caller.user_id)]

    with SessionFactory() as session:
        session.execute(
            update(Season).where(Season.id == season_id).values(provider_connection_id=connection)
        )
        session.commit()

    remove(caller, league_id)

    with SessionFactory() as session:
        assert session.get(Season, season_id).provider_connection_id is None
        assert session.get(ProviderConnection, connection) is None
    assert datetime.now(UTC)
