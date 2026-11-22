"""Provider-independent transactional email contract."""

from typing import Protocol


class TransactionalEmailDeliveryError(RuntimeError):
    """Transactional email could not be delivered safely."""


class TransactionalEmailSender(Protocol):
    """Send identity and security messages without exposing tokens."""

    def send_email_verification(
        self,
        *,
        recipient_email: str,
        recipient_display_name: str,
        verification_token: str,
    ) -> None:
        """Deliver one administrator email-verification message."""

    def send_password_reset(
        self,
        *,
        recipient_email: str,
        recipient_display_name: str,
        password_reset_token: str,
    ) -> None:
        """Deliver one administrator password-reset message."""


class UnconfiguredTransactionalEmailSender:
    """Fail closed until an external email provider is configured."""

    def send_email_verification(
        self,
        *,
        recipient_email: str,
        recipient_display_name: str,
        verification_token: str,
    ) -> None:
        del (
            recipient_email,
            recipient_display_name,
            verification_token,
        )

        raise TransactionalEmailDeliveryError("Transactional email delivery is not configured.")

    def send_password_reset(
        self,
        *,
        recipient_email: str,
        recipient_display_name: str,
        password_reset_token: str,
    ) -> None:
        del (
            recipient_email,
            recipient_display_name,
            password_reset_token,
        )

        raise TransactionalEmailDeliveryError("Transactional email delivery is not configured.")
