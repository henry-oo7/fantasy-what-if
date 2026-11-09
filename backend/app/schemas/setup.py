"""Public setup lifecycle schemas."""

from typing import Literal

from pydantic import BaseModel


class SetupStatusResponse(BaseModel):
    """Minimum public state required for frontend routing."""

    status: Literal[
        "setup_required",
        "setup_in_progress",
        "active",
    ]
    installation_exists: bool
    bootstrap_available: bool
