"""Start the API with explicit trusted-proxy configuration."""

from __future__ import annotations

import uvicorn

from app.core.settings import get_settings


def main() -> None:
    """Run Uvicorn using validated application settings."""

    settings = get_settings()
    forwarded_allow_ips = list(settings.forwarded_allow_ip_list)

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        proxy_headers=True,
        forwarded_allow_ips=forwarded_allow_ips,
        log_level=settings.app_log_level.casefold(),
    )


if __name__ == "__main__":
    main()
