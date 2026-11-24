# 0008: Initial Homelab SaaS Deployment, Operations, and Recovery

## Status

Proposed

## Date

2026-11-18

## Context

Fantasy What-If will initially operate as a hosted SaaS from Henry's homelab.

The initial service target is approximately:

```text
50 to 100 registered users
```

The initial workload is expected to include:

- Administrator registration and onboarding
- Administrator and spectator login
- Multiple concurrent spectator sessions
- Analytics reads
- Provider synchronization
- Historical season imports
- Email verification and password recovery
- PostgreSQL backups
- Background-job processing
- Scheduled synchronization
- Provider OAuth callbacks
- Mobile-web traffic

The current v1.0.1 production deployment uses:

- Docker Compose
- PostgreSQL 18
- FastAPI backend
- React frontend served through Nginx
- A synchronization scheduler
- Immutable backend and frontend container images
- Alembic migrations
- Health checks
- Versioned GitHub Container Registry images
- A production-only environment file
- A Cloudflare-based network path
- Separate development and production environments

The SaaS conversion introduces new operational risks:

- Public registration
- Internet-facing authentication
- Multiple tenants in one database
- Email delivery dependency
- Provider rate limits
- Background-job accumulation
- OAuth callbacks
- Credential encryption-key rotation
- Increased backup importance
- Tenant-wide deletion
- User expectations for availability
- Greater consequences from cross-tenant defects
- Increased consequences from database loss

The first deployment must remain manageable from the homelab without prematurely introducing Kubernetes, public-cloud managed services, or microservices.

At the same time, the architecture must not make later cloud migration unnecessarily difficult.

## Decision

Fantasy What-If will initially run as a containerized modular monolith on the existing homelab infrastructure.

Production will remain separate from development.

The production stack will use Docker Compose and PostgreSQL with distinct process roles:

```text
reverse proxy and TLS edge
frontend
backend API
background worker
synchronization scheduler
migration runner
PostgreSQL
backup process
monitoring and alerting
```

The backend API, worker, scheduler, migration runner, and operator commands may use the same versioned backend image with different process entry points.

The first production configuration will begin conservatively with:

```text
One backend API service
One background worker
One synchronization scheduler
One PostgreSQL instance
One frontend service
```

Additional API or worker replicas may be added only after session behavior, background-job locking, provider concurrency, and database connection usage are validated.

## Deployment Principles

The homelab SaaS deployment must follow these principles:

1. Development and production remain fully separate.
2. Production uses immutable versioned images.
3. Production does not mount application source code.
4. PostgreSQL is not exposed publicly.
5. The backend is not exposed directly to the public internet.
6. The frontend reverse proxy is the application ingress.
7. TLS is mandatory.
8. Secrets remain outside Git.
9. Database backups are automated.
10. An off-host backup copy is required.
11. Restore procedures are tested.
12. Application processes use least-privilege networking.
13. Background work is isolated from API request execution.
14. Registration can be disabled independently.
15. Deployments support health verification and rollback.
16. Production migrations are explicit and observable.
17. Provider credentials remain encrypted at rest.
18. A homelab outage is acknowledged as an availability limitation.

## Initial Production Topology

The conceptual production topology is:

```text
Internet
    |
    v
Cloudflare DNS and edge protections
    |
    v
Cloudflare Tunnel
    |
    v
Frontend Nginx container
    |
    +-------------------------+
    |                         |
    v                         v
Static frontend          /api and /health
                              |
                              v
                       Backend API container
                              |
                              v
                        PostgreSQL network

Background worker container
    |
    +---- PostgreSQL job queue
    |
    +---- Provider APIs

Scheduler container
    |
    +---- PostgreSQL job queue

Migration container
    |
    +---- PostgreSQL schema

Backup container or host-managed backup task
    |
    +---- PostgreSQL backup storage
    |
    +---- Off-host backup destination
```

The public network path must not expose PostgreSQL, the worker, the scheduler, or the migration process.

## Container Roles

### Frontend

The frontend container is responsible for:

- Serving the production React application
- Proxying `/api` requests to the backend
- Proxying `/health` according to the health contract
- Applying safe HTTP headers
- Enforcing request-body limits
- Applying proxy timeouts appropriate to ordinary APIs
- Avoiding long synchronization request dependencies

Once background jobs replace long synchronization requests, the frontend proxy should not require a five-minute timeout for normal synchronization workflows.

The 300-second timeout may remain during the migration period and should be reviewed after queued synchronization is fully operational.

