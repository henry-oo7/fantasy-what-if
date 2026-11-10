"""Canonical platform enumeration types."""

from enum import StrEnum


class FantasyProvider(StrEnum):
    ESPN = "espn"
    SLEEPER = "sleeper"
    YAHOO = "yahoo"


class InstallationStatus(StrEnum):
    SETUP_REQUIRED = "setup_required"
    SETUP_IN_PROGRESS = "setup_in_progress"
    ACTIVE = "active"


class SeasonStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class FantasySport(StrEnum):
    FOOTBALL = "football"
