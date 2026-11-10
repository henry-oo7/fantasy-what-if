from app.auth.login_identity import (
    browser_identifier_hash,
    client_bucket_hash,
    generate_login_browser_id,
    network_prefix_hash,
    normalize_network_prefix,
)

SECRET = "test-login-identity-secret-32-characters"


def test_browser_identifiers_are_random() -> None:
    first = generate_login_browser_id()
    second = generate_login_browser_id()

    assert first
    assert second
    assert first != second


def test_ipv4_is_normalized_to_24_prefix() -> None:
    assert normalize_network_prefix("203.0.113.117") == "203.0.113.0/24"


def test_ipv6_is_normalized_to_64_prefix() -> None:
    assert normalize_network_prefix("2001:db8:abcd:1234:5678::1") == "2001:db8:abcd:1234::/64"


def test_invalid_or_missing_address_is_unknown() -> None:
    assert normalize_network_prefix(None) == "unknown"
    assert normalize_network_prefix("") == "unknown"
    assert normalize_network_prefix("not-an-address") == "unknown"


def test_raw_browser_and_network_values_are_not_hashes() -> None:
    browser_id = "opaque-browser-identifier"
    source = "203.0.113.117"

    browser_hash = browser_identifier_hash(
        browser_id,
        secret=SECRET,
    )
    network_hash = network_prefix_hash(
        source,
        secret=SECRET,
    )

    assert browser_hash != browser_id
    assert network_hash != source
    assert len(browser_hash) == 64
    assert len(network_hash) == 64


def test_client_bucket_changes_with_browser_or_network() -> None:
    base = client_bucket_hash(
        browser_id="browser-one",
        source_address="203.0.113.10",
        secret=SECRET,
    )
    other_browser = client_bucket_hash(
        browser_id="browser-two",
        source_address="203.0.113.10",
        secret=SECRET,
    )
    other_network = client_bucket_hash(
        browser_id="browser-one",
        source_address="198.51.100.10",
        secret=SECRET,
    )

    assert base != other_browser
    assert base != other_network


def test_same_subnet_uses_same_client_network_signal() -> None:
    first = client_bucket_hash(
        browser_id="browser-one",
        source_address="203.0.113.10",
        secret=SECRET,
    )
    second = client_bucket_hash(
        browser_id="browser-one",
        source_address="203.0.113.240",
        secret=SECRET,
    )

    assert first == second
