from app.auth.login_throttle_policy import (
    SOURCE_SPRAY_LOCKOUT_SECONDS,
    SOURCE_SPRAY_MAXIMUM_FAILURES,
    LoginThrottlePolicyKind,
    resolve_login_throttle_policy,
    resolve_source_spray_policy,
)
from app.db.auth_models import UserRole

SECRET = "role-aware-throttle-secret-32-characters"


def admin_policy(
    *,
    username: str = "administrator",
    browser_id: str = "browser-one",
    source_address: str = "203.0.113.10",
):
    return resolve_login_throttle_policy(
        normalized_username=username,
        user_role=UserRole.ADMIN,
        browser_id=browser_id,
        source_address=source_address,
        secret=SECRET,
    )


def client_policy(
    *,
    username: str = "spectator",
    role: UserRole | None = UserRole.SPECTATOR,
    browser_id: str = "browser-one",
    source_address: str = "203.0.113.10",
):
    return resolve_login_throttle_policy(
        normalized_username=username,
        user_role=role,
        browser_id=browser_id,
        source_address=source_address,
        secret=SECRET,
    )


def test_admin_policy_is_account_wide() -> None:
    first = admin_policy(
        browser_id="browser-one",
        source_address="203.0.113.10",
    )
    second = admin_policy(
        browser_id="browser-two",
        source_address="198.51.100.25",
    )

    assert first.kind is (LoginThrottlePolicyKind.ADMIN_ACCOUNT)
    assert first.bucket_username == (second.bucket_username)
    assert first.bucket_source == "account-wide"
    assert second.bucket_source == "account-wide"


def test_admin_policy_uses_progressive_delays() -> None:
    policy = admin_policy()

    assert policy.delay_for_failure(1) == 0
    assert policy.delay_for_failure(2) == 0
    assert policy.delay_for_failure(3) == 15
    assert policy.delay_for_failure(4) == 60
    assert policy.delay_for_failure(5) == 900
    assert policy.delay_for_failure(20) == 900


def test_spectator_policy_is_client_specific() -> None:
    first = client_policy(
        browser_id="browser-one",
        source_address="203.0.113.10",
    )
    other_browser = client_policy(
        browser_id="browser-two",
        source_address="203.0.113.10",
    )
    other_network = client_policy(
        browser_id="browser-one",
        source_address="198.51.100.25",
    )

    assert first.kind is LoginThrottlePolicyKind.CLIENT
    assert first.bucket_source != (other_browser.bucket_source)
    assert first.bucket_source != (other_network.bucket_source)


def test_spectator_policy_reuses_same_subnet() -> None:
    first = client_policy(
        browser_id="browser-one",
        source_address="203.0.113.10",
    )
    second = client_policy(
        browser_id="browser-one",
        source_address="203.0.113.240",
    )

    assert first.bucket_source == second.bucket_source


def test_spectator_policy_uses_progressive_delays() -> None:
    policy = client_policy()

    assert policy.delay_for_failure(1) == 0
    assert policy.delay_for_failure(2) == 0
    assert policy.delay_for_failure(3) == 30
    assert policy.delay_for_failure(4) == 120
    assert policy.delay_for_failure(5) == 900
    assert policy.delay_for_failure(20) == 900


def test_unknown_username_uses_client_policy() -> None:
    unknown = client_policy(
        username="missing-account",
        role=None,
    )
    spectator = client_policy(
        username="spectator",
        role=UserRole.SPECTATOR,
    )

    assert unknown.kind is LoginThrottlePolicyKind.CLIENT
    assert spectator.kind is LoginThrottlePolicyKind.CLIENT
    assert unknown.bucket_source == spectator.bucket_source


def test_spectator_is_not_account_wide() -> None:
    first = client_policy(
        browser_id="league-browser-one",
        source_address="203.0.113.10",
    )
    second = client_policy(
        browser_id="league-browser-two",
        source_address="198.51.100.10",
    )

    assert first.bucket_source != second.bucket_source
    assert first.bucket_source != "account-wide"
    assert second.bucket_source != "account-wide"


def test_policy_buckets_do_not_store_raw_values() -> None:
    admin = admin_policy(
        username="private-admin",
    )
    spectator = client_policy(
        username="private-spectator",
        browser_id="private-browser-id",
        source_address="203.0.113.117",
    )

    assert "private-admin" not in admin.bucket_username
    assert "private-spectator" not in spectator.bucket_username
    assert "private-browser-id" not in spectator.bucket_source
    assert "203.0.113" not in spectator.bucket_source


def test_source_spray_policy_locks_after_twenty_failures() -> None:
    policy = resolve_source_spray_policy(
        source_address="203.0.113.117",
        secret=SECRET,
    )

    assert SOURCE_SPRAY_MAXIMUM_FAILURES == 20
    assert SOURCE_SPRAY_LOCKOUT_SECONDS == 1800

    assert policy.delay_for_failure(19) == 0
    assert policy.delay_for_failure(20) == 1800
    assert policy.delay_for_failure(200) == 1800


def test_source_spray_policy_is_shared_across_usernames() -> None:
    first = resolve_source_spray_policy(
        source_address="203.0.113.10",
        secret=SECRET,
    )
    second = resolve_source_spray_policy(
        source_address="203.0.113.240",
        secret=SECRET,
    )

    assert first.bucket_username == second.bucket_username
    assert first.bucket_source == second.bucket_source


def test_source_spray_policy_separates_ipv4_networks() -> None:
    first = resolve_source_spray_policy(
        source_address="203.0.113.10",
        secret=SECRET,
    )
    second = resolve_source_spray_policy(
        source_address="198.51.100.10",
        secret=SECRET,
    )

    assert first.bucket_source != second.bucket_source


def test_source_spray_policy_uses_ipv6_64_prefix() -> None:
    first = resolve_source_spray_policy(
        source_address=("2001:db8:abcd:1234:1111::1"),
        secret=SECRET,
    )
    second = resolve_source_spray_policy(
        source_address=("2001:db8:abcd:1234:ffff::2"),
        secret=SECRET,
    )
    other_network = resolve_source_spray_policy(
        source_address=("2001:db8:abcd:5678::1"),
        secret=SECRET,
    )

    assert first.bucket_source == second.bucket_source
    assert first.bucket_source != other_network.bucket_source


def test_source_spray_bucket_contains_no_raw_address() -> None:
    source_address = "203.0.113.117"

    policy = resolve_source_spray_policy(
        source_address=source_address,
        secret=SECRET,
    )

    assert source_address not in policy.bucket_source
    assert "203.0.113" not in policy.bucket_source
    assert len(policy.bucket_source) == 64
    assert len(policy.bucket_username) == 64
