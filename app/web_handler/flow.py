"""The flow file: creating, saving and checking it before a run."""
import hashlib
import json
from pathlib import Path
import re
import uuid

from . import paths, templating
from .i18n import t

FLOW_VERSION = 1
DEFAULT_SETTINGS = {
    "step_timeout_seconds": 15,
    "navigation_timeout_seconds": 60,
    "pause_after_consecutive_failures": 3,
}
STEP_NAMES = {
    "goto": "Open link",
    "login": "Login",
    "fill": "Fill",
    "click": "Click",
    "select": "Select",
    "check": "Check",
    "read": "Read value",
    "ocr": "Read OCR",
    "wait_for": "Wait for element",
    "wait": "Wait",
    "verify": "Verify",
    "loop": "Loop",
    "if": "If",
    "manual_pause": "Manual step",
}
TARGET_STEPS = {"fill", "click", "select", "check", "read", "ocr", "wait_for"}
READING_STEPS = ("read", "ocr")
TEMPLATE_FIELDS = ("url", "account", "password", "value", "equals", "contains", "url_contains", "url_not_contains")
WHOLE_SECRET = re.compile(r"^\{\{\s*secret:[^{}]+\}\}$")
# What an "If" block can ask about the page. Only "exists" needs no value.
IF_TESTS = ("exists", "equals", "contains", "url_contains")
IF_TESTS_WITH_ELEMENT = ("exists", "equals", "contains")
IF_WAIT_SECONDS = 3


def new_flow(name):
    return {
        "version": FLOW_VERSION,
        "name": name,
        "settings": dict(DEFAULT_SETTINGS),
        "excel": None,
        "steps": [],
    }


def flow_path(flow_id):
    if not flow_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError(t("Invalid flow ID."))
    return paths.flows_dir() / f"{flow_id}.json"


def new_flow_id():
    return uuid.uuid4().hex[:12]


def load_flow(path):
    with Path(path).open(encoding="utf-8") as file:
        flow = json.load(file)
    flow["settings"] = {**DEFAULT_SETTINGS, **(flow.get("settings") or {})}
    flow.setdefault("excel", None)
    flow.setdefault("steps", [])
    return flow


def save_flow(path, flow):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(flow, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def flow_hash(flow):
    return hashlib.sha256(json.dumps(flow, sort_keys=True).encode("utf-8")).hexdigest()


def find_loop(flow):
    for step in flow.get("steps", []):
        if step.get("do") == "loop":
            return step
    return None


def branches(step):
    """The lists of blocks a block holds: a loop has one, an "If" block has its Yes and No branches."""
    if step.get("do") == "loop":
        return [step.get("steps") or []]
    if step.get("do") == "if":
        return [step.get("then") or [], step.get("else") or []]
    return []


def flatten(steps):
    """Every block in these lists and the blocks they hold, in running order."""
    for step in steps:
        yield step
        for branch in branches(step):
            yield from flatten(branch)


def walk(flow):
    """Yield (step, in_loop) in running order. Loop and "If" blocks themselves are included."""

    def visit(steps, in_loop):
        for step in steps:
            yield step, in_loop
            for branch in branches(step):
                yield from visit(branch, in_loop or step.get("do") == "loop")

    yield from visit(flow.get("steps", []), False)


def stop_column(flow):
    """The column that decides where the rows end: the first one the loop's blocks use."""
    loop = find_loop(flow)
    if not loop:
        return None
    for step in flatten(loop.get("steps", [])):
        for field in TEMPLATE_FIELDS:
            for kind, name in _safe_references(step.get(field)):
                if kind == "column":
                    return name
    required = (flow.get("excel") or {}).get("required") or ()
    return required[0] if required else None


def _safe_references(text):
    # A broken template is reported by validate(), not here.
    try:
        return templating.references(text)
    except templating.TemplateError:
        return []


def step_label(step):
    name = t(STEP_NAMES.get(step.get("do"), step.get("do")))
    if step.get("do") == "wait":
        return t("{name} {seconds} s", name=name, seconds=step.get("seconds"))
    if step.get("do") == "if" and step.get("test") == "url_contains":
        return t("{name} the URL contains {value}", name=name, value=step.get("value") or "")
    target = step.get("target") or {}
    detail = target.get("primary") or step.get("url") or step.get("message") or ""
    return f"{name} {detail}".strip()


class Issues:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, message, step=None):
        self.errors.append({"step": step.get("id") if step else None, "message": message})

    def warn(self, message, step=None):
        self.warnings.append({"step": step.get("id") if step else None, "message": message})

    def as_dict(self):
        return {"errors": self.errors, "warnings": self.warnings}