### Backend API

The backend API container is responsible for:

- Registration
- Authentication
- Email verification
- Password recovery
- Workspace-scoped authorization
- Provider validation
- Atomic provider league claims
- Background-job creation
- Job-status APIs
- Analytics reads
- Administrative actions
- Health and readiness endpoints

The API container must not execute long synchronization jobs after queued execution becomes operational.

### Background Worker

The background worker container is responsible for:

- Claiming PostgreSQL-backed jobs
- Executing provider synchronization
- Executing historical imports
- Updating job progress
- Updating synchronization runs
- Retrying eligible failures
- Renewing job leases
- Respecting provider concurrency
- Preserving workspace isolation
- Handling graceful shutdown

The initial deployment will use one worker process.

The worker count may increase only after load testing proves that:

- PostgreSQL connection usage remains safe
- `FOR UPDATE SKIP LOCKED` behavior is correct
- Provider concurrency limits are respected
- Season-scoped advisory locks prevent overlap
- Job handlers are idempotent
- Logs and metrics distinguish worker executions

### Scheduler

The scheduler container is responsible for:

- Identifying synchronization candidates
- Creating scheduled jobs
- Applying jitter
- Avoiding duplicate jobs
- Skipping suspended workspaces
- Skipping deletion-pending workspaces
- Respecting maintenance mode
- Avoiding direct provider synchronization

Exactly one active scheduler should create scheduled jobs in the initial deployment.

If multiple scheduler replicas are introduced later, scheduler leadership or transaction-safe deduplication must prevent duplicate scheduling.

### Migration Runner

The migration runner is a short-lived container.

The migration runner must:

1. Connect to the production database.
2. Apply supported Alembic migrations.
3. Exit with code `0` on success.
4. Exit nonzero on failure.
5. Prevent the upgraded application from becoming ready when migration fails.

Production migrations must not run implicitly on every API or worker startup.

### PostgreSQL

PostgreSQL remains the authoritative store for:

- Workspaces
- Users
- Sessions
- Provider connections
- Provider league claims
- Canonical league data
- Analytics results
- Background jobs
- Synchronization runs
- Audit events
- Registration state
- Verification and recovery tokens

PostgreSQL must not be published to a public network interface.

When host access is required, PostgreSQL should bind only to a trusted private address or loopback interface.

### Backup Process

The backup process is responsible for:

- Scheduled PostgreSQL backups
- Backup integrity checks
- Retention
- Off-host transfer
- Failure reporting
- Restore-test support

The backup process may initially run through:

- A dedicated container
- A protected host systemd timer
- A protected host cron replacement
- A restic-based backup workflow

The implementation must be documented in `docs/OPERATIONS.md`.

## Network Segmentation

The production Compose deployment should use separate networks where practical.

Conceptual networks:

```text
edge
application
data
```

### Edge Network

Permitted services:

- Frontend reverse proxy
- Cloudflare Tunnel connector when containerized

### Application Network

Permitted services:

- Frontend
- Backend API
- Worker when required for internal APIs
- Scheduler when required for internal APIs

### Data Network

Permitted services:

- Backend API
- Worker
- Scheduler
- Migration runner
- Backup process
- PostgreSQL

The frontend should not require direct PostgreSQL access.

The migration runner and backup process should not require public ingress.

## Cloudflare

Cloudflare may provide:

- DNS
- TLS edge termination
- Cloudflare Tunnel
- Basic denial-of-service protection
- Web Application Firewall controls when available
- Bot and rate controls when available
- Access restrictions for operator-only surfaces

Cloudflare protections do not replace application authorization, CSRF protection, login throttling, registration limits, or tenant isolation.

The application must correctly validate trusted proxy behavior.

### Staging safety decisions

- Production processes receive `APP_ENV=production` explicitly in Compose; no default is relied on.
- Production startup fails closed unless the provider-credential and transactional-email keyrings are valid and select a configured current version.
- API documentation routes are disabled in production.
- `restore.sh` stops the ingress and every database-writing service (`backend`, `backend-worker`, `sync-scheduler`, `migrate`), verifies none is running, and restarts only those services after a successful restore.
- With `cloudflared` on the Docker host and Nginx published on loopback, Nginx accepts `CF-Connecting-IP` only from loopback or private peers, replaces `X-Forwarded-For` with the validated address, and the backend trusts only Nginx. Cloudflare edge ranges are not hard-coded. A non-loopback `APP_BIND_ADDRESS` invalidates this boundary.
- Closed staging uses Cloudflare Access plus temporary `REGISTRATION_MODE=open`; native invitations and allowlists are not implemented.

