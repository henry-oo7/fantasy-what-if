"""Legacy workspace-slug normalization (retired with the workspace schema).

Kept only for the legacy spectator login and workspace-deletion confirmation
until ``chore/retire-workspace-schema``. Personal accounts have no slug.
"""

import re

WORKSPACE_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
WORKSPACE_SLUG_MINIMUM_LENGTH = 3
WORKSPACE_SLUG_MAXIMUM_LENGTH = 50

RESERVED_WORKSPACE_SLUGS = frozenset(
    {
        "admin",
        "administrator",
        "api",
        "app",
        "auth",
        "billing",
        "help",
        "login",
        "logout",
        "operator",
        "register",
        "registration",
        "security",
        "setup",
        "spectator",
        "status",
        "support",
        "system",
        "www",
    }
)


class InvalidWorkspaceSlugError(ValueError):
    """The requested workspace slug is invalid."""


def normalize_workspace_slug(
    workspace_slug: str,
) -> str:
    normalized = workspace_slug.strip().casefold()

    if not (WORKSPACE_SLUG_MINIMUM_LENGTH <= len(normalized) <= WORKSPACE_SLUG_MAXIMUM_LENGTH):
        raise InvalidWorkspaceSlugError(
            "Workspace slug must contain between "
            f"{WORKSPACE_SLUG_MINIMUM_LENGTH} and "
            f"{WORKSPACE_SLUG_MAXIMUM_LENGTH} characters."
        )

    if not WORKSPACE_SLUG_PATTERN.fullmatch(normalized):
        raise InvalidWorkspaceSlugError(
            "Workspace slug may contain lowercase letters, "
            "numbers, and single hyphens between segments."
        )

    if normalized in RESERVED_WORKSPACE_SLUGS:
        raise InvalidWorkspaceSlugError("That workspace slug is reserved.")

    return normalized
