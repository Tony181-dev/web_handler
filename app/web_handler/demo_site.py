"""A small web app that ships with the tool, to try flows on without any real system.

It is also the site the automated tests run against. Nothing here is persisted:
the items reset when the app restarts.
"""
from html import escape
from http.cookies import SimpleCookie
import secrets
import threading
from urllib.parse import parse_qs

DEMO_USER = "demo"
DEMO_PASSWORD = "matkhau-demo"
LOCKED_ITEM = 3

STYLE = """
<style>
  :root { color-scheme: light; }
  body { font: 15px/1.5 "Segoe UI", system-ui, sans-serif; margin: 0; background: #f3f4f7; color: #1c2230; }
  header { background: #27304a; color: #fff; padding: 12px 24px; font-weight: 600; display: flex; gap: 20px; align-items: center; }
  header a { color: #cdd6f4; text-decoration: none; font-weight: 400; }
  main { max-width: 780px; margin: 24px auto; background: #fff; padding: 24px; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  label { display: block; margin: 12px 0 4px; font-weight: 600; }
  input[type=text], input[type=password], select, textarea { width: 100%; padding: 8px 10px; box-sizing: border-box; border: 1px solid #c6ccd8; border-radius: 6px; font: inherit; }
  input[type=submit], button { padding: 8px 16px; margin-top: 16px; border: 0; border-radius: 6px; background: #3f51b5; color: #fff; font: inherit; cursor: pointer; }
  table { width: 100%; border-collapse: collapse; } td, th { text-align: left; padding: 8px; border-bottom: 1px solid #e6e8ee; }
  .error { background: #fdecea; color: #a12622; padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; }
  .note { background: #eef2ff; padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; }
  #popup { display: none; position: fixed; inset: 20% 30%; background: #fff; border: 1px solid #889; padding: 20px; border-radius: 10px; box-shadow: 0 8px 30px rgba(0,0,0,.25); }
</style>
"""


