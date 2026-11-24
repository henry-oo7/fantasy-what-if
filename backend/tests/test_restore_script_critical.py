# ruff: noqa: E501
"""Critical restore-script safety tests using an intercepted docker command.

A fake `docker` executable records every invocation and never touches a real
container or database. The restore script is run against it in a temp dir.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RESTORE = REPO_ROOT / "scripts" / "restore.sh"
WRITERS = {"backend", "backend-worker", "sync-scheduler", "migrate"}

FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
if [ "$1" = "compose" ]; then
    printf 'POSTGRES_DB=%s POSTGRES_USER=%s\\n' "${POSTGRES_DB:-unset}" "${POSTGRES_USER:-unset}" >> "$FAKE_ENV_LOG"
fi
if [ -n "${FAKE_SIGNAL_EARLY:-}" ]; then
    kill -"$FAKE_SIGNAL_EARLY" "$PPID"
    sleep 1
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
    *" stop "*)
        if [ -n "${FAKE_FAIL_STOP:-}" ]; then exit 1; fi ;;
    *" ps "*"--status running"*)
        if [ -n "${FAKE_RUNNING_WRITER:-}" ]; then printf 'abc123\\n'; fi
        exit 0 ;;
    *" ps "*"--quiet"*"database"*)
        printf 'dbcontainer\\n'; exit 0 ;;
    *"pg_restore"*"--exit-on-error"*)
        cat > /dev/null
        if [ -n "${FAKE_SIGNAL:-}" ]; then
            kill -"$FAKE_SIGNAL" "$PPID"
            sleep 1
            exit 0
        fi
        if [ -n "${FAKE_FAIL_RESTORE:-}" ]; then exit 1; fi ;;
esac
case "$1" in
    inspect) printf 'healthy\\n' ;;
esac
exit 0
"""


