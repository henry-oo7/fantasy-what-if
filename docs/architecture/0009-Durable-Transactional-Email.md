# 0009: Durable Transactional Email

## Status

Proposed

## Date

2026-11-23

## Context

ADR 0005 requires verified administrator email, password recovery, and (through FR-20) deletion notices. ADR 0006 defines PostgreSQL-backed background jobs. Neither selects a provider or defines a delivery architecture.

The current implementation sends verification and reset email synchronously after commit and returns a public `503 email_delivery_unavailable` when the sender fails. That behavior has these defects:

- A provider outage turns public registration and recovery routes into visible failures, which also discloses account state through status and timing differences.
- Delivery depends on the request process surviving after commit.
- Raw verification and reset tokens exist only in the request that sends them, so a failed or uncertain send cannot be retried without a new token and no durable owner for that decision.
- The FR-20 deletion worker cannot send email without a best-effort side effect that a crash can lose or duplicate.
- The completion notice must outlive the tenant whose administrator email it targets.

The product operates at roughly 50 to 100 registered users from a homelab with a $0 budget. The provider must be usable on a permanent free plan without a credit card.

## Decision

Use Resend through direct, bounded HTTP calls with the existing `httpx` dependency. Add no email SDK and no new runtime dependency. Make delivery durable through a delivery-state table and the existing `BackgroundJob` queue. Public routes only queue delivery.

### Provider and Spending Boundary

- Resend permanent free plan only.
- $0 spend. Pay-as-you-go disabled. No automatic paid upgrade. No credit card or paid plan is required to launch.
- A configured daily limit, initially 100, and a configured monthly ceiling, initially 2,900, which keeps margin beneath the provider allowance.
- Capacity is reserved for account recovery and deletion notices (see Free-Tier Priority Budget).
- Quota exhaustion defers delivery and pauses new registration. It never spends money.

The limits are operational defaults held in configuration. They are not hard-coded provider guarantees.

Currently verified operational assumptions about the Resend free plan (as of this ADR; not permanent guarantees, to be re-confirmed before launch and whenever the plan changes):

- 3,000 transactional emails per month.
- 100 emails per UTC calendar day.
- Three sending domains.
- A request over quota is rejected with HTTP 429. Resend does not queue or retry it. Local delivery jobs therefore own all deferral and retry behavior.

The application budget below (100 per day, 2,900 per month) is operated by the application beneath those limits. It is not a Resend contractual guarantee.

### Queue and Delivery State

1. `BackgroundJob` remains the only queue. This ADR adds job type `transactional_email_delivery`. It adds no second queue, scheduler, or polling loop.
2. A new table, `transactional_email_deliveries`, holds durable delivery state. It records what must be sent and what happened. It never decides when work runs.
3. An email job payload contains only `version` and `delivery_id`. It never contains a recipient address, a token, a link, a subject, or a body.
4. The job idempotency key is `transactional-email:{delivery_id}`, enforced by the existing active-idempotency-key unique index.
5. The worker derives everything else from persisted rows.

Conceptual delivery row (names may change during implementation; the behavior may not):

```text
TransactionalEmailDelivery
├── id
├── purpose
├── budget_class
├── scope                   workspace | unscoped
├── workspace_id            nullable, ON DELETE CASCADE
├── user_id                 nullable, ON DELETE CASCADE
├── recipient_ciphertext    nullable, encrypted
├── recipient_key_version   nullable
├── status
├── attempt_count
├── next_attempt_at
├── provider_message_id     nullable, expires at 30 days
├── safe_failure_code       nullable
├── created_at
├── updated_at
├── sent_at
├── final_at
└── retention_expires_at
```

`purpose` is one of: `email_verification`, `password_reset`, `password_changed`, `deletion_request_confirmation`, `deletion_reminder`, `deletion_completion`.

`status` is one of: `pending`, `sending`, `sent`, `deferred`, `failed`, `canceled`. `sent` means the provider accepted the message. `pending`, `sending`, and `deferred` are non-final.

Attempts are recorded in a child table with the attempt number, outcome code, timestamps, and the provider message ID. They never contain a body, a link, a token, or a raw provider response.

