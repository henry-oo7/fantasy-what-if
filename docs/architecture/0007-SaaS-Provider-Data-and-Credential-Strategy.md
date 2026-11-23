# 0007: SaaS Provider Data, Identity, and Credential Strategy

## Status

Proposed

## Date

2026-11-18

## Context

ADR 0003 established Fantasy What-If's canonical provider synchronization architecture.

That architecture separates:

- Provider SDK and API behavior
- Canonical provider domain objects
- Generic synchronization
- Canonical PostgreSQL persistence
- Analytics processing

Fantasy What-If v1.0.1 implements ESPN as the only operational provider.

Sleeper and Yahoo exist as planned provider names but are not complete integrations.

The SaaS direction introduces additional requirements:

- Multiple workspaces may connect provider accounts simultaneously.
- Provider league ownership must be globally exclusive.
- Provider credentials must be isolated by workspace.
- Provider identities must be normalized consistently.
- OAuth callbacks must resolve the correct provisional workspace.
- Provider synchronization must execute through background jobs.
- Rate limits must be respected across the entire hosted service.
- Provider payloads must be serialized safely.
- Sanitized fixtures must support deterministic contract testing.
- Provider capabilities may vary by provider, season, and league.
- Provider outages must not expose secrets or affect unrelated workspaces.
- Existing ESPN environment credentials must not become a SaaS credential source.

This ADR extends ADR 0003 for hosted multi-tenant operation.

ADR 0003 remains authoritative for:

- Canonical provider domain objects
- Provider adapters
- Provider registry
- Generic synchronization
- Decimal handling
- Provider capabilities
- Provider error hierarchy
- Provider-neutral analytics
- Idempotent canonical persistence

## Decision

Fantasy What-If will maintain one canonical provider contract for ESPN, Sleeper, and Yahoo.

Each provider package will own:

- Provider API communication
- Provider authentication behavior
- Provider identifiers
- Provider response validation
- Provider-specific normalization
- Provider-specific pagination
- Provider-specific rate-limit interpretation
- Provider-specific retry hints
- Provider-specific token refresh
- Translation into canonical domain objects
- Sanitized provider fixtures

Generic services will own:

- Workspace authorization
- Provider league claims
- Credential vault access
- Background-job execution
- Synchronization policy
- Canonical consistency validation
- Canonical persistence
- Analytics rebuilds
- Synchronization-run persistence
- Safe progress reporting

Analytics and repositories must not consume raw provider SDK objects or raw provider API payloads.

## Provider Package Boundaries

The target package structure is:

```text
app/providers/
├── base.py
├── credential_vault.py
├── domain.py
├── errors.py
├── registry.py
├── contract_tests/
├── espn/
│   ├── adapter.py
│   ├── client.py
│   ├── credentials.py
│   ├── fixtures.py
│   ├── mappings.py
│   ├── protocols.py
│   └── provider.py
├── sleeper/
│   ├── adapter.py
│   ├── client.py
│   ├── credentials.py
│   ├── fixtures.py
│   ├── mappings.py
│   ├── protocols.py
│   └── provider.py
└── yahoo/
    ├── adapter.py
    ├── client.py
    ├── credentials.py
    ├── fixtures.py
    ├── mappings.py
    ├── oauth.py
    ├── protocols.py
    └── provider.py
```

The exact filenames may change, but provider-specific behavior must remain inside the provider package.

Generic services must not contain conditionals such as:

```python
if provider is FantasyProvider.ESPN:
    ...
elif provider is FantasyProvider.SLEEPER:
    ...
elif provider is FantasyProvider.YAHOO:
    ...
```

Provider-specific branching is acceptable only at provider registration, credential-schema selection, or provider-owned user-interface metadata boundaries.

## Canonical Provider Identity

Every provider integration must expose a normalized provider identity.

The canonical provider identity consists of:

```text
provider
normalized_provider_league_id
provider_season_id
```

The exclusive SaaS league claim uses:

```text
provider
normalized_provider_league_id
```

The season identifier is not part of league ownership because one claimed league may contain multiple imported seasons.

## Provider Identifier Rules

Provider identifiers are always represented as strings in application domain contracts.

Adapters must not expose provider identifiers as integers even when the provider currently returns numeric values.

This prevents:

- Integer overflow assumptions
- Loss of leading characters
- Provider-specific parsing in generic services
- Inconsistent Yahoo composite keys
- Future identifier-format migration

