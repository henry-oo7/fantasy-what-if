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

compose() {
    docker compose \
        --project-name "$project_name" \
        --env-file "$env_file" \
        --file "$compose_file" \
        "$@"
}

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

printf 'Stopping application services.\n'

compose stop \
    frontend \
    backend \
    sync-scheduler

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

database_name="${POSTGRES_DB:-fantasy_what_if}"
database_user="${POSTGRES_USER:-fantasy_app}"

printf 'Replacing database: %s\n' "$database_name"

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

printf 'Starting application services.\n'

compose up \
    --detach \
    backend \
    sync-scheduler \
    frontend

printf 'Restore completed successfully.\n'
printf 'Run the following command to inspect service health:\n'
printf '  docker compose --project-name %s --env-file %s --file %s ps\n' \
    "$project_name" \
    "$env_file" \
    "$compose_file"
