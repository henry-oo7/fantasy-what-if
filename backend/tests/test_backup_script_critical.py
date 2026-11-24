# ruff: noqa: E501
"""Critical backup-script safety tests using intercepted docker/pg_dump.

A fake `docker` records every call; the synthetic pg_dump never touches a
database. No real Compose service, env file, or backup destination is used.
"""

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP = REPO_ROOT / "scripts" / "backup.sh"

FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
if [ "$1" = "compose" ]; then
    printf 'POSTGRES_DB=%s POSTGRES_USER=%s\\n' "${POSTGRES_DB:-unset}" "${POSTGRES_USER:-unset}" >> "$FAKE_ENV_LOG"
fi
case " $* " in
    *" config "*"--format json"*)
        if [ -n "${FAKE_COMPOSE_JSON:-}" ]; then
            printf '%s\\n' "$FAKE_COMPOSE_JSON"
            exit 0
        fi
        env_file=""
        previous=""
        for argument in "$@"; do
            if [ "$previous" = "--env-file" ]; then env_file="$argument"; fi
            previous="$argument"
        done
        db="$(grep '^POSTGRES_DB=' "$env_file" | cut -d= -f2-)"
        user="$(grep '^POSTGRES_USER=' "$env_file" | cut -d= -f2-)"
        printf '{"services":{"database":{"environment":{"POSTGRES_DB":"%s","POSTGRES_USER":"%s","POSTGRES_PASSWORD":"never-printed"}}}}\\n' "$db" "$user"
        exit 0 ;;
    *" ps "*"--quiet"*)
        printf 'dbcontainer\\n'; exit 0 ;;
    *"pg_dump"*)
        printf 'SYNTHETIC-DUMP-BYTES'
        if [ -n "${FAKE_FAIL_DUMP:-}" ]; then exit 1; fi
        if [ -n "${FAKE_SIGNAL:-}" ]; then
            kill -"$FAKE_SIGNAL" "$PPID"
            exit 0
        fi
        exit 0 ;;
    *"pg_restore"*"--list"*)
        cat > /dev/null
        if [ -n "${FAKE_FAIL_LIST:-}" ]; then exit 1; fi
        exit 0 ;;
esac
if [ "$1" = "inspect" ]; then
    printf 'running\\n'
