#!/bin/sh

set -eu

template="${1:-.env.production.example}"
output="${2:-.env}"

if [ ! -f "$template" ]; then
    printf 'Template not found: %s\n' "$template" >&2
    exit 1
fi

if [ -e "$output" ]; then
    printf 'Refusing to overwrite existing file: %s\n' "$output" >&2
    exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
    printf 'OpenSSL is required to generate secure secrets.\n' >&2
    exit 1
fi

app_secret="$(
    openssl rand -base64 48 |
    tr -d '\n' |
    tr '+/' '-_'
)"

provider_key="$(
    openssl rand -base64 32 |
    tr -d '\n' |
    tr '+/' '-_'
)"

database_password="$(
    openssl rand -hex 32
)"

sed     -e "s|^APP_SECRET_KEY=generate-this-value$|APP_SECRET_KEY=${app_secret}|"     -e "s|^PROVIDER_CREDENTIAL_KEY=generate-this-value$|PROVIDER_CREDENTIAL_KEY=${provider_key}|"     -e "s|^POSTGRES_PASSWORD=generate-this-value$|POSTGRES_PASSWORD=${database_password}|"     "$template" > "$output"

chmod 600 "$output"

if grep -q 'generate-this-value' "$output"; then
    rm -f "$output"
    printf 'Secret placeholder replacement failed.\n' >&2
    exit 1
fi

printf 'Created %s with mode 600.\n' "$output"
printf 'Edit FRONTEND_ORIGIN and TRUSTED_ORIGINS before starting production.\n'
printf 'Keep this file private and include it in encrypted backups.\n'