Original provider identifiers may be preserved for safe diagnostics, but uniqueness must use normalized identifiers.

## ESPN League Identity

The normalized ESPN league identity is the trimmed decimal league ID represented as a string.

Example:

```text
529250970
```

Normalization must:

1. Accept the provider value as text.
2. Trim surrounding whitespace.
3. Require only decimal digits.
4. Reject empty values.
5. Reject signs, decimal points, and scientific notation.
6. Preserve the canonical digit sequence.

The application must not cast the ID through a floating-point type.

The ESPN season identifier is the four-digit season year represented as a string.

Example:

```text
2026
```

The canonical claim is:

```text
espn
529250970
```

## Sleeper League Identity

The normalized Sleeper league identity is the trimmed Sleeper league ID represented as a string.

Sleeper identifiers must not be converted to JavaScript numbers because provider IDs may exceed JavaScript's safe integer range.

Frontend and backend contracts must treat Sleeper IDs as strings.

Example:

```text
112233445566778899
```

Normalization must:

1. Treat the value as text.
2. Trim surrounding whitespace.
3. Require the validated Sleeper identifier format.
4. Preserve the complete digit sequence.
5. Reject values that lose precision during numeric conversion.

The canonical claim is:

```text
sleeper
112233445566778899
```

## Yahoo League Identity

Yahoo league identity uses the Yahoo league key rather than a locally parsed numeric fragment.

Example:

```text
449.l.123456
```

The complete normalized league key is authoritative.

The application must not treat only `123456` as the league identity because Yahoo identifiers include contextual game or season information.

Normalization must:

1. Treat the value as text.
2. Trim surrounding whitespace.
3. Validate the expected Yahoo league-key structure.
4. Preserve meaningful separators.
5. Reject malformed or incomplete keys.
6. Use the complete normalized value for claims.

The canonical claim is:

```text
yahoo
449.l.123456
```

## Claim Identity Versioning

Provider identity normalization rules may evolve.

Provider league claims should record an identity-normalization version.

Conceptual fields include:

```text
provider
normalized_provider_league_id
identity_version
original_provider_league_id
```

Initial identity version:

```text
1
```

Changing normalization behavior must include:

- Migration analysis
- Collision detection
- Existing-claim reconciliation
- Provider-specific tests
- Rollback or forward-recovery planning

Normalization changes must not silently create duplicate claims.

## Provider Connection Ownership

Each provider connection belongs to exactly one League Workspace.

A provider connection must not be reused across workspaces.

A provider connection owns or references:

- Provider
- Encrypted credential envelope
- Credential-key version
- Credential-schema version
- Validation status
- Last validation time
- Safe validation error
- Authorization expiration when applicable
- Reauthorization requirement
- Created and updated timestamps

A workspace may initially have one active provider connection because the first SaaS release supports one claimed league.

The schema should not prevent multiple provider connections per workspace in the future.

## Credential Envelope

Provider credentials must be serialized into a versioned provider-specific envelope before encryption.

Conceptual plaintext envelope before encryption:

```json
{
  "schema_version": 1,
  "provider": "espn",
  "values": {
    "espn_s2": "<secret>",
    "swid": "<secret>"
  }
}
```

The plaintext envelope exists only in process memory during:

- Validation
- Encryption
- Decryption
- Provider construction
- Credential replacement

The database stores only the encrypted envelope.

The envelope must not be written to:

- Application logs
- Audit logs
- Job payloads
- Synchronization metadata
- Provider metadata
- API responses
- Error messages
- Test snapshots

## Credential Schema Versioning

Credential-key version and credential-schema version are separate concepts.

### Credential-Key Version

Credential-key version identifies the encryption key.

Example:

```text
credential_key_version = 2
```

### Credential-Schema Version

Credential-schema version identifies the provider credential payload format.

Example:

```text
credential_schema_version = 1
```

Separating the versions allows:

- Encryption-key rotation without changing provider fields
- Provider credential migration without immediate key rotation
- Support for Yahoo token metadata evolution
- Safe backward-compatible decryption

## Encryption

Provider credentials remain encrypted at rest using the existing credential vault.

The vault must support:

- Multiple active decryption keys during rotation
- One current encryption key
- Explicit key versions
- Authenticated encryption
- Safe decryption failure
- Re-encryption to the current key
- Credential replacement
- Secure logical deletion

Encryption keys must not be stored in PostgreSQL.

Encryption keys must be provided through protected deployment secrets.

## Key Rotation