class DemoSite:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = set()
        self.reset()

    def reset(self):
        with self.lock:
            self.items = {
                number: {"summary": f"Tên cũ {number}", "priority": "Thấp", "done": False}
                for number in range(1, 7)
            }
            self.login_attempts = 0
            self.saves = 0

    # Pages

    def page(self, title, body, user=True):
        nav = (
            "<a href='/demo/items'>Danh sách</a><a href='/demo/playground'>Sân chơi</a>"
            "<a href='/demo/logout'>Đăng xuất</a>" if user else ""
        )
        return (
            f"<!doctype html><html lang='vi'><head><meta charset='utf-8'><title>{escape(title)}</title>{STYLE}</head>"
            f"<body><header>Trang demo của Web Handler {nav}</header><main>{body}</main></body></html>"
        )

    def login_page(self, error=""):
        message = f"<div class='error' id='login-error'>{escape(error)}</div>" if error else ""
        body = (
            f"<h2>Đăng nhập</h2>{message}"
            f"<div class='note'>Tài khoản demo: <b>{DEMO_USER}</b> / <b>{DEMO_PASSWORD}</b></div>"
            "<form method='post' action='/demo/login'>"
            "<label for='user'>Tài khoản</label><input type='text' id='user' name='user'>"
            "<label for='password'>Mật khẩu</label><input type='password' id='password' name='password'>"
            "<input type='submit' class='login_button' title='Login' value='Đăng nhập'>"
            "</form>"
        )
        return self.page("Đăng nhập", body, user=False)

    def items_page(self):
        rows = "".join(
            f"<tr><td>{number}</td><td><a href='/demo/item/{number}/edit' class='item-link'>{escape(item['summary'])}</a></td>"
            f"<td class='priority'>{escape(item['priority'])}</td><td>{'Xong' if item['done'] else ''}</td></tr>"
            for number, item in self.items.items()
        )
        return self.page(
            "Danh sách",
            "<h2>Danh sách mục</h2>"
            f"<table id='items'><tr><th>ID</th><th>Tên</th><th>Ưu tiên</th><th>Trạng thái</th></tr>{rows}</table>",
        )

    def edit_page(self, number, error=""):
        item = self.items[number]
        message = f"<div class='error' id='save-error'>{escape(error)}</div>" if error else ""
        options = "".join(
            f"<option{' selected' if item['priority'] == name else ''}>{name}</option>"
            for name in ("Thấp", "Vừa", "Cao")
        )
        checked = " checked" if item["done"] else ""
        return self.page(
            f"Sửa mục {number}",
            f"<h2>Sửa mục {number}</h2>{message}"
            f"<form method='post' action='/demo/item/{number}/edit'>"
            f"<label for='summary'>Tên</label><input type='text' id='summary' name='summary' value='{escape(item['summary'], quote=True)}'>"
            f"<label for='priority'>Ưu tiên</label><select id='priority' name='priority'>{options}</select>"
            f"<label><input type='checkbox' id='done' name='done'{checked}> Đã xong</label>"
            "<input type='submit' name='SUBMIT' value='Save'>"
            "</form>",
        )

    def view_page(self, number):
        item = self.items[number]
        return self.page(
            f"Mục {number}",
            f"<h2>Mục {number}</h2><p id='view-summary'>{escape(item['summary'])}</p>"
            f"<p id='view-priority'>{escape(item['priority'])}</p>"
            f"<a href='/demo/item/{number}/edit' id='edit-link'>Sửa</a>",
        )

    def playground_page(self):
        return self.page(
            "Sân chơi",
            "<h2>Sân chơi thử phần tử</h2>"
            "<label for='note'>Ghi chú</label><textarea id='note' name='note'></textarea>"
            f"<label for='auto'>Ô có ID tự sinh</label><input type='text' id='ember{secrets.randbelow(90000) + 10000}' name='auto-field'>"
            "<button id='open-popup' type='button' onclick=\"document.getElementById('popup').style.display='block'\">Mở popup</button> "
            "<button class='same' type='button'>Trùng</button> <button class='same' type='button'>Trùng</button> "
            "<button id='delayed' type='button' onclick=\"setTimeout(()=>{const p=document.createElement('p');p.id='late-text';p.textContent='Đã xuất hiện';document.querySelector('main').append(p)},600)\">Hiện chữ sau 0,6 giây</button>"
            "<div id='popup'><label for='popup-input'>Trong popup</label><input type='text' id='popup-input'>"
            "<button id='popup-close' type='button' onclick=\"this.parentElement.style.display='none'\">Đóng</button></div>"
            "<h3>Trong iframe</h3><iframe id='inner' name='inner-frame' src='/demo/frame' style='width:100%;height:150px;border:1px solid #ccd'></iframe>",
        )

    def frame_page(self):
        return (
            f"<!doctype html><html><head><meta charset='utf-8'>{STYLE}</head><body style='background:#fff;padding:10px'>"
            "<label for='frame-input'>Ô trong iframe</label><input type='text' id='frame-input'>"
            "<button id='frame-button' type='button' onclick=\"document.getElementById('frame-result').textContent='Đã bấm'\">Bấm trong iframe</button>"
            "<p id='frame-result'></p></body></html>"
        )

    # Requests. Returns (status, headers, body text).

    def handle(self, method, path, body, cookie_header):
        path = path.split("?", 1)[0]
        cookies = SimpleCookie(cookie_header or "")
        session = cookies["demo_session"].value if "demo_session" in cookies else None
        signed_in = session in self.sessions
        form = {key: values[-1] for key, values in parse_qs(body or "", keep_blank_values=True).items()}

        if path in ("/demo", "/demo/"):
            return self.redirect("/demo/items" if signed_in else "/demo/login")
        if path == "/demo/login":
            if method == "POST":
                self.login_attempts += 1
                if form.get("user") == DEMO_USER and form.get("password") == DEMO_PASSWORD:
                    token = secrets.token_hex(12)
                    self.sessions.add(token)
                    return self.redirect("/demo/items", {"Set-Cookie": f"demo_session={token}; Path=/demo; HttpOnly"})
                return 200, {}, self.login_page("Sai tài khoản hoặc mật khẩu.")
            return 200, {}, self.login_page()
        if path == "/demo/logout":
            self.sessions.discard(session)
            return self.redirect("/demo/login")
        if path == "/demo/expire-sessions":
            self.sessions.clear()
            return 200, {}, "ok"
        if path == "/demo/frame":
            return 200, {}, self.frame_page()
        if not signed_in:
            return self.redirect("/demo/login")
        if path == "/demo/items":
            return 200, {}, self.items_page()
        if path == "/demo/playground":
            return 200, {}, self.playground_page()
        return self.handle_item(method, path, form)

    def handle_item(self, method, path, form):
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[1] == "item" and parts[2].isdigit() and int(parts[2]) in self.items:
            number = int(parts[2])
            if len(parts) == 3:
                return 200, {}, self.view_page(number)
            if len(parts) == 4 and parts[3] == "edit":
                if method == "POST":
                    if number == LOCKED_ITEM:
                        return 200, {}, self.edit_page(number, "Mục này đang bị khóa, không lưu được.")
                    with self.lock:
                        self.saves += 1
                        item = self.items[number]
                        item["summary"] = form.get("summary", item["summary"])
                        item["priority"] = form.get("priority", item["priority"])
                        item["done"] = "done" in form
                    return self.redirect(f"/demo/item/{number}")
                return 200, {}, self.edit_page(number)
        return 404, {}, self.page("Không tìm thấy", "<h2>404: không tìm thấy trang</h2>")

    def redirect(self, location, headers=None):
        return 303, {"Location": location, **(headers or {})}, ""


