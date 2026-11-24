#!/bin/sh

set -eu

compose_file="${COMPOSE_FILE:-compose.release.yaml}"
env_file="${ENV_FILE:-.env}"
project_name="${COMPOSE_PROJECT_NAME:-fantasy-what-if}"
backup_file=""
confirmed="false"

usage() {
    cat <<'EOF'
Usage:
  scripts/restore.sh --backup PATH --confirm-database-replacement

This operation replaces the current Fantasy What-If PostgreSQL
database with the selected backup.

Required:
  --backup PATH
  --confirm-database-replacement

Optional environment variables:
  COMPOSE_FILE          Default: compose.release.yaml
  ENV_FILE              Default: .env
  COMPOSE_PROJECT_NAME  Default: fantasy-what-if
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --backup)
            if [ "$#" -lt 2 ]; then
                printf 'Missing value after --backup.\n' >&2
                exit 2
            fi

            backup_file="$2"
            shift 2
            ;;
        --confirm-database-replacement)
            confirmed="true"
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown argument: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ -z "$backup_file" ]; then
    printf 'A backup path is required.\n' >&2
    usage >&2
    exit 2
fi

if [ "$confirmed" != "true" ]; then
    printf 'Restore confirmation was not supplied.\n' >&2
    printf 'No containers or databases were changed.\n' >&2
    exit 2
fi

if [ ! -f "$backup_file" ]; then
    printf 'Backup file not found: %s\n' "$backup_file" >&2
    exit 1
fi

if [ ! -s "$backup_file" ]; then
    printf 'Backup file is empty: %s\n' "$backup_file" >&2
    exit 1
fi

if [ ! -f "$compose_file" ]; then
    printf 'Compose file not found: %s\n' "$compose_file" >&2
    exit 1
fi

if [ ! -f "$env_file" ]; then
    printf 'Environment file not found: %s\n' "$env_file" >&2
    exit 1
fi

# Exported shell values for the database settings must never override the
# selected env file, so they are removed from every Compose invocation.
compose() {
    env \
        -u POSTGRES_DB \
        -u POSTGRES_USER \
        -u POSTGRES_PASSWORD \
        docker compose \
        --project-name "$project_name" \
        --env-file "$env_file" \
        --file "$compose_file" \
        "$@"
}

# Resolve the restore target from the resolved Compose configuration of the
# PostgreSQL service. The env file is never sourced, executed, or printed, and
# the resolved values are never echoed.
resolve_restore_target() {
    if ! command -v python3 > /dev/null 2>&1; then
        printf 'python3 is required to resolve the restore target.\n' >&2
        return 1
    fi

    compose config --format json 2> /dev/null |
        python3 -c '
import json
import re
import sys

IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def fail():
    sys.stderr.write("Restore target could not be resolved safely.\n")
    sys.exit(1)


try:
    service = json.load(sys.stdin)["services"]["database"]
    environment = service["environment"]
except Exception:
    fail()

values = {"POSTGRES_DB": [], "POSTGRES_USER": []}

if isinstance(environment, dict):
    for name in values:
        if name in environment:
            values[name].append(environment[name])
elif isinstance(environment, list):
    for entry in environment:
        name, _, value = str(entry).partition("=")
        if name in values:
            values[name].append(value)
else:
    fail()

for found in values.values():
    if len(found) != 1 or not isinstance(found[0], str) or not IDENTIFIER.match(found[0]):
        fail()

sys.stdout.write(values["POSTGRES_DB"][0] + " " + values["POSTGRES_USER"][0] + "\n")
'
}

if ! restore_target="$(resolve_restore_target)" || [ -z "$restore_target" ]; then
    printf 'Restore target could not be resolved. No services or databases were changed.\n' >&2
    exit 1
fi

# The identifiers were validated above, so word splitting is safe here.
# shellcheck disable=SC2086
set -- $restore_target
database_name="$1"
database_user="$2"
unset restore_target

printf 'Restore target resolved from Compose configuration.\n'

printf 'Validating backup archive before stopping services.\n'

compose exec -T database \
    pg_restore \
        --list \
    < "$backup_file" \
    > /dev/null

checksum_file="${backup_file}.sha256"

if [ -f "$checksum_file" ]; then
    printf 'Validating recorded SHA-256 checksum.\n'
    (
        cd "$(dirname "$backup_file")"
        sha256sum \
            --check \
            "$(basename "$checksum_file")"
    )
