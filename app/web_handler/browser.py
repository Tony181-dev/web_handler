"""The only module that talks to the browser automation library."""
import os
from pathlib import Path
import time

from . import paths, setup_checks
from .browser_errors import BrowserClosed, ElementError

CLOSED_HINTS = ("has been closed", "Target closed", "Browser closed", "Connection closed")
TRIAL_TIMEOUT_MS = 3000
FILLABLE = (
    "textarea, [contenteditable], input:not([type=submit], [type=button], [type=checkbox], "
    "[type=radio], [type=file], [type=image], [type=reset])"
)


def launch_options():
    """Prefer the installed Chrome, and use the bundled Chromium only without one."""
    options = {"headless": os.environ.get("WEB_HANDLER_HEADLESS") == "1"}
    if setup_checks.find_chrome():
        options["channel"] = "chrome"
        return options
    chromium = setup_checks.find_bundled_chromium()
    if chromium is None:
        raise RuntimeError("Không tìm thấy Chrome hay Chromium. Hãy mở màn hình Setup.")
    options["executable_path"] = str(chromium)
    return options


class ChromeDriver:
    def __init__(self):
        self._playwright = None
        self.context = None
        self.page = None
        self._notices = []

    def open(self, on_new_context=None):
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        profile = paths.profile_dir()
        profile.mkdir(parents=True, exist_ok=True)
        try:
            self.context = self._playwright.chromium.launch_persistent_context(
                str(profile), no_viewport=True, **launch_options()
            )
        except Exception as error:
            self._stop_playwright()
            raise RuntimeError(
                "Không mở được trình duyệt. Có thể một cửa sổ khác của app đang dùng nó. "
                f"Chi tiết: {str(error).splitlines()[0]}"
            ) from error
        if on_new_context:
            on_new_context(self.context)
        self.context.on("page", self._on_page)
        pages = self.context.pages
        self.page = pages[0] if pages else self.context.new_page()

    def _on_page(self, page):
        # A click that opens a new window continues in that window, as a person would.
        self.page = page
        page.on("close", lambda closed: self._on_page_closed(closed))

    def _on_page_closed(self, closed):
        if self.page is closed:
            remaining = [page for page in self.context.pages if not page.is_closed()]
            if remaining:
                self.page = remaining[-1]

    def close(self):
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        self._stop_playwright()

    def _stop_playwright(self):
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None

    def is_open(self):
        try:
            return any(not page.is_closed() for page in self.context.pages)
        except Exception:
            return False

    # Finding elements

    def _locator(self, target, selector):
        root = self.page
        for frame in target.get("frames") or []:
            root = root.frame_locator(frame)
        return root.locator(selector)

    def count(self, target, selector=None):
        return self._guard(lambda: self._locator(target, selector or target["primary"]).count())

    def _resolve(self, target, timeout):
        primary = target["primary"]
        deadline = time.monotonic() + timeout
        count = 0
        while True:
            count = self.count(target)
            if count == 1:
                return self._locator(target, primary)
            if count > 1 or time.monotonic() >= deadline:
                break
            self.page.wait_for_timeout(100)
        for fallback in target.get("fallbacks") or []:
            if self.count(target, fallback) == 1:
                self._notices.append(f"{primary} → {fallback}")
                return self._locator(target, fallback)
        if count > 1:
            raise ElementError(f"Phần tử {primary} khớp {count} chỗ trên trang; tool không đoán.")
        raise ElementError(f"Không tìm thấy phần tử {primary} sau {timeout} giây.")

    def take_fallback_notices(self):
        notices, self._notices = self._notices, []
        return notices

    def _guard(self, action):
        try:
            return action()
        except (ElementError, BrowserClosed):
            raise
        except Exception as error:
            if self._closed(error):
                raise BrowserClosed() from error
            raise

    def _closed(self, error):
        text = str(error)
        return type(error).__name__ == "TargetClosedError" or any(hint in text for hint in CLOSED_HINTS)

    # Actions

    def goto(self, url, timeout):
        def action():
            response = self.page.goto(url, timeout=timeout * 1000, wait_until="load")
            return response.status if response else None

        return self._guard(action)

    def fill(self, target, value, timeout):
        def action():
            locator = self._resolve(target, timeout)
            locator.fill(value, timeout=timeout * 1000)
            if self._read(locator, timeout) != value:
                raise ElementError(f"Ô {target['primary']} không giữ giá trị vừa điền.")

        self._guard(action)

    def click(self, target, timeout, navigation_timeout=None):
        def action():
            locator = self._resolve(target, timeout)
            clicked = False
            try:
                if navigation_timeout:
                    with self.page.expect_navigation(timeout=navigation_timeout * 1000, wait_until="load"):
                        locator.click(timeout=timeout * 1000)
                        clicked = True
                else:
                    locator.click(timeout=timeout * 1000)
            except Exception as error:
                if type(error).__name__ != "TimeoutError":
                    raise
                if clicked:
                    raise ElementError(
                        f"Đã bấm {target['primary']} nhưng trang không chuyển sau {navigation_timeout} giây. "
                        "Nếu nút này không mở trang khác, hãy bỏ tick Chờ chuyển trang."
                    ) from error
                raise ElementError(f"Không bấm được {target['primary']}: phần tử đang bị ẩn, bị che hoặc bị khóa.") from error

        self._guard(action)

    def select(self, target, value, timeout):
        self._guard(lambda: self._resolve(target, timeout).select_option(label=value, timeout=timeout * 1000))

    def check(self, target, checked, timeout):
        self._guard(lambda: self._resolve(target, timeout).set_checked(checked, timeout=timeout * 1000))

    def _read(self, locator, timeout):
        return locator.evaluate(
            "el => ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)"
            " ? el.value : (el.innerText || el.textContent || '').trim()",
            timeout=timeout * 1000,
        )

    def read(self, target, timeout):
        return self._guard(lambda: self._read(self._resolve(target, timeout), timeout))

    def wait_for(self, target, timeout):
        self._guard(lambda: self._resolve(target, timeout).wait_for(state="visible", timeout=timeout * 1000))

    def url(self):
        return self._guard(lambda: self.page.url)

    def screenshot(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._guard(lambda: self.page.screenshot(path=str(path), timeout=10000))

    def idle(self, milliseconds):
        self._guard(lambda: self.page.wait_for_timeout(milliseconds))

    # Inspecting pages

    def highlight(self, target):
        def action():
            locator = self._locator(target, target["primary"]).first
            try:
                locator.scroll_into_view_if_needed(timeout=3000)
            except Exception as error:
                # A hidden element cannot be scrolled to, and is reported by action_problem.
                if self._closed(error):
                    raise
            locator.evaluate(
                "el => { el.style.outline = '3px solid #e5484d'; el.style.outlineOffset = '2px'; }"
            )

        self._guard(action)

    def action_problem(self, target, kind):
        """Why a block's action would fail on this element, found without carrying it out."""

        def action():
            locator = self._locator(target, target["primary"])
            try:
                if kind == "fill":
                    if not locator.evaluate(f"el => el.matches('{FILLABLE}')"):
                        return "Phần tử này không phải ô nhập nên khối Fill không điền được."
                    if not locator.is_editable(timeout=TRIAL_TIMEOUT_MS):
                        return "Ô này đang bị khóa nên khối Fill không điền được."
                elif kind == "select" and not locator.evaluate("el => el.tagName === 'SELECT'"):
                    return "Phần tử này không phải danh sách chọn nên khối Select không dùng được."
                if kind == "click":
                    locator.click(trial=True, timeout=TRIAL_TIMEOUT_MS)
                elif kind == "check":
                    locator.set_checked(True, trial=True, timeout=TRIAL_TIMEOUT_MS)
                elif kind in ("fill", "select", "wait_for"):
                    locator.wait_for(state="visible", timeout=TRIAL_TIMEOUT_MS)
            except Exception as error:
                if self._closed(error):
                    raise
                return "Phần tử đang bị ẩn, bị che hoặc bị khóa nên khối này chưa thực hiện được."
            return None

        return self._guard(action)

    def frame_chain(self, frame):
        """Selectors leading from the main page down to this frame."""
        chain = []
        current = frame
        while current.parent_frame is not None:
            chain.insert(0, frame_selector(current))
            current = current.parent_frame
        return chain

    def scan(self):
        """HTML of the open page and each of its frames."""

        def action():
            documents = []
            for frame in self.page.frames:
                try:
                    documents.append({"frames": self.frame_chain(frame), "html": frame.content(), "url": frame.url})
                except Exception as error:
                    if self._closed(error):
                        raise
            return documents

        return self._guard(action)


def frame_selector(frame):
    element = frame.frame_element()
    for attribute in ("id", "name"):
        value = element.get_attribute(attribute)
        if value:
            return f'iframe[{attribute}="{value}"]'
    source = element.get_attribute("src")
    if source and not source.startswith(("about:", "data:", "javascript:")):
        return f'iframe[src="{source}"]'
    return "iframe"
