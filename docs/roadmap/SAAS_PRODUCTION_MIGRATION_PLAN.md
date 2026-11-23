# Fantasy What-If SaaS Production Migration Plan

## Document Status

- **Status:** Superseded
- **Superseded by:** the hosted deployment decision in `docs/product/SAAS_REQUIREMENTS.md`

## Decision

The hosted multi-workspace SaaS is a new deployment. There is no production migration from v1.

The hosted deployment does not preserve, migrate, import, or remain backward compatible with:

- v1 single-installation production databases
- The legacy installation bootstrap and setup wizard
- Username-based administrator or spectator login
- Administrator-chosen spectator usernames
- The single-value `PROVIDER_CREDENTIAL_KEY` setting
- Environment-based ESPN credentials

A new deployment starts with an empty database at the current Alembic head. Every League Workspace, including any league previously served by a v1 installation, is created through SaaS onboarding: administrator registration and email verification, ESPN validation, an atomic league claim, background initial synchronization, shared spectator setup, and activation.

The earlier draft that described converting the v1.0.1 installation into the first League Workspace remains available in Git history only. Do not follow it.

## Launch Checklist Owner

Pre-launch deployment, backup, and recovery steps are governed by ADR 0008 and `docs/OPERATIONS.md`.
