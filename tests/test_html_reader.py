from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "runtime" / "site"))

from web_handler import html_reader

PAGE = """
<html><body>
  <label for="user">Tài khoản</label><input id="user" name="user">
  <input type="password" id="pw" name="password" value="bi-mat">
  <input type="hidden" name="csrf" value="token">
  <input type="submit" name="SUBMIT" value="Save">
  <button class="same">Trùng</button><button class="same">Trùng</button>
  <button id="ember12345" name="go">Đi</button>
  <button id="a1b2c3d4e5f6a7b8c9d0">Hex</button>
  <select id="level"><option>Thấp</option></select>
  <input type="checkbox" id="agree">
  <a href="/x">Liên kết</a>
  <div role="button" data-testid="fancy">Tùy chỉnh</div>
  <div><span><input></span></div>
</body></html>
"""


class ReadElementTests(unittest.TestCase):
    def test_spec_example_save_button(self):
        info = html_reader.read_element('<input type="submit" name="SUBMIT" value="Save">')
        self.assertEqual(info["selector"], 'input[name="SUBMIT"]')
        self.assertEqual(info["action"], "click")
        self.assertIn('input[type="submit"][value="Save"]', [item["selector"] for item in info["candidates"]])
        self.assertIsNone(info["match_count"])

    def test_id_comes_first(self):
        self.assertEqual(html_reader.read_element('<input id="summary" type="text">')["selector"], "#summary")

    def test_actions(self):
        cases = {
            "<input>": "fill", '<input type="password">': "fill", "<textarea></textarea>": "fill",
            "<select></select>": "select", '<input type="checkbox">': "check", '<input type="radio">': "check",
            "<button>x</button>": "click", "<a href='#'>x</a>": "click", '<input type="submit">': "click",
            '<div role="checkbox">x</div>': "check",
        }
        for html, expected in cases.items():
            with self.subTest(html):
                self.assertEqual(html_reader.read_element(html)["action"], expected)

    def test_suspicious_id_is_demoted_with_warning(self):
        for element_id in ("ember12345", "react-select-3-input", ":r1:", "a1b2c3d4e5f6a7b8c9d0", "field_123456",
                           "123e4567-e89b-12d3-a456-426614174000"):
            with self.subTest(element_id):
                info = html_reader.read_element(f'<input id="{element_id}" name="stable">')
                self.assertEqual(info["selector"], 'input[name="stable"]')
                self.assertTrue(any("tự sinh" in item for item in info["warnings"]))

    def test_id_with_special_characters_is_quoted(self):
        self.assertEqual(html_reader.read_element('<input id="a.b:c">')["selector"], '[id="a.b:c"]')

    def test_text_is_used_for_buttons_and_quotes_are_safe(self):
        info = html_reader.read_element("<button>Lưu lại</button>")
        self.assertEqual(info["selector"], 'xpath=//button[normalize-space()="Lưu lại"]')
        info = html_reader.read_element("""<button>Nó "đã" lưu</button>""")
        self.assertEqual(info["selector"], """xpath=//button[normalize-space()='Nó "đã" lưu']""")

    def test_non_interactive_paste_still_gives_something(self):
        self.assertEqual(html_reader.read_element('<p id="note">x</p>')["selector"], "#note")

    def test_nested_paste_finds_the_control(self):
        self.assertEqual(html_reader.read_element('<div class="row"><input name="q"></div>')["selector"], 'input[name="q"]')

    def test_empty_and_broken_input(self):
        self.assertIsNone(html_reader.read_element(""))
        self.assertIsNone(html_reader.read_element("chỉ có chữ"))
        self.assertEqual(html_reader.read_element('<input id="x"')["tag"] if html_reader.read_element('<input id="x"') else None, None)

    def test_hidden_inline_style_warns(self):
        info = html_reader.read_element('<input id="x" style="display: none">')
        self.assertTrue(any("ẩn" in item for item in info["warnings"]))


class ReadPageTests(unittest.TestCase):
    def setUp(self):
        self.elements = html_reader.read_page(PAGE)

    def find(self, label):
        return next(item for item in self.elements if item["label"] == label)

    def test_hidden_inputs_are_left_out(self):
        self.assertFalse(any("csrf" in item["selector"] for item in self.elements))

    def test_label_comes_from_label_for(self):
        self.assertEqual(self.find("Tài khoản")["selector"], "#user")

    def test_password_value_is_not_exposed(self):
        password = next(item for item in self.elements if item["is_password"])
        self.assertNotIn("bi-mat", str(password))

    def test_counts_are_known(self):
        self.assertEqual(self.find("Tài khoản")["match_count"], 1)

    def test_duplicate_elements_warn_and_get_a_unique_position(self):
        buttons = [item for item in self.elements if item["label"] == "Trùng"]
        self.assertEqual(len(buttons), 2)
        for button in buttons:
            self.assertEqual(button["match_count"], 1)
            self.assertIn(" > ", button["selector"])
            self.assertTrue(any("vị trí" in item for item in button["warnings"]))
        self.assertNotEqual(buttons[0]["selector"], buttons[1]["selector"])

    def test_structural_path_matches_exactly_one_element(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(PAGE, "html.parser")
        plain = next(item for item in self.elements if item["tag"] == "input" and " > " in item["selector"])
        self.assertEqual(len(soup.select(plain["selector"])), 1)

    def test_role_elements_are_listed(self):
        self.assertEqual(self.find("Tùy chỉnh")["selector"], '[data-testid="fancy"]')

    def test_too_large_input_is_refused(self):
        with self.assertRaises(html_reader.HtmlTooLarge):
            html_reader.read_page("x" * (html_reader.MAX_HTML_BYTES + 1))


if __name__ == "__main__":
    unittest.main()
