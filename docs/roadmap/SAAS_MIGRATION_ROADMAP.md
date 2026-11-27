# Fantasy What-If SaaS Migration Roadmap

## Document Status

- **Status:** Draft, replaced for ADR 0010
- **Governing decision:** `docs/architecture/0010-Personal-Accounts-and-Shared-Canonical-Leagues.md`
- **Target:** Hosted SaaS of independent personal accounts and shared canonical leagues
- **Initial hosting:** Henry's homelab
- **Initial capacity target:** 50 to 100 registered users
- **Initial sport:** Fantasy football
- **Implemented provider:** ESPN Fantasy Football
- **Intended public-launch providers:** Sleeper and Yahoo, **not implemented**
- **Registration:** Public registration remains closed

## Purpose

This roadmap defines the sequence that replaces the implemented League Workspace model with independent personal accounts backed by shared canonical league data (ADR 0010). It supersedes the earlier workspace-centered phase plan, which survives only in Git history and in the implemented-history section below.

Each branch preserves a working application and includes automated tests, migration validation where applicable, documentation updates, and a coherent merge boundary.

## Governing Documents

1. `AGENTS.md`
2. `docs/architecture/0010-Personal-Accounts-and-Shared-Canonical-Leagues.md`
3. `docs/product/SAAS_REQUIREMENTS.md`
4. `docs/architecture/SAAS_TARGET_ARCHITECTURE.md`
5. ADRs 0001, 0003, 0008, 0009, and the non-superseded parts of ADRs 0002, 0005, 0006, 0007
6. ADR 0004 is superseded and historical

## Migration Principles

- **New hosted deployment.** No v1 data is migrated or preserved.
- **Add before removing.** The canonical access schema lands before behavior changes. The workspace schema is retired only after the account-only cutover and the frontend switcher are validated.
- **Keep main deployable.** Every branch leaves main working.
- **Prove authorization before opening registration.** Cross-user negative tests precede any registration reopening.
- **Prove recovery before public launch.** Backup, restore, and rollback are exercised.
- **No provider network call inside an open transaction.** Branch 1 fixes the existing violation first.
- **No unsupported provider claims.** Sleeper and Yahoo are described as unimplemented until their branches merge and validate.

## Branch Sequence

```text
0.  docs/personal-account-league-model
1.  fix/sync-fetch-outside-transaction
2.  feature/canonical-league-access-schema
3.  feature/league-import-service
4.  feature/league-season-sync-orchestration
5.  feature/league-season-authorization
6.  feature/account-only-identity-cutover
7.  feature/frontend-league-switcher
8.  feature/account-deletion-and-league-retention
9.  chore/retire-workspace-schema
10. feature/canonical-player-identity
11. feature/league-scoring-profiles
12. feature/sleeper-provider
13. feature/yahoo-provider
```

The independent NFL-statistics pipeline remains a separately estimated project and is not a numbered branch.

### Branch 0: `docs/personal-account-league-model`

Documentation only. Creates ADR 0010, marks ADR 0004 superseded, annotates ADRs 0002, 0005, 0006, and 0007, and rewrites `AGENTS.md`, `CLAUDE.md`, the requirements, the target architecture, and this roadmap.

Exit: documents are consistent, ADR 0010 is linked from every superseded ADR, and registration remains closed in all launch language.

### Branch 1: `fix/sync-fetch-outside-transaction`

Move provider fetches out of open database transactions in the existing synchronization path: short load transaction, fetch with no transaction, short persistence transaction under the season advisory lock.

Exit: tests prove no provider call occurs inside an open transaction and existing behavior is preserved.

Status: implemented on this branch without a migration for scheduled, manual, initial, and season-import synchronization, provider credential replacement, and the `verify_provider` operator script. Until Branch 4 adds data-version comparison, the SyncRun id allocated during preparation acts as the stale-write marker.

### Branch 2: `feature/canonical-league-access-schema`

Additive migration: user-owned provider connection reference, `user_league_season_access` (unique on user and season), canonical uniqueness on provider and normalized league ID, `orphaned_at`, season sync lifecycle fields, and `user_preferences`. Migration rehearsal per the validation standard.

Exit: single Alembic head, upgrade and downgrade clean, constraints proven.

Status: implemented on this branch as Alembic revision `f1b6d2c8a9e4` (expand-only). Nothing reads the new tables as authorization yet, and the workspace model remains the active authority.