else
    printf 'Warning: no checksum file was found.\n' >&2
fi

# Every service that can write to PostgreSQL, plus the ingress.
# Keep this list in sync with the compose files; the restore tests
# assert that every database-writing service is listed.
writer_services="backend backend-worker sync-scheduler migrate"
ingress_services="frontend"
restart_services="backend backend-worker sync-scheduler frontend"

writers_stopped="false"
restore_finished="false"

on_exit() {
    status=$?

    # A second signal must not interrupt or duplicate the warning.
    trap '' INT TERM

    if [ "$restore_finished" = "true" ]; then
        return 0
    fi

    if [ "$writers_stopped" = "true" ]; then
        printf '\nRestore did not complete (exit %s).\n' "$status" >&2
        printf 'Application services remain stopped.\n' >&2
        printf 'Do not start writers until the database state is verified.\n' >&2
    fi
}

on_signal() {
    # Exit nonzero; the EXIT trap prints the single warning, if applicable.
    exit "$1"
}

trap on_exit EXIT
trap 'on_signal 130' INT
trap 'on_signal 143' TERM

stop_writers() {
    printf 'Stopping ingress and database-writing services.\n'

    # Ingress first so no new requests arrive while writers drain.
    # shellcheck disable=SC2086
    if ! compose stop $ingress_services; then
        printf 'Could not stop ingress services.\n' >&2
        return 1
    fi

    # shellcheck disable=SC2086
    if ! compose stop $writer_services; then
        printf 'Could not stop every database-writing service.\n' >&2
        return 1
    fi

    writers_stopped="true"
}

verify_writers_stopped() {
    # shellcheck disable=SC2086
    running="$(compose ps --status running --quiet $writer_services)" || {
        printf 'Could not verify that writers are stopped.\n' >&2
        return 1
    }

    if [ -n "$running" ]; then
        printf 'A database-writing service is still running.\n' >&2
        printf 'Refusing to replace the database.\n' >&2
        return 1
    fi
}

stop_writers
verify_writers_stopped

printf 'Ensuring PostgreSQL remains available.\n'

compose up \
    --detach \
    database

database_container="$(
    compose ps \
        --quiet \
        database
)"

if [ -z "$database_container" ]; then
    printf 'PostgreSQL container could not be located.\n' >&2
    exit 1
fi

database_health=""

attempt=1

while [ "$attempt" -le 30 ]; do
    database_health="$(
        docker inspect \
            --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
            "$database_container"
    )"

    printf 'Database health attempt %02d: %s\n' \
        "$attempt" \
        "$database_health"

    if [ "$database_health" = "healthy" ]; then
        break
    fi

    if [ "$database_health" = "unhealthy" ] || \
       [ "$database_health" = "exited" ]; then
        printf 'PostgreSQL did not become healthy.\n' >&2
        exit 1
    fi

    attempt=$((attempt + 1))
    sleep 2
done

if [ "$database_health" != "healthy" ]; then
    printf 'Timed out waiting for PostgreSQL.\n' >&2
    exit 1
fi

printf 'Replacing the resolved application database.\n'

compose exec -T database \
    psql \
        --username="$database_user" \
        --dbname=postgres \
        --set=ON_ERROR_STOP=1 \
        --command="DROP DATABASE IF EXISTS \"$database_name\" WITH (FORCE);" \
        --command="CREATE DATABASE \"$database_name\" OWNER \"$database_user\";"

printf 'Restoring PostgreSQL backup.\n'

if ! compose exec -T database \
    pg_restore \
        --username="$database_user" \
        --dbname="$database_name" \
        --no-owner \
        --no-acl \
        --exit-on-error \
        --verbose \
    < "$backup_file"; then
    printf '\nRestore failed.\n' >&2
    printf 'Application services remain stopped.\n' >&2
    printf 'Investigate PostgreSQL logs before retrying.\n' >&2
    exit 1
fi

printf 'Applying any migrations newer than the backup.\n'

compose run \
    --rm \
    migrate

# Re-verify no writer was started while the restore ran.
verify_writers_stopped

printf 'Starting application services.\n'

# shellcheck disable=SC2086
compose up \
    --detach \
    $restart_services

restore_finished="true"

printf 'Restore completed successfully.\n'
printf 'Run the following command to inspect service health:\n'
printf '  docker compose --project-name %s --env-file %s --file %s ps\n' \
    "$project_name" \
    "$env_file" \
    "$compose_file"
