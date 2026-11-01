"""Replace {{...}} in step fields with row, variable and secret values."""
import re
from .i18n import t

TOKEN = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
COLUMN = re.compile(r"^[A-Z]{1,3}$")


class TemplateError(Exception):
    pass


def parse_token(token):
    """Return (kind, name) for the text between the braces."""
    if COLUMN.match(token):
        return "column", token
    if token == "row":
        return "row", None
    if token == "i":
        return "i", None
    for kind in ("var", "secret"):
        if token.startswith(kind + ":") and token[len(kind) + 1:].strip():
            return kind, token[len(kind) + 1:].strip()
    raise TemplateError(t("Cannot understand the template {{{token}}}.", token=token))


def references(text):
    return [parse_token(match.group(1)) for match in TOKEN.finditer(text or "")]


def has_secret(text):
    return any(kind == "secret" for kind, _ in references(text))


class Context:
    def __init__(self, get_secret=None):
        self.columns = {}
        self.variables = {}
        self.row = None
        self.index = None
        self.get_secret = get_secret
        self.secret_values = set()

    def value_of(self, kind, name):
        if kind == "column":
            if self.row is None:
                raise TemplateError(t("Column {name} only works inside a loop over Excel rows.", name=name))
            return self.columns.get(name, "")
        if kind == "row":
            if self.row is None:
                raise TemplateError(t("{{row}} only works inside a loop over Excel rows."))
            return str(self.row)
        if kind == "i":
            if self.index is None:
                raise TemplateError(t("{{i}} only works inside a loop."))
            return str(self.index)
        if kind == "var":
            if name not in self.variables:
                raise TemplateError(t("Variable {name} has no value yet.", name=name))
            return self.variables[name]
        value = self.get_secret(name) if self.get_secret else None
        if value is None:
            raise TemplateError(t("Secret {name} has not been saved.", name=name))
        self.secret_values.add(value)
        return value

    def mask(self, text):
        """Hide every secret used so far. Longest first, so no partial value is left."""
        text = str(text)
        for value in sorted(self.secret_values, key=len, reverse=True):
            if value:
                text = text.replace(value, "••••")
        return text


def render(text, context):
    return TOKEN.sub(lambda match: context.value_of(*parse_token(match.group(1))), text or "")