### Branch 3: `feature/league-import-service`

Proof-first import: T0 throttle, provider proof with no transaction, uniform failure, atomic T1. Idempotent repeats, concurrent-import safety, safe coordination with orphan purge.

Exit: concurrency tests yield one league, one season, one access row per user, and at most one active job. Failed proof persists nothing.

Status: implemented on this branch without a migration as additive `POST /api/league-imports` (ESPN only). Transitional caller eligibility until the identity cutover: a verified, active administrator with a standard session in an active workspace. Access and private connections are user-owned; no `ProviderLeagueClaim` is read, created or changed. Workspace onboarding is unchanged. Final workspace deletion now also locks Leagues where its users hold active access and decides orphan state after its users cascade; a League with a matching workspace claim is never orphaned.

Test infrastructure note: `chore/isolate-migration-rehearsal-database` moves every destructive migration rehearsal into a disposable `_test` database so `fantasy_what_if_test` no longer accumulates dropped-column slots. No product or migration change.

### Branch 4: `feature/league-season-sync-orchestration`

Replace workspace synchronization with league-season synchronization: `league-season-sync:{season_id}` key, credential selection through active access and active user-owned connections, anonymous public synchronization, sync states, data-version comparison, and no secrets or owner identity in payloads or status.

Exit: one active job per season, status responses disclose nothing about credential ownership.

Status: implemented on this branch as Alembic revision `b9e4c2d7a1f3` (`league_season_sync` job, payload exactly `{version: 1, season_id}`, NULL workspace allowed). `data_version` replaces the SyncRun-id stale-write marker, and every Season synchronization is a full sync. Manual and scheduled synchronization enqueue the Season job, persisted workspace incremental/full jobs re-enqueue it, onboarding and season import use the same Season-owned fetch/persist boundary, and `last_synced_at` is kept in step for current responses until a later branch retires it. Transitional: a Season with no active personal access whose League still belongs to a workspace synchronizes with that workspace's own connection under the workspace lifecycle rules, and its job carries the League's workspace ID so workspace job-status reads and deletion keep working. Branch 5 removes this fallback.

Temporary compatibility (`feature/canonical-league-workspace-compatibility`, no migration, between Branches 4 and 5): workspace onboarding resolves the canonical League through the global provider identity (`LeagueAccessRepository.lock_or_create_league`) and reuses the Season for the year; `ProviderLeagueClaim` only reserves the identity for the workspace and owns no canonical data. `League.installation_id` is nullable compatibility metadata set only by the claiming workspace's onboarding and never reassigned. Final workspace deletion no longer deletes Leagues: under the League lock it clears `installation_id` and the Season links to the workspace's connections, then sets `orphaned_at` when no active personal access remains (retention only; the purge worker is Branch 8). The deletion completion email no longer claims that league data was destroyed. Workspace authorization still reads `League.installation_id`; Branch 5 replaces it with per-user Season access and removes the claim, `installation_id`, and the sync fallback.

### Branch 5: `feature/league-season-authorization`

Per-user, per-season authorization on every league-data endpoint, uniform inaccessible-resource responses, seven-day reverification with the non-revoking outcome rules, and cross-user negative tests.

Exit: cross-user suite passes for every league-data endpoint.

Status: implemented on this branch without a migration as additive personal routes: `GET /api/leagues`, `GET /api/seasons/{season_id}` (safe access and sync state), Season-explicit snapshot, playoff-predictor and analytics reads under `/api/seasons/{season_id}/`, and `PUT /api/preferences/default-season`. `require_league_season_access` is the only authority: the user's own readable access row, `active` or `reverification_required` (the status is the bounded indicator), while synchronization eligibility and orphan retention still count only `active`; share-locked for the read's transaction so a concurrent revocation or deletion is authoritative. Missing, inaccessible and revoked Seasons return one identical 404. Data is served only after a recorded successful synchronization; otherwise the Season reports its `sync_state` and a 409. Transitional eligibility: a standard session of an active, email-verified user, role and workspace not consulted (spectators are never verified). A stale default preference is ignored on read and replaced only by explicit selection. Workspace routes, claims, `installation_id` and the sync fallback are unchanged. Deferred: periodic reverification, removal of workspace authority, and preference clearing on access removal (Branch 8).

### Branch 6: `feature/account-only-identity-cutover`

