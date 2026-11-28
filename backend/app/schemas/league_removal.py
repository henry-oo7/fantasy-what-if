"""Personal League removal contracts.

The response carries no access-row, connection, job, user, workspace or claim
identifier and never says whether another account keeps the League.
"""

from typing import Literal

from pydantic import BaseModel, Field


class LeagueRemovalRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=200)


class LeagueRemovedResponse(BaseModel):
    detail: str
    code: Literal["league_access_removed"] = "league_access_removed"
