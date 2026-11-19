# ESPN Hardening Code-Health Review

## Scope

This review follows the automated multi-workspace ESPN isolation validation and precedes Sleeper implementation.

The review focuses on:

- synchronization entry points
- durable background-job boundaries
- duplicated synchronization job contracts
- dead-code candidates
- production-code complexity
- provider-extension risks

## Completed Cleanup

### Removed direct synchronization CLI

`backend/app/scripts/sync_provider.py` was removed.

The command had no tracked callers, tests, documentation, or Compose entrypoint. It also performed provider synchronization directly instead of using the durable PostgreSQL-backed job queue.

Production active-season synchronization now executes through the background-job handler boundary.

### Centralized synchronization job payload contract

`backend/app/services/synchronization_job_contract.py` now owns:

- synchronization payload version 1
- secret-bearing prohibited field names
- secret-free payload construction
- season identifier validation
- synchronization-mode validation
- trigger normalization
- prohibited-name detection

Administrator and scheduler services retain their separate:

- priorities
- maximum-attempt policies
- idempotency-key formats
- trigger values

Those values encode distinct business policy and are not generic payload concerns.

## Intentionally Retained Transitional Paths

### Setup finalization

`setup_finalization.py` remains until resumable onboarding and the `initial_workspace_sync` job replace synchronous first-run setup.

Removing it earlier would discard current league claiming, season creation, activation, locking, and transaction semantics.

### Historical season import

`season_import.py` remains until the `historical_season_import` job supports:

- HTTP 202 acceptance
- durable job status
- worker execution
- workspace ownership checks
- historical-season activation rules
- safe failure recovery

### Provider verification

`verify_provider.py` remains because short provider validation is permitted when it does not perform canonical synchronization.

### ESPN environment credential fallback

Environment fallback remains temporarily for migration compatibility and controlled diagnostics.

It must not be removed until credential replacement, credential-schema versioning, multi-key decryption, and key-rotation tooling are implemented.

Ordinary SaaS workspaces must ultimately use workspace-owned encrypted credentials.

## Static Analysis

Static analysis was run with temporary isolated `uvx` environments. No dependency was added to `pyproject.toml` or `uv.lock`.

### Vulture

At 80 percent confidence, Vulture reported only Pydantic validator `cls` parameters in `Settings`.

These are framework-required method signatures and are not removal candidates.

### Radon

The highest-complexity production functions include:

| Function or class | Complexity |
| --- | ---: |
| `audit_saas_migration_readiness` | 55 |
| `_build_substitutions` | 26 |
| `import_provider_season` | 23 |
| `log_in_user` | 20 |
| `select_completed_weeks` | 19 |
| `finalize_installation_setup` | 19 |
| `providers.domain.ProviderMatchup` | 15 |
| `providers.domain.ProviderLeague` | 15 |

`app/providers/domain.py` received maintainability grade B. Other analyzed production modules remained grade A.

## Deferred Refactoring

The following work should use separate characterization-first slices:

1. Split migration-readiness auditing into individually named invariant checks.
2. Extract historical-import validation, persistence, and activation phases before moving import to a durable job.
3. Replace setup finalization with resumable onboarding rather than merely splitting the synchronous function.
4. Simplify optimal-lineup substitution logic with dedicated behavioral fixtures.
5. Review provider-domain validation helpers before adding Sleeper DTOs.
6. Split large provider and repository classes only where responsibilities and transaction ownership remain explicit.
7. Review authentication orchestration separately from provider hardening.

File size or cyclomatic complexity alone is not sufficient justification for merging repositories, services, handlers, or provider boundaries.

## Validation

The cleanup passed:

- Ruff formatting
- Ruff lint
- 793 backend tests
- Alembic model comparison
- Alembic head verification
- Alembic current-revision verification
- synchronization boundary checks
- migration-impact checks
- repository diff checks

No database migration is required.

## Next Provider Work

Before Sleeper implementation:

1. Create the controlled ESPN live-validation runbook.
2. Complete credential hardening:
   - credential-schema versioning
   - credential replacement
   - multi-key decryption
   - key-rotation rehearsal
   - ordinary SaaS environment-fallback disablement
3. Add sanitized ESPN fixtures and shared provider contract tests.
4. Add conservative provider concurrency and outage behavior.
5. Run authorized public/private ESPN smoke tests when controlled leagues are available.