Only known proxy sources should be trusted for forwarded headers.

Production must not use:

```text
FORWARDED_ALLOW_IPS=*
```

unless a documented deployment boundary makes the configuration safe.

## TLS

Public SaaS access must use HTTPS.

Production requirements include:

- Secure cookies
- HTTP-to-HTTPS redirection where applicable
- Valid certificates
- Certificate-expiration monitoring
- No mixed-content API calls
- HTTPS OAuth callback URLs
- HTTPS email-verification links
- HTTPS password-reset links

Native-app deep links and universal links are deferred.

## Domain Strategy

The production service should use a stable application domain.

Conceptual examples:

```text
app.example.com
api.example.com
```

The initial deployment may continue using one origin:

```text
app.example.com
```

with `/api` reverse proxying.

A same-origin browser deployment simplifies:

- Cookies
- CSRF protection
- CORS
- OAuth callbacks
- Frontend configuration

Separate API and frontend domains are not required for the first SaaS release.

## Production Environment Separation

Development and production must use separate:

- PostgreSQL databases
- PostgreSQL volumes
- Compose project names
- Environment files
- Encryption keys
- Application secrets
- OAuth callback URLs
- Email-provider credentials
- Cloudflare Tunnel configuration
- Container ports
- Backup destinations
- Logs
- Monitoring targets

Development data must never be used as production tenant data.

Production provider credentials must never be copied into development without explicit sanitization and authorization.

## Secret Management

Initial homelab secrets may use root-readable or service-account-readable environment files with restrictive filesystem permissions.

Production secret files should use:

```text
chmod 600
```

and ownership limited to the deployment operator.

Secrets include:

- Application secret key
- PostgreSQL password
- Provider credential encryption keys
- Email-service API key
- Yahoo OAuth client secret
- Cloudflare Tunnel credential
- Backup repository password
- Off-host storage credentials
- Monitoring notification credentials

Secrets must not appear in:

- Git
- Docker image layers
- Compose files committed to Git
- CI logs
- Application logs
- Audit events
- Job payloads
- API responses
- Frontend JavaScript bundles

## Encryption-Key Storage

Provider credential encryption keys must remain outside PostgreSQL.

The production environment must support:

- One current encryption key
- Older decryption keys during rotation
- Explicit key versions
- Emergency key backup
- Controlled key retirement

A secure copy of required encryption keys must be included in disaster-recovery planning.

A database backup without the required encryption keys cannot recover provider credentials.

Encryption keys must not be stored only on the same physical disk as the production database.

## Email Delivery

Public registration requires transactional email.

Initial email functions include:

- Administrator email verification
- Password reset
- Security notification
- Workspace suspension notification
- Provider reauthorization notification
- Important service notices

Provider decision (ADR 0009):

- Resend is the approved initial provider.
- Integration uses direct bounded HTTP through the existing `httpx` dependency, with no provider SDK.
- The application retains a provider-neutral interface so the provider can change later.
- SMTP and Amazon SES are not current implementation targets.
- The deployment stays on the permanent free plan with no automatic paid upgrade and no pay-as-you-go spending.
- Provider quotas are configurable operational settings, not hard-coded guarantees.

The provider must support a verified sending domain with SPF and DKIM. DMARC planning, bounce handling, and delivery status are desirable and deferred until after launch.

Email delivery is durable and asynchronous. ADR 0009 governs delivery, quota, retention, and registration readiness.

The application must not expose whether an email exists through verification or password-reset responses.

## Registration Controls

The operator must be able to change registration mode without rebuilding the application.

Conceptual modes:

```text
open
invite_only
closed
maintenance
```

The first release requires at least:

```text
open
closed
maintenance
```

Registration mode must be enforced by the backend.

The frontend may reflect the mode but is not the authorization boundary.

### Open

New administrator registrations are accepted subject to throttling and validation.

### Closed

New registrations are rejected.

Existing users may continue logging in and using active workspaces.

### Maintenance

New registrations are rejected.

Selected write operations may be disabled.

The operator may preserve read-only analytics access.

## Maintenance Modes

Operational controls should distinguish:

```text
registration_enabled
provider_changes_enabled
synchronization_enabled
application_writes_enabled
```

A single global maintenance switch may be provided for emergencies, but separate controls avoid unnecessary downtime.

Examples:

