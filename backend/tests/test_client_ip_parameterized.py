"""Additional normalize_ip_address cases."""

from __future__ import annotations

import pytest

from app.core.client_ip import normalize_ip_address


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2001:db8:0:0:0:0:0:1", "2001:db8::1"),
        ("2001:0db8::0001", "2001:db8::1"),
        ("::", "::"),
        ("0:0:0:0:0:0:0:1", "::1"),
        ("2001:DB8::ABCD", "2001:db8::abcd"),
        ("FE80::1", "fe80::1"),
        ("  192.0.2.1  ", "192.0.2.1"),
        ("\t2001:db8::1\n", "2001:db8::1"),
        ("::ffff:192.0.2.1", "192.0.2.1"),
        ("::FFFF:C000:0201", "192.0.2.1"),
        ("0:0:0:0:0:ffff:192.0.2.1", "192.0.2.1"),
        ("203.0.113.9", "203.0.113.9"),
    ],
)
def test_valid_addresses_are_normalized(value: str, expected: str) -> None:
    assert normalize_ip_address(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "[::1]",
        "[::1]:8080",
        "192.0.2.1,198.51.100.2",
        "192.0.2.1 198.51.100.2",
        "192.0.2.1:8080",
        "256.0.0.1",
        "192.0.2",
        "2001:db8::g",
        "2001:db8:::1",
        "1:2:3:4:5:6:7:8:9",
        "unknown",
    ],
)
def test_invalid_values_return_none(value: str | None) -> None:
    assert normalize_ip_address(value) is None