Credential-key rotation must follow a staged process:

1. Add the new decryption key.
2. Mark the new key as the current encryption key.
3. Continue decrypting older envelopes.
4. Re-encrypt stored credentials in controlled batches.
5. Verify re-encrypted credentials.
6. Confirm no records use the retired key.
7. Remove the retired key from the deployment only after verification.

Rotation must be resumable and idempotent.

A failed credential rotation must not destroy the previously decryptable envelope.

## Credential Deletion

Deleting or disconnecting a provider connection must:

1. Disable synchronization.
2. Revoke provider authorization when supported.
3. Remove or null the encrypted credential envelope.
4. Record the credential deletion time.
5. Revoke pending jobs that require the credentials.
6. Create a safe audit event.
7. Preserve canonical data only according to workspace-deletion or disconnection policy.

Database backups may retain encrypted historical values until backup expiration.

The privacy and deletion documentation must explain this limitation.

## Workspace-Owned Provider Credentials

Shared ESPN environment credential fallback has been removed from normal application execution.

Every SaaS workspace must resolve provider credentials from its own encrypted `provider_connections` envelope. The active connection's recorded credential-key version selects the configured decryption key, and its credential-schema version validates the decrypted payload.

Setup and administrator credential replacement may accept ESPN credential fields from an authenticated request, but those values must be validated, encrypted, and stored for that workspace before later synchronization can use them.

The application does not read shared provider credentials from `ESPN_S2`, `ESPN_SWID`, `ESPN_LEAGUE_ID`, or `ESPN_SEASON` environment variables. This prevents separate workspaces from silently resolving the same operator-level ESPN account.

## Provider Validation Contract

Provider validation occurs before the permanent provider league claim.

Validation must:

- Confirm required credential fields
- Confirm provider access
- Confirm league visibility
- Resolve the normalized provider league identity
- Return safe league metadata
- Return safe failure information
- Use bounded network timeouts
- Avoid canonical persistence
- Avoid plaintext credential persistence
- Avoid holding a database transaction during the network request

Conceptual validation result:

```python
@dataclass(frozen=True, slots=True)
class ProviderValidationResult:
    is_valid: bool
    provider: FantasyProvider
    normalized_provider_league_id: str | None
    provider_season_id: str | None
    league_name: str | None
    safe_message: str
    retryable: bool
    metadata: Mapping[str, object]
```

Validation metadata must be sanitized.

## Discovery Contract

Provider discovery behavior differs by provider.

### ESPN

Initial ESPN onboarding requires explicit:

- League ID
- Season year
- Private-league credentials only when ESPN requires them

Account-wide league discovery is not assumed to be stable, and undocumented fan or account endpoints are not used. `SWID` is never placed in a URL.

ESPN access mode is detected, not asked:

1. One minimal anonymous request reads league settings (`view=mSettings`) for the exact League ID and season, with a 3-second connect and 7-second read timeout. A 401 on the season endpoint is retried once against the league-history endpoint, which serves older seasons.
2. A structurally valid response for the exact league and season means the league is public. No credentials are requested or stored.
3. A 401 from both endpoints means authentication is required. The administrator then submits `SWID` and `espn_s2`, which travel only as cookies, and the same bounded check runs with them.
4. A 404, other 4xx, redirect, malformed body, timeout, or outage cannot be classified safely. It returns one safe failure and never reveals credential fields.

Validation does not require `isLeagueManager`, a team-owner match, or a commissioner role, and does not persist member identities.

The validated target is stored on the workspace's single active `provider_connections` row: `provider_league_id`, `provider_season_year`, and `access_mode` (`public` or `private`). Public rows carry no ciphertext and no credential key version. Private rows carry ciphertext and the current key version. Database constraints enforce these states, and a partial unique index allows at most one active connection per workspace, regardless of provider.

The authoritative claim created in the next slice is unique by `provider` plus the normalized provider League ID. Season is not part of claim uniqueness.

### Sleeper

Sleeper may support discovery using a username or user ID.

Discovery must return only provider league references and safe display metadata.

### Yahoo

Yahoo discovery occurs after OAuth authorization and uses the authorized Yahoo account.

Discovery must not create claims automatically.

The user selects the intended league before the claim transaction.

## Canonical Data Completeness

Provider synchronization must distinguish among:

- Required canonical data
- Optional canonical data
- Unsupported canonical data
- Temporarily unavailable data
- Invalid provider data

Missing optional data must not silently become fabricated values.

Examples:

- Missing team logo becomes `None`, not a generated provider URL.
- Unsupported lineup history disables optimal-lineup capability.
- Missing playoff settings make the predictor configuration unavailable or require an allowed administrator fallback.
- Missing completed matchup scores cause synchronization validation failure.
- Missing future scores remain `None`.

## Provider Capabilities

Capabilities are persisted per season because provider behavior and available data may differ by season.

The capability model established in ADR 0003 remains authoritative.

Providers must report capabilities honestly.

A capability may depend on:

- Provider
- League type
- Season year
- Available historical data
- Scoring configuration
- Roster data
- Matchup data
- Provider authorization scopes

A provider being generally capable of a feature does not mean every season supports the feature.

## Canonical Configuration Snapshot

Each synchronized season should retain a sanitized canonical configuration snapshot.

The snapshot may include:

- Provider
- Provider season ID
- Team count
- Current week
- Regular-season length
- Playoff configuration
- Lineup-slot definitions
- Scoring-type name
- Capability flags
- Safe provider setting codes
- Snapshot schema version

The snapshot must not contain:

- Authentication cookies
- OAuth tokens
- Provider account email
- Provider account name unless explicitly required and approved
- Authorization headers
- Raw private API responses
- Unbounded provider payloads

## Provider Metadata Versioning

Provider metadata and configuration snapshots must include a schema version when the structure may evolve.

Conceptual shape:

```json
{
  "schema_version": 1,
  "provider": "espn",
  "values": {
    "playoff_seeding_rule": "TOTAL_POINTS_SCORED"
  }
}
```

Generic code must not depend on undocumented provider metadata keys.

When a provider-specific metadata key becomes important to generic behavior, the value should be promoted into a canonical field.

## Raw Provider Payload Policy

Production synchronization must not persist complete raw provider responses by default.

Raw provider responses may include:

- Personal information
- Credentials
- Authorization context
- Undocumented private data
- Large nested payloads
- Provider fields unrelated to Fantasy What-If

Permitted production persistence includes:

- Canonical records
- Sanitized configuration snapshots
- Safe request identifiers
- Safe provider-revision markers
- Safe diagnostic codes
- Sanitized fixture records when explicitly generated outside production requests

Temporary raw payload inspection must occur through protected operator tooling and must not write secrets to ordinary logs.

## Sanitized Provider Fixtures

Every provider must maintain deterministic sanitized fixtures for tests.

Fixtures must be derived from:

- Hand-authored representative payloads
- Sanitized captured provider payloads
- Edge-case payloads built from verified provider behavior

Fixtures must remove or replace:

- Provider account names
- Personal names when not required
- Email addresses
- Cookies
- OAuth tokens
- Authorization headers
- Private league secrets
- Session IDs
- Device identifiers
- Message-board content
- Unrelated personal metadata

Synthetic fixture identifiers should be clearly nonproduction values.

Sanitization must preserve the response structure required for adapter tests.

## Fixture Provenance

Each sanitized captured fixture should document:

- Provider
- Capture date
- Provider API or SDK version
- Season context
- Sanitization method
- Features represented
- Known omissions
- Fixture schema version

The repository must not include a raw unsanitized source payload.

## Provider Contract Tests

Every implemented provider must pass a shared contract suite for supported operations.

Contract tests must verify:

- Provider identity is stable.
- League IDs are strings.
- Season IDs are strings.
- Team IDs are strings.
- Player IDs are strings.
- Domain objects are immutable.
- Points and scores use finite decimal values.
- Requested weeks are respected.
- Matchups reference known teams.
- Rosters reference known teams.
- Provider metadata contains no credential fields.
- Capabilities match available data.
- Stable ordering is preserved.
- Canonical validation errors are safe.
- Unsupported operations fail explicitly.

Provider contract tests must not require internet access.

## ESPN Hardening

ESPN must be hardened before public SaaS registration.

Required ESPN work includes:

- Remove ordinary SaaS environment credential fallback.
- Validate private-league credentials per workspace.
- Preserve encrypted `espn_s2` and `SWID`.
- Normalize `SWID` consistently.
- Validate future schedules using the correct ESPN schedule source.
- Test current, completed, and future matchup retrieval.
- Test private and public leagues.
- Test historical season import.
- Test playoff settings.
- Test lineup-slot mappings.
- Test scoring-rule serialization.
- Test missing and malformed provider fields.
- Test credential expiration behavior.
- Add sanitized fixtures covering representative leagues.
- Apply conservative background-job concurrency.
- Record provider-library version in diagnostics.

