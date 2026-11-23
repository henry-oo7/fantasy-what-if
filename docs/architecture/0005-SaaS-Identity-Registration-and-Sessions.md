# 0005: SaaS Identity, Registration, Login, and Session Policy

## Status

Proposed

## Date

2026-11-18

## Context

Fantasy What-If v1.0.1 uses a single-installation authentication model.

The current model includes:

- Globally unique usernames
- Globally unique optional email addresses
- One administrator
- One shared spectator
- Password authentication
- Argon2 password hashing
- Hashed session tokens
- CSRF protection
- Login throttling
- Session revocation
- Secure browser cookies

The SaaS architecture defined by ADR 0004 introduces multiple League Workspaces in one hosted application.

Each League Workspace retains:

- Exactly one administrator identity
- Zero or one shared spectator identity
- At most one active administrator session
- Multiple concurrent spectator sessions

The identity design must support:

- Public administrator registration
- Workspace isolation
- Shared spectator access
- Administrator password recovery
- Email verification
- Duplicate-claim privacy
- Future native mobile clients
- No migration of v1.0.1 administrators, spectators, or sessions
- Prevention of ambiguous credentials across workspaces

A single global username namespace would preserve the current login form, but it would create unnecessary conflicts for shared spectator names such as `spectator`.

A workspace-local username model would permit repeated spectator names, but every administrator would need to remember and enter a workspace slug even though the administrator email already provides a natural global identity.

## Decision

Fantasy What-If will use two explicit login modes.

### Administrator Login

Administrators authenticate with:

```text
email
password
```

Administrator email addresses are globally unique and verified.

### Spectator Login

Spectators authenticate with:

```text
workspace_slug
spectator_password
```

The spectator does not need a separate username in the first SaaS release.

The spectator identity remains one shared read-only identity owned by the League Workspace.

### Existing Username Field

The `users.username` column remains as generated internal storage for administrators and spectators. It is not a login identifier: administrators sign in by email and spectators by workspace slug plus the shared password. Username login has been removed.

Physically removing the column requires a separately approved migration.

## Rationale

This split model matches the two account types.

Administrators are individual account owners who need:

- A globally unambiguous identity
- Email verification
- Password recovery
- Security notifications
- One active session

Spectators use shared workspace access and need:

- A simple workspace-specific credential
- No individual registration
- No globally unique spectator username
- Multiple concurrent sessions
- Read-only access

This avoids forcing every spectator account to claim a globally unique username such as:

```text
spectator-529250970
jimmy-and-friends-viewer
league-spectator-8421
```

The workspace slug already identifies which shared spectator credential should be checked.

## Login Routes

The frontend will present two clear login choices:

```text
Administrator
Spectator
```

The administrator login form collects:

```text
Email
Password
```

The spectator login form collects:

```text
Workspace
Password
```

The forms may exist on one page with tabs or segmented controls.

The selected login mode must be preserved during validation errors.

The forms must not submit account-type guesses based on the entered value.

## Administrator Registration

Public registration creates an administrator account and a provisional League Workspace.

The registration form must collect:

- Display name
- Email address
- Password
- Password confirmation
- Workspace display name when required
- Workspace slug or slug preference
- Acceptance of required legal terms when introduced

The registration form does not need to collect a permanent administrator username.

If the existing database still requires a username during an intermediate migration, the backend may generate an internal unique value. That generated value must not become a public login dependency.

## Administrator Email

Administrator email is mandatory for SaaS registration.

Administrator email must be:

- Normalized
- Globally unique
- Verified before the workspace becomes fully active
- Used for password recovery
- Used for security notifications
- Hidden from spectators and other workspaces
- Excluded from ordinary application logs

Recommended normalization:

1. Trim surrounding whitespace.
2. Apply Unicode-safe case normalization.
3. Lowercase the domain portion.
4. Use the normalized value for uniqueness.
5. Preserve a suitable presentation form only when needed.

The application must not apply provider-specific email transformations such as removing dots from Gmail addresses.

For example, the service must not assume these are identical:

```text
first.last@example.com
firstlast@example.com
```

Email-provider-specific alias behavior is outside the application's responsibility.

## Email Verification

