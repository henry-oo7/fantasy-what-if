#!/bin/sh

set -eu

compose_file="${COMPOSE_FILE:-compose.release.yaml}"
env_file="${ENV_FILE:-.env}"
project_name="${COMPOSE_PROJECT_NAME:-fantasy-what-if}"
backup_directory="${BACKUP_DIRECTORY:-backups}"
timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
backup_file="${1:-${backup_directory}/fantasy-what-if-${timestamp}.dump}"

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

# Resolve the backup target from the resolved Compose configuration of the
# PostgreSQL service. The env file is never sourced, executed, or printed, and
# the resolved values are never echoed.
resolve_backup_target() {
    if ! command -v python3 > /dev/null 2>&1; then
        printf 'python3 is required to resolve the backup target.\n' >&2
        return 1
    fi

    compose config --format json 2> /dev/null |
        python3 -c '
import json
import re
import sys

IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def fail():
    sys.stderr.write("Backup target could not be resolved safely.\n")
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

if ! backup_target="$(resolve_backup_target)" || [ -z "$backup_target" ]; then
    printf 'Backup target could not be resolved. No backup was created.\n' >&2
    exit 1
fi

# The identifiers were validated above, so word splitting is safe here.
# shellcheck disable=SC2086
set -- $backup_target
database_name="$1"
database_user="$2"
unset backup_target

printf 'Backup target resolved from Compose configuration.\n'

database_container="$(
    compose ps \
        --quiet \
        database
)"

if [ -z "$database_container" ]; then
    printf 'The PostgreSQL service is not running.\n' >&2
    printf 'Start the stack before creating a backup.\n' >&2
    exit 1
fi

database_status="$(
    docker inspect \
        --format '{{.State.Status}}' \
        "$database_container"
)"

if [ "$database_status" != "running" ]; then
    printf 'The PostgreSQL container is not running.\n' >&2
    exit 1
fi

backup_parent="$(dirname "$backup_file")"
temporary_file_name="$(basename "$backup_file").partial"

mkdir -p "$backup_parent"

temporary_file="${backup_file}.partial"
temporary_checksum="${backup_file}.sha256.partial"
checksum_file="${backup_file}.sha256"

rm -f "$temporary_file" "$temporary_checksum"

published="false"

# Remove every unfinished artifact. A final checksum is removed too when the
# dump itself was never published, so nothing looks like a completed backup.
cleanup() {
    trap '' HUP INT TERM
    rm -f "$temporary_file" "$temporary_checksum"

    if [ "$published" != "true" ]; then
        rm -f "$checksum_file"
    fi
}

on_signal() {
    exit "$1"
}

trap cleanup EXIT
trap 'on_signal 129' HUP
trap 'on_signal 130' INT
trap 'on_signal 143' TERM

compose exec -T database \
    pg_dump \
        --username="$database_user" \
        --dbname="$database_name" \
        --format=custom \
        --no-owner \
        --no-acl \
        --verbose \
    > "$temporary_file"

if [ ! -s "$temporary_file" ]; then
    printf 'PostgreSQL produced an empty backup.\n' >&2
    exit 1
fi

compose exec -T database \
    pg_restore \
        --list \
    < "$temporary_file" \
    > /dev/null

chmod 600 "$temporary_file"

# The checksum names the final file, so it can be verified after publication.
(
    cd "$backup_parent"
    sha256sum "$temporary_file_name" | sed "s|  .*|  $(basename "$backup_file")|"
) > "$temporary_checksum"
chmod 600 "$temporary_checksum"

# Publish the checksum first and the dump last: the dump only appears once
# every required step has succeeded.
mv "$temporary_checksum" "$checksum_file"
mv "$temporary_file" "$backup_file"
published="true"

printf 'Backup created successfully.\n'
printf 'Backup:   %s\n' "$backup_file"
printf 'Checksum: %s\n' "$checksum_file"
printf 'Mode:     %s\n' "$(stat -c '%a' "$backup_file")"
