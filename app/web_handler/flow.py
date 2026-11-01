"""The flow file: creating, saving and checking it before a run."""
import hashlib
import json
from pathlib import Path
import uuid

from . import paths, templating

FLOW_VERSION = 1
DEFAULT_SETTINGS = {
    "step_timeout_seconds": 15,
    "navigation_timeout_seconds": 60,
    "pause_after_consecutive_failures": 3,
}
STEP_NAMES = {
    "goto": "Open link",
    "fill": "Fill",
    "click": "Click",
    "select": "Select",
    "check": "Check",
    "read": "Read value",
    "wait_for": "Wait for element",
    "wait": "Wait",
    "verify": "Verify",
    "loop": "Loop",
    "manual_pause": "Manual step",
}
TARGET_STEPS = {"fill", "click", "select", "check", "read", "wait_for"}
TEMPLATE_FIELDS = ("url", "value", "equals", "contains", "url_contains", "url_not_contains")


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
        raise ValueError("Mã quy trình không hợp lệ.")
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


def walk(flow):
    """Yield (step, in_loop) in running order. Loop steps themselves are included."""
    for step in flow.get("steps", []):
        yield step, False
        if step.get("do") == "loop":
            for child in step.get("steps", []):
                yield child, True


def stop_column(flow):
    """The column that decides where the rows end: the first one the loop's blocks use."""
    loop = find_loop(flow)
    if not loop:
        return None
    for step in loop.get("steps", []):
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
    name = STEP_NAMES.get(step.get("do"), step.get("do"))
    if step.get("do") == "wait":
        return f"{name} {step.get('seconds')} giây"
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
        issues.error(f"Không biết loại bước {kind!r}.", step)
        return
    name = STEP_NAMES[kind]
    if kind == "goto" and not (step.get("url") or "").strip():
        issues.error(f"Khối {name} chưa có link.", step)
    if kind in TARGET_STEPS and not ((step.get("target") or {}).get("primary") or "").strip():
        issues.error(f"Khối {name} chưa có phần tử.", step)
    if kind in ("fill", "select") and step.get("value") in (None, ""):
        issues.error(f"Khối {name} chưa có giá trị.", step)
    if kind == "read" and not (step.get("save_to_var") or step.get("save_to_column")):
        issues.error("Khối Read value chưa chọn nơi lưu.", step)
    if kind == "wait":
        seconds = step.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
            issues.error("Khối Wait cần số giây lớn hơn 0.", step)
    if kind == "verify":
        checks_url = step.get("url_contains") or step.get("url_not_contains")
        checks_element = step.get("equals") not in (None, "") or step.get("contains") not in (None, "")
        if not checks_url and not checks_element:
            issues.error("Khối Verify chưa có điều kiện.", step)
        if checks_element and not ((step.get("target") or {}).get("primary") or "").strip():
            issues.error("Khối Verify chưa có phần tử.", step)


def validate(flow, context=None):
    context = context or CheckContext()
    issues = Issues()
    loops = [step for step in flow.get("steps", []) if step.get("do") == "loop"]
    if len(loops) > 1:
        issues.error("Mỗi quy trình chỉ có một vùng lặp.", loops[1])
    if not flow.get("steps"):
        issues.error("Quy trình chưa có bước nào.")
    loop = loops[0] if loops else None
    uses_excel = bool(loop and loop.get("source") == "excel")
    if loop:
        if not loop.get("steps"):
            issues.error("Vùng lặp chưa có bước nào.", loop)
        if any(child.get("do") == "loop" for child in loop.get("steps", [])):
            issues.error("Không được đặt vùng lặp trong vùng lặp.", loop)
        if loop.get("source") == "count" and not (isinstance(loop.get("count"), int) and loop["count"] >= 1):
            issues.error("Số lần lặp phải từ 1 trở lên.", loop)
        if loop.get("source") not in ("excel", "count"):
            issues.error("Vùng lặp chưa chọn nguồn lặp.", loop)
    if uses_excel:
        if not flow.get("excel"):
            issues.error("Vùng lặp theo Excel nhưng chưa cấu hình Excel.", loop)
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
                        issues.error(f"Cột {name} chỉ dùng được trong vùng lặp theo Excel.", step)
                    elif context.columns is not None and name not in context.columns:
                        issues.error(f"Sheet không có cột {name}.", step)
                elif kind == "row" and not (in_loop and uses_excel):
                    issues.error("{{row}} chỉ dùng được trong vùng lặp theo Excel.", step)
                elif kind == "i" and not in_loop:
                    issues.error("{{i}} chỉ dùng được trong vùng lặp.", step)
                elif kind == "var" and name not in known_vars:
                    issues.error(f"Biến {name} được dùng trước khi đọc.", step)
                elif kind == "secret":
                    if not context.secret_exists(name):
                        issues.error(f"Bí mật {name} chưa được lưu.", step)
                    if field == "value" and last_url.lower().startswith("http://") and "127.0.0.1" not in last_url:
                        issues.warn("Bí mật được điền trên trang http://, không được mã hóa.", step)
        if step.get("do") == "goto":
            last_url = step.get("url") or ""
        if step.get("do") == "read":
            if step.get("save_to_var"):
                known_vars.add(step["save_to_var"])
            column = step.get("save_to_column")
            if column:
                if not (in_loop and uses_excel):
                    issues.error("Chỉ ghi được vào cột khi chạy trong vùng lặp theo Excel.", step)
                elif column in context.nonempty_columns:
                    issues.warn(f"Cột {column} đã có dữ liệu và sẽ bị ghi đè trong file kết quả.", step)
        target = step.get("target") or {}
        if target.get("primary") and not target.get("verified"):
            issues.warn(f"Phần tử {target['primary']} chưa kiểm chứng trên trang thật.", step)

    excel = flow.get("excel") or {}
    status_column = excel.get("status_column")
    if uses_excel and status_column and status_column in context.nonempty_columns:
        issues.warn(f"Cột trạng thái {status_column} đã có dữ liệu và sẽ bị ghi đè trong file kết quả.")
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