A new administrator must verify the registered email address.

The recommended sequence is:

1. Accept the registration form.
2. Create provisional registration state.
3. Generate a random verification token.
4. Store only a hash of the token.
5. Send a verification link.
6. Mark the email as verified after successful token redemption.
7. Revoke the token after use.
8. Continue or resume onboarding.

Verification tokens must be:

- Random
- Single-use
- Stored only as hashes
- Time limited
- Associated with the intended user
- Invalidated after successful verification
- Invalidated when the email address changes

Recommended initial expiration:

```text
24 hours
```

The administrator may request another verification message.

A new verification request should invalidate prior unused verification tokens for that user.

The resend endpoint must be rate limited.

## Registration Order

The registration and onboarding sequence will be:

1. Submit administrator registration.
2. Create provisional workspace and administrator.
3. Send email verification.
4. Verify administrator email.
5. Create an onboarding administrator session.
6. Select a provider.
7. Enter or authorize provider access.
8. Validate provider access.
9. Identify the provider league.
10. Claim the provider league atomically.
11. Queue initial synchronization.
12. Wait for persisted synchronization completion.
13. Configure the shared spectator password.
14. Activate the workspace.
15. Enter the application.

The initial synchronization must not execute inside the email-verification transaction or league-claim transaction.

## Provisional Registration

A registration that has not completed onboarding is provisional.

Provisional state may include:

- League Workspace
- Administrator identity
- Email-verification state
- Onboarding progress
- Onboarding session
- Validated provider details that are safe to persist
- Provider league claim after the claim stage

Provisional workspaces must not appear in ordinary spectator or analytics flows.

A provisional workspace must not be treated as active.

Abandoned provisional workspaces must be eligible for cleanup.

Recommended initial expiration policy:

```text
Unverified registration:
24 hours

Verified registration without league claim:
7 days

Claimed league with failed or incomplete synchronization:
30 days before operator review or cleanup
```

Cleanup must not release an active provider league claim without applying the documented claim-release policy.

## Workspace Slug

Every League Workspace will have a public login slug.

Example:

```text
jimmy-and-friends
```

The workspace slug is used for spectator login and human-friendly workspace identification.

The workspace slug is not an authorization secret.

### Slug Format

Recommended initial rules:

- Lowercase ASCII letters
- Digits
- Single hyphens between segments
- No leading hyphen
- No trailing hyphen
- No consecutive hyphens
- Minimum length: 3 characters
- Maximum length: 50 characters

Recommended pattern:

```regex
^[a-z0-9]+(?:-[a-z0-9]+)*$
```

### Slug Uniqueness

Workspace slugs must be globally unique.

Slug uniqueness is separate from provider league claim uniqueness.

The database must enforce slug uniqueness.

### Reserved Slugs

The service must prevent registration of reserved slugs.

Initial reserved examples include:

```text
admin
administrator
api
app
auth
billing
help
login
logout
operator
register
registration
security
setup
spectator
status
support
system
www
```

The final list should include frontend routes, API route prefixes, operational names, and protected brand terms.

### Slug Changes

Workspace slug changes are deferred for the first SaaS release.

Preventing slug changes avoids broken spectator login instructions and reduces account-recovery ambiguity.

A future slug-change feature must define:

- Reauthentication
- Redirect behavior
- Security notifications
- Audit events
- Cooling-off periods
- Old-slug reservation

## Spectator Identity

Each League Workspace may have zero or one spectator identity.

The spectator identity is not a separate registered person.

The spectator account represents shared read-only access to the workspace.

The spectator identity must have:

- Workspace ownership
- Spectator role
- Password hash
- Display name
- Active or disabled state
- Session-revocation support
- Created and updated timestamps

The spectator identity does not require:

- Email
- Email verification
- Individual username
- Password recovery email
- Personal profile
- Invitation

### Approved Spectator Model

The spectator decision is final:

- Exactly one shared spectator identity exists per workspace.
- The administrator creates and manages the shared spectator password.
- Spectators sign in with the workspace slug plus the shared password.
- Spectators do not register, provide or verify an email address, choose a username, recover their own password, receive individual invitations, or receive team-specific accounts.
- A retained spectator username is an internal compatibility value only. It is never chosen by the administrator, entered by a spectator, shown in the modern interface, returned by the modern spectator API, or described as part of the product account model.
- Multiple spectator browser sessions remain allowed.
- Changing the spectator password or disabling spectator access revokes existing spectator sessions. Re-enabling access never reveals the stored password.
- Spectator setup happens during onboarding after successful initial synchronization and before activation.

The public entry points read "Create a workspace", "Administrator sign in", and "Spectator sign in", never "Join as spectator".

Implementation:

- **Service scope:** every spectator operation is scoped to the administrator's workspace and serialized by locking that workspace row. `uq_users_one_spectator_per_workspace` remains the authoritative guard against a second spectator.
- **Internal username:** generated as `spectator-<random>` and never returned by any modern API.
- **Password:** 14 to 128 characters, never trimmed, stored only as an Argon2 hash.
- **Administrator management:** `/api/admin/spectator` returns only the workspace slug and the `configured` and `enabled` flags. Password change revokes sessions with `spectator_password_reset`, and disable revokes them with `spectator_disabled`.
- **Re-enabling:** requires a new password and revokes any remaining sessions with `spectator_password_reset`, because the shared password is replaced.

## Spectator Login

Spectator login requires:

```text
workspace_slug
spectator_password
```

The spectator login endpoint must:

1. Normalize the workspace slug.
2. Resolve the workspace without returning owner details.
3. Verify that the workspace permits spectator login.
4. Load the shared spectator identity.
5. Verify the password.
6. Create an additional spectator session.
7. Return the normal safe authentication response.

A successful spectator login must not revoke other spectator sessions.

## Spectator Login Privacy

Spectator login failures must avoid unnecessary workspace enumeration.

The API should use a common safe message for:

- Unknown workspace slug
- Missing spectator identity
- Disabled spectator access
- Incorrect spectator password
- Suspended workspace when disclosure is unsafe

Recommended safe message:

> The workspace or spectator credentials are incorrect.

Rate limiting may internally distinguish these cases without disclosing them to the caller.

The user interface may separately explain the expected format of a workspace slug.

## Spectator Credential Sharing

The administrator is responsible for sharing the workspace slug and spectator password with intended viewers.

Fantasy What-If must never display the existing spectator password after creation.

The administrator interface may:

- Create a new spectator password
- Replace the spectator password
- Disable spectator access
- Display the workspace slug
- Provide copyable login instructions

The administrator interface must not retrieve or decrypt a password because only the password hash is stored.

## Spectator Password Reset

Resetting the spectator password must:

1. Require an authenticated administrator.
2. Require current administrator reauthentication when the security design introduces sensitive-action confirmation.
3. Hash the replacement password with Argon2.
4. Replace the previous password hash.
5. Revoke all active spectator sessions for the workspace.
6. Create an audit event.
7. Return no password value.

If the administrator generated the password in the frontend, the plaintext may be displayed only before submission and must not be recoverable afterward.

## Administrator Login

Administrator login requires:

```text
email
password
```

The backend must:

1. Normalize the email.
2. Locate the administrator identity without exposing account existence.
3. Verify the password.
4. Verify that the account is active.
5. Verify that the workspace permits administrator login.
6. Revoke every existing active administrator session for the workspace.
7. Create the replacement session.
8. Commit revocation and creation atomically.
9. Return the safe authentication response.
10. Create an audit event.

A failed login must not revoke an existing administrator session.

## Administrator Session Replacement

Only one administrator session may remain active per workspace.

A successful login from Browser B replaces Browser A's administrator session.

Browser A must receive an unauthenticated response on the next protected request.

Recommended application error behavior:

```text
HTTP 401 Unauthorized
session_replaced
```

Recommended user-facing message:

> This administrator account was signed in from another device. Sign in again to continue.

The response must not disclose the replacing device, IP address, user agent, or location.

## Spectator Session Concurrency

Multiple spectator sessions may remain active at the same time.

Spectator sessions remain independently revocable.

The first SaaS release does not impose a hard spectator-session count.

Operational monitoring should track unusually high session creation or request volume.

A future abuse-control limit may be added without changing the account cardinality model.