class CheckContext:
    """What validation needs to know from outside the flow file."""

    def __init__(self, secret_exists=None, columns=None, nonempty_columns=None, excel_error=None):
        self.secret_exists = secret_exists or (lambda name: True)
        self.columns = columns
        self.nonempty_columns = nonempty_columns or set()
        self.excel_error = excel_error


def _check_step_fields(step, issues):
    kind = step.get("do")
    if kind not in STEP_NAMES:
        issues.error(t("Unknown block type {kind}.", kind=repr(kind)), step)
        return
    name = t(STEP_NAMES[kind])
    delay = step.get("delay_after")
    if delay is not None and (isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay <= 0):
        issues.error(t('The delay of the "{name}" block must be a number of seconds above 0.', name=name), step)
    if kind == "goto" and not (step.get("url") or "").strip():
        issues.error(t('The "{name}" block has no link.', name=name), step)
    if kind in TARGET_STEPS and not ((step.get("target") or {}).get("primary") or "").strip():
        issues.error(t('The "{name}" block has no element.', name=name), step)
    if kind == "login":
        password = step.get("password") or ""
        account = step.get("account") or ""
        if not account.strip():
            issues.error(t('The "{name}" block has no account.', name=name), step)
        elif any(source == "secret" for source, _ in _safe_references(account)):
            issues.error(t('The account of the "{name}" block cannot be a secret, as it shows on the page. Type it in or take it from Excel.', name=name), step)
        if not password:
            issues.error(t('The "{name}" block has no password.', name=name), step)
        elif not WHOLE_SECRET.match(password):
            issues.error(t('The password of the "{name}" block must be a saved secret, so it stays out of the flow file.', name=name), step)
    if kind in ("fill", "select") and step.get("value") in (None, ""):
        issues.error(t('The "{name}" block has no value.', name=name), step)
    if kind in READING_STEPS and not (step.get("save_to_var") or step.get("save_to_column")):
        issues.error(t('The "{name}" block has no place to save to.', name=name), step)
    if kind == "wait":
        seconds = step.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
            issues.error(t('The "{name}" block needs a number of seconds above 0.', name=name), step)
    if kind == "if":
        test = step.get("test")
        seconds = step.get("seconds")
        if test not in IF_TESTS:
            issues.error(t('The "{name}" block has no condition.', name=name), step)
        elif test in IF_TESTS_WITH_ELEMENT and not ((step.get("target") or {}).get("primary") or "").strip():
            issues.error(t('The "{name}" block has no element.', name=name), step)
        elif test != "exists" and step.get("value") in (None, ""):
            issues.error(t('The "{name}" block has no value.', name=name), step)
        if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0):
            issues.error(t('The "{name}" block needs a number of seconds above 0.', name=name), step)
        if not step.get("then") and not step.get("else"):
            issues.error(t('The "{name}" block has no blocks in either branch yet.', name=name), step)
        for child in flatten((step.get("then") or []) + (step.get("else") or [])):
            if child.get("do") in ("loop", "if"):
                issues.error(t('A "{inner}" block cannot be placed inside an "{name}" block.', inner=t(STEP_NAMES[child["do"]]), name=name), step)
                break
    if kind == "verify":
        checks_url = step.get("url_contains") or step.get("url_not_contains")
        checks_element = step.get("equals") not in (None, "") or step.get("contains") not in (None, "")
        if not checks_url and not checks_element:
            issues.error(t('The "{name}" block has no condition.', name=name), step)
        if checks_element and not ((step.get("target") or {}).get("primary") or "").strip():
            issues.error(t('The "{name}" block has no element.', name=name), step)