Because `espn-api` uses an unofficial ESPN interface, Fantasy What-If must expect provider behavior to change without notice.

## ESPN Library Boundary

The `espn-api` package must remain isolated inside the ESPN provider package.

Generic application code must not import:

```text
espn_api
League
BoxScore
Team
Player
```

from the ESPN SDK.

The ESPN provider package must translate SDK objects into Fantasy What-If canonical objects.

Provider-library upgrades require:

1. Fixture tests
2. Adapter tests
3. Full provider contract tests
4. Synchronization integration tests
5. Review of changed ESPN request behavior
6. Live verification against a controlled development league

## Sleeper Integration Strategy

Sleeper will be the second production provider.

Sleeper implementation must begin with provider research and verified fixtures.

The Sleeper adapter must investigate and document:

- User identity lookup
- League discovery
- League identifiers
- Season ancestry through prior-league references
- League settings
- Roster positions
- Scoring settings
- Rosters
- Matchups
- Weekly points
- Winners and ties
- Playoff rules
- Historical data availability
- Draft data relevance
- API pagination
- Error behavior
- Rate-limit behavior

Sleeper data must be translated into the existing canonical contracts.

Analytics must not add Sleeper-specific branches.

## Sleeper Authentication

Initial Sleeper read-only access may not require private credentials.

If public endpoints are sufficient, the provider connection may store only nonsecret configuration such as:

- Sleeper user ID
- Sleeper username
- Claimed league ID

Nonsecret configuration still belongs to the workspace.

The credential envelope may be absent when the provider requires no secret.

The provider connection must still be workspace-owned and validated.

## Sleeper Season Chains

Sleeper may represent recurring leagues through season-specific league IDs and previous-league references.

The adapter must distinguish:

- Provider league identity used for the exclusive claim
- Season-specific league identity
- Prior-season relationship
- Canonical Fantasy What-If league identity

The exact claim rule must be finalized after verified Sleeper research.

A season-specific Sleeper league ID must not accidentally allow the same continuing league to be claimed by multiple workspaces through different seasons.

If Sleeper lacks one stable cross-season identity, the claim strategy may require:

- A canonical league-chain identity
- The earliest known league ID
- Explicit prior-league traversal
- A provider-specific claim alias table

This decision must be grounded in verified Sleeper behavior before implementation.

## Yahoo Integration Strategy

Yahoo will be implemented after ESPN hardening and Sleeper support.

Yahoo requires OAuth authorization.

The Yahoo provider package owns:

- Authorization URL construction
- OAuth state generation
- Callback validation
- Authorization-code exchange
- Access-token refresh
- Token-expiration interpretation
- Scope validation
- Revocation
- Yahoo API request behavior
- Yahoo league discovery
- Yahoo identifier normalization

Generic authentication code must not interpret Yahoo tokens.

## Yahoo OAuth State

OAuth state must be:

- Random
- Single-use
- Time limited
- Stored only as a hash where practical
- Bound to the provisional workspace
- Bound to the administrator session
- Bound to the intended provider connection
- Invalidated after callback success or failure

The callback must reject:

- Missing state
- Unknown state
- Expired state
- Reused state
- Workspace mismatch
- Session mismatch
- Provider mismatch

OAuth state must not contain plaintext workspace IDs or secrets that can be modified by the browser.

## Yahoo Tokens

Yahoo access and refresh tokens must be encrypted at rest.

The credential envelope must track:

- Access token
- Refresh token
- Token type
- Granted scopes
- Access-token expiration
- Credential-schema version
- Provider account identifier when safely required

Tokens must not appear in:

- Job payloads
- Logs
- API responses
- Audit events
- Configuration snapshots
- Provider fixtures

## Yahoo Token Refresh

Token refresh occurs inside the Yahoo provider credential boundary.

Refresh must:

1. Lock the provider connection when needed.
2. Decrypt the current credential envelope.
3. Refresh using the provider endpoint.
4. Validate the returned token response.
5. Encrypt the replacement envelope.
6. Persist the replacement with optimistic or locked concurrency control.
7. Preserve the previous envelope until the replacement is valid.
8. Mark reauthorization required when refresh fails permanently.

Concurrent jobs must not overwrite newer refreshed credentials with older values.

## Provider Reauthorization

A provider connection may enter:

```text
reauthorization_required
```

When reauthorization is required:

