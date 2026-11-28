"""Critical personal account deletion and orphan retention behavior (ADR 0010).

Real PostgreSQL (``fantasy_what_if_test``) only, committed rows through
``SessionFactory`` so races use independent connections. Providers are the
in-process ``FakeEspn`` transport; users, connections, credentials and
recipients are synthetic. Races are ordered by hooks that run while row locks
are held and by lock observation in ``pg_stat_activity``, never by sleeps.
"""

# ruff: noqa: F811  (pytest fixtures are imported and then requested by parameter name)

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.db.auth_models import AuthSession, EmailVerificationToken, PasswordResetToken, User
from app.db.league_access_models import UserLeagueSeasonAccess, UserPreference
from app.db.models import League, Season
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    FantasyProvider,
    TransactionalEmailDeliveryStatus,
    TransactionalEmailEventType,
    TransactionalEmailScope,
    UserSeasonAccessStatus,
)
from app.db.platform_models import (
    BackgroundJob,
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
    TransactionalEmailDelivery,
)
from app.db.session import SessionFactory
from app.notifications.email_provider import EmailAcceptance
from app.repositories.league_access_repository import LeagueAccessRepository
from app.services import account_deletion, background_job_worker, orphan_league_purge
from app.services.access_reverification import enqueue_access_reverification
from app.services.account_deletion import (
    AccountDeletionConfirmationError,
    AccountDeletionNoticeUnavailableError,
    AccountDeletionPasswordError,
    AccountDeletionUnavailableError,
    delete_personal_account,
)
from app.services.background_job_handlers import build_background_job_handlers
from app.services.background_job_worker import BackgroundJobSnapshot
from app.services.orphan_league_purge import (
    ORPHAN_RETENTION,
    PurgeOutcome,
    enqueue_due_orphan_purges,
    list_due_orphan_league_ids,
    purge_orphaned_league,
)
from app.services.transactional_email_delivery import TransactionalEmailDeliveryHandler
from app.services.transactional_email_intents import queue_user_email
from tests.support.email_delivery import FakeTransport, email_settings
from tests.support.league_import import (  # noqa: F401
    OTHER_S2,
    SYNTHETIC_S2,
    Caller,
    FakeEspn,
    create_caller,
    espn,
    forbid_live_network,
    import_state,
    league_id,
    preference,
    private,
    registry,
    run_import,
    unique_league_id,
    user_connections,
)
from tests.support.provider_sync_fixtures import worker_settings
from tests.support.transactional_email import make_cipher

PASSWORD = "correct horse battery staple"
CONFIRMATION = "DELETE MY ACCOUNT"
WAIT_SECONDS = 15
NOW = datetime.now(UTC)
LONG_AGO = NOW - ORPHAN_RETENTION - timedelta(days=1)
DAY_29 = NOW - timedelta(days=29)
ACTIVE = UserSeasonAccessStatus.ACTIVE
FLAGGED = UserSeasonAccessStatus.REVERIFICATION_REQUIRED


@pytest.fixture(autouse=True)
def residue() -> Iterator[None]:
    """Remove the completion deliveries and purge/reverification jobs a test created."""

    with SessionFactory() as session:
        keep_deliveries = set(session.scalars(select(TransactionalEmailDelivery.id)))
        keep_jobs = set(session.scalars(select(BackgroundJob.id)))

    yield

    with SessionFactory() as session:
        session.execute(delete(BackgroundJob).where(BackgroundJob.id.not_in(keep_jobs)))
        session.execute(
            delete(TransactionalEmailDelivery).where(
                TransactionalEmailDelivery.id.not_in(keep_deliveries)
            )
        )
        session.commit()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def person(registry) -> Caller:  # noqa: F811
    return create_caller(registry, personal=True)


def import_league_for(caller: Caller, league: str, fake: FakeEspn, *, secret: str | None = None):
    """Private proof with the caller's own synthetic credentials."""

    return run_import(caller, league, fake, private_access=private(secret or SYNTHETIC_S2))


def private_espn() -> FakeEspn:
    return FakeEspn(mode="private")


def delete_account(
    caller: Caller,
    *,
    password: str = PASSWORD,
    confirmation: str = CONFIRMATION,
    factory: Callable[[], Any] | None = None,
    session_id: int | None = None,
) -> None:
    cipher = make_cipher()

    with SessionFactory() as session:
        delete_personal_account(
            session,
            user_id=caller.user_id,
            auth_session_id=session_id or caller.session_id,
            current_password=password,
            confirmation=confirmation,
            recipient_cipher_factory=factory or (lambda: cipher),
        )
        session.commit()


def user_present(user_id: int) -> bool:
    with SessionFactory() as session:
        return session.get(User, user_id) is not None


def league_row(provider_league_id: str) -> League | None:
    with SessionFactory() as session:
        row = session.scalar(
            select(League).where(
                League.provider == FantasyProvider.ESPN,
                League.provider_league_id == provider_league_id,
            )
        )
        if row is not None:
            session.expunge(row)
        return row


def seasons_of(league_pk: int) -> list[int]:
    with SessionFactory() as session:
        return list(session.scalars(select(Season.id).where(Season.league_id == league_pk)))


def set_orphaned(league_pk: int, when: datetime | None) -> None:
    with SessionFactory() as session:
        session.execute(update(League).where(League.id == league_pk).values(orphaned_at=when))
        session.commit()


