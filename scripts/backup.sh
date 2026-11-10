#!/bin/sh

set -eu

compose_file="${COMPOSE_FILE:-compose.release.yaml}"
env_file="${ENV_FILE:-.env}"
project_name="${COMPOSE_PROJECT_NAME:-fantasy-what-if}"
backup_directory="${BACKUP_DIRECTORY:-backups}"
timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
backup_file="${1:-${backup_directory}/fantasy-what-if-${timestamp}.dump}"

compose() {
    docker compose \
        --project-name "$project_name" \
        --env-file "$env_file" \
        --file "$compose_file" \
        "$@"
}

if [ ! -f "$compose_file" ]; then
    printf 'Compose file not found: %s\n' "$compose_file" >&2
    exit 1
fi

if [ ! -f "$env_file" ]; then
    printf 'Environment file not found: %s\n' "$env_file" >&2
    exit 1
fi

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

mkdir -p "$backup_parent"

temporary_file="${backup_file}.partial"

rm -f "$temporary_file"

cleanup() {
    rm -f "$temporary_file"
}

trap cleanup EXIT HUP INT TERM

compose exec -T database \
    pg_dump \
        --username="${POSTGRES_USER:-fantasy_app}" \
        --dbname="${POSTGRES_DB:-fantasy_what_if}" \
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

mv "$temporary_file" "$backup_file"
chmod 600 "$backup_file"

trap - EXIT HUP INT TERM

checksum_file="${backup_file}.sha256"

sha256sum "$backup_file" > "$checksum_file"
chmod 600 "$checksum_file"

printf 'Backup created successfully.\n'
printf 'Backup:   %s\n' "$backup_file"
printf 'Checksum: %s\n' "$checksum_file"
printf 'Mode:     %s\n' "$(stat -c '%a' "$backup_file")"
