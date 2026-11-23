"""Operator recovery: re-enqueue one due FR-20 workspace deletion job.

Use after a ``workspace_deletion`` job failed terminally (for example,
exhausted retries) while the workspace remains ``deletion_pending``.
The command never deletes data and never changes workspace status; the
normal worker performs the deletion. Output contains only the workspace
ID, job ID, job status, and a refusal code.
"""

from __future__ import annotations

import argparse
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.db import model_registry  # noqa: F401
from app.services.workspace_deletion_execution import (
    WorkspaceDeletionReenqueueRefusedError,
    reenqueue_workspace_deletion,
)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create or return the one active workspace_deletion job for a due, "
            "deletion-pending workspace. Never deletes data directly."
        )
    )
    parser.add_argument(
        "--workspace-id",
        type=int,
        required=True,
        help="ID of the deletion-pending workspace.",
    )

    return parser.parse_args(argv)


def run_reenqueue(
    *,
    workspace_id: int,
    session_factory: sessionmaker[Session] | None = None,
    now: datetime | None = None,
) -> int:
    """Return a process exit code: 0 on success, 2 on refusal."""

    if workspace_id < 1:
        print("REFUSED: workspace_id must be positive.")
        return 2

    if session_factory is None:
        from app.db.session import SessionFactory

        session_factory = SessionFactory

    with session_factory() as session:
        try:
            result = reenqueue_workspace_deletion(
                session,
                workspace_id=workspace_id,
                now=now,
            )
        except WorkspaceDeletionReenqueueRefusedError as exc:
            session.rollback()
            print(f"REFUSED: workspace_id={workspace_id} reason={exc.refusal.value}")
            return 2

        session.commit()

    state = "existing" if result.existing else "created"
    print(
        f"OK: workspace_id={workspace_id} job_id={result.job_id} "
        f"job_status={result.status.value} job={state}"
    )

    return 0


def main(argv: list[str] | None = None) -> None:
    arguments = parse_arguments(argv)

    raise SystemExit(run_reenqueue(workspace_id=arguments.workspace_id))


if __name__ == "__main__":
    main()
