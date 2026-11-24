"""Critical client-IP trust-boundary tests (no network, no database use)."""

import ipaddress
import re
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.registration import normalize_request_source
from app.core.client_ip import normalize_ip_address, resolve_client_ip

NGINX_PROXY = "172.31.100.10"
NGINX_CONF = Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf"


def build_client(peer: str, *, trusted: str = NGINX_PROXY) -> TestClient:
    app = FastAPI()

    @app.get("/who")
    def who(request: Request) -> dict[str, str | None]:
        ip = resolve_client_ip(request)
        return {"ip": ip, "key": normalize_request_source(ip)}

    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=trusted)
    return TestClient(wrapped, client=(peer, 40000))


def who(client: TestClient, **headers: str) -> dict[str, str | None]:
    return client.get("/who", headers=headers).json()


def test_trusted_proxy_path_yields_real_client_ip() -> None:
    client = build_client(NGINX_PROXY)

    assert who(client, **{"X-Forwarded-For": "203.0.113.7"})["ip"] == "203.0.113.7"


def test_untrusted_direct_request_cannot_spoof_x_forwarded_for() -> None:
    client = build_client("198.51.100.9")

    assert who(client, **{"X-Forwarded-For": "203.0.113.7"})["ip"] == "198.51.100.9"


def test_untrusted_direct_request_cannot_spoof_cf_connecting_ip() -> None:
    client = build_client("198.51.100.9")

    result = who(client, **{"CF-Connecting-IP": "203.0.113.7"})

    assert result["ip"] == "198.51.100.9"


def test_cf_connecting_ip_is_never_read_by_the_backend_even_from_trusted_proxy() -> None:
    client = build_client(NGINX_PROXY)

    result = who(
        client,
        **{"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "203.0.113.50"},
    )

    assert result["ip"] == "203.0.113.50"


def test_forwarded_and_x_real_ip_headers_are_ignored() -> None:
    client = build_client("198.51.100.9")

    result = who(client, **{"Forwarded": "for=203.0.113.7", "X-Real-IP": "203.0.113.7"})

    assert result["ip"] == "198.51.100.9"


@pytest.mark.parametrize(
    "value",
    ["not-an-ip", "999.1.1.1", "203.0.113.7;evil", "<script>"],
)
def test_malformed_forwarded_value_fails_safely(value: str) -> None:
    client = build_client(NGINX_PROXY)

    result = who(client, **{"X-Forwarded-For": value})

    assert result["ip"] is None
    assert result["key"] == "unknown-source"


def test_multi_value_chain_from_trusted_proxy_uses_last_untrusted_hop() -> None:
    client = build_client(NGINX_PROXY)

    result = who(client, **{"X-Forwarded-For": "203.0.113.7, 203.0.113.99"})

    assert result["ip"] == "203.0.113.99"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("203.0.113.7", "203.0.113.7"),
        (" 203.0.113.7 ", "203.0.113.7"),
        ("2001:0DB8:0000:0000:0000:0000:0000:0001", "2001:db8::1"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
        ("203.0.113.7, 203.0.113.8", None),
        ("fe80::1%eth0", None),
        ("", None),
        (None, None),
    ],
)
def test_ip_normalization(raw: str | None, expected: str | None) -> None:
    assert normalize_ip_address(raw) == expected


def test_rate_limit_keys_distinguish_two_clients_behind_trusted_proxy() -> None:
    client = build_client(NGINX_PROXY)

    first = who(client, **{"X-Forwarded-For": "203.0.113.7"})["key"]
    second = who(client, **{"X-Forwarded-For": "203.0.113.8"})["key"]
    first_v6 = who(client, **{"X-Forwarded-For": "2001:db8::1"})["key"]

    assert len({first, second, first_v6}) == 3


def test_direct_client_without_proxy_remains_usable() -> None:
    client = build_client("127.0.0.1", trusted="10.0.0.1")

    assert who(client)["ip"] == "127.0.0.1"


def nginx_directives(name: str) -> list[str]:
    text = NGINX_CONF.read_text()
    return re.findall(rf"^\s*{name}\s+([^;]+);", text, re.MULTILINE)


def test_nginx_trusts_cf_connecting_ip_only_from_private_peers() -> None:
    sources = nginx_directives("set_real_ip_from")

    assert sources
    for source in sources:
        network = ipaddress.ip_network(source.strip())
        assert network.is_private or network.is_loopback, source
        assert network.prefixlen > 0, source

    assert nginx_directives("real_ip_header") == ["CF-Connecting-IP"]
    assert nginx_directives("real_ip_recursive") == ["off"]


def test_nginx_replaces_forwarding_headers_instead_of_appending() -> None:
    text = NGINX_CONF.read_text()

    assert "$proxy_add_x_forwarded_for" not in text
    assert text.count("proxy_set_header X-Forwarded-For $remote_addr;") == 2
    for header in ("X-Real-IP", "Forwarded", "CF-Connecting-IP", "True-Client-IP"):
        assert text.count(f'proxy_set_header {header} "";') == 2
