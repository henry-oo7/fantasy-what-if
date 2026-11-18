"""Audit the current database before SaaS ownership migrations."""

from app.core.settings import get_settings
from app.db import model_registry  # noqa: F401
from app.db.session import SessionFactory
from app.services.saas_migration_audit import (
    audit_saas_migration_readiness,
)


def main() -> None:
    settings = get_settings()
    key_setting = settings.provider_credential_key
    credential_key = key_setting.get_secret_value() if key_setting is not None else None

    with SessionFactory() as session:
        audit = audit_saas_migration_readiness(
            session,
            provider_credential_key=credential_key,
        )

    print("=== SAAS MIGRATION READINESS AUDIT ===")

    for check in audit.checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"{status}: {check.message}")

    passed_count = sum(check.passed for check in audit.checks)
    failed_count = len(audit.checks) - passed_count

    print()
    print("=== AUDIT SUMMARY ===")
    print(f"Checks passed: {passed_count}")
    print(f"Checks failed: {failed_count}")

    if not audit.passed:
        raise SystemExit("FAIL: the database is not ready for SaaS migration.")

    print("PASS: the database satisfies the current SaaS migration prerequisites.")
    print("No database changes or provider requests were performed.")


if __name__ == "__main__":
    main()
