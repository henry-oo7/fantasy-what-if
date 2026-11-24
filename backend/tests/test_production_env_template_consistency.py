"""Production env template, generator script, and Settings must agree."""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.core.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / ".env.production.example"
SCRIPT = ROOT / "scripts" / "generate-production-env.sh"
PLACEHOLDER = "generate-this-value"


def _lines(text: str) -> list[str]:
    return text.splitlines()


def _parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _lines(text):
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


def test_template_declares_production_and_keyring_placeholders() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    lines = _lines(text)
    assert "APP_ENV=production" in lines
    assert f"TRANSACTIONAL_EMAIL_KEYS={PLACEHOLDER}" in lines
    assert "TRANSACTIONAL_EMAIL_CURRENT_KEY_VERSION=1" in lines
    assert f"PROVIDER_CREDENTIAL_KEYS={PLACEHOLDER}" in lines
    assert "PROVIDER_CREDENTIAL_CURRENT_KEY_VERSION=1" in lines


def test_generator_script_substitutes_both_keyrings() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert "^PROVIDER_CREDENTIAL_KEYS=generate-this-value$" in script
    assert "^TRANSACTIONAL_EMAIL_KEYS=generate-this-value$" in script
    assert "chmod 600" in script


@pytest.mark.parametrize("script", sorted((ROOT / "scripts").glob("*.sh")), ids=lambda p: p.name)
def test_shell_scripts_pass_syntax_check(script: Path) -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("sh is unavailable")
    result = subprocess.run([shell, "-n", str(script)], capture_output=True, check=False)
    assert result.returncode == 0


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is unavailable")
def test_generated_environment_is_valid_production_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = tmp_path / "template.env"
    output = tmp_path / "generated.env"
    shutil.copy(TEMPLATE, template)

    result = subprocess.run(
        ["sh", str(SCRIPT), str(template), str(output)],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o600

    text = output.read_text(encoding="utf-8")
    assert PLACEHOLDER not in text
    values = _parse(text)

    for name in ("PROVIDER_CREDENTIAL_KEYS", "TRANSACTIONAL_EMAIL_KEYS"):
        keyring = json.loads(values[name])
        assert set(keyring) == {"1"}
        Fernet(keyring["1"].encode())  # raises when not a valid Fernet key
    assert values["PROVIDER_CREDENTIAL_KEYS"] != values["TRANSACTIONAL_EMAIL_KEYS"]

    # Isolate from the process environment and any real .env file.
    for name in list(values) + ["DATABASE_URL"]:
        monkeypatch.delenv(name, raising=False)
    settings = Settings(
        _env_file=None,
        app_env=values["APP_ENV"],
        app_debug=False,
        app_secret_key=values["APP_SECRET_KEY"],
        frontend_origin=values["FRONTEND_ORIGIN"],
        trusted_origins=values["TRUSTED_ORIGINS"],
        forwarded_allow_ips=values["FORWARDED_ALLOW_IPS"],
        session_cookie_name=values["SESSION_COOKIE_NAME"],
        session_cookie_secure=True,
        provider_credential_keys=values["PROVIDER_CREDENTIAL_KEYS"],
        transactional_email_keys=values["TRANSACTIONAL_EMAIL_KEYS"],
        database_url="postgresql+psycopg://synthetic:synthetic@localhost:5432/synthetic",
    )
    assert settings.is_production
    assert settings.provider_credential_keyring().configured
    assert settings.transactional_email_keyring().configured
