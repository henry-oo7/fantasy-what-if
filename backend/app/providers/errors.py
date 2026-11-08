"""Safe provider-independent exception hierarchy."""

from __future__ import annotations

from dataclasses import dataclass

from app.db.platform_enums import FantasyProvider


@dataclass(frozen=True, slots=True)
class ProviderErrorContext:
    """Safe context attached to a provider error."""

    provider: FantasyProvider
    operation: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if not self.operation.strip():
            raise ValueError("Provider error operation must not be empty.")


class ProviderError(Exception):
    """Base exception for provider operations."""

    default_safe_message = "The fantasy provider operation failed."

    def __init__(
        self,
        *,
        context: ProviderErrorContext,
        safe_message: str | None = None,
    ) -> None:
        normalized_message = (
            safe_message.strip() if safe_message is not None else self.default_safe_message
        )

        if not normalized_message:
            normalized_message = self.default_safe_message

        self.context = context
        self.safe_message = normalized_message

        super().__init__(normalized_message)

    @property
    def provider(self) -> FantasyProvider:
        return self.context.provider

    @property
    def operation(self) -> str:
        return self.context.operation

    @property
    def retryable(self) -> bool:
        return self.context.retryable

    def __str__(self) -> str:
        return self.safe_message


class ProviderConfigurationError(ProviderError):
    """Provider settings are missing or invalid."""

    default_safe_message = "The fantasy provider configuration is invalid."


class ProviderAuthenticationError(ProviderError):
    """Provider credentials could not be authenticated."""

    default_safe_message = "The fantasy provider credentials were rejected."


class ProviderAuthorizationError(ProviderError):
    """Authenticated provider identity lacks access."""

    default_safe_message = "The fantasy provider account cannot access the requested league."


class ProviderNotFoundError(ProviderError):
    """Requested provider league or season does not exist."""

    default_safe_message = "The requested fantasy provider resource was not found."


class ProviderRateLimitError(ProviderError):
    """Provider temporarily rejected requests due to limits."""

    default_safe_message = "The fantasy provider rate limit was reached."


class ProviderUnavailableError(ProviderError):
    """Provider service is temporarily unavailable."""

    default_safe_message = "The fantasy provider is temporarily unavailable."


class ProviderResponseError(ProviderError):
    """Provider returned an unusable response."""

    default_safe_message = "The fantasy provider returned an invalid response."


class ProviderCapabilityError(ProviderError):
    """Requested operation is unsupported by the provider."""

    default_safe_message = "The fantasy provider does not support the requested operation."


class ProviderNotSupportedError(ProviderError):
    """No adapter is registered for the provider."""

    default_safe_message = "The fantasy provider is not supported."
