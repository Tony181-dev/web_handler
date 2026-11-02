"""The app's languages. The code is written in English; other languages live in ui/lang/<language>.json.

The chosen language is saved in the data folder. Workers are told it when they start.
"""
import json

from . import paths, settings

DEFAULT_LANGUAGE = "en"
LANGUAGES = ("en", "vi")

_current = None
_tables = {DEFAULT_LANGUAGE: {}}


def language():
    global _current
    if _current is None:
        saved = settings.read().get("language")
        _current = saved if saved in LANGUAGES else DEFAULT_LANGUAGE
    return _current


def use(chosen):
    """Use a language in this process only. None means read the saved choice again."""
    global _current
    _current = chosen if chosen in LANGUAGES else None


def save(chosen):
    if chosen not in LANGUAGES:
        raise ValueError(t("Unknown language."))
    settings.update(language=chosen)
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
