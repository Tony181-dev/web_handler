"""Turn what a person does in the browser into flow steps."""
import time

from . import html_reader
from .i18n import t

BINDING = "__webHandlerRecord"
NAVIGATION_AFTER_ACTION_SECONDS = 3.0
REDIRECT_SECONDS = 1.5
MAX_DOCUMENT_CHARS = 5_000_000

# Runs inside every frame. The value of a password field never leaves the page:
# not as a value, and not as an attribute in the HTML that is sent for matching.
RECORDER_SCRIPT = r"""
(() => {
  if (window.__webHandlerRecorderReady) return;
  window.__webHandlerRecorderReady = true;
  const BINDING = '__webHandlerRecord';
  const MAX_DOCUMENT = 5000000;

  const isPassword = el => el.tagName === 'INPUT' && (el.type || '').toLowerCase() === 'password';
  const clearPasswords = root => {
    root.querySelectorAll('input[type=password i]').forEach(el => el.removeAttribute('value'));
  };

  const documentHtml = () => {
    try {
      const copy = document.documentElement.cloneNode(true);
      clearPasswords(copy);
      copy.querySelectorAll('script, style').forEach(el => el.remove());
      const html = copy.outerHTML;
      return html.length > MAX_DOCUMENT ? '' : html;
    } catch (error) {
      return '';
    }
  };

  const elementHtml = el => {
    const copy = el.cloneNode(false);
    if (isPassword(copy)) copy.removeAttribute('value');
    if (['INPUT', 'TEXTAREA'].includes(el.tagName)) copy.removeAttribute('value');
    if (el.tagName === 'INPUT' && ['submit', 'button', 'reset'].includes((el.type || '').toLowerCase())) {
      copy.setAttribute('value', el.value);
    }
    if (!['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) {
      copy.textContent = (el.innerText || el.textContent || '').trim().slice(0, 200);
    }
    return copy.outerHTML;
  };

  const structuralPath = el => {
    const parts = [];
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const name = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (!parent) { parts.unshift(name); break; }
      const same = Array.from(parent.children).filter(child => child.tagName === node.tagName);
      parts.unshift(same.length > 1 ? `${name}:nth-of-type(${same.indexOf(node) + 1})` : name);
    }
    return parts.join(' > ');
  };

  const send = (kind, el, extra) => {
    const send = window[BINDING];
    if (!send) return;
    send({
      kind,
      html: elementHtml(el),
      path: structuralPath(el),
      document: documentHtml(),
      password: isPassword(el),
      ...extra,
    });
  };

  const CLICKABLE = 'button, a, input, select, textarea, option, label, summary, [role], [onclick]';
  const NOT_CLICKS = new Set(['text', 'password', 'email', 'number', 'search', 'tel', 'url', 'date',
    'datetime-local', 'month', 'time', 'week', 'checkbox', 'radio', 'file', 'range', 'color', '']);

  document.addEventListener('click', event => {
    const origin = event.target;
    if (!origin || origin.nodeType !== 1) return;
    const el = origin.closest(CLICKABLE) || origin;
    const tag = el.tagName;
    if (tag === 'HTML' || tag === 'BODY') return;
    if (['TEXTAREA', 'SELECT', 'OPTION'].includes(tag)) return;
    if (tag === 'INPUT' && NOT_CLICKS.has((el.type || '').toLowerCase())) return;
    if (el.isContentEditable) return;
    if (tag === 'LABEL' && el.control) return;
    send('click', el, {});
  }, true);

  document.addEventListener('change', event => {
    const el = event.target;
    if (!el || el.nodeType !== 1) return;
    if (el.tagName === 'SELECT') {
      const chosen = el.selectedOptions && el.selectedOptions[0];
      send('select', el, { value: chosen ? chosen.text.trim() : '' });
    } else if (el.tagName === 'INPUT' && ['checkbox', 'radio'].includes((el.type || '').toLowerCase())) {
      send('check', el, { checked: el.checked });
    } else if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
      if ((el.type || '').toLowerCase() === 'file') { send('file', el, {}); return; }
      send('fill', el, isPassword(el) ? {} : { value: el.value });
    }
  }, true);

  document.addEventListener('submit', event => {
    if (!event.submitter) send('submit', event.target, {});
  }, true);
})();
"""