- Disable registration during an abuse incident.
- Disable provider changes during credential-vault maintenance.
- Disable synchronization during a provider outage.
- Disable all writes during a risky database migration.
- Preserve analytics reads during provider downtime.

## Health Endpoints

The application should expose separate liveness and readiness behavior.

### Liveness

Liveness indicates that the process is running and its event loop or request loop is responsive.

Liveness should not fail solely because a provider is unavailable.

Conceptual endpoint:

```text
/health/live
```

### Readiness

Readiness indicates that the process can serve its intended role.

API readiness may verify:

- Database connectivity
- Supported schema revision
- Required configuration
- Credential-key availability

Worker readiness may verify:

- Database connectivity
- Supported schema revision
- Job-queue access
- Credential-key availability

Conceptual endpoint:

```text
/health/ready
```

### Public Health

The public health response should remain minimal.

Example:

```json
{
  "status": "ok"
}
```

Detailed component health belongs in protected operator tooling.

## Logging

Production logs should be structured JSON or another consistently parseable format.

Required correlation fields include:

```text
timestamp
level
service_role
request_id
job_id
workspace_id
user_id
session_id
provider
season_id
sync_run_id
operation
safe_error_code
```

Fields should be omitted when not applicable.

Logs must not contain:

- Passwords
- Password hashes
- Session tokens
- CSRF tokens
- Verification tokens
- Password-reset tokens
- Provider cookies
- OAuth access tokens
- OAuth refresh tokens
- Authorization codes
- Encryption keys
- Raw private provider payloads

Workspace IDs may be used in protected logs but should not be included as unbounded metric labels.

## Audit Events

Application audit events and operational logs serve different purposes.

### Operational Logs

Operational logs support:

- Debugging
- Monitoring
- Incident response
- Performance analysis

### Audit Events

Audit events record security-sensitive product actions.

Audit events should be persisted in PostgreSQL or another durable protected store.

Required audit categories are defined in the SaaS requirements and identity ADR.

Audit events must include workspace ownership when the action is tenant-specific.

Audit events must remain available after ordinary log rotation according to the retention policy.

## Metrics

Initial metrics should include:

### API

- Request count
- Request duration
- Error count
- Active requests
- Login successes and failures
- Registration successes and failures
- Rate-limit rejections

### Worker

- Pending jobs
- Running jobs
- Failed jobs
- Retry-scheduled jobs
- Oldest pending-job age
- Job wait duration
- Job execution duration
- Lease expirations
- Provider failures
- Synchronization outcomes

### PostgreSQL

- Connection usage
- Transaction rate
- Long-running transactions
- Lock waits
- Database size
- Table growth
- Index size
- Checkpoint activity
- Backup status

### Host

- CPU
- Memory
- Disk usage
- Disk latency
- Network usage
- Container restart count
- System load
- Clock synchronization

### Application

- Active workspaces
- Provisional workspaces
- Suspended workspaces
- Active sessions
- Synchronization freshness
- Provider reauthorization count
- Email delivery failure count

Metrics must not include:

- Email addresses
- Usernames
- Workspace slugs
- Provider league IDs
- Provider credentials

## Monitoring Stack

The initial monitoring implementation may use:

- Prometheus
- Grafana
- Grafana Loki
- Promtail or Grafana Alloy
- Uptime Kuma
- PostgreSQL exporter
- Node Exporter
- cAdvisor

The exact stack is an implementation decision.

The selected stack must provide:

- Service availability alerts
- Database alerts
- Backup alerts
- Disk alerts
- Queue-depth alerts
- Failed-job alerts
- Certificate alerts
- Host-resource alerts

A smaller initial stack is acceptable if all required alert categories are covered.

## Alert Delivery

Alerts must reach the operator outside the affected application.

Suitable channels include:

- Email
- Discord webhook
- Microsoft Teams webhook
- Pushover
- ntfy
- Healthchecks.io
- UptimeRobot

At least one external alert path is required.

An alert mechanism hosted only on the same failed machine is insufficient.

## Backup Strategy

The production PostgreSQL backup strategy must use at least two forms of protection:

1. Regular logical or physical database backup
2. Off-host encrypted backup copy

A representative initial strategy is:

```text
Nightly PostgreSQL logical backup
    -> local encrypted backup staging
    -> restic encrypted repository
    -> off-host destination
```

Potential off-host destinations include:

- Backblaze B2
- Cloudflare R2
- Amazon S3
- Another trusted physical machine
- A separate NAS in another location

The selected destination must be protected by separate credentials.

