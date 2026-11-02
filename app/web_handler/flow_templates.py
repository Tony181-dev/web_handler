"""Ready-made flows for the New flow dialog. They fit any site: blocks that depend on the page stay blank."""
from . import flow as flow_module
from .i18n import t


def _element(kind, note, **fields):
    target = {"primary": "", "fallbacks": [], "frames": [], "verified": False}
    return {"do": kind, "note": note, "target": target, **fields}


def _excel(*required):
    return {"sheet": "", "start_row": 2, "end_row": None, "required": list(required), "status_column": None}


def _login_excel():
    return {
        "excel": _excel("A", "B"),
        "steps": [
            {"do": "goto", "note": t("Login page link"), "url": ""},
            {"do": "login", "account": "", "password": "{{secret:your_password}}"},
            {
                "do": "loop", "source": "excel", "count": 1,
                "steps": [
                    {"do": "goto", "url": "{{A}}"},
                    _element("fill", t("Field to fill"), value="{{B}}"),
                    _element("click", t("Save button"), wait_navigation=True),
                ],
            },
        ],
    }


def _read_excel():
    return {
        "excel": _excel("A"),
        "steps": [{
            "do": "loop", "source": "excel", "count": 1,
            "steps": [
                {"do": "goto", "url": "{{A}}"},
                _element("wait_for", t("Element to read")),
                _element("read", t("Element to read"), save_to_column="B"),
            ],
        }],
    }


def _repeat_clicks():
    return {
        "excel": None,
        "steps": [
            {"do": "goto", "note": t("Link of the page to work on"), "url": ""},
            {
                "do": "loop", "source": "count", "count": 3,
                "steps": [
                    _element("click", t("Button to click"), wait_navigation=False),
                    {"do": "wait", "seconds": 3},
                ],
            },
        ],
    }


TEMPLATES = {
    "login_excel": (
        "Login + Excel rows", _login_excel,
        "Logs in, then for each Excel row opens the link in column A, fills in column B and saves.",
    ),
    "read_excel": (
        "Read into Excel", _read_excel,
        "For each Excel row, opens the link in column A, reads a value on the page and writes it to column B of the result file.",
    ),
    "repeat_clicks": (
        "Repeat clicks", _repeat_clicks,
        "Opens a page, then clicks a button 3 times, 3 seconds apart. No Excel needed.",
    ),
}


def catalog():
    return [{"id": key, "label": t(label), "hint": t(hint)} for key, (label, _, hint) in TEMPLATES.items()]


def _assign_ids(steps, numbers):
    for step in steps:
        step["id"] = f"s{next(numbers)}"
        _assign_ids(step.get("steps", ()), numbers)


def build(template_id, name=None):
    if template_id not in TEMPLATES:
        raise ValueError(t("There is no such flow template."))
    label, make, _ = TEMPLATES[template_id]
    flow = {**flow_module.new_flow(name or t(label)), **make()}
    _assign_ids(flow["steps"], iter(range(1, 1000)))
    return flow