## Browser Session Transport

The browser application will continue using secure cookies.

Production cookies must retain the existing security requirements:

- `Secure`
- `HttpOnly`
- `SameSite=Lax` unless architecture changes require otherwise
- Host-only scope where supported
- No token exposure to frontend JavaScript
- Explicit expiration
- Server-side revocation

The current CSRF protections remain required for state-changing browser requests.

## Future Native Mobile Authentication

A future native application must not depend on browser cookies or browser CSRF behavior.

Native authentication is deferred to a separate ADR.

The shared authorization model must still include:

```text
user_id
workspace_id
role
session_id
```

Possible native transport options include:

- Opaque bearer sessions
- Device-bound refresh tokens
- Short-lived access tokens plus rotating refresh tokens

The future mobile design must preserve:

- One active administrator session policy unless explicitly changed
- Concurrent spectator sessions
- Workspace authorization
- Session revocation
- Secure token storage using iOS Keychain or Android Keystore

The SaaS database model must not assume that every session uses a browser cookie.

## Password Requirements

The current password validation behavior should remain unless security review identifies a deficiency.

Passwords must:

- Be hashed using Argon2
- Never be logged
- Never be returned
- Never be stored reversibly
- Be subject to a reasonable minimum length
- Permit password-manager-generated values
- Permit spaces and common Unicode characters where safely supported
- Avoid arbitrary composition rules that reduce usability

Recommended minimum length:

```text
12 characters
```

A longer minimum may be required for the shared spectator password because multiple people may use the credential.

Recommended spectator minimum:

```text
14 characters
```

Maximum accepted password length should protect the application from unreasonable hashing input while supporting password managers.

Recommended maximum:

```text
128 characters
```

Exact limits must be validated against the existing password implementation before migration.

## Password Recovery

Administrator password recovery uses verified email.

The recovery process must:

1. Accept an email address.
2. Return a generic response regardless of account existence.
3. Generate a random reset token.
4. Store only a hash of the reset token.
5. Associate the token with the administrator.
6. Set a short expiration.
7. Send a reset link.
8. Require a new valid password.
9. Mark the token used.
10. Invalidate other outstanding reset tokens.
11. Revoke active administrator sessions.
12. Create an audit event.

Recommended reset-token expiration:

```text
30 minutes
```

Recommended generic response:

> If an eligible administrator account exists for that email address, password reset instructions have been sent.

Spectator password recovery is not offered directly to spectators.

The workspace administrator resets the shared spectator password.

## Email Change

Administrator email changes are deferred unless implemented as a complete verified workflow.

A future email-change process must:

- Require administrator reauthentication
- Verify the new email
- Preserve the old email until verification succeeds
- Enforce global uniqueness
- Notify the old email address
- Revoke or confirm sessions according to security policy
- Create an audit event

Direct unverified email replacement is not permitted.

## Registration and Login Throttling

Existing persistent login throttling must be preserved and adapted to email and workspace-aware spectator login.

Administrator throttling should derive privacy-preserving keys from:

```text
normalized_email
request_source
```

Spectator throttling should derive privacy-preserving keys from:

```text
normalized_workspace_slug
request_source
```

The stored throttle keys must remain hashes rather than plaintext login identifiers.

Registration endpoints must have separate limits for:

- Registration submission
- Verification-email resend
- Password-reset request
- Provider validation
- League-claim attempts

Exact production limits should be configurable.

Initial recommended limits include:

```text
Registration submissions:
5 per source per hour

Verification resends:
3 per account per hour

Password-reset requests:
3 per identity per hour

Provider validation attempts:
10 per workspace per hour

League-claim attempts:
10 per workspace per hour
```

These initial values require adjustment after observing legitimate use and abuse patterns.

## Provider League Claim Timing

Creating an administrator account does not claim a provider league.

The claim occurs only after:

- Email verification
- Provider selection
- Provider access validation
- Provider league identity normalization

The database claim insert is authoritative.

If the claim fails because another workspace already holds the league, onboarding must display:

> This fantasy league is already connected to Fantasy What-If. Contact the league owner and ask for the workspace's spectator account if you need viewing access.

