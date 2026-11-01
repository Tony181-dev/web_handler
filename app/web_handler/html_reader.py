"""Turn HTML into a list of elements and ways to find them."""
import re

MAX_HTML_BYTES = 20 * 1024 * 1024
MAX_ELEMENTS = 3000
INTERACTIVE_ROLES = ("button", "link", "checkbox", "textbox", "combobox", "radio", "menuitem", "tab")
PAGE_SELECTOR = (
    "input, textarea, select, button, a[href], "
    + ", ".join(f'[role="{role}"]' for role in INTERACTIVE_ROLES)
)
DATA_ATTRIBUTES = ("data-testid", "data-test", "data-qa", "data-cy")
LABEL_ATTRIBUTES = ("aria-label", "title", "placeholder")
TEXT_INPUT_TYPES = {
    "", "text", "password", "email", "number", "search", "tel", "url", "date",
    "datetime-local", "month", "time", "week",
}
FIXED_VALUE_TYPES = {"submit", "button", "reset", "image", "radio", "checkbox"}
SUSPICIOUS_ID = re.compile(
    r"""^(
        [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}
        | [0-9a-f]{16,}
        | :r[0-9a-z]+:
        | (ember|react-select-|mui-|radix-|headlessui-|ext-gen|gwt-uid-|yui_).*\d.*
    )$""",
    re.IGNORECASE | re.VERBOSE,
)
LONG_NUMBER_END = re.compile(r"\d{4,}$")
UNSTABLE_CLASS = re.compile(r"(^|-)(active|hover|focus|selected|disabled|open|show|hidden|is-\w+)$|\d{3,}|^css-|__[a-z0-9]{5,}$", re.I)


class HtmlTooLarge(ValueError):
    pass


def _parse(html):
    from bs4 import BeautifulSoup

    if len(html.encode("utf-8", "ignore")) > MAX_HTML_BYTES:
        raise HtmlTooLarge("HTML lớn hơn 20 MB.")
    return BeautifulSoup(html, "html.parser")


