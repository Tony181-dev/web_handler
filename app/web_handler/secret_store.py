"""Secrets live in Windows Credential Manager. Only their names are stored on disk."""
import json

from . import paths
from .i18n import t

SERVICE_PREFIX = "WebHandler/"
ACCOUNT = "web_handler"


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
    return bool(name) and name.replace("_", "").replace("-", "").isalnum()


def set_secret(name, value):
    import keyring

    if not valid_name(name):
        raise ValueError(t("A secret name can only hold letters, digits, dashes and underscores."))
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