@pytest.fixture
def sandbox(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(sleep.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    (tmp_path / "env").write_text(
        "POSTGRES_DB=restore_target_db\n"
        "POSTGRES_USER=restore_target_user\n"
        "POSTGRES_PASSWORD=never-printed\n"
        "touch ENV_FILE_WAS_EXECUTED\n"
        "$(touch ENV_FILE_WAS_SOURCED)\n"
    )
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"synthetic")
    return tmp_path, bin_dir, backup


def run_restore(sandbox, **extra_env: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    tmp_path, bin_dir, backup = sandbox
    log = tmp_path / "docker.log"
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "COMPOSE_FILE": str(tmp_path / "compose.yaml"),
        "ENV_FILE": str(tmp_path / "env"),
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_ENV_LOG": str(tmp_path / "env.log"),
        **extra_env,
    }
    result = subprocess.run(
        ["sh", str(RESTORE), "--backup", str(backup), "--confirm-database-replacement"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )
    lines = log.read_text().splitlines() if log.exists() else []
    return result, lines


def index_of(lines: list[str], needle: str, start: int = 0) -> int:
    for position in range(start, len(lines)):
        if needle in lines[position]:
            return position
    raise AssertionError(f"{needle!r} not found in docker calls")


def test_every_writer_is_stopped_before_the_database_is_replaced(sandbox) -> None:
    result, lines = run_restore(sandbox)

    assert result.returncode == 0, result.stderr
    stop_call = " ".join(line for line in lines if " stop " in f" {line} ")
    for writer in WRITERS:
        assert writer in stop_call
    assert index_of(lines, " stop ") < index_of(lines, "DROP DATABASE")
    assert index_of(lines, "--status running") < index_of(lines, "DROP DATABASE")


def test_restore_refuses_when_a_writer_cannot_be_stopped(sandbox) -> None:
    result, lines = run_restore(sandbox, FAKE_FAIL_STOP="1")

    assert result.returncode != 0
    assert not any("DROP DATABASE" in line for line in lines)
    assert not any(" up " in f" {line} " for line in lines)


def test_restore_refuses_when_a_writer_is_still_running(sandbox) -> None:
    result, lines = run_restore(sandbox, FAKE_RUNNING_WRITER="1")

    assert result.returncode != 0
    assert "still running" in result.stderr
    assert not any("DROP DATABASE" in line for line in lines)
    assert not any("pg_restore" in line and "--exit-on-error" in line for line in lines)


def test_failed_restore_leaves_writers_stopped(sandbox) -> None:
    result, lines = run_restore(sandbox, FAKE_FAIL_RESTORE="1")

    assert result.returncode != 0
    assert "remain stopped" in result.stderr
    drop = index_of(lines, "DROP DATABASE")
    after = lines[drop:]
    assert not any("backend" in line and " up " in f" {line} " for line in after)
    assert not any("run --rm migrate" in line or "run" in line.split() for line in after)


def test_successful_restore_restarts_only_owned_services(sandbox) -> None:
    result, lines = run_restore(sandbox)

    assert result.returncode == 0, result.stderr
    final_up = [line for line in lines if "--detach" in line and "database" not in line.split()]
    assert len(final_up) == 1
    started = set(final_up[0].split()) & (WRITERS | {"frontend"})
    assert started == {"backend", "backend-worker", "sync-scheduler", "frontend"}
    assert "migrate" in " ".join(lines)  # migration ran before the restart
    assert index_of(lines, "migrate") < lines.index(final_up[0])


def test_restore_output_never_contains_secrets(sandbox) -> None:
    result, _ = run_restore(sandbox)

    assert "never-printed" not in result.stdout + result.stderr


def test_restore_script_lists_every_database_writing_compose_service() -> None:
    import re

    script = RESTORE.read_text()
    declared = set(re.search(r'writer_services="([^"]+)"', script).group(1).split())

    for compose_name in ("compose.prod.yaml", "compose.release.yaml"):
        services = re.findall(r"^  ([a-z-]+):$", (REPO_ROOT / compose_name).read_text(), re.M)
        backend_image = {
            name
            for name, block in _service_blocks(REPO_ROOT / compose_name).items()
            if "DATABASE_URL" in block
        }
        assert backend_image <= declared, (compose_name, backend_image - declared)
        assert set(services) >= declared


def _service_blocks(path: Path) -> dict[str, str]:
    import re

    text = path.read_text()
    parts = re.split(r"^  ([a-z-]+):$", text, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


# --- Restore target resolution, interruption and no-secret contracts ---------


def calls_matching(lines: list[str], needle: str) -> list[str]:
    return [line for line in lines if needle in line]


def compose_json(environment) -> str:
    import json

    return json.dumps({"services": {"database": {"environment": environment}}})


def test_restore_script_has_no_default_target_and_never_sources_the_env_file() -> None:
    script = RESTORE.read_text()

    assert "POSTGRES_DB:-" not in script
    assert "POSTGRES_USER:-" not in script
    assert "fantasy_what_if" not in script
    assert "fantasy_app" not in script
    for forbidden in ('. "$env_file"', "source ", 'eval "', "set -a"):
        assert forbidden not in script


def test_target_comes_from_the_selected_env_file_through_compose(sandbox) -> None:
    result, lines = run_restore(sandbox)

    assert result.returncode == 0, result.stderr
    assert any(line.startswith("compose") or " config " in f" {line} " for line in lines)
    config_call = calls_matching(lines, "--format json")[0]
    assert str(sandbox[0] / "env") in config_call
    drop = calls_matching(lines, "DROP DATABASE")[0]
    assert '"restore_target_db"' in drop
    assert "--username=restore_target_user" in drop
    restore_call = calls_matching(lines, "--exit-on-error")[0]
    assert "--dbname=restore_target_db" in restore_call
    assert "--username=restore_target_user" in restore_call


def test_env_file_is_never_sourced_or_executed(sandbox) -> None:
    result, _ = run_restore(sandbox)

    assert result.returncode == 0, result.stderr
    assert not (sandbox[0] / "ENV_FILE_WAS_EXECUTED").exists()
    assert not (sandbox[0] / "ENV_FILE_WAS_SOURCED").exists()


def test_exported_shell_values_cannot_override_the_resolved_target(sandbox) -> None:
    result, lines = run_restore(
        sandbox,
        POSTGRES_DB="exported_other_db",
        POSTGRES_USER="exported_other_user",
        POSTGRES_PASSWORD="exported-secret",
    )

    assert result.returncode == 0, result.stderr
    drop = calls_matching(lines, "DROP DATABASE")[0]
    assert "restore_target_db" in drop
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
    ],
)
def test_invalid_target_fails_before_any_service_or_database_change(sandbox, document) -> None:
    result, lines = run_restore(sandbox, FAKE_COMPOSE_JSON=document)

    assert result.returncode != 0
    assert "could not be resolved" in result.stderr
    assert not any(" stop " in f" {line} " for line in lines)
    assert not any("DROP DATABASE" in line or "CREATE DATABASE" in line for line in lines)
    assert not any("pg_restore" in line for line in lines)
    assert not any(" up " in f" {line} " for line in lines)


def test_target_mismatch_with_the_database_service_fails_before_drop(sandbox) -> None:
    # The target is read only from the "database" service; a document that
    # describes a different service cannot supply it.
    import json

    document = json.dumps(
        {
            "services": {
                "backend": {"environment": {"POSTGRES_DB": "app_db", "POSTGRES_USER": "app_user"}}
            }
        }
    )

    result, lines = run_restore(sandbox, FAKE_COMPOSE_JSON=document)

    assert result.returncode != 0
    assert not any("DROP DATABASE" in line for line in lines)
    assert not any(" stop " in f" {line} " for line in lines)


def test_output_never_contains_target_identifiers_or_secrets(sandbox) -> None:
    result, _ = run_restore(sandbox)
    output = result.stdout + result.stderr

    assert "Restore target resolved from Compose configuration." in output
    for secret in ("restore_target_db", "restore_target_user", "never-printed", '"services"'):
        assert secret not in output
    assert "DATABASE_URL" not in output


def test_failed_resolution_output_never_contains_values(sandbox) -> None:
    result, _ = run_restore(
        sandbox,
        FAKE_COMPOSE_JSON=compose_json({"POSTGRES_DB": "Bad-Name-123", "POSTGRES_USER": "u"}),
    )

    assert result.returncode != 0
    assert "Bad-Name-123" not in result.stdout + result.stderr


@pytest.mark.parametrize("signal_name", ["INT", "TERM"])
def test_interrupt_after_writer_shutdown_leaves_writers_stopped(sandbox, signal_name) -> None:
    result, lines = run_restore(sandbox, FAKE_SIGNAL=signal_name)

    assert result.returncode == {"INT": 130, "TERM": 143}[signal_name]
    assert result.stderr.count("Application services remain stopped.") == 1
    drop = index_of(lines, "DROP DATABASE")
    after = lines[drop:]
    assert not any(" up " in f" {line} " for line in after)
    assert not any("--detach" in line for line in after)
    assert "Restore completed successfully" not in result.stdout


@pytest.mark.parametrize("signal_name", ["INT", "TERM"])
def test_interrupt_before_writer_shutdown_makes_no_stopped_claim(sandbox, signal_name) -> None:
    result, lines = run_restore(sandbox, FAKE_SIGNAL_EARLY=signal_name)

    assert result.returncode != 0
    assert "remain stopped" not in result.stderr
    assert not any(" stop " in f" {line} " for line in lines)
    assert not any("DROP DATABASE" in line for line in lines)


def test_only_a_successful_restore_reaches_restart_commands(sandbox) -> None:
    for extra in ({"FAKE_FAIL_RESTORE": "1"}, {"FAKE_SIGNAL": "TERM"}, {"FAKE_FAIL_STOP": "1"}):
        result, lines = run_restore(sandbox, **extra)
        assert result.returncode != 0
        assert not any("--detach" in line and "backend" in line.split() for line in lines), extra
        (sandbox[0] / "docker.log").unlink(missing_ok=True)

    result, lines = run_restore(sandbox)
    assert result.returncode == 0
    assert any("--detach" in line and "backend-worker" in line.split() for line in lines)


def test_all_writers_are_confirmed_stopped_before_drop_with_resolved_target(sandbox) -> None:
    result, lines = run_restore(sandbox)

    assert result.returncode == 0, result.stderr
    stop_index = index_of(lines, " stop ")
    verify_index = index_of(lines, "--status running")
    drop_index = index_of(lines, "DROP DATABASE")
    assert index_of(lines, "--format json") < stop_index < verify_index < drop_index
    verify_call = lines[verify_index]
    for writer in WRITERS:
        assert writer in verify_call.split()
