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

## Provider Credentials

ESPN credentials are encrypted before database storage.

The encryption key is:

```text
PROVIDER_CREDENTIAL_KEY
```

Losing this key prevents recovery of encrypted provider credentials.

If the key or database is exposed:

1. Rotate ESPN credentials.
2. Generate a new provider encryption key.
3. Reconfigure the provider connection.
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