"""Checks for the Setup screen that need the third-party libraries."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import threading

PAGE = (
    "<!doctype html><meta charset='utf-8'><title>Web Handler Test</title>"
    "<input id='field'><button id='go'>Bấm</button>"
)


class LocalPage:
    def __enter__(self):
        class Handler(BaseHTTPRequestHandler):
            wbufsize = 64 * 1024

            def do_GET(self):
                body = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()


def _launch(playwright):
    from .browser import launch_options

    return playwright.chromium.launch(**launch_options())


def test_browser():
    from playwright.sync_api import sync_playwright

    with LocalPage() as site, sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = browser.new_page()
            page.goto(site.url)
            page.fill("#field", "Xin chào")
            if page.input_value("#field") != "Xin chào":
                raise RuntimeError("Giá trị đã điền không khớp.")
            return f"Trình duyệt {browser.version}: mở trang, điền và đọc lại được."
        finally:
            browser.close()


def test_recording():
    from playwright.sync_api import sync_playwright

    received = []
    with LocalPage() as site, sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            context = browser.new_context()
            context.expose_binding("__webHandlerTest", lambda source, value: received.append(value))
            context.add_init_script(
                "document.addEventListener('click', e => window.__webHandlerTest(e.target.id), true);"
            )
            page = context.new_page()
            page.goto(site.url)
            page.click("#go")
            for _ in range(50):
                if received:
                    break
                page.wait_for_timeout(100)
        finally:
            browser.close()
    if received != ["go"]:
        raise RuntimeError(f"Không nhận được click: {received!r}")
    return "Chèn script và nhận được click."


def test_credentials():
    import keyring

    service = "WebHandler/selftest"
    value = secrets.token_urlsafe(16)
    try:
        keyring.set_password(service, "selftest", value)
        if keyring.get_password(service, "selftest") != value:
            raise RuntimeError("Giá trị đọc lại không khớp.")
    finally:
        try:
            keyring.delete_password(service, "selftest")
        except keyring.errors.PasswordDeleteError:
            pass
    return "Ghi, đọc và xóa khóa thử được."


TESTS = {"browser": test_browser, "recording": test_recording, "credentials": test_credentials}


def run(name):
    try:
        return {"ok": True, "detail": TESTS[name]()}
    except Exception as error:
        text = str(error).strip()
        return {"ok": False, "detail": text.splitlines()[0] if text else type(error).__name__}