def set_status(user_id: int, status: UserSeasonAccessStatus) -> None:
    with SessionFactory() as session:
        session.execute(
            update(UserLeagueSeasonAccess)
            .where(UserLeagueSeasonAccess.user_id == user_id)
            .values(status=status)
        )
        session.commit()


def count_for_user(model: Any, user_id: int) -> int:
    with SessionFactory() as session:
        return int(
            session.scalar(select(func.count()).select_from(model).where(model.user_id == user_id))
            or 0
        )


def completion_deliveries() -> list[TransactionalEmailDelivery]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(TransactionalEmailDelivery).where(
                    TransactionalEmailDelivery.event_type
                    == TransactionalEmailEventType.ACCOUNT_DELETION_COMPLETED
                )
            )
        )
        session.expunge_all()
        return rows


def jobs_of_type(job_type: BackgroundJobType) -> list[BackgroundJob]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(select(BackgroundJob).where(BackgroundJob.job_type == job_type))
        )
        session.expunge_all()
        return rows


def snapshot_for(job: BackgroundJob) -> BackgroundJobSnapshot:
    return BackgroundJobSnapshot(
        id=job.id,
        workspace_id=job.workspace_id,
        job_type=job.job_type,
        payload_version=job.payload_version,
        payload=dict(job.payload),
        attempt_count=job.attempt_count,
        maximum_attempts=job.maximum_attempts,
    )


class Worker(threading.Thread):
    """Run ``target`` in a thread and keep its result or exception."""

    def __init__(self, name: str, target: Callable[[], Any]) -> None:
        super().__init__(name=name, daemon=True)
        self._target_function = target
        self.result: Any = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.result = self._target_function()
        except BaseException as exc:  # noqa: BLE001 - surfaced by the test
            self.error = exc

    def finish(self) -> Any:
        self.join(WAIT_SECONDS)
        assert not self.is_alive(), "worker did not finish"
        if self.error is not None:
            raise self.error
        return self.result


def wait_for_lock_waiter() -> None:
    """Block until some backend is waiting on a heavyweight lock (a real lock wait)."""

    gate = threading.Event()

    for _ in range(WAIT_SECONDS * 50):
        with SessionFactory() as session:
            waiting = session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                    "AND pid <> pg_backend_pid()"
                )
            )
        if waiting:
            return
        gate.wait(0.02)

    raise AssertionError("no backend ever waited on a lock")


