from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from app.providers.credential_keyring import (
    ProviderCredentialKeyring,
)
from app.scripts.rotate_provider_credential_keys import (
    run_rotation,
)


def keyring() -> ProviderCredentialKeyring:
    return ProviderCredentialKeyring(
        keys={
            1: Fernet.generate_key().decode("ascii"),
            2: Fernet.generate_key().decode("ascii"),
        },
        current_version=2,
    )


def settings():
    configured_keyring = keyring()

    return SimpleNamespace(provider_credential_keyring=(lambda: configured_keyring))


def result(
    *,
    selected_count: int,
    rotated_count: int,
    remaining_count: int | None = None,
):
    effective_remaining_count = remaining_count if remaining_count is not None else selected_count

    return SimpleNamespace(
        selected_count=selected_count,
        rotated_count=rotated_count,
        current_version=2,
        remaining_count=effective_remaining_count,
        complete=effective_remaining_count == 0,
        blocked=(selected_count == 0 and effective_remaining_count > 0),
    )


def test_confirmation_is_required() -> None:
    with pytest.raises(
        SystemExit,
        match="confirm-rotation",
    ):
        run_rotation(
            batch_size=100,
            max_batches=None,
            confirm_rotation=False,
        )


@pytest.mark.parametrize(
    ("batch_size", "max_batches", "message"),
    [
        (0, None, "batch-size"),
        (100, 0, "max-batches"),
    ],
)
def test_positive_limits_are_required(
    batch_size: int,
    max_batches: int | None,
    message: str,
) -> None:
    with pytest.raises(
        SystemExit,
        match=message,
    ):
        run_rotation(
            batch_size=batch_size,
            max_batches=max_batches,
            confirm_rotation=True,
        )


def test_rotation_runs_until_complete(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rotate = MagicMock(
        side_effect=[
            result(
                selected_count=2,
                rotated_count=2,
            ),
            result(
                selected_count=1,
                rotated_count=1,
            ),
            result(
                selected_count=0,
                rotated_count=0,
            ),
        ]
    )

    with (
        patch(
            "app.scripts.rotate_provider_credential_keys.get_settings",
            return_value=settings(),
        ),
        patch(
            "app.scripts.rotate_provider_credential_keys."
            "rotate_provider_credential_key_batch_committed",
            rotate,
        ),
    ):
        run_rotation(
            batch_size=2,
            max_batches=None,
            confirm_rotation=True,
        )

    output = capsys.readouterr().out

    assert rotate.call_count == 3
    assert "ROTATION COMPLETE" in output
    assert "Provider connections rotated: 3" in output
    assert "cookie" not in output.casefold()


def test_locked_rows_pause_without_false_completion(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rotate = MagicMock(
        return_value=result(
            selected_count=0,
            rotated_count=0,
            remaining_count=2,
        )
    )

    with (
        patch(
            "app.scripts.rotate_provider_credential_keys.get_settings",
            return_value=settings(),
        ),
        patch(
            "app.scripts.rotate_provider_credential_keys."
            "rotate_provider_credential_key_batch_committed",
            rotate,
        ),
    ):
        run_rotation(
            batch_size=10,
            max_batches=None,
            confirm_rotation=True,
        )

    output = capsys.readouterr().out

    rotate.assert_called_once()
    assert "ROTATION PAUSED" in output
    assert "currently locked" in output
    assert "ROTATION COMPLETE" not in output


def test_max_batches_pauses_resumably(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rotate = MagicMock(
        return_value=result(
            selected_count=1,
            rotated_count=1,
        )
    )

    with (
        patch(
            "app.scripts.rotate_provider_credential_keys.get_settings",
            return_value=settings(),
        ),
        patch(
            "app.scripts.rotate_provider_credential_keys."
            "rotate_provider_credential_key_batch_committed",
            rotate,
        ),
    ):
        run_rotation(
            batch_size=10,
            max_batches=1,
            confirm_rotation=True,
        )

    output = capsys.readouterr().out

    rotate.assert_called_once()
    assert "ROTATION PAUSED" in output
    assert "Re-run the same command" in output


def test_unconfigured_keyring_is_rejected() -> None:
    unconfigured = ProviderCredentialKeyring(
        keys={},
        current_version=1,
    )
    fake_settings = SimpleNamespace(provider_credential_keyring=(lambda: unconfigured))

    with (
        patch(
            "app.scripts.rotate_provider_credential_keys.get_settings",
            return_value=fake_settings,
        ),
        pytest.raises(
            SystemExit,
            match="not configured",
        ),
    ):
        run_rotation(
            batch_size=100,
            max_batches=None,
            confirm_rotation=True,
        )