Personal-account registration (display name, email, password, confirm), email-only login, concurrent sessions, all-session revocation on reset. Retire administrator and spectator login, workspace slug login, claim onboarding, spectator setup, and activation.

Exit: no role-based or workspace-based login path remains reachable. **Public registration stays closed.**

### Branch 7: `feature/frontend-league-switcher`

Connected-leagues list, league and season switcher, import another league, default-season preference, empty-account and import states, synchronization states, owner-only reconnection messaging, season-isolated query cache. Retire the admin page and single-current-league assumptions.

Exit: frontend tests and 320-pixel layout pass.

### Branch 8: `feature/account-deletion-and-league-retention`

Remove league connection, account deletion with shared-data safety, orphan marking, 30-day retention, revival on re-import, and the purge job with a zero-active-access recheck under lock. Integrate durable deletion email (ADR 0009).

Exit: deletion and purge race tests pass.

### Branch 9: `chore/retire-workspace-schema`

Remove `workspace_id`, `role`, `username`, workspace and claim tables, and administrator and spectator constraints, after the earlier branches are validated.

Exit: migration rehearsal passes and no workspace code remains.

### Branch 10: `feature/canonical-player-identity`

`nfl_players`, `provider_player_mappings`, unresolved mappings, and season-aware affiliation. Not a blocker for the account cutover.

### Branch 11: `feature/league-scoring-profiles`

League-season scoring configuration, normalized representation, normalizer version, and deterministic scoring-profile fingerprint with profile reuse. Launch continues to use ESPN provider-supplied points.

### Branch 12: `feature/sleeper-provider`

Research first. Establish identity, authentication, privacy, and season behavior from evidence, then implement against the canonical provider contract. Not supported until merged and validated.

### Branch 13: `feature/yahoo-provider`

OAuth state and callback validation, token storage and refresh, revocation, league-key identity, scopes, and reauthorization. Requires a later consequential network and deployment review of OAuth callback exposure. Not supported until merged and validated.

## Separately Estimated Project: NFL Statistics

Independent NFL-statistics ingestion (source selection, licensing, corrections, player matching, defensive scoring, unusual bonuses, rounding, and long-term calculated points keyed by player, season, week, scoring-profile fingerprint, and calculator version) is not launch-critical for the account pivot and has its own estimate.

## Homelab Operations (Carried Forward)

The deployment and recovery work of ADR 0008 continues unchanged by this model change: automated off-host backups, isolated restore tests, external alerts, queue visibility, immediate registration disable, maintenance mode, incident runbooks, and documented rollback.

### Closed-Registration Staging Checkpoint (Historical)

Deployment safety implemented before closed staging:

- Explicit `APP_ENV=production` in production Compose.
- Fail-closed production keyring validation.
- API documentation disabled in production.
- Restore stops every database-writing service.
- Cloudflare Tunnel client-address trust boundary (loopback-bound Nginx).

Staging remains Cloudflare Access protected with temporary registration; native invitations or allowlists are not implemented and this is not the public-registration design.

Staging validated the retired workspace model. It is not acceptance evidence for the personal-account model.

## Launch Gates

**Public registration remains closed** until all of these hold:

- The account cutover (Branch 6), authorization (Branch 5), import (Branch 3), synchronization (Branch 4), and frontend selection flows (Branch 7) are complete and validated.
- Account deletion and retention (Branch 8) are implemented and tested.
- Cross-user negative tests pass for every league-data endpoint.
- Durable email (ADR 0009), audit events, registration throttling, enumeration-safe responses, and email readiness gating are in place.
- ESPN public and private smoke tests pass using user-owned credentials.
- Off-host backup, restore test, external alerts, maintenance mode, and rollback documentation exist.
- Privacy policy, terms, acceptable-use expectations, and the data-deletion process are published.
- Homelab availability limitations are disclosed, and mobile registration and import work at 320 pixels.
- Sleeper and Yahoo are not claimed as supported unless their branches are merged and validated.

Current staging is not acceptance evidence for the retired workspace model, and registration reopens only after the gates above are met.

### Launch Strategy

Open gradually after the gates: 10 to 20 users, then 25 to 50, then up to 100. Pause expansion on queue delay, API latency, provider failure growth, unsafe backup windows, unsustainable support load, or security concerns.

## Branch Rules

Each branch starts from updated `main`, contains one coherent slice, includes migrations and tests, passes validation, updates documentation, is smoke tested, and is merged only when complete. Do not combine slices.

