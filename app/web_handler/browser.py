"""The only module that talks to the browser automation library."""
import os
from pathlib import Path
import sys
import time

from . import desktop, ocr, paths, settings, setup_checks
from .browser_errors import BrowserClosed, ElementError
from .i18n import t

CLOSED_HINTS = ("has been closed", "Target closed", "Browser closed", "Connection closed")
TRIAL_TIMEOUT_MS = 3000
LOGIN_FORM_WAIT_SECONDS = 5
# The nearest visible text box before the password field, in the same form if it has one.
FIND_ACCOUNT_FIELD = """password => {
    const boxes = [...(password.form || document).querySelectorAll(
        'input:not([type]), input[type=text], input[type=email], input[type=tel]'
    )].filter(box => box.offsetParent !== null && (box.compareDocumentPosition(password) & Node.DOCUMENT_POSITION_FOLLOWING));
    return boxes[boxes.length - 1] || null;
}"""
FIND_SUBMIT_BUTTON = """password => [...(password.form || document).querySelectorAll(
    'button[type=submit], input[type=submit], button:not([type])'
)].find(button => button.offsetParent !== null) || null"""
FILLABLE = (
    "textarea, [contenteditable], input:not([type=submit], [type=button], [type=checkbox], "
    "[type=radio], [type=file], [type=image], [type=reset])"
)


def launch_options(mode=settings.BROWSER_VISIBLE):
    """Prefer the installed Chrome, and use the bundled Chromium only without one."""
    options = {"headless": mode == settings.BROWSER_HEADLESS or os.environ.get("WEB_HANDLER_HEADLESS") == "1"}
    if not options["headless"]:
        options["args"] = ["--start-minimized" if mode == settings.BROWSER_OFFSCREEN else "--start-maximized"]
    chrome = setup_checks.find_chrome()
    if chrome:
        # On macOS the installed Chrome may also be in the person's own Applications folder,
        # where the chrome channel does not look.
        if sys.platform == "win32":
            options["channel"] = "chrome"
        else:
            options["executable_path"] = str(chrome)
        return options
    chromium = setup_checks.find_bundled_chromium()
    if chromium is None:
        raise RuntimeError(t("Neither Chrome nor Chromium was found. Open the Setup screen."))
    options["executable_path"] = str(chromium)
    return options