def validate(flow, context=None):
    context = context or CheckContext()
    issues = Issues()
    loops = [step for step in flow.get("steps", []) if step.get("do") == "loop"]
    if len(loops) > 1:
        issues.error(t("A flow can have only one loop."), loops[1])
    if not flow.get("steps"):
        issues.error(t("The flow has no blocks yet."))
    loop = loops[0] if loops else None
    uses_excel = bool(loop and loop.get("source") == "excel")
    if loop:
        if not loop.get("steps"):
            issues.error(t("The loop has no blocks yet."), loop)
        if any(child.get("do") == "loop" for child in loop.get("steps", [])):
            issues.error(t("A loop cannot be placed inside a loop."), loop)
        if loop.get("source") == "count" and not (isinstance(loop.get("count"), int) and loop["count"] >= 1):
            issues.error(t("The number of repeats must be 1 or more."), loop)
        if loop.get("source") not in ("excel", "count"):
            issues.error(t("The loop has no source to repeat over."), loop)
    if uses_excel:
        if not flow.get("excel"):
            issues.error(t("The loop runs over Excel rows, but Excel is not set up."), loop)
        if context.excel_error:
            issues.error(context.excel_error)

    known_vars = set()
    last_url = ""
    for step, in_loop in walk(flow):
        _check_step_fields(step, issues)
        for field in TEMPLATE_FIELDS:
            try:
                tokens = templating.references(step.get(field))
            except templating.TemplateError as error:
                issues.error(str(error), step)
                continue
            for kind, name in tokens:
                if kind == "column":
                    if not (in_loop and uses_excel):
                        issues.error(t("Column {name} only works inside a loop over Excel rows.", name=name), step)
                    elif context.columns is not None and name not in context.columns:
                        issues.error(t("The sheet has no column {name}.", name=name), step)
                elif kind == "row" and not (in_loop and uses_excel):
                    issues.error(t("{{row}} only works inside a loop over Excel rows."), step)
                elif kind == "i" and not in_loop:
                    issues.error(t("{{i}} only works inside a loop."), step)
                elif kind == "var" and name not in known_vars:
                    issues.error(t("Variable {name} is used before it is read.", name=name), step)
                elif kind == "secret":
                    if not context.secret_exists(name):
                        issues.error(t("Secret {name} has not been saved.", name=name), step)
                    if field in ("value", "password") and last_url.lower().startswith("http://") and "127.0.0.1" not in last_url:
                        issues.warn(t("A secret is filled in on an http:// page, which is not encrypted."), step)
        if step.get("do") == "goto":
            last_url = step.get("url") or ""
        if step.get("do") in READING_STEPS:
            if step.get("save_to_var"):
                known_vars.add(step["save_to_var"])
            column = step.get("save_to_column")
            if column:
                if not (in_loop and uses_excel):
                    issues.error(t("Writing to a column only works inside a loop over Excel rows."), step)
                elif column in context.nonempty_columns:
                    issues.warn(t("Column {column} already has data, which will be overwritten in the result file.", column=column), step)
        target = step.get("target") or {}
        if target.get("primary") and not target.get("verified"):
            issues.warn(t("Element {element} has not been checked on a live page.", element=target["primary"]), step)

    excel = flow.get("excel") or {}
    status_column = excel.get("status_column")
    if uses_excel and status_column and status_column in context.nonempty_columns:
        issues.warn(t("Status column {column} already has data, which will be overwritten in the result file.", column=status_column))
    return issues


def hosts_of(flow, urls):
    """Every host the run will reach: fixed links in the flow plus links from Excel."""
    from urllib.parse import urlsplit

    hosts = []
    for url in urls:
        host = urlsplit(url).netloc
        if host and host not in hosts:
            hosts.append(host)
    return hosts