The response must not expose the existing workspace slug.

The registrant must obtain the workspace slug and spectator password directly from the league owner.

## Duplicate League Registration Outcome

The duplicate-claim outcome must not leave an active registration.

The exact provisional-account disposition is:

1. Do not create canonical league, season, team, matchup, or synchronization data.
2. Do not store provider credentials after the claim conflict.
3. Revoke the provisional onboarding session.
4. Remove the unactivated provisional workspace and administrator in the claim-failure transaction or a deterministic compensation transaction.
5. Preserve only permitted security and abuse telemetry.
6. Return the safe conflict response.

This means a registrant cannot retain an empty SaaS account after attempting to register a provider league that is already claimed.

If support for empty user accounts is introduced later, that behavior requires a new product decision.

## Provisional Registration Cleanup

An unverified or abandoned provisional registration must be removable.

Cleanup must:

- Exclude active workspaces
- Exclude workspaces with an active synchronization
- Respect provider-claim retention policy
- Delete unused onboarding sessions
- Delete unused verification and reset tokens
- Delete unused encrypted provider credentials
- Create safe cleanup telemetry
- Avoid exposing another workspace's state

Cleanup should run as an operator or background maintenance task.

## Account Enumeration Protection

Public authentication and recovery endpoints must avoid disclosing whether an administrator email, workspace slug, or spectator identity exists.

Enumeration-sensitive routes include:

- Administrator login
- Spectator login
- Password-reset request
- Verification resend
- Registration availability checks
- Workspace-slug checks

A slug-selection interface may report that a requested slug is unavailable because slug uniqueness is inherently user-facing.

That response must not reveal whether the slug belongs to an active, suspended, provisional, or deleted workspace.

## Audit Events

The identity system must create structured audit events for:

- Administrator registration submitted
- Administrator email verified
- Registration expired
- Administrator login succeeded
- Administrator login failed
- Administrator session replaced
- Administrator logout
- Password-reset requested
- Password reset completed
- Spectator identity created
- Spectator login succeeded
- Spectator login failed
- Spectator password replaced
- Spectator access enabled
- Spectator access disabled
- Spectator sessions revoked
- Workspace suspended
- Workspace reactivated

Audit events must not contain:

- Passwords
- Password hashes
- Session tokens
- Session-token hashes
- Verification tokens
- Password-reset tokens
- Provider credentials
- OAuth authorization codes

## Database Direction

The target identity ownership model is:

```text
LeagueWorkspace
    -> User
        -> AuthSession
        -> EmailVerificationToken
        -> PasswordResetToken
```

Conceptual `users` additions include:

```text
workspace_id
email_verified_at
```

The final schema may also include:

```text
normalized_email
```

if normalization cannot be expressed safely through an index or database extension.

Conceptual workspace additions include:

```text
slug
normalized_slug
registration_status
```

A spectator may remain represented as a `User` row with role `spectator`, but spectator login does not require the row's username.

## Database Constraints

The identity schema must enforce:

### One Administrator per Workspace

```sql
CREATE UNIQUE INDEX
    uq_users_one_admin_per_workspace
ON users (workspace_id)
WHERE role = 'admin';
```

### One Spectator per Workspace

```sql
CREATE UNIQUE INDEX
    uq_users_one_spectator_per_workspace
ON users (workspace_id)
WHERE role = 'spectator';
```

### Unique Administrator Email

Administrator normalized email must be globally unique.

The final constraint may be partial if spectators do not have email addresses.

Conceptual form:

```sql
CREATE UNIQUE INDEX
    uq_users_admin_normalized_email
ON users (normalized_email)
WHERE role = 'admin';
```

### Unique Workspace Slug

```sql
UNIQUE (normalized_slug)
```

The database constraint is authoritative even when the frontend performs an availability check.

## No v1.0.1 Identity Migration

The hosted deployment is new. No v1.0.1 administrator, spectator, or session is migrated, and the username login, setup bootstrap administrator, and administrator-created spectator usernames have been removed.

- Administrators exist only through public registration with email verification.
- Spectators exist only through a workspace administrator setting the shared spectator password.
- `POST /api/auth/login` no longer exists; it returns ordinary 404 routing behavior.
- Generated internal usernames remain stored for administrators and spectators. They are never accepted by a login form, chosen by anyone, returned by an API, or displayed.

