from app.schemas.setup import (
    SetupProviderConnectionRequest,
)


def test_provider_connection_request_uses_provider_identity() -> None:
    request = SetupProviderConnectionRequest(
        provider="espn",
        league_id="529250970",
        season="2026",
        espn_s2="private-cookie",
        swid="{private-swid}",
    )

    assert request.provider == "espn"
    assert request.league_id == "529250970"
    assert request.season == "2026"
    assert not hasattr(
        request,
        "display_name",
    )