def make_demo_workbook(path):
    """The Excel file for the demo flow. Row 4 targets a locked item, to show a failed row."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["ID mục", "Ghi chú", "Tên mới", "Kết quả", "Tên đã lưu"])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.append([1, "Bình thường", "Tên mới 1"])
    sheet.append([2, "Bình thường", "Tên mới 2"])
    sheet.append([3, "Mục bị khóa: hàng này sẽ lỗi", "Tên mới 3"])
    sheet.append([4, "Thiếu tên mới: hàng này bị bỏ qua", None])
    sheet.append([5, "Bình thường", "Tên mới 5"])
    for column, width in (("A", 10), ("B", 36), ("C", 18), ("D", 40), ("E", 18)):
        sheet.column_dimensions[column].width = width
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    workbook.close()


def make_demo_flow(base_url):
    target = lambda selector, fallbacks=(): {
        "primary": selector, "fallbacks": list(fallbacks), "frames": [], "verified": True,
    }
    return {
        "version": 1,
        "name": "Sample flow",
        "settings": {
            "step_timeout_seconds": 10, "navigation_timeout_seconds": 30,
            "pause_after_consecutive_failures": 3,
        },
        "excel": {"sheet": "Sheet1", "start_row": 2, "end_row": None, "required": ["A", "C"], "status_column": "D"},
        "steps": [
            {"id": "s1", "do": "goto", "url": f"{base_url}/demo/login"},
            {"id": "s2", "do": "fill", "target": target("#user"), "value": "{{secret:demo_user}}"},
            {"id": "s3", "do": "fill", "target": target("#password"), "value": "{{secret:demo_password}}"},
            {"id": "s4", "do": "click", "target": target("input.login_button"), "wait_navigation": True},
            {"id": "s5", "do": "verify", "url_not_contains": "/login"},
            {
                "id": "s6", "do": "loop", "source": "excel", "count": 1,
                "steps": [
                    {"id": "s7", "do": "goto", "url": f"{base_url}/demo/item/{{{{A}}}}/edit"},
                    {"id": "s8", "do": "fill", "target": target("#summary"), "value": "{{C}}"},
                    {"id": "s9", "do": "click", "target": target("input[name=\"SUBMIT\"]"), "wait_navigation": True},
                    {"id": "s10", "do": "verify", "target": target("#view-summary"), "equals": "{{C}}"},
                    {"id": "s11", "do": "read", "target": target("#view-summary"), "save_to_column": "E"},
                ],
            },
        ],
    }