## Validation Standard

### Backend

```bash
cd backend

uv run ruff format --check .
uv run ruff check .
uv run pytest -q
uv run alembic check
uv run alembic heads
uv run alembic current
```

### Frontend

```bash
cd frontend

npm test
npm run lint
npm run build
npm audit --audit-level=low
```

### Repository

```bash
git diff --check
git status --short --branch
```

### Migration Rehearsal

### Migration Rehearsal

For every identity or ownership migration:

1. Create or reuse a disposable PostgreSQL database. Never use the normal development or production database.
2. Apply migrations to head, downgrade to base, and upgrade again.
3. Compare the migrated schema with SQLAlchemy metadata when applicable.
4. Run post-migration validation, including uniqueness and access-relation constraints.
5. Start the application.
6. Verify two independent users can reach the same canonical season only through their own access rows.
7. Verify provider credential decryption.
8. Run synchronization.
9. Verify analytics.
10. Record duration and failures.

## Definition of Done for a Vertical Slice

- Product behavior matches ADR 0010 and the requirements.
- A database migration is included when required and rehearsed on a disposable database.
- Backend and frontend tests pass, including cross-user negative cases when applicable.
- Lint, format, and production build pass.
- Safe error behavior is verified and secrets are absent from logs and payloads.
- Documentation is updated and a rollback or forward-recovery strategy is documented.

## Roadmap Completion Criteria

1. Public users register independent personal accounts.
2. One account connects multiple leagues across providers.
3. Several users share canonical league data after independent proof.
4. Authorization is per user and season with uniform inaccessible-resource responses.
5. Import is proof-first and atomic.
6. Synchronization is league-season keyed and never calls a provider inside a transaction.
7. Connection removal and account deletion never harm other authorized users.
8. Orphaned leagues are retained 30 days and purged safely.
9. The workspace schema is retired.
10. ESPN is stable; Sleeper and Yahoo are operational only when implemented and validated.
11. Backups, monitoring, and incident response are proven on the homelab.

## Implemented History (Historical, Workspace Model)

> **Historical.** The items below describe the implemented League Workspace baseline that ADR 0010 supersedes. They record what exists in the code and are not active requirements. Terms such as administrator, spectator, workspace slug, claim, and activation refer to the retired model.

