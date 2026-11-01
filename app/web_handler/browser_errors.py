"""Errors shared by the runner and the browser layer, with no browser library needed."""


class BrowserClosed(Exception):
    """The user closed the window, or the browser died."""


class ElementError(Exception):
    """An element could not be found, or matched more than once."""
