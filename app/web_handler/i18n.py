"""The app's languages. The code is written in English; other languages live in ui/lang/<language>.json.

The chosen language is saved in the data folder. Workers are told it when they start.
"""
import json

from . import paths

DEFAULT_LANGUAGE = "en"
LANGUAGES = ("en", "vi")
SETTINGS_FILE = "settings.json"

_current = None
_tables = {DEFAULT_LANGUAGE: {}}


def _read_settings():
    try:
        settings = json.loads((paths.data_dir() / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def language():
    global _current
    if _current is None:
        saved = _read_settings().get("language")
        _current = saved if saved in LANGUAGES else DEFAULT_LANGUAGE
    return _current


def use(chosen):
    """Use a language in this process only. None means read the saved choice again."""
    global _current
    _current = chosen if chosen in LANGUAGES else None


def save(chosen):
    if chosen not in LANGUAGES:
        raise ValueError(t("Unknown language."))
    settings = _read_settings()
    settings["language"] = chosen
    folder = paths.data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / SETTINGS_FILE).write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    use(chosen)


def _table(chosen):
    if chosen not in _tables:
        path = paths.UI_DIR / "lang" / f"{chosen}.json"
        _tables[chosen] = json.loads(path.read_text(encoding="utf-8"))
    return _tables[chosen]


def t(text, **values):
    """The text in the current language, with each {name} filled in. Unlike format(), {{row}} stays as it is."""
    result = _table(language()).get(text, text)
    for name, value in values.items():
        result = result.replace("{" + name + "}", str(value))
    return result