- Scheduled synchronization is paused.
- Automatic retries stop.
- The administrator receives safe guidance.
- The provider league claim remains held.
- Canonical historical data remains available.
- Spectators may continue reading existing analytics unless the workspace is otherwise restricted.
- No token or credential details are exposed.

## Provider Errors

The canonical provider error hierarchy from ADR 0003 remains authoritative.

Each error must carry or allow derivation of:

- Provider
- Operation
- Safe user message
- Retryability
- Safe error code
- Original exception through chaining
- Optional retry-after duration

Provider errors must not include credentials or raw private payloads.

## Rate Limits

Provider rate limits must be enforced across the hosted service, not only per HTTP request.

The background worker must support:

- Provider-wide concurrency limits
- Provider-wide temporary backoff
- Workspace-specific retry state
- `Retry-After` handling
- Exponential backoff
- Jitter
- Safe terminal failure after maximum attempts

Exact provider limits must be based on verified behavior or official documentation.

Unverified numerical rate limits must not be invented.

Until verified, the homelab deployment should use conservative concurrency.

## Provider Outage Behavior

During a provider outage:

- Existing canonical analytics remain readable.
- New synchronization jobs may wait or retry.
- One provider's outage must not block jobs for another provider.
- Authentication into Fantasy What-If remains available.
- Safe status messages must distinguish stale data from application failure.
- Provider credentials remain protected.
- Workspaces must not see another workspace's job state.

The frontend should display the last successful synchronization time when useful.

## Synchronization Freshness

Canonical data is eventually refreshed from providers.

The application must distinguish:

- Last successful synchronization
- Current running synchronization
- Failed synchronization
- Pending retry
- Reauthorization required
- Provider outage
- Never synchronized

The application must not represent stale data as current when the last successful synchronization is materially old.

Exact stale-data thresholds may vary by provider and season state.

## Provider Connection Replacement

An administrator may need to replace credentials without changing the claimed league.

Credential replacement must:

1. Require administrator authorization.
2. Validate the replacement credentials.
3. Confirm access to the already claimed provider league.
4. Prevent replacement with credentials for a different league unless an explicit migration flow exists.
5. Encrypt the replacement envelope.
6. Commit the replacement atomically.
7. Preserve the old envelope until the replacement is valid.
8. Create an audit event.
9. Resume paused synchronization when appropriate.

## Provider Change

Changing a workspace from one provider to another is not supported in the first SaaS release.

A workspace claim is provider-specific.

Migrating a workspace to another provider could change:

- League identity
- Season identity
- Team identity
- Player identity
- Historical availability
- Analytics capabilities

Provider migration requires a separate product and data-migration design.

## Multiple Provider Connections

The first SaaS release supports one active provider connection per workspace.

The schema may permit additional inactive or historical provider connections if required for:

- Credential rotation
- Yahoo reauthorization
- Migration auditing

Generic application behavior must not assume that the lowest provider-connection ID is active.

The workspace or active season must reference the intended active connection explicitly.

## Provider Connection Deletion

A provider connection referenced by canonical seasons must not be deleted casually.

Safe options include:

- Disable the connection
- Remove credentials
- Preserve provider identity
- Preserve historical synchronization references
- Mark reauthorization required
- Delete the entire workspace through the tenant-deletion workflow

Foreign-key behavior should prevent accidental destruction of canonical season provenance.

## Data Serialization

Provider canonical objects are not persisted by serializing Python dataclasses directly.

Repositories map canonical fields explicitly to database models.

This avoids coupling the database schema to:

- Python module paths
- Dataclass field ordering
- Pickle behavior
- Provider SDK classes
- Undocumented metadata

JSON serialization is limited to versioned snapshots, metadata, job payloads, and safe configuration structures.

## JSON Validation

Versioned JSON fields must be validated at application boundaries.

The application must reject:

- Unsupported schema versions
- Missing required keys
- Unexpected secret-bearing keys
- Invalid value types
- Unbounded nested structures
- Nonfinite numeric values
- Oversized payloads

JSON fields must not become an unstructured substitute for proper relational columns.

## Decimal Serialization

Scores and points remain decimal-safe.

JSON API responses may serialize decimals as strings or validated JSON numbers according to the established API contract.

Internal provider conversion must continue using:

```python
Decimal(str(provider_value))
```

Binary floating-point values must not be persisted directly as canonical scores.

## Timestamp Serialization

Canonical timestamps must:

- Be timezone aware
- Use UTC in persistence
- Use ISO 8601 in API responses
- Preserve provider timestamps only when semantically useful
- Avoid local-time assumptions

Provider-local schedule labels must not replace canonical UTC timestamps when exact time is available.

## Audit Events

Provider-sensitive audit events include:

- Provider connection created
- Provider connection validated
- Provider connection validation failed
- Provider credentials replaced
- Provider credentials deleted
- Provider reauthorization required
- Provider OAuth authorized
- Provider OAuth revoked
- Provider league claim created
- Provider league claim conflict
- Provider synchronization requested
- Provider synchronization failed

Audit events must not include secrets or raw provider payloads.

## Provider Operator Diagnostics

Operator tools may display:

- Provider
- Workspace internal ID
- Provider connection internal ID
- Season internal ID
- Job ID
- Synchronization-run ID
- Safe error code
- Retryability
- Last validation time
- Last successful synchronization time
- Credential-key version
- Credential-schema version
- Reauthorization-required state

Operator tools must not display plaintext credentials or tokens.

## Migration from v1.0.1

The existing ESPN connection must migrate into the first League Workspace.

Migration must verify:

1. The provider is ESPN.
2. The league has a canonical ESPN provider league ID.
3. The existing credential envelope can be decrypted.
4. The credential-key version is known.
5. The migrated provider claim can be created.
6. No conflicting claim exists.
7. Existing seasons reference the migrated provider connection.
8. Existing synchronization history remains intact.
9. Environment credential fallback is no longer required for normal execution.
10. A synchronization succeeds using workspace-owned stored credentials.

If a provider connection lacks encrypted stored credentials, normal provider construction fails safely. The workspace administrator must submit replacement credentials through the supported credential-capture flow before synchronization can resume.

## Implementation Sequence

### Phase 1: Provider Identity Foundation

- Add provider identity normalization functions.
- Add identity-version fields.
- Add normalization contract tests.
- Add provider league claims.
- Backfill the existing ESPN claim.

### Phase 2: Credential Hardening

- Separate key version from schema version.
- Add credential replacement.
- Add key-rotation tooling.
- Disable ordinary SaaS environment fallback.
- Add safe credential diagnostics.

### Phase 3: ESPN Stabilization

- Add sanitized fixtures.
- Expand provider contract tests.
- Test public and private leagues.
- Test historical and future schedules.
- Test capability persistence.
- Test provider settings serialization.
- Add conservative worker concurrency.

### Phase 4: Sleeper

- Complete provider research.
- Record claim-identity decision.
- Implement Sleeper client and adapter.
- Add sanitized fixtures.
- Pass provider contract tests.
- Add setup and synchronization support.

### Phase 5: Yahoo OAuth

- Configure Yahoo OAuth application.
- Implement state and callback validation.
- Implement encrypted token storage.
- Implement refresh locking.
- Implement league discovery.
- Implement Yahoo adapter.
- Pass provider contract tests.
- Add reauthorization workflow.

### Phase 6: Provider Operations

- Add provider-specific metrics.
- Add rate-limit backoff.
- Add reauthorization alerts.
- Add operator diagnostics.
- Add credential-rotation runbook.

## Testing Requirements

### Identity Normalization

- ESPN IDs normalize deterministically.
- Sleeper IDs remain strings without precision loss.
- Yahoo league keys preserve required structure.
- The same textual ID may be claimed once per provider.
- Different providers may use the same textual ID.
- Malformed IDs are rejected.
- Normalization-version changes detect collisions.

### Credentials

- Credential envelopes encrypt and decrypt.
- Unknown key versions fail safely.
- Unknown schema versions fail safely.
- Key rotation preserves credentials.
- Failed rotation preserves the old envelope.
- Credential deletion prevents provider construction.
- Secrets do not appear in serialized metadata.
- Secrets do not appear in job payloads.
- Secrets do not appear in logs captured by tests.

### Provider Contracts

- Every implemented provider passes shared contract tests.
- Identifiers are strings.
- Scores are finite decimals.
- Requested weeks are respected.
- Stable ordering is preserved.
- Capabilities match source data.
- Unsupported features remain disabled.
- Safe errors contain no provider secrets.

### ESPN

- Public league validation succeeds.
- Private league validation succeeds with valid credentials.
- Invalid private credentials fail safely.
- Future schedule pairings use the correct source.
- Completed scores are preserved.
- Historical imports remain isolated by workspace.
- Environment fallback is disabled for SaaS tenants.