fi
exit 0
"""


@pytest.fixture
def sandbox(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    (tmp_path / "env").write_text(
        "POSTGRES_DB=backup_target_db\n"
        "POSTGRES_USER=backup_target_user\n"
        "POSTGRES_PASSWORD=never-printed\n"
        "touch ENV_FILE_WAS_EXECUTED\n"
        "$(touch ENV_FILE_WAS_SOURCED)\n"
    )
    return tmp_path, bin_dir


def run_backup(sandbox, **extra_env: str):
    tmp_path, bin_dir = sandbox
    log = tmp_path / "docker.log"
    destination = tmp_path / "out" / "backup.dump"
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "COMPOSE_FILE": str(tmp_path / "compose.yaml"),
        "ENV_FILE": str(tmp_path / "env"),
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_ENV_LOG": str(tmp_path / "env.log"),
        **extra_env,
    }
    result = subprocess.run(
        ["sh", str(BACKUP), str(destination)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )
    lines = log.read_text().splitlines() if log.exists() else []
    return result, lines, destination


def compose_json(environment) -> str:
    return json.dumps({"services": {"database": {"environment": environment}}})


def artifacts(destination: Path) -> list[str]:
    directory = destination.parent
    return sorted(p.name for p in directory.iterdir()) if directory.exists() else []


def dump_calls(lines: list[str]) -> list[str]:
    return [line for line in lines if "pg_dump" in line]


def test_backup_script_has_no_fallback_target_and_never_sources_the_env_file() -> None:
    script = BACKUP.read_text()

    assert "POSTGRES_DB:-" not in script
    assert "POSTGRES_USER:-" not in script
    assert "fantasy_what_if" not in script.replace("fantasy-what-if", "")
    assert "fantasy_app" not in script
    for forbidden in ('. "$env_file"', "source ", 'eval "', "set -a"):
        assert forbidden not in script
    assert "-u POSTGRES_DB" in script
    assert "-u POSTGRES_USER" in script
    assert "-u POSTGRES_PASSWORD" in script
    assert "[a-z_][a-z0-9_]{0,62}" in script


def test_successful_backup_uses_the_resolved_target_and_publishes_atomically(sandbox) -> None:
    result, lines, destination = run_backup(sandbox)

    assert result.returncode == 0, result.stderr
    dump = dump_calls(lines)
    assert len(dump) == 1
    assert "--username=backup_target_user" in dump[0]
    assert "--dbname=backup_target_db" in dump[0]
    assert str(sandbox[0] / "env") in next(line for line in lines if "--format json" in line)
    assert artifacts(destination) == ["backup.dump", "backup.dump.sha256"]
    assert destination.read_bytes() == b"SYNTHETIC-DUMP-BYTES"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    checksum = destination.with_name("backup.dump.sha256")
    assert stat.S_IMODE(checksum.stat().st_mode) == 0o600
    verify = subprocess.run(
        ["sha256sum", "--check", checksum.name],
        cwd=destination.parent,
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr


def test_env_file_is_never_sourced_or_executed(sandbox) -> None:
    result, _, _ = run_backup(sandbox)

    assert result.returncode == 0, result.stderr
    assert not (sandbox[0] / "ENV_FILE_WAS_EXECUTED").exists()
    assert not (sandbox[0] / "ENV_FILE_WAS_SOURCED").exists()


def test_exported_shell_values_cannot_override_the_resolved_target(sandbox) -> None:
    result, lines, _ = run_backup(
        sandbox,
        POSTGRES_DB="exported_other_db",
        POSTGRES_USER="exported_other_user",
        POSTGRES_PASSWORD="exported-secret",
    )

    assert result.returncode == 0, result.stderr
    assert "--dbname=backup_target_db" in dump_calls(lines)[0]
    assert "exported_other" not in " ".join(lines)
    env_log = (sandbox[0] / "env.log").read_text()
    assert "exported_other" not in env_log
    assert "POSTGRES_DB=unset POSTGRES_USER=unset" in env_log


@pytest.mark.parametrize(
    "document",
    [
        "not json",
        "{}",
        '{"services":{}}',
        '{"services":{"database":{}}}',
        compose_json({}),
        compose_json({"POSTGRES_DB": "app_db"}),
        compose_json({"POSTGRES_USER": "app_user"}),
        compose_json({"POSTGRES_DB": "", "POSTGRES_USER": "app_user"}),
        compose_json({"POSTGRES_DB": "app_db", "POSTGRES_USER": ""}),
        compose_json({"POSTGRES_DB": 'bad"; DROP DATABASE x; --', "POSTGRES_USER": "app_user"}),
        compose_json({"POSTGRES_DB": "App-Db", "POSTGRES_USER": "app_user"}),
        compose_json({"POSTGRES_DB": "app_db", "POSTGRES_USER": "user name"}),
        compose_json({"POSTGRES_DB": "1db", "POSTGRES_USER": "app_user"}),
        compose_json({"POSTGRES_DB": "a" * 64, "POSTGRES_USER": "app_user"}),
        compose_json({"POSTGRES_DB": None, "POSTGRES_USER": "app_user"}),
        compose_json(["POSTGRES_DB=a", "POSTGRES_DB=b", "POSTGRES_USER=u"]),
        compose_json(["POSTGRES_DB=a", "POSTGRES_USER=u", "POSTGRES_USER=v"]),
        compose_json("POSTGRES_DB=a"),
        json.dumps(
            {"services": {"backend": {"environment": {"POSTGRES_DB": "a", "POSTGRES_USER": "u"}}}}
        ),
    ],
)
def test_invalid_or_mismatched_target_fails_before_pg_dump(sandbox, document) -> None:
    result, lines, destination = run_backup(sandbox, FAKE_COMPOSE_JSON=document)

    assert result.returncode != 0
    assert "could not be resolved" in result.stderr
    assert dump_calls(lines) == []
    assert not any("exec" in line.split() for line in lines)
    assert artifacts(destination) == []
    assert "Backup created successfully" not in result.stdout


def test_output_never_contains_identifiers_secrets_or_compose_output(sandbox) -> None:
    result, _, _ = run_backup(sandbox)
    output = result.stdout + result.stderr

    assert "Backup target resolved from Compose configuration." in output
    for secret in (
        "backup_target_db",
        "backup_target_user",
        "never-printed",
        '"services"',
        "DATABASE_URL",
    ):
        assert secret not in output


def test_failed_resolution_output_never_contains_values(sandbox) -> None:
    result, _, _ = run_backup(
        sandbox,
        FAKE_COMPOSE_JSON=compose_json({"POSTGRES_DB": "Bad-Name-123", "POSTGRES_USER": "u"}),
    )

    assert "Bad-Name-123" not in result.stdout + result.stderr


def test_failed_pg_dump_leaves_no_artifact(sandbox) -> None:
    result, _, destination = run_backup(sandbox, FAKE_FAIL_DUMP="1")

    assert result.returncode != 0
    assert artifacts(destination) == []
    assert "Backup created successfully" not in result.stdout


def test_failed_archive_validation_leaves_no_artifact(sandbox) -> None:
    result, _, destination = run_backup(sandbox, FAKE_FAIL_LIST="1")

    assert result.returncode != 0
    assert artifacts(destination) == []


@pytest.mark.parametrize(("signal_name", "code"), [("INT", 130), ("TERM", 143), ("HUP", 129)])
def test_interrupt_removes_temporary_artifacts_and_publishes_nothing(
    sandbox, signal_name, code
) -> None:
    result, lines, destination = run_backup(sandbox, FAKE_SIGNAL=signal_name)

    assert result.returncode == code
    assert artifacts(destination) == []
    assert not any("pg_restore" in line for line in lines)
    assert "Backup created successfully" not in result.stdout


def test_dump_is_published_last_after_every_required_step() -> None:
    script = BACKUP.read_text()

    steps = [
        script.index("--format=custom"),
        script.index("pg_restore"),
        script.index('mv "$temporary_checksum" "$checksum_file"'),
        script.index('mv "$temporary_file" "$backup_file"'),
        script.index('published="true"', script.index('mv "$temporary_file" "$backup_file"')),
    ]
    assert steps == sorted(steps)
    assert len(re.findall(r'mv "\$temporary_file" "\$backup_file"', script)) == 1


def test_resolution_happens_before_the_dump_or_any_artifact() -> None:
    script = BACKUP.read_text()

    assert script.index('backup_target="$(resolve_backup_target)"') < script.index("mkdir -p")
    assert script.index('backup_target="$(resolve_backup_target)"') < script.index(
        "--format=custom"
    )
