# Security Policy

## Supported Versions

Security fixes are provided for the latest published v1 release.

| Version | Support |
|---|---|
| Latest v1 release | Supported |
| Older v1 releases | Upgrade required |
| Unreleased branches | Best effort |

## Reporting a Vulnerability

Do not open a public GitHub issue for a suspected vulnerability.

Use GitHub private vulnerability reporting for:

```text
henry-oo7/fantasy-what-if
```

Include:

- Affected version or commit
- Reproduction steps
- Expected behavior
- Observed behavior
- Security impact
- Relevant logs with secrets removed
- Suggested remediation when available

Do not include:

- Real ESPN credentials
- Passwords
- Session cookies
- CSRF tokens
- Application secrets
- Provider encryption keys
- PostgreSQL passwords
- Private database dumps
- Personal user data

## Response Targets

Best-effort response targets:

| Stage | Target |
|---|---|
| Acknowledgment | Within 7 days |
| Initial assessment | Within 14 days |
| Remediation timeline | Based on severity and reproducibility |

These are project targets rather than guaranteed service-level agreements.

## Supported Security Boundary

The supported production architecture assumes:

- HTTPS is provided by Cloudflare Tunnel or another reverse proxy.
- Only the frontend container receives ingress.
- The frontend proxies `/api/` and `/health` to the backend.
- PostgreSQL remains on the internal Docker network.
- The backend remains on the internal Docker network.
- Production secrets remain in the local `.env`.
- `.env` has permissions set to `0600`.
- Database backups and checksum files remain private.
- Container images come from the official GHCR packages or trusted local builds.
- `FORWARDED_ALLOW_IPS` identifies only the frontend proxy.
- Production cookies remain secure.
- Administrator credentials are not shared.

Deployments outside this architecture may require additional security review.

## Authentication Security

Fantasy What-If uses:

- Argon2id password hashing
- Hashed session tokens
- Hashed CSRF tokens
- Pre-authentication CSRF protection
- Authenticated CSRF protection
- Trusted-origin validation
- Administrator authorization
- Secure production cookies
- Persistent PostgreSQL-backed login throttling
- HMAC-protected throttle identifiers
- Browser-specific failure tracking
- Network-prefix failure tracking
- Broad password-spray protection

Throttle records do not store raw:

- Usernames
- Browser identifiers
- IPv4 addresses
- IPv6 addresses

Authentication failures use generic responses to reduce account enumeration.

## Session Security

Production sessions require:

```text
SESSION_COOKIE_NAME=__Host-fantasy_session
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=lax
```

Production access must use HTTPS.

Session records store hashed tokens rather than raw session-cookie values.

Disabling spectator access revokes active spectator sessions.

## CSRF and Origin Security

State-changing browser requests require valid CSRF protection.

The application validates trusted origins using:

```text
FRONTEND_ORIGIN=
TRUSTED_ORIGINS=
```

These values must exactly match the browser-visible HTTPS origin.

Do not use wildcard trusted origins.

## Trusted Proxy Security

The supported production network gives the frontend proxy a fixed internal address.

Example:

```text
FRONTEND_PROXY_IP=172.31.100.10
FORWARDED_ALLOW_IPS=172.31.100.10
```

Never configure:

```text
FORWARDED_ALLOW_IPS=*
```

Trusting every forwarding source allows untrusted clients to spoof forwarded client information.

### Client address behind Cloudflare Tunnel

`cloudflared` runs on the Docker host and connects to the loopback-published frontend port. Docker presents that connection to Nginx from a private bridge address.

- Nginx accepts `CF-Connecting-IP` only when the immediate peer is a loopback or private address (`set_real_ip_from`). For any other peer the header is ignored.
- Nginx then replaces `X-Forwarded-For` with that single validated address and clears `Forwarded`, `X-Real-IP`, `CF-Connecting-IP`, and `True-Client-IP` before proxying. Client-supplied values are never appended.
- The backend never reads `CF-Connecting-IP`. Uvicorn honours `X-Forwarded-For` only from the Nginx address in `FORWARDED_ALLOW_IPS`, and the resolved address is normalized (IPv4/IPv6, IPv4-mapped) before it is used for login and registration throttling. Invalid values resolve to an unknown source.
- Cloudflare edge ranges are not hard-coded because the edge never connects to Nginx directly.

This boundary depends on `APP_BIND_ADDRESS=127.0.0.1`. Nginx cannot tell a LAN client from `cloudflared` when the port is published beyond loopback, so a non-loopback bind lets that client spoof `CF-Connecting-IP`. Do not combine Direct LAN Access with Cloudflare-based throttling expectations.

