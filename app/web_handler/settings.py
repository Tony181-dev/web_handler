"""Choices kept between starts, in the data folder."""
import json

from . import paths

SETTINGS_FILE = "settings.json"
BROWSER_VISIBLE = "visible"
BROWSER_OFFSCREEN = "offscreen"
BROWSER_HEADLESS = "headless"
BROWSER_MODES = (BROWSER_VISIBLE, BROWSER_OFFSCREEN, BROWSER_HEADLESS)


def read():
    try:
        settings = json.loads((paths.data_dir() / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def update(**values):
    settings = {**read(), **values}
    folder = paths.data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / SETTINGS_FILE).write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")


def browser_mode():
    saved = read().get("browser_mode")
    return saved if saved in BROWSER_MODES else BROWSER_VISIBLE


def save_browser_mode(mode):
    if mode not in BROWSER_MODES:
        raise ValueError(mode)
    update(browser_mode=mode)


def keep_browser():
    """Whether a run leaves its window open at the end, so the person can carry on by hand."""
    return read().get("keep_browser") is True


def save_keep_browser(keep):
    if not isinstance(keep, bool):
        raise ValueError(keep)
    update(keep_browser=keep)