def _quote_css(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _clean_text(tag):
    return " ".join(tag.get_text(" ", strip=True).split())


def _is_suspicious_id(value):
    return bool(SUSPICIOUS_ID.match(value) or LONG_NUMBER_END.search(value))


def _css_id(value):
    if re.fullmatch(r"[A-Za-z_][\w-]*", value):
        return f"#{value}"
    return f"[id={_quote_css(value)}]"


def _xpath_literal(value):
    if '"' not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"
    return None


def _structural_path(tag):
    parts = []
    current = tag
    while current is not None and getattr(current, "name", None) and current.name != "[document]":
        parent = current.parent
        if parent is None or parent.name == "[document]":
            parts.insert(0, current.name)
        else:
            siblings = parent.find_all(current.name, recursive=False)
            if len(siblings) > 1:
                index = next(i for i, sibling in enumerate(siblings) if sibling is current) + 1
                parts.insert(0, f"{current.name}:nth-of-type({index})")
            else:
                parts.insert(0, current.name)
        current = parent
    return " > ".join(parts)


def _candidates(tag, structural=None):
    """Ways to find this element, best first. Each is (selector, note)."""
    name = tag.name
    found = []
    element_id = tag.get("id")
    suspicious = bool(element_id and _is_suspicious_id(element_id))
    if element_id and not suspicious:
        found.append(_css_id(element_id))
    if tag.get("name"):
        found.append(f"{name}[name={_quote_css(tag['name'])}]")
    for attribute in DATA_ATTRIBUTES:
        if tag.get(attribute):
            found.append(f"[{attribute}={_quote_css(tag[attribute])}]")
    for attribute in LABEL_ATTRIBUTES:
        if tag.get(attribute):
            found.append(f"{name}[{attribute}={_quote_css(tag[attribute])}]")
    # Only for buttons and options: a text field's value is user data, perhaps a password.
    if (tag.get("type") or "").lower() in FIXED_VALUE_TYPES and tag.get("value"):
        found.append(f"{name}[type={_quote_css(tag['type'])}][value={_quote_css(tag['value'])}]")
    text = _clean_text(tag)
    if text and len(text) <= 80:
        literal = _xpath_literal(text)
        if literal:
            found.append(f"xpath=//{name}[normalize-space()={literal}]")
    classes = [item for item in (tag.get("class") or []) if not UNSTABLE_CLASS.search(item)]
    if classes and all(re.fullmatch(r"[A-Za-z_][\w-]*", item) for item in classes):
        found.append(name + "".join(f".{item}" for item in classes[:3]))
    if element_id and suspicious:
        found.append(_css_id(element_id))
    if structural:
        found.append(structural)
    unique = []
    for selector in found:
        if selector not in unique:
            unique.append(selector)
    return unique


def _count(soup, selector):
    """How many elements in this document match, or None when it cannot be told."""
    if selector.startswith("xpath="):
        match = re.fullmatch(r"xpath=//([\w-]+)\[normalize-space\(\)=(['\"])(.*)\2\]", selector)
        if not match:
            return None
        return sum(1 for tag in soup.find_all(match.group(1)) if _clean_text(tag) == match.group(3))
    try:
        return len(soup.select(selector))
    except Exception:
        return None


def _action(tag):
    if tag.name == "select" or tag.get("role") == "combobox":
        return "select"
    if tag.name == "textarea" or tag.get("role") == "textbox":
        return "fill"
    if tag.name == "input":
        kind = (tag.get("type") or "").lower()
        if kind in ("checkbox", "radio"):
            return "check"
        if kind in TEXT_INPUT_TYPES:
            return "fill"
        return "click"
    if tag.get("role") in ("checkbox", "radio"):
        return "check"
    return "click"


def _label(tag, soup):
    if tag.get("id"):
        label = soup.find("label", attrs={"for": tag["id"]})
        if label:
            return _clean_text(label)
    parent_label = tag.find_parent("label")
    if parent_label:
        return _clean_text(parent_label)
    for attribute in LABEL_ATTRIBUTES:
        if tag.get(attribute):
            return tag[attribute]
    text = _clean_text(tag)
    if text:
        return text[:80]
    if tag.get("value") and (tag.get("type") or "").lower() != "password":
        return tag["value"]
    return tag.get("name") or tag.get("id") or tag.name


def _warnings(tag, primary, match_count):
    notes = []
    if tag.get("id") and _is_suspicious_id(tag["id"]):
        notes.append(f"ID '{tag['id']}' có vẻ tự sinh, có thể đổi sau mỗi lần tải trang.")
    if primary and " > " in primary:
        notes.append("Chỉ nhận diện được theo vị trí trong trang, dễ hỏng khi trang đổi.")
    if match_count is not None and match_count == 0:
        notes.append("Không tìm thấy phần tử trong HTML bằng cách nhận diện này.")
    if match_count is not None and match_count > 1:
        notes.append(f"Cách nhận diện này khớp {match_count} phần tử.")
    style = (tag.get("style") or "").replace(" ", "").lower()
    if tag.has_attr("hidden") or "display:none" in style or "visibility:hidden" in style:
        notes.append("Phần tử đang bị ẩn trong HTML này.")
    return notes


def describe(tag, soup, counted=True, structural=None):
    selectors = _candidates(tag, structural)
    candidates = [
        {"selector": selector, "count": _count(soup, selector) if counted else None}
        for selector in selectors
    ]
    if counted:
        # A way that matches exactly once beats a higher ranked way that does not.
        ordered = [item for item in candidates if item["count"] == 1] + [
            item for item in candidates if item["count"] != 1
        ]
    else:
        ordered = candidates
    primary = ordered[0] if ordered else {"selector": "", "count": None}
    fallbacks = [item["selector"] for item in ordered[1:] if not counted or item["count"] == 1][:2]
    return {
        "tag": tag.name,
        "action": _action(tag),
        "label": _label(tag, soup),
        "selector": primary["selector"],
        "match_count": primary["count"],
        "fallbacks": fallbacks,
        "candidates": ordered,
        "warnings": _warnings(tag, primary["selector"], primary["count"]),
        "is_password": tag.name == "input" and (tag.get("type") or "").lower() == "password",
    }


def _is_listed(tag):
    if tag.name == "input" and (tag.get("type") or "").lower() == "hidden":
        return False
    return True


def read_page(html):
    """Every element a person could interact with in a whole page."""
    soup = _parse(html)
    elements = []
    for tag in soup.select(PAGE_SELECTOR):
        if _is_listed(tag):
            elements.append(describe(tag, soup, True, _structural_path(tag)))
            if len(elements) >= MAX_ELEMENTS:
                break
    return elements


def read_element(html):
    """One element pasted on its own. Its uniqueness on the page is unknown here."""
    soup = _parse(html)
    tag = soup.find(lambda item: item.name in ("input", "textarea", "select", "button", "a") or item.get("role") in INTERACTIVE_ROLES)
    if tag is None:
        tag = soup.find(True)
    if tag is None:
        return None
    return describe(tag, soup, counted=False)


def describe_recorded(html, structural, document_html):
    """One element from a recording, counted against the page as it was then."""
    fragment = _parse(html)
    tag = fragment.find(True)
    if tag is None:
        return None
    soup = _parse(document_html) if document_html else None
    if soup is None:
        return describe(tag, fragment, counted=False, structural=structural)
    selectors = _candidates(tag, structural)
    candidates = [{"selector": selector, "count": _count(soup, selector)} for selector in selectors]
    ordered = [item for item in candidates if item["count"] == 1] + [
        item for item in candidates if item["count"] != 1
    ]
    primary = ordered[0] if ordered else {"selector": "", "count": None}
    return {
        "tag": tag.name,
        "action": _action(tag),
        "label": _label(tag, fragment),
        "selector": primary["selector"],
        "match_count": primary["count"],
        "fallbacks": [item["selector"] for item in ordered[1:] if item["count"] == 1][:2],
        "candidates": ordered,
        "warnings": _warnings(tag, primary["selector"], primary["count"]),
        "is_password": tag.name == "input" and (tag.get("type") or "").lower() == "password",
    }
