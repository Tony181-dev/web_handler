"""Secrets live in Windows Credential Manager, or the macOS Keychain. Only their names are stored on disk."""
import json
import sys

from . import paths
from .i18n import t

SERVICE_PREFIX = "WebHandler/"
ACCOUNT = "web_handler"


def store_name():
    """Where the secrets are kept, as the person's system calls it."""
    return t("Windows Credential Manager") if sys.platform == "win32" else t("macOS Keychain")


def _index_path():
    return paths.data_dir() / "secrets.json"


def names():
    try:
        return sorted(json.loads(_index_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def _write_names(values):
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(values), ensure_ascii=False), encoding="utf-8")


def valid_name(name):
    """Any characters, except what would break the {{secret:name}} template around them."""
    return bool(name) and name == name.strip() and name.isprintable() and not set(name) & set("{}")


def set_secret(name, value):
    import keyring

    if not valid_name(name):
        raise ValueError(t("A secret name cannot be empty, start or end with a space, or hold { or }."))
    keyring.set_password(SERVICE_PREFIX + name, ACCOUNT, value)
    _write_names(set(names()) | {name})


def get_secret(name):
    import keyring

    return keyring.get_password(SERVICE_PREFIX + name, ACCOUNT)


def exists(name):
    return get_secret(name) is not None


def delete_secret(name):
    import keyring
    from keyring.errors import PasswordDeleteError

    try:
        keyring.delete_password(SERVICE_PREFIX + name, ACCOUNT)
    except PasswordDeleteError:
        pass
    _write_names(set(names()) - {name})
