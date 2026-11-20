"""Rotate stored provider credentials to the current key version."""

from __future__ import annotations

import argparse

from app.core.settings import get_settings
from app.db import model_registry  # noqa: F401
from app.db.session import SessionFactory
from app.services.provider_credential_key_rotation import (
    DEFAULT_ROTATION_BATCH_SIZE,
    rotate_provider_credential_key_batch_committed,
)


def parse_arguments() -> argparse.Namespace:
    """Parse safe credential-rotation arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Rotate stored provider credential envelopes to the configured current key version."
        )
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_ROTATION_BATCH_SIZE,
        help=(
            "Maximum provider connections committed per batch. "
            f"Default: {DEFAULT_ROTATION_BATCH_SIZE}."
        ),
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        help=(
            "Stop after this many committed batches. "
            "Omit to continue until no outdated envelopes remain."
        ),
    )
    parser.add_argument(
        "--confirm-rotation",
        action="store_true",
        help=("Required acknowledgement that the command will update encrypted database values."),
    )

    return parser.parse_args()


def run_rotation(
    *,
    batch_size: int,
    max_batches: int | None,
    confirm_rotation: bool,
) -> None:
    """Run resumable committed rotation batches."""

    if batch_size < 1:
        raise SystemExit("--batch-size must be positive.")

    if max_batches is not None and max_batches < 1:
        raise SystemExit("--max-batches must be positive.")

    if not confirm_rotation:
        raise SystemExit(
            "Rotation was not started. Re-run with "
            "--confirm-rotation after verifying that the "
            "complete keyring is backed up."
        )

    settings = get_settings()
    keyring = settings.provider_credential_keyring()

    if not keyring.configured:
        raise SystemExit("Provider credential encryption is not configured.")

    keyring.key_for_version(keyring.current_version)

    print("=== PROVIDER CREDENTIAL KEY ROTATION ===")
    print(f"Current key version: {keyring.current_version}")
    print(f"Batch size: {batch_size}")
    print("Credential values will not be displayed.")

    batch_number = 0
    total_rotated = 0

    while True:
        if max_batches is not None and batch_number >= max_batches:
            print()
            print("=== ROTATION PAUSED ===")
            print(f"Committed batches: {batch_number}")
            print(f"Provider connections rotated: {total_rotated}")
            print("Re-run the same command to continue idempotently.")
            return

        result = rotate_provider_credential_key_batch_committed(
            session_factory=SessionFactory,
            credential_keyring=keyring,
            batch_size=batch_size,
        )

        if result.blocked:
            print()
            print("=== ROTATION PAUSED ===")
            print(
                "Outdated provider credential envelopes "
                "remain, but they are currently locked by "
                "another database transaction."
            )
            print("Wait for the other operation to finish, then rerun this command.")
            return

        if result.complete:
            print()
            print("=== ROTATION COMPLETE ===")
            print(f"Committed batches: {batch_number}")
            print(f"Provider connections rotated: {total_rotated}")
            print("No outdated encrypted credential envelopes remain.")
            return

        batch_number += 1
        total_rotated += result.rotated_count

        print(
            "Committed batch "
            f"{batch_number}: "
            f"{result.rotated_count} connection(s) "
            f"moved to key version "
            f"{result.current_version}."
        )


def main() -> None:
    """Run provider credential key rotation."""

    arguments = parse_arguments()

    run_rotation(
        batch_size=arguments.batch_size,
        max_batches=arguments.max_batches,
        confirm_rotation=arguments.confirm_rotation,
    )


if __name__ == "__main__":
    main()
