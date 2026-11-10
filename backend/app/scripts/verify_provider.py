"""Verify the active fantasy provider without exposing credentials."""

from app.core.settings import get_settings
from app.db.models import Season
from app.db.session import SessionFactory
from app.providers.errors import ProviderError
from app.services.provider_adapter_resolver import (
    ProviderAdapterResolutionError,
    resolve_active_provider_adapter,
)


def main() -> None:
    """Validate and inspect the active provider connection."""

    settings = get_settings()

    print("=== ACTIVE FANTASY PROVIDER VERIFICATION ===")
    print("Provider credentials intentionally hidden.")

    try:
        with SessionFactory() as session:
            context, provider = resolve_active_provider_adapter(
                session=session,
                settings=settings,
            )

            season = session.get(
                Season,
                context.season_id,
            )

            if season is None:
                raise ProviderAdapterResolutionError("The active season does not exist.")

            league = season.league

            if league.provider_league_id is None:
                raise ProviderAdapterResolutionError(
                    "The active league does not have a provider league identifier."
                )

            validation = provider.validate_configuration()

            if not validation.is_valid:
                print()
                print("=== VALIDATION FAILED ===")
                print(validation.safe_message)
                raise SystemExit(1)

            provider_league = provider.fetch_league(
                provider_league_id=(league.provider_league_id),
                provider_season_id=(season.provider_season_id),
            )

    except (
        ProviderAdapterResolutionError,
        ProviderError,
    ) as exc:
        print()
        print("=== VERIFICATION FAILED ===")

        safe_message = getattr(
            exc,
            "safe_message",
            str(exc),
        )

        print(safe_message)
        print("Credential values were not displayed.")
        raise SystemExit(1) from None

    print()
    print("=== ACTIVE CONTEXT ===")
    print(f"Installation ID: {context.installation_id}")
    print(f"Internal season ID: {context.season_id}")
    print(f"Provider: {context.provider.value}")
    print(f"Credential source: {context.credentials_source}")

    print()
    print("=== PROVIDER LEAGUE ===")
    print(f"Name: {provider_league.name}")
    print(f"Provider league ID: {provider_league.provider_league_id}")
    print(f"Provider season ID: {provider_league.provider_season_id}")
    print(f"Season year: {provider_league.season_year}")
    print(f"Current week: {provider_league.current_week}")
    print(f"Regular-season weeks: {provider_league.regular_season_weeks}")
    print(f"Teams returned: {len(provider_league.teams)}")

    print()
    print("=== CAPABILITIES ===")
    print(f"Weekly matchups: {provider_league.capabilities.supports_weekly_matchups}")
    print(f"Lineup history: {provider_league.capabilities.supports_lineup_history}")
    print(f"Schedule swap: {provider_league.capabilities.supports_schedule_swap}")
    print(f"All-play: {provider_league.capabilities.supports_all_play}")
    print(f"League average: {provider_league.capabilities.supports_league_average}")

    print()
    print("=== RESULT ===")
    print(validation.safe_message)
    print("Active provider verification succeeded.")
    print("Credential values were not displayed.")
    print("No database rows were written.")


if __name__ == "__main__":
    main()