Ordinary deliveries are workspace-scoped. Verification, reset, password-changed, deletion-request, reminder, and cancellation deliveries carry `workspace_id` (and `user_id` where a user exists) with cascading foreign keys. They are deleted with the workspace and are never retained as orphaned metadata by nulling the relationship. Final deletion does not cancel them separately; any workspace-scoped delivery rows and jobs that still exist are removed by the workspace cascade in the final-deletion transaction.

The deletion-completion delivery is the sole special case. The final-deletion transaction creates it as an explicitly **unscoped** delivery: `scope = 'unscoped'`, `workspace_id = NULL`, no user foreign key (`user_id = NULL`), the encrypted recipient and key version, and only the minimum template and event metadata (`purpose`, timestamps, status). It holds no slug, League ID, display name, or other tenant attribute.

The future schema must enforce the distinction with database constraints, not application code. A conceptual rule:

```text
CHECK (
  (scope = 'workspace' AND workspace_id IS NOT NULL)
  OR (scope = 'unscoped' AND purpose = 'deletion_completion'
      AND workspace_id IS NULL AND user_id IS NULL)
)
```

An unscoped row therefore cannot be created for any other purpose, and a workspace-scoped row cannot lose its workspace.

### Nullable Job Workspace for Unscoped Email

`BackgroundJob.workspace_id` becomes nullable, but only for transactional-email jobs whose delivery intent is explicitly unscoped. In this release that is the deletion-completion notice, which must survive tenant deletion.

A database check constraint enforces the rule:

```text
CHECK (workspace_id IS NOT NULL OR job_type = 'transactional_email_delivery')
```

Application code is not the control. Every other job type still requires a workspace, which preserves the AGENTS.md rule that jobs carry workspace ownership. Email jobs that belong to a tenant (verification, reset, request confirmation, reminder) keep their `workspace_id` and are removed with the tenant, together with their delivery rows. The schema must also tie the job to its delivery: a NULL-workspace job may reference only an unscoped delivery.

The workspace-keyed claim, lease, and listing queries must treat a `NULL` workspace as "no tenant", never as a wildcard. Workspace-scoped job reads and cancellation must not return or touch these rows.

### Token Handling

Raw verification and reset tokens never enter job payloads, delivery metadata, logs, provider tags, metrics, or browser storage.

1. A public route or service that needs an email creates a delivery row and an email job in its own transaction. It creates no token.
2. The delivery worker generates the token, stores only its hash (linked to the `delivery_id`) and expiry, and builds the link in process memory from `TRANSACTIONAL_EMAIL_PUBLIC_BASE_URL`.
3. The worker sends the message and discards the plaintext. Plaintext is never written to the database, the log, or a retry record.
4. Token hash lifecycle, expiry, single use, and enumeration protection remain as defined by ADR 0005.

If a provider call has an uncertain outcome (timeout, connection reset, unparseable response), the worker cannot know whether the message was sent and cannot recover the plaintext. A retry therefore generates a new token for the same delivery. It does not invalidate earlier tokens from that delivery, because the earlier message may have been delivered. Delivery is at-least-once, and a user may receive more than one message for one request.

Invalidation rules:

- Consuming one token invalidates every remaining token of that type for the account.
- Expiry and email change invalidate tokens as ADR 0005 already requires.
- A newer verification or reset delivery supersedes tokens from earlier deliveries once the provider accepts the newer message. Tokens from a delivery that never reached `sent` do not supersede anything, so a failed resend cannot lock out a link the user already holds.

Password-changed confirmation and the deletion notices carry no token.

### Public Route Behavior

- Registration, verification resend, password-reset request, and duplicate-email registration call no provider and perform no network I/O. They queue delivery and return.
- Registration success means delivery was queued, not that email arrived.
- The public `503 email_delivery_unavailable` response is removed when durable delivery is implemented. Provider outages and quota exhaustion are invisible to these routes. A route reports only conditions that do not depend on provider state.
- Resend, reset-request, and duplicate-registration paths share one response-time floor. It is configurable, initially 350 ms plus up to 75 ms of random jitter, measured from request start. The wait happens after the database session is released. A path that takes longer than the floor is not padded further. The floor reduces timing differences between "account exists" and "account does not exist" paths. It does not make the paths indistinguishable under heavy load, and the ADR does not claim it does.
- Duplicate-email registration sends no notification to the existing owner in this release (deferred).

