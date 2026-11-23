"""FR-20 workspace deletion request, status, and cancellation contracts.

Responses never carry passwords, hashes, provider credentials, claim
identity, internal usernames, session hashes, or job payloads.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.services.workspace_deletion import WorkspaceDeletionStatus


class WorkspaceDeletionStatusResponse(BaseModel):
    """Active review (standard session) or pending state (recovery session)."""

    status: WorkspaceDeletionStatus
    grace_period_days: int | None = None
    confirmation_slug: str | None = None
    requested_at: datetime | None = None
    scheduled_for: datetime | None = None
    cancellable: bool | None = None


class WorkspaceDeletionRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    confirmation_slug: str = Field(min_length=1, max_length=50)


class WorkspaceDeletionRequestedResponse(BaseModel):
    detail: str
    code: Literal["workspace_deletion_requested"] = "workspace_deletion_requested"
    scheduled_for: datetime


class WorkspaceDeletionCancellationRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)


class WorkspaceDeletionCanceledResponse(BaseModel):
    detail: str
    code: Literal["workspace_deletion_canceled"] = "workspace_deletion_canceled"