class Pause:
    """Pause the calling thread (named ``name``) once, after its locks are held."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.reached = threading.Event()
        self.release = threading.Event()

    def __call__(self) -> None:
        if threading.current_thread().name == self.name:
            self.reached.set()
            assert self.release.wait(WAIT_SECONDS)

    def wait_reached(self) -> None:
        assert self.reached.wait(WAIT_SECONDS)


# --------------------------------------------------------------------------
# Shared data survives; the user's personal graph does not
# --------------------------------------------------------------------------


def test_deleting_one_of_two_users_keeps_league_and_the_other_user(registry, league_id, espn):  # noqa: F811
    deleted, kept = person(registry), person(registry)
    fake = private_espn()
    import_league_for(deleted, league_id, fake)
    import_league_for(kept, league_id, fake, secret=OTHER_S2)
    before = league_row(league_id)
    kept_connections = [row.id for row in user_connections(kept.user_id)]

    delete_account(deleted)

    after = league_row(league_id)
    state = import_state(league_id)
    assert after is not None and after.id == before.id and after.orphaned_at is None
    assert (state.leagues, state.seasons, state.access) == (1, 1, 1)
    assert user_present(kept.user_id) and not user_present(deleted.user_id)
    assert [row.id for row in user_connections(kept.user_id)] == kept_connections
    assert count_for_user(AuthSession, kept.user_id) == 1
    assert preference(kept.user_id) is not None
    assert count_for_user(UserLeagueSeasonAccess, kept.user_id) == 1
    # Shared synchronization can continue through the remaining user's authority.
    with SessionFactory() as session:
        eligible = LeagueAccessRepository(session).list_eligible_access_for_season(
            season_id=seasons_of(after.id)[0]
        )
    assert [row.user_id for row in eligible] == [kept.user_id]


def test_final_active_user_deletion_orphans_league_and_removes_every_personal_row(
    registry,  # noqa: F811
    league_id,  # noqa: F811
):
    caller = person(registry)
    import_league_for(caller, league_id, private_espn())
    league = league_row(league_id)

    delete_account(caller)

    assert not user_present(caller.user_id)
    for model in (AuthSession, UserLeagueSeasonAccess, ProviderConnection, UserPreference):
        assert count_for_user(model, caller.user_id) == 0
    assert count_for_user(EmailVerificationToken, caller.user_id) == 0
    assert count_for_user(PasswordResetToken, caller.user_id) == 0
    after = league_row(league_id)
    assert after is not None and after.orphaned_at is not None
    assert seasons_of(league.id) != []
    assert (import_state(league_id).leagues, import_state(league_id).seasons) == (1, 1)


@pytest.mark.parametrize(
    ("deleted_status", "other_status", "orphaned"),
    [
        (ACTIVE, FLAGGED, True),  # readable but not active: retention counts active only
        (ACTIVE, ACTIVE, False),
        (FLAGGED, ACTIVE, False),
        (FLAGGED, FLAGGED, True),
    ],
)
def test_orphan_rule_counts_active_access_only(
    registry,  # noqa: F811
    league_id,  # noqa: F811
    deleted_status,
    other_status,
    orphaned,
):
    deleted, other = person(registry), person(registry)
    fake = private_espn()
    import_league_for(deleted, league_id, fake)
    import_league_for(other, league_id, fake, secret=OTHER_S2)
    set_status(deleted.user_id, deleted_status)
    set_status(other.user_id, other_status)

    delete_account(deleted)

    assert (league_row(league_id).orphaned_at is not None) is orphaned
    assert count_for_user(UserLeagueSeasonAccess, other.user_id) == 1


def test_valid_workspace_claim_prevents_orphan_marking(registry, league_id):  # noqa: F811
    caller = person(registry)
    import_league_for(caller, league_id, private_espn())
    league = league_row(league_id)

    with SessionFactory() as session:
        workspace = Installation(application_name="Claim Holder", slug=f"claim-{league_id}")
        session.add(workspace)
        session.flush()
        registry.workspaces.append(workspace.id)
        session.add(
            ProviderLeagueClaim(
                workspace_id=workspace.id,
                league_id=league.id,
                provider=FantasyProvider.ESPN,
                provider_league_id=league_id,
                normalized_provider_league_id=league_id,
            )
        )
        session.execute(
            update(League).where(League.id == league.id).values(installation_id=workspace.id)
        )
        session.commit()

    delete_account(caller)

    assert league_row(league_id).orphaned_at is None


def test_stale_installation_id_without_claim_is_not_a_dependency(registry, league_id):  # noqa: F811
    caller = person(registry)
    import_league_for(caller, league_id, private_espn())
    league = league_row(league_id)

    with SessionFactory() as session:
        workspace = Installation(application_name="No Claim", slug=f"stale-{league_id}")
        session.add(workspace)
        session.flush()
        registry.workspaces.append(workspace.id)
        session.execute(
            update(League).where(League.id == league.id).values(installation_id=workspace.id)
        )
        session.commit()

    delete_account(caller)

    assert league_row(league_id).orphaned_at is not None


# --------------------------------------------------------------------------
# Refusals change nothing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"password": "not the password at all"}, AccountDeletionPasswordError),
        ({"confirmation": "delete my league"}, AccountDeletionConfirmationError),
        ({"confirmation": ""}, AccountDeletionConfirmationError),
    ],
)
def test_wrong_password_or_confirmation_deletes_nothing(registry, league_id, kwargs, error):  # noqa: F811
    caller = person(registry)
    import_league_for(caller, league_id, private_espn())

    with pytest.raises(error):
        delete_account(caller, **kwargs)

    assert user_present(caller.user_id)
    assert count_for_user(ProviderConnection, caller.user_id) == 1
    assert count_for_user(UserLeagueSeasonAccess, caller.user_id) == 1
    assert league_row(league_id).orphaned_at is None
    assert completion_deliveries() == []


def test_confirmation_is_whitespace_and_case_tolerant_but_exact_otherwise(registry):  # noqa: F811
    caller = person(registry)

    delete_account(caller, confirmation="  delete   my account ")

    assert not user_present(caller.user_id)


def test_other_users_session_revoked_session_and_legacy_users_are_refused(registry, league_id):  # noqa: F811
    first, second = person(registry), person(registry)
    legacy = create_caller(registry)  # workspace administrator
    spectator = create_caller(registry, personal=True, verified=False)

    with pytest.raises(AccountDeletionUnavailableError):
        delete_account(first, session_id=second.session_id)  # another user's session

    with SessionFactory() as session:
        session.execute(
            update(AuthSession)
            .where(AuthSession.id == first.session_id)
            .values(revoked_at=datetime.now(UTC))
        )
        session.commit()

    with pytest.raises(AccountDeletionUnavailableError):
        delete_account(first)

    with pytest.raises(AccountDeletionUnavailableError):
        delete_account(legacy)

    with pytest.raises(AccountDeletionUnavailableError):
        delete_account(spectator)

    assert all(user_present(c.user_id) for c in (first, second, legacy, spectator))


def test_missing_keyring_rolls_the_whole_deletion_back(registry, league_id):  # noqa: F811
    from app.notifications.recipient_encryption import TransactionalEmailKeyringConfigurationError

    caller = person(registry)
    import_league_for(caller, league_id, private_espn())

    def broken():
        raise TransactionalEmailKeyringConfigurationError("synthetic")

    with pytest.raises(AccountDeletionNoticeUnavailableError):
        delete_account(caller, factory=broken)

    assert user_present(caller.user_id)
    assert count_for_user(AuthSession, caller.user_id) == 1
    assert count_for_user(ProviderConnection, caller.user_id) == 1
    assert count_for_user(UserLeagueSeasonAccess, caller.user_id) == 1
    assert league_row(league_id).orphaned_at is None
    assert completion_deliveries() == []


def test_failure_after_access_removal_rolls_everything_back(registry, league_id, monkeypatch):  # noqa: F811
    caller = person(registry)
    import_league_for(caller, league_id, private_espn())

    def fail(self, **kwargs):
        raise RuntimeError("synthetic failure after the access rows were deleted")

    monkeypatch.setattr(LeagueAccessRepository, "mark_league_orphaned", fail)

    with pytest.raises(RuntimeError):
        delete_account(caller)

    assert user_present(caller.user_id)
    assert count_for_user(UserLeagueSeasonAccess, caller.user_id) == 1
    assert count_for_user(ProviderConnection, caller.user_id) == 1
    assert count_for_user(AuthSession, caller.user_id) == 1
    assert completion_deliveries() == []


# --------------------------------------------------------------------------
# Completion notice and email/job cleanup
# --------------------------------------------------------------------------


def test_completion_notice_outlives_the_user_without_plaintext_recipient(registry, league_id):  # noqa: F811
    caller = person(registry)
    import_league_for(caller, league_id, private_espn())
    with SessionFactory() as session:
        recipient = session.get(User, caller.user_id).normalized_email
    cipher = make_cipher()

    with SessionFactory() as session:
        delete_personal_account(
            session,
            user_id=caller.user_id,
            auth_session_id=caller.session_id,
            current_password=PASSWORD,
            confirmation=CONFIRMATION,
            recipient_cipher_factory=lambda: cipher,
        )
        session.commit()

    [delivery] = completion_deliveries()
    assert delivery.scope is TransactionalEmailScope.UNSCOPED
    assert delivery.user_id is None and delivery.workspace_id is None
    assert delivery.status is TransactionalEmailDeliveryStatus.PENDING
    assert delivery.encrypted_recipient is not None
    assert recipient not in str(delivery.encrypted_recipient)
    assert (
        cipher.decrypt(
            ciphertext=delivery.encrypted_recipient,
            key_version=delivery.recipient_key_version,
            delivery_id=delivery.id,
        )
        == recipient
    )
    assert str(caller.user_id) not in delivery.idempotency_key

    [job] = [
        row
        for row in jobs_of_type(BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY)
        if row.payload.get("delivery_id") == delivery.id
    ]
    assert job.workspace_id is None
    assert job.payload == {"version": 1, "delivery_id": delivery.id}
    assert recipient not in str(job.payload) and recipient not in (job.idempotency_key or "")
    assert job.status is BackgroundJobStatus.PENDING


def test_pending_identity_mail_and_reverification_are_canceled_and_others_untouched(
    registry,  # noqa: F811
    league_id,  # noqa: F811
):
    deleted, other = person(registry), person(registry)
    fake = private_espn()
    import_league_for(deleted, league_id, fake)
    import_league_for(other, league_id, fake, secret=OTHER_S2)
    season_id = seasons_of(league_row(league_id).id)[0]

    with SessionFactory() as session:
        mine = queue_user_email(
            session,
            user_id=deleted.user_id,
            event_type=TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET,
            idempotency_key=f"test:mine:{league_id}",
        )
        theirs = queue_user_email(
            session,
            user_id=other.user_id,
            event_type=TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET,
            idempotency_key=f"test:theirs:{league_id}",
        )
        enqueue_access_reverification(session, user_id=deleted.user_id, season_id=season_id)
        enqueue_access_reverification(session, user_id=other.user_id, season_id=season_id)
        mine_job, theirs_job = mine.job.id, theirs.job.id
        session.commit()

    sync_before = [
        (job.id, job.status)
        for job in jobs_of_type(BackgroundJobType.LEAGUE_SEASON_SYNC)
        if job.idempotency_key == f"league-season-sync:{season_id}"
    ]

    delete_account(deleted)

    with SessionFactory() as session:
        assert session.get(BackgroundJob, mine_job).status is BackgroundJobStatus.CANCELED
        assert session.get(BackgroundJob, theirs_job).status is BackgroundJobStatus.PENDING
        statuses = {
            job.payload["user_id"]: job.status
            for job in session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.job_type == BackgroundJobType.ACCESS_REVERIFICATION,
                    BackgroundJob.payload["season_id"].as_integer() == season_id,
                )
            )
        }
        assert statuses[deleted.user_id] is BackgroundJobStatus.CANCELED
        assert statuses[other.user_id] is BackgroundJobStatus.PENDING
        assert (
            session.scalar(
                select(func.count())
                .select_from(TransactionalEmailDelivery)
                .where(TransactionalEmailDelivery.user_id == deleted.user_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(TransactionalEmailDelivery)
                .where(TransactionalEmailDelivery.user_id == other.user_id)
            )
            == 1
        )
    # A shared Season job is never canceled because one user left.
    assert [
        (job.id, job.status)
        for job in jobs_of_type(BackgroundJobType.LEAGUE_SEASON_SYNC)
        if job.idempotency_key == f"league-season-sync:{season_id}"
    ] == sync_before


IDENTITY_EVENTS = [
    TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION,
    TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET,
    TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_CHANGED,
]


def queue_identity_job(caller: Caller, event: TransactionalEmailEventType) -> tuple[int, int]:
    with SessionFactory() as session:
        queued = queue_user_email(
            session,
            user_id=caller.user_id,
            event_type=event,
            idempotency_key=f"test:{event.value}:{caller.user_id}",
        )
        ids = (queued.delivery.id, queued.job.id)
        session.commit()
    return ids


def mark_running(job_id: int) -> None:
    with SessionFactory() as session:
        session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job_id)
            .values(
                status=BackgroundJobStatus.RUNNING,
                attempt_count=1,
                claimed_at=datetime.now(UTC),
                started_at=datetime.now(UTC),
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                claimed_by="identity-test",
            )
        )
        session.commit()


def email_handler(transport: Any) -> TransactionalEmailDeliveryHandler:
    return TransactionalEmailDeliveryHandler(
        session_factory=SessionFactory,
        settings=email_settings(),
        transport_factory=transport.factory,
    )


def job_snapshot(job_id: int) -> BackgroundJobSnapshot:
    with SessionFactory() as session:
        return snapshot_for(session.get(BackgroundJob, job_id))


@pytest.mark.parametrize("event", IDENTITY_EVENTS, ids=lambda event: event.value)
def test_pending_identity_job_is_canceled_before_its_delivery_is_deleted(registry, event):
    caller = person(registry)
    delivery_id, job_id = queue_identity_job(caller, event)

    delete_account(caller)

    with SessionFactory() as session:
        job = session.get(BackgroundJob, job_id)
        assert session.get(TransactionalEmailDelivery, delivery_id) is None
        assert job.status is BackgroundJobStatus.CANCELED and job.canceled_at is not None
        assert job.payload == {"version": 1, "delivery_id": delivery_id}  # nothing was added


def test_running_identity_worker_loses_authority_and_neither_sends_nor_fails(registry):
    caller = person(registry)
    delivery_id, job_id = queue_identity_job(
        caller, TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET
    )
    mark_running(job_id)
    snapshot = job_snapshot(job_id)

    delete_account(caller)

    transport = FakeTransport(outcomes=[EmailAcceptance(provider_message_id="must-not-send")])
    email_handler(transport)(snapshot)  # returns quietly: no recipient, token, send or failure

    assert transport.sent == []
    assert count_for_user(PasswordResetToken, caller.user_id) == 0
    with SessionFactory() as session:
        assert session.get(BackgroundJob, job_id).status is BackgroundJobStatus.CANCELED
    # The worker reports the lost lease as the expected canceled outcome, not a failure.
    assert background_job_worker._email_job_canceled(
        session_factory=SessionFactory, snapshot=snapshot
    )


def test_deletion_during_an_in_flight_send_persists_nothing_afterwards(registry):
    caller = person(registry)
    delivery_id, job_id = queue_identity_job(
        caller, TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET
    )
    mark_running(job_id)
    snapshot = job_snapshot(job_id)

    def delete_while_sending(message):
        delete_account(caller)
        return EmailAcceptance(provider_message_id="in-flight")

    email_handler(FakeTransport(outcomes=[delete_while_sending]))(snapshot)

    with SessionFactory() as session:
        assert session.get(TransactionalEmailDelivery, delivery_id) is None
    assert count_for_user(PasswordResetToken, caller.user_id) == 0
    assert not user_present(caller.user_id)


def test_account_completion_delivery_survives_but_a_lost_one_is_delivery_missing(registry):
    from app.services.background_job_worker import BackgroundJobExecutionError

    caller = person(registry)
    delete_account(caller)
    [delivery] = completion_deliveries()
    [job] = [
        row
        for row in jobs_of_type(BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY)
        if row.payload.get("delivery_id") == delivery.id
    ]
    assert job.status is BackgroundJobStatus.PENDING and not user_present(caller.user_id)

    with SessionFactory() as session:  # an unexpected loss, not an intentional cancellation
        session.execute(
            delete(TransactionalEmailDelivery).where(TransactionalEmailDelivery.id == delivery.id)
        )
        session.commit()

    with pytest.raises(BackgroundJobExecutionError) as caught:
        email_handler(FakeTransport())(snapshot_for(job))

    assert caught.value.safe_error_code == "delivery_missing"
    assert caught.value.retryable is False


def test_account_completion_notice_is_rendered_truthfully():
    from app.notifications.email_rendering import render_transactional_email

    rendered = render_transactional_email(
        TransactionalEmailEventType.ACCOUNT_DELETION_COMPLETED,
        public_base_url="https://example.invalid",
    )
    body = rendered.text_body.lower()

    assert "account" in body and "credentials" in body
    assert "shared with other accounts" in body and "retention" in body
    assert "workspace" not in body and "permanently deleted" not in body
    assert "all league data" not in body


# --------------------------------------------------------------------------
# Re-import revival and purge
# --------------------------------------------------------------------------


def test_reimport_at_day_29_revives_the_same_league_and_purge_loses(registry, league_id):  # noqa: F811
    first, second = person(registry), person(registry)
    import_league_for(first, league_id, private_espn())
    league = league_row(league_id)
    season_ids = seasons_of(league.id)
    delete_account(first)
    set_orphaned(league.id, DAY_29)

    run_import(second, league_id, FakeEspn(mode="public"))

    revived = league_row(league_id)
    state = import_state(league_id)
    assert revived.id == league.id and revived.orphaned_at is None
    assert seasons_of(league.id) == season_ids
    assert (state.leagues, state.seasons, state.access) == (1, 1, 1)
    assert (
        count_for_user(ProviderConnection, second.user_id) == 0
    )  # public proof: no credentials kept
    with SessionFactory() as session:
        assert list_due_orphan_league_ids(session, now=NOW + timedelta(days=60)) == []
        assert (
            purge_orphaned_league(session, league_id=league.id, now=NOW + timedelta(days=60))
            is PurgeOutcome.NOT_ORPHANED
        )


def test_reimport_credentials_belong_only_to_the_importing_user(registry, league_id):  # noqa: F811
    first, second = person(registry), person(registry)
    import_league_for(first, league_id, private_espn())
    delete_account(first)

    import_league_for(second, league_id, private_espn(), secret=OTHER_S2)

    assert [row.user_id for row in user_connections(second.user_id)] == [second.user_id]
    assert user_connections(first.user_id) == []


def test_purge_only_deletes_due_unused_unclaimed_orphans(registry, league_id):  # noqa: F811
    caller = person(registry)
    import_league_for(caller, league_id, private_espn())
    league = league_row(league_id)
    delete_account(caller)

    # Not due yet.
    with SessionFactory() as session:
        assert purge_orphaned_league(session, league_id=league.id, now=NOW) is PurgeOutcome.NOT_DUE

    # Due, but the League was revived (not orphaned).
    set_orphaned(league.id, None)
    with SessionFactory() as session:
        assert (
            purge_orphaned_league(session, league_id=league.id, now=NOW)
            is PurgeOutcome.NOT_ORPHANED
        )

    # Due, but a user holds active access again.
    other = person(registry)
    run_import(other, league_id, FakeEspn(mode="public"))
    set_orphaned(league.id, LONG_AGO)
    with SessionFactory() as session:
        assert (
            purge_orphaned_league(session, league_id=league.id, now=NOW)
            is PurgeOutcome.ACTIVE_ACCESS
        )
    assert league_row(league_id) is not None

    # Due and unused, but a valid workspace claim exists.
    set_status(other.user_id, FLAGGED)
    with SessionFactory() as session:
        workspace = Installation(application_name="Claim", slug=f"purge-{league_id}")
        session.add(workspace)
        session.flush()
        registry.workspaces.append(workspace.id)
        session.add(
            ProviderLeagueClaim(
                workspace_id=workspace.id,
                league_id=league.id,
                provider=FantasyProvider.ESPN,
                provider_league_id=league_id,
                normalized_provider_league_id=league_id,
            )
        )
        session.execute(
            update(League).where(League.id == league.id).values(installation_id=workspace.id)
        )
        session.commit()
    with SessionFactory() as session:
        assert (
            purge_orphaned_league(session, league_id=league.id, now=NOW)
            is PurgeOutcome.WORKSPACE_DEPENDENCY
        )
    assert league_row(league_id) is not None

    # Due, no active access, no claim: purged, with its Seasons; a repeat is a no-op.
    with SessionFactory() as session:
        session.execute(
            delete(ProviderLeagueClaim).where(ProviderLeagueClaim.league_id == league.id)
        )
        session.execute(update(League).where(League.id == league.id).values(installation_id=None))
        session.commit()
    with SessionFactory() as session:
        assert purge_orphaned_league(session, league_id=league.id, now=NOW) is PurgeOutcome.PURGED
        session.commit()
    assert league_row(league_id) is None and seasons_of(league.id) == []
    with SessionFactory() as session:
        assert (
            purge_orphaned_league(session, league_id=league.id, now=NOW) is PurgeOutcome.NOT_FOUND
        )
    # The reverification_required user's stale row went with the League's data.
    assert count_for_user(UserLeagueSeasonAccess, other.user_id) == 0


def test_scheduler_enqueues_one_exact_job_per_due_league_across_passes(registry):  # noqa: F811
    due, fresh, active = person(registry), person(registry), person(registry)
    ids = [unique_league_id() for _ in range(3)]
    registry.league_ids.extend(ids)
    for caller, league in zip((due, fresh, active), ids, strict=True):
        import_league_for(caller, league, private_espn())
    due_pk, fresh_pk = (league_row(ids[0]).id, league_row(ids[1]).id)
    delete_account(due)
    delete_account(fresh)
    set_orphaned(due_pk, LONG_AGO)  # due; `fresh` stays orphaned only just now

    with SessionFactory() as session:
        assert list_due_orphan_league_ids(session, now=NOW) == [due_pk]
        assert enqueue_due_orphan_purges(session, now=NOW) == 1
        session.commit()
    with SessionFactory() as session:
        assert enqueue_due_orphan_purges(session, now=NOW) == 0
        session.commit()

    jobs = [
        job
        for job in jobs_of_type(BackgroundJobType.ORPHAN_LEAGUE_PURGE)
        if job.payload.get("league_id") in (due_pk, fresh_pk)
    ]
    assert len(jobs) == 1
    [job] = jobs
    assert job.payload == {"version": 1, "league_id": due_pk}
    assert job.idempotency_key == f"orphan-league-purge:{due_pk}"
    assert job.workspace_id is None
    assert fresh_pk not in {j.payload["league_id"] for j in jobs}


def test_purge_handler_deletes_only_with_current_job_authority(registry, league_id):  # noqa: F811
    from app.services.background_job_worker import BackgroundJobExecutionError

    caller = person(registry)
    import_league_for(caller, league_id, private_espn())
    league = league_row(league_id)
    delete_account(caller)
    set_orphaned(league.id, LONG_AGO)
    with SessionFactory() as session:
        enqueue_due_orphan_purges(session, now=NOW)
        session.commit()
    [job] = [
        j
        for j in jobs_of_type(BackgroundJobType.ORPHAN_LEAGUE_PURGE)
        if j.payload["league_id"] == league.id
    ]
    handler = build_background_job_handlers(
        session_factory=SessionFactory, settings=worker_settings()
    )[BackgroundJobType.ORPHAN_LEAGUE_PURGE]

    # A worker that does not own a running, unexpired lease deletes nothing.
    with pytest.raises(BackgroundJobExecutionError) as lost:
        handler(snapshot_for(job))
    assert lost.value.safe_error_code == "job_authority_lost"
    assert league_row(league_id) is not None

    with SessionFactory() as session:
        session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job.id)
            .values(
                status=BackgroundJobStatus.RUNNING,
                attempt_count=1,
                claimed_at=datetime.now(UTC),
                started_at=datetime.now(UTC),
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                claimed_by="purge-test",
            )
        )
        session.commit()
        running = snapshot_for(session.get(BackgroundJob, job.id))

    handler(running)
    handler(running)  # a repeated delivery of the same job is a harmless no-op

    assert league_row(league_id) is None


def test_purge_failure_rolls_back_and_leaves_the_graph_intact(registry, league_id, monkeypatch):  # noqa: F811
    caller = person(registry)
    import_league_for(caller, league_id, private_espn())
    league = league_row(league_id)
    delete_account(caller)
    set_orphaned(league.id, LONG_AGO)

    original = orphan_league_purge.delete

    def exploding_delete(*args, **kwargs):
        raise IntegrityError("synthetic", {}, Exception("synthetic"))

    monkeypatch.setattr(orphan_league_purge, "delete", exploding_delete)

    with pytest.raises(IntegrityError), SessionFactory() as session:
        purge_orphaned_league(session, league_id=league.id, now=NOW)
        session.commit()

    monkeypatch.setattr(orphan_league_purge, "delete", original)
    assert league_row(league_id) is not None and seasons_of(league.id) != []


# --------------------------------------------------------------------------
# Races on real PostgreSQL
# --------------------------------------------------------------------------


def test_deletion_versus_reimport_by_another_user_keeps_one_league_and_season(
    registry, league_id, monkeypatch
):  # noqa: F811
    first, second = person(registry), person(registry)
    import_league_for(first, league_id, private_espn())
    league = league_row(league_id)
    pause = Pause("deleter")
    original = LeagueAccessRepository.mark_league_orphaned

    def paused(self, **kwargs):
        result = original(self, **kwargs)  # League locked, orphan mark written, uncommitted
        pause()
        return result

    monkeypatch.setattr(LeagueAccessRepository, "mark_league_orphaned", paused)
    deleter = Worker("deleter", lambda: delete_account(first))
    deleter.start()
    pause.wait_reached()
    importer = Worker("importer", lambda: run_import(second, league_id, FakeEspn(mode="public")))
    importer.start()
    wait_for_lock_waiter()  # the import is blocked on the League lock
    pause.release.set()
    deleter.finish()
    importer.finish()

    revived = league_row(league_id)
    state = import_state(league_id)
    assert revived.id == league.id and revived.orphaned_at is None
    assert (state.leagues, state.seasons, state.access) == (1, 1, 1)
    assert not user_present(first.user_id) and user_present(second.user_id)


def test_purge_versus_reimport_purge_first_recreates_exactly_one_league(
    registry, league_id, monkeypatch
):  # noqa: F811
    first, second = person(registry), person(registry)
    import_league_for(first, league_id, private_espn())
    league = league_row(league_id)
    delete_account(first)
    set_orphaned(league.id, LONG_AGO)
    pause = Pause("purger")
    original = orphan_league_purge.claimed_workspace_id

    def paused(session, row):
        result = original(session, row)  # League row lock is held here
        pause()
        return result

    monkeypatch.setattr(orphan_league_purge, "claimed_workspace_id", paused)

    def purge() -> PurgeOutcome:
        with SessionFactory() as session:
            outcome = purge_orphaned_league(session, league_id=league.id, now=NOW)
            session.commit()
            return outcome

    purger = Worker("purger", purge)
    purger.start()
    pause.wait_reached()
    importer = Worker("importer", lambda: run_import(second, league_id, FakeEspn(mode="public")))
    importer.start()
    wait_for_lock_waiter()
    pause.release.set()

    assert purger.finish() is PurgeOutcome.PURGED
    importer.finish()
    state = import_state(league_id)
    assert (state.leagues, state.seasons, state.access) == (1, 1, 1)
    assert league_row(league_id).orphaned_at is None


def test_purge_waits_for_a_committing_access_insert_and_then_deletes_nothing(registry, league_id):  # noqa: F811
    first, second = person(registry), person(registry)
    import_league_for(first, league_id, private_espn())
    league = league_row(league_id)
    season_id = seasons_of(league.id)[0]
    delete_account(first)
    set_orphaned(league.id, LONG_AGO)

    with SessionFactory() as session:
        LeagueAccessRepository(session).create_or_get_access(
            user_id=second.user_id,
            season_id=season_id,
            proof_mode=orphan_proof(),
            provider_connection_id=None,
            verified_at=datetime.now(UTC),
        )  # holds the League lock, uncommitted

        def purge() -> PurgeOutcome:
            with SessionFactory() as other:
                outcome = purge_orphaned_league(other, league_id=league.id, now=NOW)
                other.commit()
                return outcome

        purger = Worker("purger", purge)
        purger.start()
        wait_for_lock_waiter()
        session.commit()

    assert purger.finish() in (PurgeOutcome.NOT_ORPHANED, PurgeOutcome.ACTIVE_ACCESS)
    assert league_row(league_id) is not None and seasons_of(league.id) == [season_id]


def orphan_proof():
    from app.db.platform_enums import UserSeasonProofMode

    return UserSeasonProofMode.PUBLIC


def test_two_purge_workers_delete_once_and_the_second_is_a_bounded_noop(
    registry, league_id, monkeypatch
):  # noqa: F811
    caller = person(registry)
    import_league_for(caller, league_id, private_espn())
    league = league_row(league_id)
    delete_account(caller)
    set_orphaned(league.id, LONG_AGO)
    pause = Pause("purge-1")
    original = orphan_league_purge.claimed_workspace_id

    def paused(session, row):
        result = original(session, row)
        pause()
        return result

    monkeypatch.setattr(orphan_league_purge, "claimed_workspace_id", paused)

    def purge() -> PurgeOutcome:
        with SessionFactory() as session:
            outcome = purge_orphaned_league(session, league_id=league.id, now=NOW)
            session.commit()
            return outcome

    first = Worker("purge-1", purge)
    first.start()
    pause.wait_reached()
    second = Worker("purge-2", purge)
    second.start()
    wait_for_lock_waiter()
    pause.release.set()

    assert sorted([first.finish(), second.finish()]) == sorted(
        [PurgeOutcome.PURGED, PurgeOutcome.NOT_FOUND]
    )
    assert league_row(league_id) is None


def test_deletion_blocks_login_reset_and_session_creation_until_it_commits(registry, monkeypatch):  # noqa: F811
    caller = person(registry)
    pause = Pause("deleter")
    original = account_deletion.verify_password

    def paused(password, password_hash):
        result = original(password, password_hash)  # the user row is locked here
        pause()
        return result

    monkeypatch.setattr(account_deletion, "verify_password", paused)
    deleter = Worker("deleter", lambda: delete_account(caller))
    deleter.start()
    pause.wait_reached()

    def login_lock() -> Any:
        with SessionFactory() as session:
            return session.scalar(
                select(User.id).where(User.id == caller.user_id).with_for_update()
            )

    def reset_password() -> int:
        with SessionFactory() as session:
            count = session.execute(
                update(User)
                .where(User.id == caller.user_id)
                .values(password_hash="$argon2id$changed")
            ).rowcount
            session.commit()
            return count

    login, reset = Worker("login", login_lock), Worker("reset", reset_password)
    login.start()
    reset.start()
    wait_for_lock_waiter()
    pause.release.set()
    deleter.finish()

    assert login.finish() is None  # the login finds no account
    assert reset.finish() == 0  # the reset revives nothing
    with pytest.raises(IntegrityError), SessionFactory() as session:
        session.add(
            AuthSession(
                user_id=caller.user_id,
                session_token_hash="x" * 64,
                csrf_token_hash="y" * 64,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        session.commit()
    assert count_for_user(AuthSession, caller.user_id) == 0


def test_deleting_an_account_does_not_alter_any_other_users_rows(registry, league_id):  # noqa: F811
    deleted, other = person(registry), person(registry)
    fake = private_espn()
    import_league_for(deleted, league_id, fake)
    import_league_for(other, league_id, fake, secret=OTHER_S2)

    with SessionFactory() as session:
        queue_user_email(
            session,
            user_id=other.user_id,
            event_type=TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET,
            idempotency_key=f"test:isolation:{other.user_id}",
        )
        session.commit()

    def fingerprint() -> dict[str, Any]:
        with SessionFactory() as session:
            return {
                "user": session.get(User, other.user_id).password_hash,
                "sessions": sorted(
                    session.scalars(
                        select(AuthSession.id).where(AuthSession.user_id == other.user_id)
                    )
                ),
                "preference": preference(other.user_id),
                "connections": [
                    (row.id, row.encrypted_credentials, row.is_active)
                    for row in user_connections(other.user_id)
                ],
                "access": [
                    (row.id, row.status)
                    for row in session.scalars(
                        select(UserLeagueSeasonAccess).where(
                            UserLeagueSeasonAccess.user_id == other.user_id
                        )
                    )
                ],
                "deliveries": sorted(
                    session.scalars(
                        select(TransactionalEmailDelivery.id).where(
                            TransactionalEmailDelivery.user_id == other.user_id
                        )
                    )
                ),
            }

    before = fingerprint()
    delete_account(deleted)

    assert fingerprint() == before


def test_scheduler_pass_commits_a_purge_job_for_a_due_league(registry, league_id):
    from app.scripts.sync_scheduler import enqueue_orphan_purge_batch

    caller = person(registry)
    import_league_for(caller, league_id, private_espn())
    league = league_row(league_id)
    delete_account(caller)
    set_orphaned(league.id, LONG_AGO)

    assert enqueue_orphan_purge_batch(now=NOW) >= 1
    assert enqueue_orphan_purge_batch(now=NOW) == 0  # a second pass adds nothing

    assert [
        job.payload
        for job in jobs_of_type(BackgroundJobType.ORPHAN_LEAGUE_PURGE)
        if job.payload["league_id"] == league.id
    ] == [{"version": 1, "league_id": league.id}]


def test_destructive_throttle_blocks_on_either_the_user_or_the_address_bucket(registry):
    from app.api.destructive_throttle import record_destructive_attempt
    from app.core.settings import get_settings

    settings = get_settings()
    first, second = person(registry), person(registry)
    address = f"203.0.113.{first.user_id % 200}"

    def attempt(user: Caller, source: str | None):
        return record_destructive_attempt(
            settings,
            action="throttle_test_action",
            user_id=user.user_id,
            source=source,
            maximum_requests=2,
            window_minutes=60,
            code="throttle_test_throttled",
        )

    assert attempt(first, address) is None and attempt(first, address) is None
    blocked = attempt(first, address)
    assert blocked is not None and blocked.status_code == 429
    assert b"throttle_test_throttled" in blocked.body and "retry-after" in blocked.headers

    # A different user from the same address is stopped by the address bucket alone.
    assert attempt(second, address) is not None
    # The same user without a resolvable address is stopped by the user bucket alone.
    assert attempt(first, None) is not None
    # A different user with no address has a bucket of their own.
    assert attempt(second, None) is None

    with SessionFactory() as session:
        from app.db.auth_models import RegistrationRateLimit

        session.execute(
            delete(RegistrationRateLimit).where(
                RegistrationRateLimit.action.like("throttle_test_action_%")
            )
        )
        session.commit()