## Backup Contents

Disaster-recovery backups must include:

- PostgreSQL data
- Migration revision information
- Production environment configuration excluding replaceable values
- Provider credential encryption keys
- Application secret-key requirements
- Yahoo OAuth client configuration
- Email-service configuration
- Cloudflare Tunnel configuration
- Compose release files
- Version information
- Operational runbooks

Secrets must be backed up through a protected secret-recovery process rather than committed alongside ordinary documentation.

## Backup Schedule

Recommended initial database schedule:

```text
Daily backup:
Retain 14 days

Weekly backup:
Retain 8 weeks

Monthly backup:
Retain 12 months
```

This is an initial operational target and may be adjusted based on storage, legal, privacy, and recovery requirements.

Background-job and audit retention may differ from database backup retention.

## Backup Encryption

Off-host backups must be encrypted before or during transfer.

Backup encryption credentials must not exist only inside the backup repository.

The operator must retain a protected recovery copy of:

- Backup repository password or key
- Provider credential encryption keys
- Required production secret material
- Recovery instructions

Loss of these values may make provider credentials or backups unrecoverable.

## Backup Verification

Backup success is not established solely by a zero exit code.

The backup process must verify:

- Backup artifact exists
- Artifact size is plausible
- Repository integrity check succeeds
- Retention policy runs successfully
- Off-host transfer succeeds
- Backup timestamp is recent
- Alert is generated on failure

## Restore Testing

A restore test must be completed before public SaaS registration.

Restore tests must:

1. Use an isolated database.
2. Restore a production-like backup.
3. Apply or verify the expected schema revision.
4. Start a compatible backend.
5. Verify workspace ownership.
6. Verify users and role cardinality.
7. Verify provider claims.
8. Verify canonical league data.
9. Verify encrypted credentials can be decrypted when included.
10. Avoid contacting live providers unless explicitly intended.
11. Record restore duration.
12. Document every recovery step.

Recommended initial restore-test frequency:

```text
Quarterly
```

Restore tests should also occur after material backup-process changes.

## Recovery Objectives

Initial homelab recovery objectives are targets, not contractual service-level agreements.

Recommended initial targets:

```text
Recovery Point Objective:
24 hours

Recovery Time Objective:
8 hours
```

The Recovery Point Objective means a catastrophic failure may lose up to approximately one day of recently changed data if only daily backups are available.

The Recovery Time Objective means the target is to restore service within approximately eight hours when hardware, backups, and operator availability permit.

Provider data may be resynchronized after recovery, but the following cannot be assumed recoverable from providers:

- User accounts
- Password hashes
- Workspace settings
- Audit events
- Provider claims
- Historical local configuration
- Email-verification state
- Password-reset state
- Some provider credentials
- SaaS operational history

## Hardware Failure

The recovery plan must address:

- Storage-device failure
- Host failure
- Power-supply failure
- Memory failure
- Network-interface failure
- Router failure
- UPS failure
- File-system corruption

The service should run on storage with SMART monitoring.

RAID may reduce downtime from one disk failure, but RAID is not a backup.

A UPS is recommended for:

- Production host
- Network equipment
- Storage device
- Cloudflare Tunnel path

The UPS should support graceful shutdown when practical.

## Internet Failure

The homelab service depends on residential or small-site internet availability.

During an internet outage:

- Public access may be unavailable.
- Provider synchronization may fail or retry.
- Local database operations may continue.
- Background jobs should back off.
- Monitoring should alert through an independent path when possible.

The application must not corrupt jobs or canonical data because internet connectivity disappears.

## Power Failure

After unexpected power loss:

1. PostgreSQL must recover through its normal write-ahead log process.
2. Containers must restart through documented policies.
3. Migrations must not rerun unsafely.
4. Running job leases must expire and be reconciled.
5. Health checks must verify readiness.
6. The scheduler must avoid duplicating active jobs.
7. The operator must verify backup recency.

## Deployment Strategy

Production deployments use explicit application versions.

Example:

```text
APP_VERSION=1.1.0
```

The deployment process should:

1. Confirm the Git release and workflow.
2. Confirm backend and frontend images exist.
3. Back up the production database.
4. Validate the Compose configuration.
5. Pull versioned images.
6. Run the migration container.
7. Verify migration success.
8. Recreate API, worker, scheduler, and frontend services.
9. Verify liveness and readiness.
10. Verify the frontend root.
11. Verify login.
12. Verify background-job claiming.
13. Monitor logs and metrics.
14. Retain rollback information.

