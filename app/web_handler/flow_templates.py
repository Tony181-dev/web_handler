"""Ready-made flows for the New flow dialog. They fit any site: blocks that depend on the page stay blank."""
from . import flow as flow_module


def _element(kind, note, **fields):
    target = {"primary": "", "fallbacks": [], "frames": [], "verified": False}
    return {"do": kind, "note": note, "target": target, **fields}


def _excel(*required):
    return {"sheet": "", "start_row": 2, "end_row": None, "required": list(required), "status_column": None}


def _login_excel():
    return {
        "excel": _excel("A", "B"),
        "steps": [
            {"do": "goto", "note": "Link trang đăng nhập", "url": ""},
            _element("fill", "Ô tài khoản", value="{{secret:tai_khoan}}"),
            _element("fill", "Ô mật khẩu", value="{{secret:mat_khau}}"),
            _element("click", "Nút đăng nhập", wait_navigation=True),
            {"do": "verify", "url_not_contains": "/login"},
            {
                "do": "loop", "source": "excel", "count": 1,
                "steps": [
                    {"do": "goto", "url": "{{A}}"},
                    _element("fill", "Ô cần điền", value="{{B}}"),
                    _element("click", "Nút lưu", wait_navigation=True),
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
                _element("wait_for", "Phần tử cần đọc"),
                _element("read", "Phần tử cần đọc", save_to_column="B"),
            ],
        }],
    }


def _repeat_clicks():
    return {
        "excel": None,
        "steps": [
            {"do": "goto", "note": "Link trang cần thao tác", "url": ""},
            {
                "do": "loop", "source": "count", "count": 3,
                "steps": [
                    _element("click", "Nút cần bấm", wait_navigation=False),
                    {"do": "wait", "seconds": 3},
                ],
            },
        ],
    }


TEMPLATES = {
    "login_excel": (
        "Login + Excel rows", _login_excel,
        "Đăng nhập, rồi mỗi hàng Excel mở link ở cột A, điền nội dung cột B và lưu.",
    ),
    "read_excel": (
        "Read into Excel", _read_excel,
        "Mỗi hàng Excel mở link ở cột A, đọc một giá trị trên trang và ghi vào cột B của file kết quả.",
    ),
    "repeat_clicks": (
        "Repeat clicks", _repeat_clicks,
        "Mở một trang, rồi bấm một nút 3 lần, mỗi lần cách nhau 3 giây. Không cần Excel.",
    ),
}


def catalog():
    return [{"id": key, "label": label, "hint": hint} for key, (label, _, hint) in TEMPLATES.items()]


def _assign_ids(steps, numbers):
    for step in steps:
        step["id"] = f"s{next(numbers)}"
        _assign_ids(step.get("steps", ()), numbers)


def build(template_id, name=None):
    if template_id not in TEMPLATES:
        raise ValueError("Không có mẫu quy trình này.")
    label, make, _ = TEMPLATES[template_id]
    flow = {**flow_module.new_flow(name or label), **make()}
    _assign_ids(flow["steps"], iter(range(1, 1000)))
    return flow