### Resend Adapter

The adapter lives behind the provider boundary and uses `httpx` directly:

- 3-second connect timeout and 7-second read timeout, with explicit write and pool timeouts.
- No adapter-level retries. The job system owns retry, backoff, and attempt limits.
- A server-side Resend idempotency key of `{delivery_id}:{attempt_number}`. Resend retains idempotency keys for 24 hours.
  - An unchanged HTTP retry of the same delivery attempt, while the worker still holds the same in-memory body and token, reuses the same key.
  - If an uncertain outcome loses the in-memory raw token (for example the worker restarts), the worker creates a new token and a new attempt number, which gives a new key. Duplicate delivery is therefore possible in that case. Either token stays valid until one is consumed or the normal token lifecycle expires it.
- Failures map to a small closed set of safe codes (for example `provider_rejected`, `provider_rate_limited`, `provider_unavailable`, `timeout`, `quota_deferred`, `configuration_invalid`). Logs, errors, and stored failure metadata never contain the API key, a recipient address, a link, a body, or a raw provider response.
- No provider tags or metadata carry an address, token, workspace slug, or user identifier. A `delivery_id` tag is permitted only if needed for support and is not a secret.

Resend acceptance means `sent`. The product does not claim inbox delivery, and user-facing copy says instructions were sent, not received. Webhooks, bounce handling, complaint handling, and suppression-list synchronization are deferred until after launch.

### Free-Tier Priority Budget

Initial configurable daily allocation, summing to 100:

| Budget class | Purposes | Daily |
| --- | --- | --- |
| `verification` | verification, verification resend | 45 |
| `security` | password reset, password-changed confirmation | 25 |
| `deletion` | request confirmation, reminder, completion | 15 |
| `reserve` | operational reserve | 15 |

The monthly ceiling is 2,900. The approved initial default monthly split follows the same proportions: 1,305, 725, 435, and 435 (total 2,900). It is a configurable, application-operated budget, not a Resend contractual guarantee.

Rules:

- The day boundary is UTC. This is an assumption to confirm against the provider's counting window.
- The worker reserves capacity before each provider call, in a short transaction, and counts an attempt with an uncertain outcome as consumed.
- `verification` may not draw on `reserve`. `security` and `deletion` may use their own allocation first, then `reserve`. Protected capacity is therefore the `security`, `deletion`, and `reserve` allocations.
- When a class has no capacity, its deliveries become `deferred` with `next_attempt_at` set to the next window with capacity. Deferral does not spend a job attempt.
- Counters hold counts per period and class. They hold no recipient or token data and are retained 90 days.

Quota exhaustion pauses new registration. When remaining `verification` capacity for the day or month is zero, the backend treats registration as closed, regardless of the configured registration mode.

### Registration Readiness

Public registration stays closed unless email readiness passes. Readiness requires all of:

- the provider is enabled;
- an API key is configured;
- a sender identity is configured;
- `TRANSACTIONAL_EMAIL_PUBLIC_BASE_URL` is valid;
- queue and worker health are good (a fresh worker heartbeat and an acceptable oldest-pending email job age);
- protected delivery capacity remains.

Readiness uses configuration, database state, and worker heartbeats. No health or readiness probe sends an email, and none calls the provider on every probe.

`TRANSACTIONAL_EMAIL_PUBLIC_BASE_URL` is separate from `FRONTEND_ORIGIN`. HTTPS is required outside development and test. The value must be an origin with no credentials, query, or fragment. Links are always built from this value and never from request headers.

### Encrypted Recipient and Keyring

Most deliveries derive the recipient from the existing user row at send time. The deletion-completion notice cannot, because final deletion removes the user.

