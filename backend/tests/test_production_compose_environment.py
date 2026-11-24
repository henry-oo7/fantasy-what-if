"""Backend-image services in production compose files must run as production."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_FILES = ("compose.prod.yaml", "compose.release.yaml")
BACKEND_SERVICES = {"migrate", "backend", "backend-worker", "sync-scheduler"}


def _services(name: str) -> dict[str, str]:
    """Split a compose file into top-level service blocks (text only)."""

    text = (ROOT / name).read_text(encoding="utf-8")
    section = re.search(r"^services:\n(.*?)(?=^\S|\Z)", text, re.S | re.M)
    assert section is not None
    blocks: dict[str, str] = {}
    current: str | None = None
    for line in section.group(1).splitlines():
        header = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if header:
            current = header.group(1)
            blocks[current] = ""
        elif current is not None:
            blocks[current] += line + "\n"
    return blocks


def _image(block: str) -> str:
    match = re.search(r"^    image:\s*(\S+)", block, re.M)
    return match.group(1) if match else ""


def _environment(block: str) -> dict[str, str]:
    match = re.search(r"^    environment:\n((?:      .*\n|\s*\n)*)", block, re.M)
    if match is None:
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        entry = re.match(r"^      ([A-Z0-9_]+):\s*(.*)$", line)
        if entry:
            values[entry.group(1)] = entry.group(2).strip().strip("\"'")
    return values


@pytest.mark.parametrize("name", PRODUCTION_FILES)
def test_backend_image_services_are_exactly_the_expected_set(name: str) -> None:
    services = _services(name)
    using_backend = {n for n, b in services.items() if "fantasy-what-if-backend" in _image(b)}
    assert using_backend == BACKEND_SERVICES


@pytest.mark.parametrize("name", PRODUCTION_FILES)
@pytest.mark.parametrize("service", sorted(BACKEND_SERVICES))
def test_backend_services_set_app_env_production(name: str, service: str) -> None:
    assert _environment(_services(name)[service]).get("APP_ENV") == "production"


@pytest.mark.parametrize("name", PRODUCTION_FILES)
@pytest.mark.parametrize("service", ["frontend", "database"])
def test_frontend_and_database_do_not_require_app_env(name: str, service: str) -> None:
    services = _services(name)
    assert service in services
    assert "APP_ENV" not in _environment(services[service])


@pytest.mark.parametrize("name", PRODUCTION_FILES)
def test_default_bind_address_is_loopback(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "${APP_BIND_ADDRESS:-127.0.0.1}" in text
    assert not re.search(r"APP_BIND_ADDRESS:-(0\.0\.0\.0|::)?\}", text)
    assert "0.0.0.0:" not in text


@pytest.mark.parametrize("name", ["compose.dev.yaml", "compose.yaml"])
def test_non_production_compose_files_do_not_set_production(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    assert not re.search(r"APP_ENV\s*[:=]\s*[\"']?production", text)