- **Session-replacement signaling:** Implemented by the session-lifecycle slice. Every session revocation now persists an explicit reason in `auth_sessions.revocation_reason` (`replaced`, `logout`, `password_reset`, `spectator_password_reset`, or `spectator_disabled`). A replaced, unexpired administrator session receives HTTP 401 with the stable `session_replaced` code. Every other failure, including rows revoked before the reason column existed, keeps the generic unauthenticated response.
- **Authoritative workspace lifecycle in `/api/auth/me`:** Implemented by the auth-context lifecycle slice. `GET /api/auth/me` returns a `workspace` object (`workspace_id`, `workspace_slug`, `installation_status`, `registration_status`, `onboarding_stage`, `workspace_access`) computed by the single mapping in `app/services/workspace_lifecycle.py`, which also backs `require_active_workspace`, administrator and spectator login, and password-reset eligibility. `workspace_access` is `active`, `provisional` (verified administrators only), `suspended`, or `inactive`; inactive responses carry no lifecycle detail, spectators never receive `onboarding_stage`, and the compatibility `username` is no longer returned. The frontend routes from this object and no longer probes `GET /api/leagues/seasons` to infer lifecycle state.
- **Provider selection and ESPN league-access validation:** Implemented by the onboarding provider-validation slice. A verified provisional administrator selects ESPN (Sleeper and Yahoo appear as "Coming later"), enters the League ID and season year, and the server detects public or private access. Credentials are requested only when ESPN requires them. Success stores the validated target on the single active provider connection and advances onboarding to `league_claim_pending`. Validation does not claim the league, import data, or start synchronization. A per-process limit on concurrent outbound ESPN requests is deferred to production hardening.
- **League claim confirmation:** Implemented by the onboarding league-claim slice. The administrator confirms the safe validated target. One transaction inserts the authoritative claim (provider plus League ID, independent of season) and advances to `initial_sync_pending`. A duplicate claim returns `409 provider_league_already_claimed`, deletes the losing provisional registration in the same transaction, clears its cookies and discloses no existing workspace details.
- **Initial synchronization:** Implemented by the onboarding initial-sync slice. The start request only queues one idempotent `initial_workspace_sync` job. The worker fetches ESPN data, creates the League and Season from real provider values, attaches the claim, and runs the existing full-season importer. Success advances to `spectator_setup_pending`; terminal failure advances to `initial_sync_failed` with an administrator retry. Spectator setup is the next slice, and activation follows it.
- **Shared spectator access:** Implemented by the onboarding spectator-access slice. At `spectator_setup_pending`, the administrator creates the single shared spectator password; onboarding then moves to `complete` without activating the workspace. Administration shows the workspace slug and the configured and enabled flags, changes the password, disables access, and re-enables access only with a new password. Each of these revokes spectator sessions with the existing explicit reasons. Spectators sign in with the workspace slug and the shared password only. Workspace activation is the next slice.
- **Workspace activation gate:** Implemented by the onboarding workspace-activation slice. At `complete`, the administrator reviews a safe summary and activates the workspace. The server independently rechecks every persisted invariant in one locked transaction, sets the active League and Season, and marks the workspace active, without provider I/O or synchronization. It is idempotent under repeats and concurrency. SaaS onboarding is now complete end to end.
- **Single-installation path retirement:** Implemented by the retirement slice. The `/api/setup` API, setup services and schemas, `POST /api/auth/login`, username authentication, the `create_admin` and v1 migration-audit scripts, the frontend `/setup` route, setup pages, legacy spectator panel and installation route guard were removed. `/setup` is now an unknown frontend route. Historical season import is scoped to the authenticated workspace. Removed endpoints return ordinary 404 responses with no aliases. Workspace deletion, which still fails on the restrictive `seasons.provider_connection_id` foreign key, is deferred to FR-20 with a deferrable `NO ACTION` recommendation.
- **Workspace deletion request and recovery (FR-20, partial):** Implemented by the deletion request-and-recovery slice on the existing `c4e8a1f7d3b9` schema, with no migration. `POST /api/admin/workspace/deletion` (standard session, CSRF, origin, current password, exact normalized slug, 5 attempts per administrator and workspace per hour) locks the workspace and, in one transaction, sets `deletion_pending` with `scheduled_for` 30 days out, revokes every administrator and spectator session with `workspace_deletion_requested`, cancels pending and retry-scheduled jobs, and queues one `workspace_deletion` job (`workspace_deletion:{workspace_id}`, empty payload). Repeated requests keep the original schedule. A correct administrator login before `scheduled_for` issues a non-renewable 15-minute `deletion_recovery` session, which the shared authentication dependency denies everywhere except `/api/auth/me`, logout, deletion status, and `POST /api/admin/workspace/deletion/cancellation`. Cancellation requires the password again, restores `active`, cancels the deletion job, revokes the recovery session, and requires a fresh login. Password reset stays available and never reactivates. Synchronization rechecks the workspace under lock before committing, and the scheduler skips non-active registrations. The `workspace_deletion` job is executed by the final-deletion worker slice below.
- **Final workspace deletion execution (FR-20):** Implemented by the final-deletion worker slice, with no migration. The `workspace_deletion` handler accepts only the empty version-1 payload and derives all state from persisted rows. Under the workspace row lock (the lock cancellation takes) it requires `deletion_pending` and `now >= scheduled_for`; at the exact cutoff deletion wins. An early claim returns the job to `pending` at `scheduled_for` without spending an attempt, and a canceled workspace is a safe no-op. One transaction deletes the connection-linked sync runs (including nullable-season runs) and the workspace leagues (seasons and imported data cascade), verifies that no season still references a workspace provider connection (the `RESTRICT` key is kept), inserts one anonymized `workspace_deletion_records` row (`completed`, retained for 365 days), and deletes the workspace row, which cascades connections, credentials, users, sessions, tokens, the provider claim, and jobs. This releases the claim, slug, and administrator email together at commit. The worker treats a deletion job whose row disappeared as complete only when the matching deletion record exists. Database lock, deadlock, and connection failures roll back and retry. Terminal or exhausted failures leave the workspace `deletion_pending` with identifiers reserved and no record. `python -m app.scripts.reenqueue_workspace_deletion --workspace-id N` lets an operator create or return the one active job for a due, still-pending workspace. It never deletes data or changes status. Provider-side OAuth revocation does not apply to ESPN and remains deferred. Request, seven-day reminder, and completion email are not sent; durable transactional email delivery remains required before public launch.
- **Durable asynchronous email delivery and response-timing equality:** Verification and password-reset email delivery currently runs synchronously after commit and can return a public `503 email_delivery_unavailable`. ADR 0009 replaces that with durable delivery: Resend on the free plan through bounded `httpx`, `transactional_email_deliveries` plus `BackgroundJob` jobs carrying only `version` and `delivery_id`, a NULL-workspace exception for email jobs enforced by a check constraint, worker-generated hashed tokens, a priority-budgeted daily and monthly quota, encrypted deletion-completion recipient, FR-20 deletion intents (request confirmation, seven-day reminder, completion), a configurable response floor on enumeration-sensitive routes, and registration readiness gating. The migration, worker, adapter, route changes, and removal of the `503` are required before public registration opens. Webhooks, bounce handling, and suppression synchronization are deferred until after launch.
- **Transactional-email schema foundation (ADR 0009, implemented):** Migration `d7f2b9a4c6e1` adds `transactional_email_deliveries` (scoped versus unscoped database constraints, no plaintext recipient), the `transactional_email_delivery` job type, nullable `background_jobs.workspace_id` guarded by a check constraint, an exact `{version, delivery_id}` job payload, idempotent atomic delivery-plus-job creation, and a separate `TRANSACTIONAL_EMAIL_KEYS` recipient keyring. No provider adapter, rendering, token generation, worker handler, route migration, FR-20 integration, quota counters, or retention cleanup exists yet; an email job without a handler fails terminally as `unsupported_job_type`.
- **Resend delivery worker (ADR 0009, implemented):** Migration `a3c9e5f1b7d2` links verification and reset tokens to the issuing delivery and attempt (nullable pair, `ON DELETE CASCADE`). The `transactional_email_delivery` handler verifies job and delivery ownership, resolves the recipient from the current user (or decrypts the unscoped completion recipient), commits a new attempt and a hashed worker-issued token before transmission, sends outside any transaction through a bounded direct-`httpx` Resend transport with idempotency key `{delivery_id}:{attempt}`, and records sent, retry-scheduled, or permanently-failed state with final-state recipient scrubbing. Uncertain outcomes retry as a new attempt with a new token; acceptance supersedes legacy and older-delivery tokens only. Provider 429 and quota responses defer the job without spending a job attempt. Delivery stays disabled by default (`TRANSACTIONAL_EMAIL_ENABLED=false`). Deletion request, reminder, and cancellation deliveries fail as unsupported until FR-20 integration; route migration, quota counters, readiness, response floor, webhooks, and retention cleanup remain open.
- **Durable identity email (ADR 0009, implemented):** No migration. Registration, verification resend, and password-reset request create a workspace-scoped delivery intent and its `transactional_email_delivery` job in the triggering transaction; they create no token and call no provider. Successful reset completion queues a tokenless `administrator_password_changed` notice atomically with the password change. The synchronous sender and the public `503 email_delivery_unavailable` are removed. The worker rechecks user and workspace eligibility. FR-20 deletion email, local quota, registration readiness, and the response-time floor remain open.
- **FR-20 deletion email (ADR 0009, implemented):** No migration. The deletion-request transaction cancels ordinary queued jobs and stale verification and earlier deletion-lifecycle deliveries (with their jobs), keeps password-reset and password-changed intents, and creates the request confirmation plus one reminder available at `scheduled_for` minus seven days; cancellation suppresses open confirmation and reminder intents and queues a cancellation notice; final deletion creates the encrypted, unscoped completion delivery and its NULL-workspace job before deleting the tenant, in the same transaction. Any intent failure rolls back the request, cancellation, or deletion. The worker rechecks the deletion generation from the persisted schedule and cancels stale notices. Local quota, registration readiness, the response-time floor, and retention cleanup remain open.

## Historical Phase Note

The earlier phase plan (Phases 0 through 15: workspace ownership schema, tenant-aware authentication, workspace-scoped analytics, provider league claims, background jobs, registration and onboarding, ESPN hardening, homelab operations, private beta, Sleeper, mobile-web, Yahoo, public registration, native foundation) is retired. Its background-job, durable-email, ESPN hardening, and homelab operations outcomes carry forward. Its workspace, claim, and role outcomes are superseded. The full text is available in Git history.