- A separate, versioned transactional-email encryption keyring encrypts the recipient. It is independent of the provider-credential keyring and the session keys. Rotating one never requires the others.
- The ciphertext is bound to the `delivery_id` as authenticated data.
- The completion delivery stores the ciphertext, the key version, and no plaintext.
- When any delivery reaches a final state (`sent`, `failed`, or `canceled`), the same transaction wipes `recipient_ciphertext` and `recipient_key_version`.
- A missing key version fails the delivery with `configuration_invalid` and does not log the ciphertext.
- Rendered email bodies are never persisted. The worker renders from a template in memory per attempt.

### Deletion Email Intents (FR-20)

Deletion email uses durable intents so that no crash window loses or duplicates a required notice. The deletion worker never sends email directly and has no best-effort path.

- **Request confirmation.** The deletion-request transaction creates the delivery and its job. The job becomes visible when that transaction commits, so a crash cannot commit the request without the confirmation intent.
- **Request-transaction job and delivery rules.** Email jobs are not exempted wholesale, and not canceled wholesale:
  - Ordinary pending and retry-scheduled workspace jobs are canceled.
  - Transactional-email deliveries are handled explicitly by event and deletion generation, each together with its queued job.
  - Stale verification deliveries and stale deletion-request, reminder, and cancellation deliveries are canceled with their queued jobs.
  - Password-reset and password-changed deliveries are retained and remain governed by worker-time eligibility.
  - The current request creates its confirmation and reminder only after stale delivery cancellation, so the same transaction never cancels the new jobs.
  - A running email job is not force-rewritten. The canceled delivery state is authoritative, and the worker ignores the stale attempt's outcome.
- **Seven-day reminder.** The same transaction creates a reminder delivery and job with `available_at = deletion_scheduled_for - 7 days`.
- **Cancellation.** The cancellation transaction cancels the reminder delivery and job, along with any not-yet-sent confirmation. A stale reminder cannot fire. The worker also rechecks that the workspace is still `deletion_pending` before sending a reminder.
- **Completion.** The final deletion transaction creates the completion delivery (with the encrypted recipient) and its email job with `workspace_id = NULL`, in the same transaction that deletes the workspace. The commit that removes the tenant is the commit that creates the intent, so the notice is neither lost nor sent for a deletion that rolled back.
- Final deletion creates the encrypted unscoped completion delivery and its NULL-workspace job inside the destructive transaction. Workspace-scoped delivery rows and jobs that still exist (for example an unsent reminder) are removed by the approved workspace cascade, not canceled separately. The completion delivery and its job survive because they have no workspace or user foreign key.
- No user-visible email is sent directly from the deletion transaction.
- **Cancellation confirmation** (recommended, not launch-blocking) follows the same pattern if implemented.

The completion notice states only that the workspace was deleted. It does not echo the slug or the League ID.

### Retention

| Data | Retention |
| --- | --- |
| Token plaintext | Never persisted |
| Token hashes | Existing token lifecycle (ADR 0005) |
| Encrypted completion recipient | Wiped at delivery final state |
| Provider message ID | 30 days |
| Delivery attempts | 30 days |
| Safe delivery state and failure code | 90 days |
| Rendered body | Never persisted |
| Provider raw response | Never persisted |

Retention windows apply to rows that exist. Workspace-scoped rows are deleted with the workspace regardless of the windows above. Future retention maintenance deletes or anonymizes expired unscoped and workspace-scoped delivery rows. It is not part of this documentation slice.

### Required Initial Use Cases

1. Administrator email verification.
2. Verification resend.
3. Password reset.
4. Password-changed confirmation.
5. Deletion-request confirmation.
6. Seven-day deletion reminder.
7. Deletion-completion notice.

Recommended but not launch-blocking: deletion-cancellation confirmation.

Deferred: duplicate-email registration-attempt notification, provider webhooks, bounce and complaint processing, and suppression synchronization.

## Contradictions Resolved