## Migration Safety

Database migrations must be designed for the current data volume and availability requirements.

Potentially disruptive operations include:

- Adding non-null workspace ownership
- Backfilling large tables
- Building unique indexes
- Changing global uniqueness
- Creating provider claims
- Altering session behavior
- Rewriting encrypted credential envelopes

Large or locking migrations should use staged patterns when necessary:

1. Add nullable columns.
2. Backfill in controlled batches.
3. Validate ownership.
4. Add indexes concurrently where supported and appropriate.
5. Add constraints.
6. Make columns non-null.
7. Switch application behavior.
8. Remove compatibility paths later.

Alembic transactional behavior and PostgreSQL restrictions must be considered for concurrent index creation.

## Rollback Strategy

Application rollback and database rollback are separate concerns.

### Application Rollback

If the schema remains backward compatible:

1. Restore the previous application image version.
2. Keep the forward-compatible database schema.
3. Verify health and functionality.

### Database Recovery

Destructive migration rollback should not be assumed.

For migrations that cannot be reversed safely, the recovery strategy is:

- Forward repair
- Restore from backup
- Restore into an isolated database
- Reconcile data before reopening writes

Every production migration must document its rollback or forward-recovery strategy.

## Zero-Downtime Expectations

The first homelab SaaS release does not promise zero-downtime deployments.

Short planned maintenance windows are acceptable.

The deployment should still minimize downtime through:

- Pre-pulled images
- Explicit migrations
- Health checks
- Fast container replacement
- Maintenance mode
- Read-only preservation when safe

Public maintenance messaging should avoid exposing internal infrastructure details.

## Resource Planning

Initial capacity planning must measure actual behavior rather than assuming that 100 registered users means 100 concurrent users.

Load tests should model at least:

```text
10 concurrent administrator or spectator sessions
25 concurrent analytics reads
5 simultaneous onboarding flows
A queue of 20 synchronization jobs
One running synchronization worker
One backup operation
```

These are initial test scenarios, not guaranteed production limits.

Testing must measure:

- API latency
- PostgreSQL connections
- Query latency
- CPU
- Memory
- Disk latency
- Queue wait time
- Provider request concurrency
- Container restarts
- Backup impact

## PostgreSQL Connection Planning

Each process role may create its own connection pool.

Connection planning must include:

```text
API pool
Worker pool
Scheduler pool
Migration connection
Backup connection
Operator connection
Monitoring connection
```

Pool sizes must not use framework defaults without review.

A representative safe initial approach is:

```text
API pool:
5 persistent connections
5 overflow connections

Worker pool:
2 persistent connections
2 overflow connections

Scheduler pool:
1 or 2 connections

Operator and monitoring reserve:
At least 5 connections
```

These values must be validated against the configured PostgreSQL `max_connections` and actual process behavior.

Exact production values belong in deployment configuration and load-test results.

## Data Growth Monitoring

Data-growth monitoring must include:

- Player-week scores
- Matchups
- Synchronization runs
- Background jobs
- Authentication sessions
- Login attempts
- Audit events
- Provider metadata
- Database indexes
- Backups

Retention tasks must remove expired operational records without deleting required canonical data.

## Session Maintenance

Expired and revoked sessions should be cleaned on a schedule.

Session cleanup must not:

- Delete active sessions
- Reveal tenant counts
- Block login for extended periods
- Require one job per workspace when a safe global maintenance task is sufficient

Session cleanup should operate in bounded batches.

## Job Maintenance

Background-job cleanup must follow ADR 0006 retention requirements.

Maintenance should:

- Remove expired succeeded jobs
- Retain failed jobs for the configured period
- Preserve synchronization runs
- Detect stale leases
- Avoid deleting jobs needed for active onboarding
- Operate in bounded batches

## Audit Retention

Audit-event retention must be longer than ordinary application-log retention.

The exact retention period depends on privacy and operational requirements.

Recommended initial target:

```text
One year
```

Security-critical audit events may require longer retention.

The privacy policy must disclose relevant event retention.

## Incident Response

The operator runbook must cover:

- Suspected credential leak
- Suspected cross-tenant access
- Provider credential compromise
- Administrator account compromise
- Database corruption
- Backup failure
- Provider outage
- Queue backlog
- Email-provider outage
- Cloudflare Tunnel outage
- Disk exhaustion
- Failed migration
- Unexpected container restart loop

A suspected cross-tenant access incident requires immediate:

