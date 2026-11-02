"""The app's languages. The code is written in English; other languages live in ui/lang/<language>.json.

The chosen language is saved in the data folder. Workers are told it when they start.
"""
import functools
import json
import re

from . import paths, settings

DEFAULT_LANGUAGE = "en"
LANGUAGES = ("en", "vi")
# A {name} to fill in, but not the {{row}} kept for the person.
PLACEHOLDER = re.compile(r"(?<!\{)\{(\w+)\}(?!\})")

_current = None
_tables = {DEFAULT_LANGUAGE: {}}
_patterns = None


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


def _sentence_patterns():
    """Every sentence, as written in each language, as a pattern giving back its English text and values."""
    global _patterns
    if _patterns is None:
        found = []
        for chosen in LANGUAGES:
            if chosen == DEFAULT_LANGUAGE:
                continue
            for english, translated in _table(chosen).items():
                for written in {english, translated}:
                    parts = PLACEHOLDER.split(written)
                    literal = "".join(parts[0::2])
                    # A sentence with no words of its own, such as "{label}: {error}", would match anything.
                    if not parts[1::2] or not re.search(r"[^\W\d_]", literal):
                        continue
                    pattern = "".join(
                        re.escape(part) if index % 2 == 0 else "(.+?)" for index, part in enumerate(parts)
                    )
                    found.append((len(literal), re.compile(pattern, re.DOTALL), parts[1::2], english))
        found.sort(key=lambda item: -item[0])
        _patterns = found
    return _patterns


@functools.cache
def _whole_sentences():
    """Each sentence as written in any language, to its English text."""
    known = {}
    for chosen in LANGUAGES[1:]:
        for english, translated in _table(chosen).items():
            known[translated] = english
            known[english] = english
    return known


@functools.lru_cache(maxsize=4096)
def _retranslate(text, chosen):
    # `chosen` only keeps answers for one language apart from those for another.
    english = _whole_sentences().get(text)
    if english is not None:
        return t(english)
    for _, pattern, names, english in _sentence_patterns():
        match = pattern.fullmatch(text)
        if match:
            return t(english, **{name: retranslate(value) for name, value in zip(names, match.groups())})
    head, separator, tail = text.partition(": ")
    if separator and head and tail:
        return f"{retranslate(head)}: {retranslate(tail)}"
    return text


def retranslate(text):
    """A message saved in any language, such as in the log of an earlier run, in the current language.

    Text that is not one of the app's sentences, such as an element name or a value, stays as it is.
    """
    if not isinstance(text, str) or not text.strip():
        return text
    return _retranslate(text, language())