| Conflict | Resolution |
| --- | --- |
| Synchronous sender versus durable delivery | Durable delivery is the contract. No public route or worker calls the provider inline or best-effort. |
| Raw tokens cannot be queued | The worker generates tokens, stores hashes, and builds links in memory. Payloads carry only `delivery_id`. |
| Public `503 email_delivery_unavailable` | Removed. Provider and quota state never reach public routes. |
| Deletion email crash boundary | Intents are created in the request and final-deletion transactions. |
| Job deleted with tenant versus completion notice | Nullable `workspace_id` for email jobs only, guarded by a check constraint. |
| Registration readiness | Registration is closed unless email readiness passes, and quota exhaustion pauses it. |
| Response timing | Shared configurable floor and jitter on the three enumeration-sensitive paths. |
| Provider acceptance versus inbox delivery | Acceptance is `sent`. Inbox delivery is never claimed. |
| New request invalidates earlier tokens versus retry after uncertain send | Supersession occurs only after a newer delivery is accepted. A retry does not invalidate its own delivery's earlier tokens. |

## Consequences

### Positive

- Provider outages and quota exhaustion no longer fail public routes.
- A committed registration, deletion request, or final deletion cannot lose its required email.
- Tokens remain unrecoverable from the database, queue, and logs.
- Spend is bounded at $0 by construction.
- Recovery and deletion notices keep reserved capacity even when registration traffic spikes.

### Negative

- Delivery is at-least-once, and an uncertain send can issue several valid tokens.
- Registration pauses whenever the free allowance runs out.
- The `background_jobs` schema, its queries, and workspace-scoped job reads must handle `NULL` workspace safely.
- Token tables need a link to the originating delivery.
- Users may wait for delivery during provider degradation and receive no immediate failure signal.
- No bounce visibility exists until webhooks are built.
- The response floor adds latency to three routes and does not eliminate timing side channels under load.

## Rejected Alternatives

- **Synchronous send after commit.** Loses messages across crashes and exposes provider state.
- **Raw token in the job payload or delivery row.** Violates the token-secrecy invariant.
- **Separate email queue or table-as-queue.** Competes with `BackgroundJob`.
- **Resend SDK or SMTP relay.** Adds a dependency or weakens timeout control. Direct `httpx` meets the need.
- **Paid plan or pay-as-you-go overage.** Violates the $0 boundary.
- **Persisted rendered bodies.** Would retain tokens and personal data.
- **Best-effort email in the deletion worker.** Can lose a required notice at the crash boundary.
- **Keeping completion email in a tenant-scoped job.** Final deletion removes the job with the tenant.
- **Retaining orphaned tenant delivery rows with a nulled workspace.** Keeps tenant-derived metadata after deletion. Only the unscoped completion delivery survives.
- **Reusing the provider-credential keyring.** Couples unrelated rotation and blast radius.

## Implementation Notes

The implementation slice must add, each with its own tests and an approval gate for the migration and any dependency or configuration change:

- the delivery and attempt tables, the usage counter, the scope check constraint, and the nullable-workspace job check constraint (one Alembic migration);
- the token-to-delivery link;
- the worker handler, the Resend adapter, the budget reservation, and readiness evaluation;
- the response-time floor;
- the FR-20 intents;
- the removal of the public `503 email_delivery_unavailable` path;
- the retention maintenance job;
- environment templates and operational guides (documented later, not in this slice);
- DNS records for the sender domain, which require Henry's explicit approval under AGENTS.md.

Test requirements include the check constraints (an email job without a workspace is accepted only for an unscoped delivery; any other job type or purpose without a workspace is rejected; workspace deletion cascades scoped deliveries and leaves only the completion delivery), absence of plaintext tokens and addresses in payloads, rows, and logs, cross-workspace isolation of `NULL`-workspace jobs, crash-boundary behavior for each deletion intent, quota deferral and registration pause, and the timeout and idempotency behavior of the adapter against a fake transport.

## Related Documents

- `docs/product/SAAS_REQUIREMENTS.md` (FR-20, SER-3)
- `docs/architecture/0005-SaaS-Identity-Registration-and-Sessions.md`
- `docs/architecture/0006-Resumable-Onboarding-and-Background-Jobs.md`
- `docs/architecture/SAAS_TARGET_ARCHITECTURE.md`
- `docs/roadmap/SAAS_MIGRATION_ROADMAP.md`