class ChromeDriver:
    def __init__(self, mode=settings.BROWSER_VISIBLE):
        self.mode = mode
        self.has_window = False
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
            options = launch_options(self.mode)
            self.has_window = not options["headless"]
            self.context = self._playwright.chromium.launch_persistent_context(
                str(profile), no_viewport=True, **options
            )
        except Exception as error:
            self._stop_playwright()
            raise RuntimeError(
                t(
                    "Could not open the browser. Another window of the app may be using it. Details: {error}",
                    error=str(error).splitlines()[0],
                )
            ) from error
        if on_new_context:
            on_new_context(self.context)
        self.context.on("page", self._on_page)
        pages = self.context.pages
        self.page = pages[0] if pages else self.context.new_page()
        if self.mode == settings.BROWSER_OFFSCREEN:
            # The start option is not always honoured, for example when Chrome restores a window.
            self._set_window_state("minimized")
        else:
            self.show()

    def _set_window_state(self, state):
        if not self.has_window:
            return

        def action():
            session = self.context.new_cdp_session(self.page)
            try:
                window = session.send("Browser.getWindowForTarget")["windowId"]
                session.send("Browser.setWindowBounds", {"windowId": window, "bounds": {"windowState": state}})
            finally:
                session.detach()

        try:
            self._guard(action)
        except BrowserClosed:
            raise
        except Exception:
            # Only about how the window looks, which must never stop a run.
            pass

    def show(self):
        """Bring the window up over the whole screen, also from the taskbar or the Dock."""
        if not self.has_window:
            return
        self._set_window_state("maximized")
        self._guard(self.page.bring_to_front)
        try:
            desktop.raise_browser_windows()
        except Exception:
            # Only about where the window sits, which must never stop a run.
            pass

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
            raise ElementError(t("Element {element} matches {count} places on the page; the tool does not guess.", element=primary, count=count))
        raise ElementError(t("Element {element} was not found after {seconds} s.", element=primary, seconds=timeout))

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
                raise ElementError(t("Field {element} did not keep the value just filled in.", element=target["primary"]))

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
                        t(
                            "Clicked {element}, but the page did not change after {seconds} s. "
                            "If this button does not open another page, untick Wait for the page to change.",
                            element=target["primary"], seconds=navigation_timeout,
                        )
                    ) from error
                raise ElementError(t("Could not click {element}: it is hidden, covered or disabled.", element=target["primary"])) from error

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

    def ocr(self, target, timeout):
        """Read the text in an element's picture. The picture stays in memory, so nothing is left to delete."""

        def action():
            image = self._resolve(target, timeout).screenshot(timeout=timeout * 1000)
            text = ocr.read_text(image)
            if not text:
                raise ElementError(t("No text could be read from {element}.", element=target["primary"]))
            return text

        return self._guard(action)

    def wait_for(self, target, timeout):
        self._guard(lambda: self._resolve(target, timeout).wait_for(state="visible", timeout=timeout * 1000))

    def appears(self, target, timeout):
        """Whether the element shows within the time. Not finding it is an answer, not an error."""
        selectors = [target["primary"], *(target.get("fallbacks") or [])]
        deadline = time.monotonic() + timeout
        while True:
            if any(self._is_visible(target, selector) for selector in selectors):
                return True
            if time.monotonic() >= deadline:
                return False
            self._guard(lambda: self.page.wait_for_timeout(150))

    def _is_visible(self, target, selector):
        try:
            return self._locator(target, selector).first.is_visible()
        except Exception as error:
            if self._closed(error):
                raise BrowserClosed() from error
            # A page in the middle of loading cannot answer yet.
            return False

    def _visible_count(self, locator):
        try:
            return locator.count()
        except Exception as error:
            # A page in the middle of loading cannot answer yet.
            if self._closed(error):
                raise
            return 1

    def login(self, account, password, selectors, timeout, navigation_timeout):
        """Fill in a login form and send it. Returns False if the page has no such form."""

        def action():
            password_field = self.page.locator(selectors["password"] or "input[type=password]:visible")
            deadline = time.monotonic() + LOGIN_FORM_WAIT_SECONDS
            count = password_field.count()
            while count == 0 and time.monotonic() < deadline:
                self.page.wait_for_timeout(100)
                count = password_field.count()
            if count == 0:
                return False
            if count > 1:
                raise ElementError(t("The page has {count} password fields, so the tool does not guess. Fill in Specify the login form yourself in the block.", count=count))
            if selectors["account"]:
                account_field = self.page.locator(selectors["account"])
            else:
                account_field = password_field.evaluate_handle(FIND_ACCOUNT_FIELD).as_element()
                if account_field is None:
                    raise ElementError(t("No account field was found before the password field. Fill in Specify the login form yourself in the block."))
            account_field.fill(account, timeout=timeout * 1000)
            password_field.fill(password, timeout=timeout * 1000)
            if selectors["submit"]:
                self.page.locator(selectors["submit"]).click(timeout=timeout * 1000)
            else:
                button = password_field.evaluate_handle(FIND_SUBMIT_BUTTON).as_element()
                if button is not None:
                    button.click(timeout=timeout * 1000)
                else:
                    password_field.press("Enter", timeout=timeout * 1000)
            # The form going away is what shows it worked, on a new page or the same one.
            deadline = time.monotonic() + timeout
            while self._visible_count(password_field) > 0:
                if time.monotonic() >= deadline:
                    raise ElementError(t("The login form is still there after {seconds} s. Check the account and password.", seconds=timeout))
                self.page.wait_for_timeout(200)
            self.page.wait_for_load_state("load", timeout=navigation_timeout * 1000)
            return True

        return self._guard(action)

    def url(self):
        return self._guard(lambda: self.page.url)

    def screenshot(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._guard(lambda: self.page.screenshot(path=str(path), timeout=10000))

    def idle(self, milliseconds):
        self._guard(lambda: self.page.wait_for_timeout(milliseconds))
        # Waiting alone does not notice a window someone closed, and the run would wait on forever.
        if not self.is_open():
            raise BrowserClosed()

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
                        return t('This element is not an input, so the "Fill" block cannot fill it.')
                    if not locator.is_editable(timeout=TRIAL_TIMEOUT_MS):
                        return t('This field is locked, so the "Fill" block cannot fill it.')
                elif kind == "select" and not locator.evaluate("el => el.tagName === 'SELECT'"):
                    return t('This element is not a dropdown, so the "Select" block cannot use it.')
                if kind == "click":
                    locator.click(trial=True, timeout=TRIAL_TIMEOUT_MS)
                elif kind == "check":
                    locator.set_checked(True, trial=True, timeout=TRIAL_TIMEOUT_MS)
                elif kind in ("fill", "select", "wait_for"):
                    locator.wait_for(state="visible", timeout=TRIAL_TIMEOUT_MS)
            except Exception as error:
                if self._closed(error):
                    raise
                return t("The element is hidden, covered or disabled, so this block cannot act on it yet.")
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
