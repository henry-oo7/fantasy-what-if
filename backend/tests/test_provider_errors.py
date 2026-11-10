import pytest

from app.db.platform_enums import FantasyProvider
from app.providers.errors import (
    ProviderAuthenticationError,
    ProviderCapabilityError,
    ProviderConfigurationError,
    ProviderError,
    ProviderErrorContext,
    ProviderNotSupportedError,
    ProviderRateLimitError,
)


def test_provider_error_exposes_safe_context() -> None:
    error = ProviderAuthenticationError(
        context=ProviderErrorContext(
            provider=FantasyProvider.ESPN,
            operation="validate_configuration",
            retryable=False,
        )
    )

    assert isinstance(error, ProviderError)
    assert error.provider is FantasyProvider.ESPN
    assert error.operation == "validate_configuration"
    assert error.retryable is False
    assert str(error) == error.safe_message


def test_rate_limit_error_can_be_retryable() -> None:
    error = ProviderRateLimitError(
        context=ProviderErrorContext(
            provider=FantasyProvider.YAHOO,
            operation="fetch_matchups",
            retryable=True,
        )
    )

    assert error.retryable is True


def test_error_hierarchy_supports_specific_failures() -> None:
    context = ProviderErrorContext(
        provider=FantasyProvider.SLEEPER,
        operation="fetch_league",
    )

    assert isinstance(
        ProviderConfigurationError(context=context),
        ProviderError,
    )
    assert isinstance(
        ProviderCapabilityError(context=context),
        ProviderError,
    )
    assert isinstance(
        ProviderNotSupportedError(context=context),
        ProviderError,
    )


def test_empty_operation_is_rejected() -> None:
    with pytest.raises(ValueError):
        ProviderErrorContext(
            provider=FantasyProvider.ESPN,
            operation=" ",
        )


def test_chained_secret_is_not_in_safe_message() -> None:
    secret = "sensitive-provider-token"

    try:
        raise RuntimeError(secret)
    except RuntimeError as cause:
        error = ProviderAuthenticationError(
            context=ProviderErrorContext(
                provider=FantasyProvider.ESPN,
                operation="validate_configuration",
            )
        )

        error.__cause__ = cause

    assert secret not in str(error)
    assert error.__cause__ is not None
