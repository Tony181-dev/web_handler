"""The two languages stay apart: English lives in the code, Vietnamese only in ui/lang/vi.json."""
import ast
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "runtime" / "site"))

from web_handler import excel, flow as flow_module, flow_templates, i18n, paths, runner  # noqa: E402

APP = ROOT / "app"
VI_FILE = APP / "web_handler" / "ui" / "lang" / "vi.json"
# Letters English does not use, in the Latin ranges Vietnamese draws from.
VIETNAMESE_LETTERS = re.compile("[À-ÿĂăĐđĨĩŨũƠơƯưẠ-ỹ]")
EXEMPT_MARK = "i18n-exempt"
JS_CALL = re.compile(r"""\bt\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")
JS_DYNAMIC_CALL = re.compile(r"""\bt\(\s*[^'"\s)]""")
# Words on screen given as bare strings instead of going through t().
JS_BARE_TEXT = re.compile(r"""(?:\b(?:text|label|title|placeholder|hint)|'aria-label')\s*:\s*['`]([^'`]*)['`]""")
NOT_WORDS = {"C", "D", "A, C", "https://...", "Sheet", "your_account", "your_password"}
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def source_files(*suffixes):
    for path in sorted(APP.rglob("*")):
        if path.suffix in suffixes and "__pycache__" not in path.parts:
            yield path


def python_keys():
    keys = set()
    for path in source_files(".py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "t" and node.args:
                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    keys.add(node.args[0].value)
    return keys


def js_unescape(text):
    return text.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")


def js_keys():
    keys = set()
    for path in source_files(".js"):
        for match in JS_CALL.finditer(path.read_text(encoding="utf-8")):
            keys.add(js_unescape(match.group(1) if match.group(1) is not None else match.group(2)))
    return keys


def all_source_text():
    return "\n".join(path.read_text(encoding="utf-8") for path in source_files(".py", ".js"))


class SeparationTests(unittest.TestCase):
    def setUp(self):
        self.vi = json.loads(VI_FILE.read_text(encoding="utf-8"))

    def test_code_holds_no_vietnamese(self):
        found = []
        for path in source_files(".py", ".js", ".html", ".css"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if VIETNAMESE_LETTERS.search(line) and EXEMPT_MARK not in line:
                    found.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:90]}")
        self.assertEqual(found, [])

    def test_every_translated_string_has_vietnamese(self):
        missing = sorted((python_keys() | js_keys()) - set(self.vi))
        self.assertEqual(missing, [])

    def test_vietnamese_file_has_no_leftovers(self):
        # Names like the block names reach t() through a variable, so looking for the text is enough.
        source = all_source_text()
        used = python_keys() | js_keys()
        unused = sorted(key for key in self.vi if key not in used and key not in source and js_escaped(key) not in source)
        self.assertEqual(unused, [])

    def test_placeholders_match(self):
        wrong = {
            key: value for key, value in self.vi.items()
            if set(PLACEHOLDER.findall(key)) != set(PLACEHOLDER.findall(value))
        }
        self.assertEqual(wrong, {})

    def test_vietnamese_entries_are_filled(self):
        self.assertEqual([key for key, value in self.vi.items() if not value.strip()], [])

    def test_keys_are_english_and_values_are_not_left_english(self):
        self.assertEqual([key for key in self.vi if VIETNAMESE_LETTERS.search(key)], [])
        same = {"OK", "Excel", "Log", "Link", "Setup", "Sheet", "Web Handler"}
        self.assertEqual([key for key, value in self.vi.items() if key == value and key not in same], [])

    def test_words_on_screen_go_through_t(self):
        bare = []
        for path in source_files(".js"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for match in JS_BARE_TEXT.finditer(line):
                    text = re.sub(r"\$\{[^}]*\}", "", match.group(1))
                    if re.search("[A-Za-z]", text) and text not in NOT_WORDS:
                        bare.append(f"{path.name}:{number}: {text[:70]}")
        self.assertEqual(bare, [])

    def test_html_has_no_hard_coded_labels(self):
        page = (APP / "web_handler" / "ui" / "index.html").read_text(encoding="utf-8")
        for label in ("Secrets", "Setup"):
            self.assertNotIn(f">{label}<", page)


def js_escaped(text):
    return text.replace("\\", "\\\\").replace("'", "\\'")


class LanguageChoiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        patcher = mock.patch.object(paths, "data_dir", lambda: Path(self.folder.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        i18n.use(None)
        self.addCleanup(i18n.use, "en")

    def test_english_is_the_default(self):
        self.assertEqual(i18n.language(), "en")
        self.assertEqual(i18n.t("Finished."), "Finished.")

    def test_saved_choice_is_used_and_survives_a_restart(self):
        i18n.save("vi")
        self.assertEqual(i18n.t("Finished."), "Đã chạy xong.")
        i18n.use(None)
        self.assertEqual(i18n.language(), "vi")

    def test_saving_keeps_other_settings(self):
        settings = Path(self.folder.name) / "settings.json"
        settings.write_text('{"other": 1}', encoding="utf-8")
        i18n.save("vi")
        self.assertEqual(json.loads(settings.read_text(encoding="utf-8")), {"other": 1, "language": "vi"})

    def test_unknown_language_is_refused(self):
        with self.assertRaises(ValueError):
            i18n.save("fr")

    def test_damaged_settings_file_means_english(self):
        (Path(self.folder.name) / "settings.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(i18n.language(), "en")

    def test_values_fill_placeholders_and_leave_template_tokens_alone(self):
        i18n.use("vi")
        self.assertEqual(i18n.t("Row {row}: ", row=4), "Hàng 4: ")
        self.assertIn("{{row}}", i18n.t("{{row}} only works inside a loop over Excel rows."))

    def test_unknown_text_falls_back_to_itself(self):
        i18n.use("vi")
        self.assertEqual(i18n.t("Not in the table {x}", x=1), "Not in the table 1")


class WhatPeopleSeeTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(i18n.use, "en")

    def test_messages_follow_the_language(self):
        flow = {"steps": [{"id": "s1", "do": "goto", "url": ""}]}
        i18n.use("en")
        self.assertIn("Open link", flow_module.validate(flow).errors[0]["message"])
        i18n.use("vi")
        self.assertIn("Mở link", flow_module.validate(flow).errors[0]["message"])

    def test_template_notes_follow_the_language(self):
        i18n.use("en")
        english = flow_templates.build("login_excel")
        i18n.use("vi")
        vietnamese = flow_templates.build("login_excel")
        self.assertEqual(english["steps"][1]["note"], "Username field")
        self.assertEqual(vietnamese["steps"][1]["note"], "Ô tài khoản")
        # Secret names are identifiers, the same in both languages.
        self.assertEqual(english["steps"][1]["value"], vietnamese["steps"][1]["value"])

    def test_old_journals_with_vietnamese_statuses_still_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "journal.jsonl"
            lines = [
                {"type": "row_end", "row": 2, "status": "Lỗi"},
                {"type": "row_end", "row": 3, "status": "Bỏ qua"},
                {"type": "row_end", "row": 4, "status": "Chưa rõ"},
                {"type": "row_end", "row": 5, "status": "OK"},
            ]
            path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
            state = runner.load_journal(path)
        self.assertEqual(state.statuses, {
            2: runner.STATUS_FAILED, 3: runner.STATUS_SKIPPED, 4: runner.STATUS_UNKNOWN, 5: runner.STATUS_OK,
        })

    def test_excel_errors_follow_the_language(self):
        i18n.use("vi")
        with self.assertRaisesRegex(excel.ExcelError, "Không tìm thấy file"):
            excel.sheet_info(str(Path(tempfile.gettempdir()) / "khong_co_file_nay.xlsx"))


if __name__ == "__main__":
    unittest.main()