1. Registration disablement
2. Write restriction when appropriate
3. Preservation of logs and audit evidence
4. Session revocation when appropriate
5. Vulnerability containment
6. Scope investigation
7. User-notification assessment
8. Corrective release
9. Post-incident documentation

## Operator Access

Operator-level access is distinct from workspace administrator access.

A workspace administrator must not gain:

- Host shell access
- PostgreSQL access
- Global queue access
- Other workspace visibility
- Global metrics containing tenant details
- Encryption-key access
- Backup access

Operator access should use:

- Separate operating-system accounts
- SSH keys
- Tailscale or another protected administration network
- Least privilege
- Audit-capable commands where practical
- No password-based public SSH

The existing application administrator role must not become the SaaS operator role.

## Operator Interface

The first operator interface may consist of protected CLI commands and dashboards.

Required operator actions include:

- Enable or disable registration
- Enable or disable synchronization
- Inspect failed jobs
- Retry eligible jobs
- Suspend a workspace
- Reactivate a workspace
- Inspect safe provider state
- Verify backup status
- Run pre-migration audits
- Run workspace-deletion workflows
- Verify provider claims
- Inspect migration revision

A tenant-facing global operator web interface is not required for the first release.

## Abuse Controls

The hosted service must protect the homelab from avoidable abuse.

Controls include:

- Registration throttling
- Login throttling
- Password-reset throttling
- Verification-email throttling
- Provider-validation throttling
- Request-body limits
- API request limits
- Provider-job deduplication
- Queue backpressure
- Cloudflare protections
- Workspace suspension
- Registration shutdown

CAPTCHA remains deferred unless observed abuse justifies use.

## Privacy and Legal Readiness

Before public registration, the service must publish:

- Privacy policy
- Terms of service
- Acceptable-use expectations
- Contact method
- Data-deletion process
- Provider credential handling summary
- Backup-retention explanation
- Service-availability limitations
- Third-party provider dependency notice

The documents must accurately describe homelab availability and provider dependencies.

Legal review may be appropriate before monetization or broader public launch.

## Cloud Migration Triggers

Cloud migration should be evaluated when one or more conditions persist.

### Capacity

- More than approximately 100 materially active users
- Queue wait time remains excessive
- Synchronization interferes with API latency
- Database resources approach safe host limits
- Backup windows materially affect service

### Availability

- Residential or site internet outages materially affect users
- Power reliability fails user expectations
- Hardware recovery exceeds the target Recovery Time Objective
- OAuth callbacks require higher availability

### Operations

- Monitoring and incident response exceed reasonable personal operational capacity
- Off-host backup recovery becomes too slow
- Manual deployment or recovery becomes unsafe
- Additional workers require more hosts

### Security and Compliance

- Customer expectations require managed infrastructure
- Contractual availability is introduced
- Paid plans introduce stronger recovery commitments
- Required audit or compliance controls exceed the homelab design

These triggers require evaluation, not automatic migration.

## Target Cloud Mapping

The architecture should permit later mapping to managed services.

Conceptual examples:

```text
PostgreSQL:
Amazon RDS for PostgreSQL
Azure Database for PostgreSQL
Google Cloud SQL for PostgreSQL

Containers:
Amazon ECS or AWS App Runner
Azure Container Apps
Google Cloud Run

Job queue:
Amazon SQS
Azure Service Bus
Google Cloud Tasks
Managed Redis when justified

Object backups:
Amazon S3
Azure Blob Storage
Google Cloud Storage

Secrets:
AWS Secrets Manager
Azure Key Vault
Google Secret Manager

Monitoring:
Cloud-native monitoring or managed Grafana
```

No cloud provider is selected by this ADR.

## Migration to Cloud

Cloud migration should preserve:

- Workspace IDs
- User IDs
- Provider claims
- Canonical league data
- Credential envelopes
- Encryption-key versions
- Job semantics
- Audit events
- API contracts
- Container images
- Alembic migrations

The application should avoid hard dependencies on homelab-only network addresses or host paths.

Deployment-specific values belong in environment configuration.

## Testing Requirements

### Deployment

- Compose configuration validates.
- Required secrets are detected.
- Services start in dependency order.
- Failed migration prevents readiness.
- API readiness fails when PostgreSQL is unavailable.
- Worker readiness fails when queue access is unavailable.
- Provider outage does not fail API liveness.
- Frontend proxies application APIs correctly.

### Backup and Restore