def _make_frames(frame):
    """Selectors for the frames around an event, from data that needs no browser call."""
    chain = []
    current = frame
    while current is not None and current.parent_frame is not None:
        if current.name:
            chain.insert(0, f'iframe[name="{current.name}"]')
        else:
            path = current.url.split("://", 1)[-1].split("/", 1)
            source = "/" + path[1] if len(path) > 1 else ""
            chain.insert(0, f'iframe[src*="{source}"]' if source else "iframe")
        current = current.parent_frame
    return chain


def make_target(event, frames):
    info = html_reader.describe_recorded(event["html"], event.get("path"), event.get("document"))
    if info is None:
        return None, None
    target = {
        "primary": info["selector"],
        "fallbacks": info["fallbacks"],
        "frames": frames,
        "verified": info["match_count"] == 1,
    }
    return target, info


class Recorder:
    """Collects raw events from the page and builds the step list."""

    def __init__(self, emit, clock=time.monotonic):
        self.emit = emit
        self.clock = clock
        self.steps = []
        self.warnings = []
        self.last_action = None
        self.last_click = None
        self.last_navigation = None
        self.counter = 0

    def attach(self, context):
        context.expose_binding(BINDING, self._on_binding)
        context.add_init_script(RECORDER_SCRIPT)
        context.on("page", self.attach_page)
        for page in context.pages:
            self.attach_page(page)

    def attach_page(self, page):
        page.on("framenavigated", lambda frame: self._on_navigation(page, frame))

    def _on_binding(self, source, event):
        self.record_event(event, _make_frames(source["frame"]))

    def _on_navigation(self, page, frame):
        if frame.parent_frame is None:
            self.record_navigation(frame.url)

    def _add(self, step, label, warnings=()):
        self.counter += 1
        step["id"] = f"r{self.counter}"
        entry = {"step": step, "label": label, "warnings": list(warnings)}
        self.steps.append(entry)
        self.emit({"type": "recorded", "entry": entry})
        return entry

    def record_navigation(self, url):
        now = self.clock()
        if url.startswith(("about:", "chrome:", "data:")):
            return
        recent_action = self.last_action is not None and now - self.last_action < NAVIGATION_AFTER_ACTION_SECONDS
        recent_navigation = self.last_navigation is not None and now - self.last_navigation < REDIRECT_SECONDS
        self.last_navigation = now
        if recent_action:
            # One action explains one navigation; a later one is the person's own.
            self.last_action = None
            if self.last_click is not None:
                self.last_click["step"]["wait_navigation"] = True
                self.emit({"type": "recorded_update", "entry": self.last_click})
            return
        if recent_navigation:
            return  # A redirect, not something the person asked for.
        self._add({"do": "goto", "url": url}, t("Open {target}", target=url))

    def record_event(self, event, frames):
        kind = event.get("kind")
        self.last_action = self.clock()
        if kind == "submit":
            message = t("The form was sent with the Enter key, without a button. Add a Click block if needed.")
            self.warnings.append(message)
            self.emit({"type": "recorder_warning", "text": message})
            return
        if kind == "file":
            message = t("Choosing a file cannot be recorded. Do this part with a Manual step block.")
            self.warnings.append(message)
            self.emit({"type": "recorder_warning", "text": message})
            return
        target, info = make_target(event, frames)
        if target is None or not target["primary"]:
            return
        label = info["label"]
        warnings = info["warnings"]
        self.last_click = None
        if kind == "click":
            step = {"do": "click", "target": target, "wait_navigation": False}
            self.last_click = self._add(step, t("Click {target}", target=label), warnings)
        elif kind == "fill":
            if event.get("password"):
                step = {"do": "fill", "target": target, "value": "{{secret:password}}"}
                self._add(step, t("Fill {target} (password, not recorded)", target=label), warnings)
            else:
                step = {"do": "fill", "target": target, "value": event.get("value", "")}
                self._add(step, t("Fill {target}", target=label), warnings)
        elif kind == "select":
            self._add({"do": "select", "target": target, "value": event.get("value", "")}, t("Select {target}", target=label), warnings)
        elif kind == "check":
            self._add({"do": "check", "target": target, "checked": bool(event.get("checked"))}, t("Check {target}", target=label), warnings)

    def result(self):
        return {"entries": self.steps, "warnings": self.warnings}
