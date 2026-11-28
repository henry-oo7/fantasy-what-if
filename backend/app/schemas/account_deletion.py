"""Personal account deletion contracts.

Requests carry the current password and the typed confirmation phrase.
Responses never carry passwords, hashes, other users, job or delivery IDs.
"""

from typing import Literal

from pydantic import BaseModel, Field


class AccountDeletionRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    confirmation: str = Field(min_length=1, max_length=64)


class AccountDeletedResponse(BaseModel):
    detail: str
    code: Literal["account_deleted"] = "account_deleted"