## Frontend Requirements

### Registration

The mobile-first registration interface must:

- Work at 320 CSS pixels
- Avoid page-level horizontal scrolling
- Use clear progress steps
- Preserve entered safe values across recoverable errors
- Avoid preserving passwords after failed navigation
- Explain email verification
- Explain league exclusivity before provider claim
- Explain the shared spectator model

### Administrator Login

The administrator form must:

- Clearly request email
- Support password managers
- Link to password recovery
- Avoid revealing account existence
- Explain session replacement when a previous session is displaced

### Spectator Login

The spectator form must:

- Clearly request workspace slug
- Clearly request spectator password
- Explain that the workspace slug and password come from the workspace administrator
- Avoid implying that spectators can self-register
- Avoid revealing whether a workspace exists

### Login Mode Selection

The two modes must remain visually distinct.

Suggested labels:

```text
League Administrator
League Spectator
```

The interface should remember the selected mode locally for convenience but must not automatically submit credentials to the other mode.

## Native-App Readiness

The identity architecture must support adding native clients without changing:

- User ownership
- Workspace ownership
- Role authorization
- Provider league claims
- Administrator session policy
- Spectator concurrency policy
- Audit-event semantics

Transport-specific authentication will be added separately.

## Security Consequences

### Positive

- Administrator identities have a natural recovery channel.
- Spectator credentials do not compete in a global username namespace.
- One administrator session is enforceable.
- Spectator concurrency is preserved.
- Workspace identity is explicit during spectator login.
- Provider-claim conflicts do not reveal workspace details.
- The model supports future native clients.

### Negative

- Administrator and spectator login flows differ.
- Email delivery becomes required before public registration.
- Username-based login is removed, so no v1 client remains compatible.
- Workspace slugs become public identifiers.
- Registration requires provisional-state cleanup.
- Session and throttle logic must distinguish administrator and spectator modes.

## Rejected Alternatives

### Globally Unique Usernames for All Accounts

Rejected because every shared spectator account would require an artificial globally unique username.

### Workspace Slug and Username for Administrators

Rejected for the first SaaS release because verified email provides a simpler administrator identity and recovery path.

### One User Record Shared Across Workspaces

Rejected because the first SaaS release intentionally supports one administrator per workspace and no cross-workspace membership.

### Individual Spectator Registration

Rejected because the product requirement is one shared spectator identity with concurrent sessions.

### Recoverable Spectator Passwords

Rejected because passwords must not be stored reversibly.

### Login Mode Inference

Rejected because guessing account type from entered credentials creates ambiguity and makes errors harder to explain safely.

## Follow-Up Work

After this ADR is accepted:

1. Add email-delivery and verification design.
2. Add provisional-registration persistence design.
3. Add workspace slug rules and reserved-slug tests.
4. Add administrator email normalization tests.
5. Add one-administrator and one-spectator constraints.
6. Add administrator session-replacement tests.
7. Add concurrent spectator-session tests.
8. Add enumeration-safe login responses.
9. Add migration validation for the existing administrator email.
10. Add native-authentication ADR before mobile-app implementation.

## Requirement Traceability

This decision supports:

- `FR-1`: Public registration
- `FR-3`: Workspace creation
- `FR-4`: One administrator per workspace
- `FR-5`: One active administrator session
- `FR-6`: Shared spectator account
- `FR-7`: Concurrent spectator sessions
- `FR-8`: Tenant-aware login
- `FR-12`: Atomic duplicate-claim rejection
- `FR-13`: Duplicate-claim privacy
- `FR-14`: Resumable onboarding
- `AR-1`: Workspace context
- `AR-2`: Administrator authorization
- `AR-3`: Spectator authorization
- `SR-1`: Secure session storage
- `SR-2`: Administrator replacement
- `SR-3`: Spectator concurrency
- `SR-4`: Role-change revocation
- `SER-2`: Registration abuse protection
- `SER-3`: Email and recovery
- `MR-1`: Responsive registration
- `MR-3`: Native-app-compatible APIs