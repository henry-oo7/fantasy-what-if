"""Dependency-injected registration infrastructure."""

from app.notifications.transactional_email import (
    TransactionalEmailSender,
    UnconfiguredTransactionalEmailSender,
)


def get_transactional_email_sender() -> TransactionalEmailSender:
    """Return the configured transactional email sender.

    The first implementation fails closed until an external provider
    is selected and configured.
    """

    return UnconfiguredTransactionalEmailSender()