- Backup artifact is created.
- Off-host transfer succeeds.
- Backup integrity check succeeds.
- Restore into an isolated database succeeds.
- Workspace ownership survives restore.
- Provider claims survive restore.
- Credentials decrypt after restore.
- The restored application starts against the expected schema.
- Restore duration is recorded.

### Failure Recovery

- API restart preserves accepted jobs.
- Worker restart recovers leased jobs.
- Scheduler restart does not duplicate active jobs.
- PostgreSQL restart preserves canonical data.
- Internet loss produces retryable provider failures.
- Disk alerts trigger before exhaustion.
- Registration can be disabled during an incident.

### Isolation

- PostgreSQL is not publicly reachable.
- Worker has no public ingress.
- Scheduler has no public ingress.
- Migration runner has no public ingress.
- Frontend has no database credentials.
- Tenant administrators cannot access operator functions.
- Logs contain no tested secret values.

### Load

- Concurrent analytics remain within initial latency targets.
- One synchronization does not block ordinary reads.
- Queue backpressure works with 20 pending jobs.
- Backup execution does not exhaust disk or database connections.
- Connection pools remain below configured PostgreSQL limits.
- Mobile and desktop requests remain responsive during worker activity.

## Consequences

### Positive

- The existing Docker Compose deployment remains useful.
- The modular monolith can support the initial SaaS population.
- API and background work are operationally separated.
- PostgreSQL backups become a tested service requirement.
- The deployment can move to managed cloud services later.
- Public registration can be disabled independently.
- Provider outages do not require total application downtime.
- Immutable images and explicit migrations remain in place.
- The system acknowledges realistic homelab availability limits.

### Negative

- The operator becomes responsible for a public SaaS production service.
- Homelab hardware and internet remain single-site dependencies.
- Backup, monitoring, email, and incident response add operational work.
- Separate worker and scheduler roles increase container count.
- Public authentication increases security exposure.
- OAuth callbacks depend on stable public availability.
- Recovery objectives require regular restore testing.
- Cloud migration may eventually be necessary.

## Rejected Alternatives

### Immediate Public-Cloud Deployment

Deferred because the initial user target can be supported from the homelab and the product model is still evolving.

### Kubernetes

Rejected for the initial deployment because Kubernetes would add operational complexity without addressing the primary product risks.

### Development and Production in One Stack

Rejected because production tenant data and secrets require strict separation.

### Public PostgreSQL Access

Rejected because PostgreSQL must remain private to the application and operator network.

### Backups Stored Only on the Production Host

Rejected because host loss, disk loss, theft, or corruption could destroy production and backup data together.

### RAID as the Only Backup

Rejected because RAID does not protect against deletion, corruption, ransomware, application mistakes, or site loss.

### One Process for API, Worker, and Scheduler

Rejected because provider work could degrade API responsiveness and complicate job recovery.

### Logs as the Only Audit Trail

Rejected because security-sensitive actions require durable structured audit events.

### Promise Zero Downtime

Rejected because the initial homelab deployment cannot responsibly guarantee zero-downtime upgrades or availability.

## Follow-Up Work

After this ADR is accepted:

1. Update `docs/OPERATIONS.md` with SaaS process roles.
2. Add backup and restore runbooks.
3. Select an off-host backup destination.
4. Transactional email provider: Resend, per ADR 0009 (decided; configuration and DNS remain to be done).
5. Define production registration controls.
6. Add liveness and readiness endpoints.
7. Add worker and queue monitoring.
8. Add structured logging.
9. Add audit-event retention policy.
10. Add incident-response runbook.
11. Add predeployment and rollback checklists.
12. Add homelab load-test plan.
13. Add Cloudflare and trusted-proxy review.
14. Add production secret-rotation runbook.
15. Define cloud-migration review criteria.

## Requirement Traceability

This decision supports:

- `FR-2`: Registration availability
- `FR-14`: Resumable onboarding
- `FR-15`: Initial synchronization
- `FR-20`: Workspace deletion
- `PR-7`: Provider rate protection
- `DR-5`: Audit events
- `SYR-3`: Background execution
- `SYR-5`: Failure isolation
- `SYR-6`: Scheduling
- `OR-1`: Initial capacity
- `OR-2`: Backups
- `OR-3`: Observability
- `OR-4`: Maintenance mode
- `OR-5`: Deployment safety
- `OR-6`: Cloud migration triggers
- `SER-1`: Existing controls
- `SER-2`: Registration abuse protection
- `SER-4`: Credential encryption
- `SER-5`: Secret logging