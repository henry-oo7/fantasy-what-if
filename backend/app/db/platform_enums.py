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


class BackgroundJobType(StrEnum):
    INITIAL_WORKSPACE_SYNC = "initial_workspace_sync"
    INCREMENTAL_SEASON_SYNC = "incremental_season_sync"
    FULL_SEASON_SYNC = "full_season_sync"
    HISTORICAL_SEASON_IMPORT = "historical_season_import"
    PROVIDER_CREDENTIAL_VALIDATION = "provider_credential_validation"
    PROVIDER_TOKEN_REFRESH = "provider_token_refresh"
    PROVISIONAL_WORKSPACE_CLEANUP = "provisional_workspace_cleanup"
    EXPIRED_SESSION_CLEANUP = "expired_session_cleanup"
    EXPIRED_TOKEN_CLEANUP = "expired_token_cleanup"
    WORKSPACE_DELETION = "workspace_deletion"


class BackgroundJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_SCHEDULED = "retry_scheduled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
