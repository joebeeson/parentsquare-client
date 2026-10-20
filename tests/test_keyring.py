import pytest

from parentsquare.keyring import KeyringCredentialStore


def test_keyring_helper_has_explicit_optional_dependency(monkeypatch) -> None:
    store = KeyringCredentialStore()
    monkeypatch.setitem(__import__("sys").modules, "keyring", None)
    with pytest.raises(ImportError, match=r"parentsquare-client\[keyring\]"):
        store.get_password("parent@example.com")