### Sleeper

- Large IDs preserve precision.
- Season chains resolve correctly.
- Matchups and rosters map to canonical teams.
- Unsupported data changes capabilities honestly.
- Public API failure behavior maps to canonical errors.

### Yahoo

- OAuth state is single-use.
- Expired state is rejected.
- Workspace mismatch is rejected.
- Authorization codes do not enter logs.
- Tokens encrypt successfully.
- Concurrent refresh does not overwrite newer credentials.
- Revoked authorization pauses synchronization.
- Reauthorization preserves canonical historical data.

### Tenant Isolation

- Workspace A cannot decrypt Workspace B's provider connection.
- Workspace A cannot validate replacement credentials against Workspace B's claim.
- Workspace A cannot trigger Workspace B's provider job.
- Provider errors do not reveal another workspace's league.
- Duplicate claims reveal no workspace identity.

### Serialization

- Configuration snapshots validate schema versions.
- Secret-bearing metadata keys are rejected.
- Oversized metadata is rejected.
- Nonfinite numbers are rejected.
- Decimal values round-trip correctly.
- UTC timestamps round-trip correctly.

## Operational Consequences

### Positive

- All providers share one canonical synchronization architecture.
- Provider credentials remain tenant isolated.
- Provider identity claims are stable and race-safe.
- ESPN hardening occurs before registration scale increases.
- Sleeper and Yahoo can be added without provider branches in analytics.
- Yahoo token refresh remains inside its provider boundary.
- Sanitized fixtures reduce reliance on live provider tests.
- The architecture remains compatible with homelab hosting and future cloud migration.

### Negative

- Provider integrations require substantial fixture and contract maintenance.
- ESPN depends on an unofficial interface that may change unexpectedly.
- Sleeper cross-season identity may require additional claim modeling.
- Yahoo OAuth adds callback, token, and reauthorization complexity.
- Credential rotation requires operational tooling.
- Versioned metadata and credential envelopes add migration work.
- Provider-wide rate protection requires shared worker coordination.

## Rejected Alternatives

### Store Raw Provider Payloads as the Primary Data Model

Rejected because raw payloads are unstable, provider-specific, potentially sensitive, and unsuitable for provider-neutral analytics.

### Share One ESPN Credential Across SaaS Tenants

Rejected because shared application credentials violate workspace isolation and provider-account ownership.

### Numeric Provider IDs

Rejected because Sleeper IDs may exceed safe JavaScript integer precision and Yahoo keys are not simple integers.

### Put OAuth Logic in Generic Authentication Services

Rejected because Yahoo token behavior is provider-specific and must remain inside the Yahoo provider boundary.

### Persist Credentials in Job Payloads

Rejected because job payloads are durable operational records and must not contain secrets.

### Depend on Live Provider APIs for Automated Tests

Rejected because tests would be nondeterministic, slow, rate-limited, and dependent on private credentials.

### Add Provider-Specific Analytics Branches

Rejected because canonical analytics must remain provider neutral.

## Follow-Up Work

After this ADR is accepted:

1. Document provider identity normalization functions.
2. Add provider league claim schema design.
3. Add credential-envelope schema versioning.
4. Add credential-key rotation runbook.
5. Remove SaaS dependence on ESPN environment fallback.
6. Build sanitized ESPN fixture tooling.
7. Expand shared provider contract tests.
8. Research and document Sleeper league-chain identity.
9. Research Yahoo OAuth scopes and token behavior.
10. Define provider-specific worker concurrency settings.
11. Add provider freshness and reauthorization states.
12. Add secure operator diagnostics.

## Requirement Traceability

This decision supports:

- `FR-9`: Provider selection
- `FR-10`: Provider validation
- `FR-11`: Exclusive provider league claim
- `FR-12`: Atomic duplicate-claim rejection
- `FR-13`: Duplicate-claim privacy
- `FR-15`: Initial synchronization
- `FR-17`: Workspace-scoped season import
- `PR-1`: Canonical provider contract
- `PR-2`: Provider identity normalization
- `PR-3`: ESPN hardening
- `PR-4`: Sleeper integration
- `PR-5`: Yahoo integration
- `PR-6`: Provider capability honesty
- `PR-7`: Provider rate protection
- `DR-3`: Canonical numeric data
- `DR-4`: Provider metadata
- `SYR-4`: Idempotency
- `SYR-5`: Failure isolation
- `SER-4`: Credential encryption
- `SER-5`: Secret logging