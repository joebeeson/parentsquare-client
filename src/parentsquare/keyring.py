"""Optional OS keyring helpers for ParentSquare credentials."""

from __future__ import annotations

from dataclasses import dataclass


def _backend():
    try:
        import keyring
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise ImportError(
            "Install parentsquare-client[keyring] to use OS credential storage"
        ) from exc
    return keyring


@dataclass(frozen=True, slots=True)
class KeyringCredentialStore:
    """Read and write passwords in the user's configured OS keyring."""

    service_name: str = "parentsquare"

    def get_password(self, username: str) -> str | None:
        return _backend().get_password(self.service_name, username)

    def set_password(self, username: str, password: str) -> None:
        _backend().set_password(self.service_name, username, password)

    def delete_password(self, username: str) -> None:
        _backend().delete_password(self.service_name, username)