## Production Startup Validation

With `APP_ENV=production`, every backend process (API, worker, scheduler, migration) refuses to start unless:

- `PROVIDER_CREDENTIAL_KEYS` and `TRANSACTIONAL_EMAIL_KEYS` are non-empty, well-formed keyrings, and
- `PROVIDER_CREDENTIAL_CURRENT_KEY_VERSION` and `TRANSACTIONAL_EMAIL_CURRENT_KEY_VERSION` select a configured key.

Keys are never generated or defaulted at startup and error messages never contain key material. The Resend API key is not required while `TRANSACTIONAL_EMAIL_ENABLED=false`.

Production Compose files set `APP_ENV: production` explicitly for every backend-image service. Swagger UI, ReDoc, and the OpenAPI document are disabled in production and return 404.

## Provider Credentials

ESPN credentials are stored per workspace and encrypted before database storage. The application never reads ESPN credentials from environment variables.

Encryption uses only the versioned keyring:

```text
PROVIDER_CREDENTIAL_KEYS
PROVIDER_CREDENTIAL_CURRENT_KEY_VERSION
```

Losing a key version still referenced by a provider connection prevents recovery of that connection's encrypted credentials.

If a key or the database is exposed:

1. Add a new key version and make it current.
2. Re-encrypt stored credentials with the key rotation script.
3. Ask affected workspace administrators to replace their ESPN credentials.
4. Review application and proxy logs.
5. Review administrator and spectator access.
6. Replace affected backups.

Do not store real provider credentials in source code, tests, issues, pull requests, or application logs.

## Error and Validation Security

Validation responses omit submitted input values and validation context that could contain secrets.

Unexpected exceptions return a generic response to clients while technical details remain in server logs.

Do not modify error handling to return:

- Passwords
- ESPN cookies
- Session tokens
- CSRF tokens
- Database connection strings
- Application secrets
- Provider encryption keys
- Stack traces in production

## Browser Security Headers

Production responses include controls such as:

- Content Security Policy
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- Permissions Policy
- HTTP Strict Transport Security

Sensitive authentication, setup, and administration responses use cache-prevention headers.

## Container Security

Application containers run as unprivileged users:

```text
Backend: fantasy
Frontend: nginx
```

The backend, frontend, and scheduler use read-only root filesystems in the supported Compose topology.

Temporary writable filesystems are limited to required locations such as:

```text
/tmp
/var/cache/nginx
/var/run
```

Containers use:

```text
no-new-privileges:true
```

The backend and PostgreSQL do not publish host ports in production.

## Backup Security

Database backups can contain:

- Usernames
- Password hashes
- Session records
- League data
- Provider credential ciphertext
- Administrative state

Backup files and checksum files use permissions set to `0600`.

Protect backups with encrypted storage.

Do not upload private database backups to:

- Public GitHub repositories
- Public issue attachments
- Public cloud buckets
- Unencrypted shared drives
- Chat systems
- Public support forums

## Upgrade Security

Before upgrading:

1. Record the deployed `APP_VERSION`.
2. Create a custom-format PostgreSQL backup.
3. Verify the SHA-256 checksum.
4. Preserve `.env`.
5. Review release notes.
6. Confirm sufficient disk space.
7. Confirm the prior image version remains available for rollback.

## Restore Security

The restore script requires:

```text
--confirm-database-replacement
```

The script validates the archive before stopping application services.

Before replacing the database it stops the frontend and every database-writing service (`backend`, `backend-worker`, `sync-scheduler`, `migrate`) and refuses to continue if any remains running.

When a checksum is available, the script verifies it before replacing the database.

If restoration fails, application services remain stopped.

Do not bypass these safeguards for production restoration.

## Dependency and Image Security

Release images are built through GitHub Actions for:

- `linux/amd64`
- `linux/arm64`

Published images include:

- OCI metadata
- Build provenance
- Software bill of materials attestations

Dependencies are locked through:

- `backend/uv.lock`
- `frontend/package-lock.json`

Security updates should include refreshed lockfiles and full regression testing.

## Secret Handling

Never commit:

- `.env`
- `.env.prod`
- ESPN cookies
- `SWID`
- Passwords
- Application secrets
- Provider encryption keys
- PostgreSQL passwords
- Private keys
- Session tokens
- CSRF tokens
- Private database dumps

Use placeholder values in examples and tests.

## Public Disclosure

Please allow reasonable time to assess and remediate a reported vulnerability before public disclosure.

Coordinated disclosure protects self-hosters who may need to upgrade before technical details become public.