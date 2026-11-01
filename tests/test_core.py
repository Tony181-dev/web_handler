import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "runtime" / "site"))

from web_handler import excel, flow as flow_module, flow_templates, runner, templating, worker
from web_handler.browser_errors import BrowserClosed, ElementError


def target(selector, verified=True):
    return {"primary": selector, "fallbacks": [], "frames": [], "verified": verified}


class TemplatingTests(unittest.TestCase):
    def setUp(self):
        self.context = templating.Context(lambda name: {"pw": "s3cret"}.get(name))
        self.context.row = 5
        self.context.index = 2
        self.context.columns = {"A": "x", "AB": "y"}
        self.context.variables["v"] = "z"

    def test_all_token_kinds(self):
        text = "{{A}}-{{AB}}-{{row}}-{{i}}-{{var:v}}-{{ secret:pw }}"
        self.assertEqual(templating.render(text, self.context), "x-y-5-2-z-s3cret")

    def test_plain_text_is_unchanged(self):
        self.assertEqual(templating.render("a { b } c", self.context), "a { b } c")

    def test_unknown_token_raises(self):
        with self.assertRaises(templating.TemplateError):
            templating.render("{{khong_co}}", self.context)

    def test_missing_secret_raises(self):
        with self.assertRaises(templating.TemplateError):
            templating.render("{{secret:khac}}", self.context)

    def test_column_outside_row_raises(self):
        context = templating.Context()
        with self.assertRaises(templating.TemplateError):
            templating.render("{{A}}", context)

    def test_mask_hides_used_secret_only(self):
        templating.render("{{secret:pw}}", self.context)
        self.assertEqual(self.context.mask("a s3cret b"), "a •••• b")


def valid_flow():
    return {
        "name": "t",
        "excel": {"sheet": "Sheet1", "start_row": 2, "required": ["A"], "status_column": "D"},
        "steps": [
            {"id": "s1", "do": "goto", "url": "https://example.test/login"},
            {"id": "s2", "do": "fill", "target": target("#p"), "value": "{{secret:pw}}"},
            {"id": "s3", "do": "loop", "source": "excel", "steps": [
                {"id": "s4", "do": "goto", "url": "{{A}}/edit"},
                {"id": "s5", "do": "read", "target": target("#x"), "save_to_var": "old"},
                {"id": "s6", "do": "fill", "target": target("#y"), "value": "{{var:old}}"},
            ]},
        ],
    }


class ValidateTests(unittest.TestCase):
    def messages(self, flow, context=None):
        issues = flow_module.validate(flow, context)
        return [item["message"] for item in issues.errors], [item["message"] for item in issues.warnings]

    def test_valid_flow_has_no_errors(self):
        errors, warnings = self.messages(valid_flow())
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_missing_target_and_url_block_the_run(self):
        flow = valid_flow()
        flow["steps"][0]["url"] = ""
        flow["steps"][1]["target"] = None
        errors, _ = self.messages(flow)
        self.assertTrue(any("chưa có link" in item for item in errors))
        self.assertTrue(any("chưa có phần tử" in item for item in errors))

    def test_missing_column_blocks_the_run(self):
        errors, _ = self.messages(valid_flow(), flow_module.CheckContext(columns={"B"}))
        self.assertTrue(any("không có cột A" in item for item in errors))

    def test_column_in_count_flow_blocks_the_run(self):
        flow = valid_flow()
        flow["steps"][2].update(source="count", count=3)
        errors, _ = self.messages(flow)
        self.assertTrue(any("Cột A chỉ dùng được" in item for item in errors))

    def test_column_outside_loop_blocks_the_run(self):
        flow = valid_flow()
        flow["steps"][0]["url"] = "{{A}}"
        errors, _ = self.messages(flow)
        self.assertTrue(any("Cột A chỉ dùng được" in item for item in errors))

    def test_unsaved_secret_blocks_the_run(self):
        errors, _ = self.messages(valid_flow(), flow_module.CheckContext(secret_exists=lambda name: False))
        self.assertTrue(any("Bí mật pw" in item for item in errors))

    def test_variable_used_before_read_blocks_the_run(self):
        flow = valid_flow()
        loop = flow["steps"][2]["steps"]
        loop[1], loop[2] = loop[2], loop[1]
        errors, _ = self.messages(flow)
        self.assertTrue(any("trước khi đọc" in item for item in errors))

    def test_two_loops_block_the_run(self):
        flow = valid_flow()
        flow["steps"].append({"id": "s9", "do": "loop", "source": "count", "count": 1, "steps": [flow["steps"][0]]})
        errors, _ = self.messages(flow)
        self.assertTrue(any("một vùng lặp" in item for item in errors))

    def test_unverified_element_only_warns(self):
        flow = valid_flow()
        flow["steps"][1]["target"]["verified"] = False
        errors, warnings = self.messages(flow)
        self.assertEqual(errors, [])
        self.assertTrue(any("chưa kiểm chứng" in item for item in warnings))

    def test_secret_on_http_page_warns(self):
        flow = valid_flow()
        flow["steps"][0]["url"] = "http://example.test/login"
        _, warnings = self.messages(flow)
        self.assertTrue(any("http://" in item for item in warnings))

    def test_status_column_with_data_warns(self):
        _, warnings = self.messages(valid_flow(), flow_module.CheckContext(nonempty_columns={"D"}))
        self.assertTrue(any("Cột trạng thái D" in item for item in warnings))


class ExcelTests(unittest.TestCase):
    def setUp(self):
        from openpyxl import Workbook

        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "data.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Sheet1"
        sheet.append(["Link", "Ghi chú", "Tên"])
        sheet["A2"] = "Mục 1"
        sheet["A2"].hyperlink = "https://example.test/item/1"
        sheet["C2"] = " Tên mới "
        sheet["A3"] = "Nội bộ"
        sheet["A3"].hyperlink = "#Sheet1!A1"
        sheet["C3"] = 5.0
        sheet["A4"] = 123
        sheet["C4"] = None
        sheet["A5"] = "https://example.test/item/5"
        sheet["C5"] = __import__("datetime").date(2026, 1, 2)
        # A row with formatting only must not count as data.
        sheet["A9"].font = __import__("openpyxl").styles.Font(bold=True)
        workbook.save(self.path)

    def tearDown(self):
        self.folder.cleanup()

    def test_cell_conversion_and_hyperlinks(self):
        rows = excel.read_rows(self.path, "Sheet1", 2, None, ["A"])
        self.assertEqual(rows[0].values["A"], "https://example.test/item/1")
        self.assertEqual(rows[0].values["C"], "Tên mới")
        self.assertEqual(rows[1].values["A"], "Nội bộ")
        self.assertEqual(rows[1].values["C"], "5")
        self.assertEqual(rows[2].values["A"], "123")
        self.assertEqual(rows[3].values["C"], "2026-01-02")

    def test_formatting_only_rows_are_ignored(self):
        rows = excel.read_rows(self.path, "Sheet1", 2, None, ["A"])
        self.assertEqual(rows[-1].number, 5)

    def test_without_end_row_rows_stop_at_first_empty_cell_of_the_column(self):
        from openpyxl import Workbook

        path = self.path.with_name("stop.xlsx")
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Sheet1"
        for row in (["Link", "Nội dung"], ["a", "1"], ["b", None], [None, "3"], ["d", "4"]):
            sheet.append(row)
        workbook.save(path)
        workbook.close()
        rows = excel.read_rows(path, "Sheet1", 2, None, (), "A")
        self.assertEqual([row.number for row in rows], [2, 3])
        rows = excel.read_rows(path, "Sheet1", 2, None, (), "B")
        self.assertEqual([row.number for row in rows], [2])
        self.assertEqual(excel.read_rows(path, "Sheet1", 4, None, (), "A"), [])
        # An end row is an explicit range, so empty cells do not end it.
        rows = excel.read_rows(path, "Sheet1", 2, 5, (), "A")
        self.assertEqual([row.number for row in rows], [2, 3, 4, 5])

    def test_stop_column_is_the_first_column_the_loop_uses(self):
        loop = {"id": "l", "do": "loop", "source": "excel", "steps": [
            {"id": "a", "do": "click", "target": {"primary": "#x"}},
            {"id": "b", "do": "goto", "url": "https://site/{{B}}/edit"},
            {"id": "c", "do": "fill", "value": "{{A}}"},
        ]}
        before = {"id": "s", "do": "goto", "url": "{{secret:x}}"}
        self.assertEqual(flow_module.stop_column({"steps": [before, loop]}), "B")
        loop["steps"] = [{"id": "a", "do": "click"}]
        self.assertEqual(flow_module.stop_column({"steps": [loop], "excel": {"required": ["C"]}}), "C")
        self.assertIsNone(flow_module.stop_column({"steps": [loop], "excel": {}}))
        self.assertIsNone(flow_module.stop_column({"steps": [before]}))

    def test_end_row_limits_the_range(self):
        rows = excel.read_rows(self.path, "Sheet1", 3, 4, ["A"])
        self.assertEqual([row.number for row in rows], [3, 4])

    def test_required_column_marks_row_skipped(self):
        rows = excel.read_rows(self.path, "Sheet1", 2, None, ["A", "C"])
        self.assertEqual(rows[2].number, 4)
        self.assertIn("C", rows[2].skip_reason)
        self.assertEqual(rows[0].skip_reason, "")

    def test_missing_sheet_and_xls_have_clear_errors(self):
        with self.assertRaisesRegex(excel.ExcelError, "Không có sheet"):
            excel.read_rows(self.path, "Khac", 2)
        old = self.path.with_suffix(".xls")
        old.write_bytes(b"x")
        with self.assertRaisesRegex(excel.ExcelError, ".xlsx"):
            excel.sheet_info(old)

    def test_sheet_info_lists_columns(self):
        info = excel.sheet_info(self.path)
        sheet = info["sheets"][0]
        self.assertEqual(sheet["name"], "Sheet1")
        self.assertEqual(sheet["columns"][2]["header"], "Tên")

    def test_result_goes_to_copy_and_source_is_unchanged(self):
        before = excel.file_hash(self.path)
        writer = excel.ResultWriter(self.path, "Sheet1", "D", "r1")
        writer.set_status(2, "OK")
        target = writer.save()
        writer.close()
        self.assertEqual(excel.file_hash(self.path), before)
        self.assertNotEqual(target, self.path)
        self.assertIn("_result_r1", target.name)
        from openpyxl import load_workbook

        workbook = load_workbook(target)
        self.assertEqual(workbook["Sheet1"]["D2"].value, "OK")
        workbook.close()

    def test_result_write_falls_back_when_folder_blocks_it(self):
        writer = excel.ResultWriter(self.path, "Sheet1", "D", "r2")
        # A folder with the target name makes the first save fail.
        writer.target.mkdir()
        with tempfile.TemporaryDirectory() as data:
            with unittest.mock.patch.dict("os.environ", {"WEB_HANDLER_DATA": data}):
                saved = writer.save()
                self.assertTrue(saved.is_file())
                self.assertIn(data.lower(), str(saved).lower())
                writer.close()
                saved.unlink()


class FakeDriver:
    """Records calls and fails on request, standing in for a real browser."""

    def __init__(self, fail_on=None, close_on=None, values=None):
        self.calls = []
        self.fail_on = fail_on or {}
        self.close_on = close_on or set()
        self.values = values or {}
        self.current_url = "https://example.test/"
        self.notices = []

    def _record(self, name, target=None, value=None):
        selector = target.get("primary") if target else None
        self.calls.append((name, selector, value))
        if value in self.close_on:
            raise BrowserClosed()
        problem = self.fail_on.get((name, value)) or self.fail_on.get((name, selector))
        if problem:
            raise ElementError(problem)

    def goto(self, url, timeout):
        self._record("goto", value=url)
        self.current_url = url
        return 200

    def fill(self, target, value, timeout):
        self._record("fill", target, value)
        self.values[target["primary"]] = value

    def click(self, target, timeout, navigation_timeout):
        self._record("click", target)

    def select(self, target, value, timeout):
        self._record("select", target, value)

    def check(self, target, checked, timeout):
        self._record("check", target, checked)

    def read(self, target, timeout):
        self._record("read", target)
        return self.values.get(target["primary"], "")

    def wait_for(self, target, timeout):
        self._record("wait_for", target)

    def url(self):
        return self.current_url

    def screenshot(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"png")

    def take_fallback_notices(self):
        notices, self.notices = self.notices, []
        return notices

    def idle(self, milliseconds):
        pass


class ScriptedControl:
    """Answers pause prompts from a list, and can request a stop after some rows."""

    def __init__(self, decisions=(), stop_after_rows=None, events=None):
        self.decisions = list(decisions)
        self.stop_after_rows = stop_after_rows
        self.events = events
        self.prompts = 0
        self.waited = []
        self.cancel_waits = False

    @property
    def stop_requested(self):
        if self.stop_after_rows is None or self.events is None:
            return False
        done = sum(1 for event in self.events if event["type"] == "row_done")
        return done >= self.stop_after_rows

    def wait_decision(self, driver):
        self.prompts += 1
        return self.decisions.pop(0) if self.decisions else "stop"

    def wait_seconds(self, driver, seconds):
        self.waited.append(seconds)
        return not self.cancel_waits


def rows_of(count, skip=()):
    return [
        excel.Row(number, {"A": f"https://example.test/{number}", "C": f"name{number}"}, "thiếu" if number in skip else "")
        for number in range(2, 2 + count)
    ]


def row_flow(pause_after=3):
    return {
        "settings": {"pause_after_consecutive_failures": pause_after},
        "steps": [
            {"id": "s1", "do": "goto", "url": "https://example.test/login"},
            {"id": "s2", "do": "loop", "source": "excel", "steps": [
                {"id": "s3", "do": "goto", "url": "{{A}}"},
                {"id": "s4", "do": "fill", "target": target("#n"), "value": "{{C}}"},
            ]},
            {"id": "s5", "do": "goto", "url": "https://example.test/logout"},
        ],
    }


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.dir = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def run_flow(self, flow, driver=None, rows=None, control=None, mode="full", secret=None):
        self.events = []
        self.driver = driver or FakeDriver()
        control = control or ScriptedControl(events=self.events)
        control.events = self.events
        self.journal = runner.Journal(self.dir / "journal.jsonl")
        instance = runner.Runner(
            flow, self.driver, self.events.append, control,
            get_secret=secret, rows=rows, journal=self.journal,
            screenshot_dir=self.dir, mode=mode,
        )
        outcome = instance.run()
        self.journal.close()
        self.counts = instance.counts
        return outcome

    def statuses(self):
        return {event["row"]: event["status"] for event in self.events if event["type"] == "row_done"}

    def test_all_rows_run_with_their_values_then_after_steps(self):
        outcome, _ = self.run_flow(row_flow(), rows=rows_of(2))
        self.assertEqual(outcome, "finished")
        self.assertEqual(self.statuses(), {2: "OK", 3: "OK"})
        self.assertIn(("fill", "#n", "name3"), self.driver.calls)
        self.assertEqual(self.driver.calls[-1], ("goto", None, "https://example.test/logout"))

    def test_login_failure_stops_before_any_row(self):
        driver = FakeDriver(fail_on={("goto", "https://example.test/login"): "hỏng"})
        outcome, message = self.run_flow(row_flow(), driver, rows_of(3))
        self.assertEqual(outcome, "failed")
        self.assertIn("trước vùng lặp", message)
        self.assertEqual(self.statuses(), {})
        self.assertEqual(len(driver.calls), 1)

    def test_failed_row_continues_with_next_and_has_screenshot(self):
        driver = FakeDriver(fail_on={("fill", "name2"): "không tìm thấy"})
        outcome, _ = self.run_flow(row_flow(), driver, rows_of(2))
        self.assertEqual(outcome, "finished")
        self.assertEqual(self.statuses(), {2: "Lỗi", 3: "OK"})
        event = next(item for item in self.events if item["type"] == "row_done" and item["row"] == 2)
        self.assertIn("không tìm thấy", event["message"])
        self.assertTrue((self.dir / event["screenshot"]).exists())

    def test_failed_step_is_not_retried(self):
        driver = FakeDriver(fail_on={("fill", "name2"): "x"})
        self.run_flow(row_flow(), driver, rows_of(1))
        self.assertEqual(driver.calls.count(("fill", "#n", "name2")), 1)

    def test_skipped_rows_do_not_touch_the_browser(self):
        self.run_flow(row_flow(), rows=rows_of(2, skip={2}))
        self.assertEqual(self.statuses(), {2: "Bỏ qua", 3: "OK"})
        self.assertNotIn(("goto", None, "https://example.test/2"), self.driver.calls)

    def test_consecutive_failures_pause_and_stop_on_request(self):
        driver = FakeDriver(fail_on={("fill", "#n"): "lỗi"})
        control = ScriptedControl(decisions=["stop"])
        outcome, _ = self.run_flow(row_flow(pause_after=2), driver, rows_of(5), control)
        self.assertEqual(outcome, "stopped")
        self.assertEqual(control.prompts, 1)
        self.assertEqual(len(self.statuses()), 2)

    def test_continue_after_pause_runs_remaining_rows(self):
        driver = FakeDriver(fail_on={("fill", "#n"): "lỗi"})
        control = ScriptedControl(decisions=["continue", "continue"])
        outcome, _ = self.run_flow(row_flow(pause_after=2), driver, rows_of(5), control)
        self.assertEqual(outcome, "finished")
        self.assertEqual(len(self.statuses()), 5)

    def test_a_success_resets_the_failure_count(self):
        driver = FakeDriver(fail_on={("fill", "name2"): "x", ("fill", "name4"): "x"})
        control = ScriptedControl()
        self.run_flow(row_flow(pause_after=2), driver, rows_of(3), control)
        self.assertEqual(control.prompts, 0)

    def test_stop_finishes_current_row_only(self):
        control = ScriptedControl(stop_after_rows=1)
        outcome, _ = self.run_flow(row_flow(), rows=rows_of(3), control=control)
        self.assertEqual(outcome, "stopped")
        self.assertEqual(self.statuses(), {2: "OK"})

    def test_browser_closed_marks_row_unknown_and_stops(self):
        driver = FakeDriver(close_on={"name3"})
        outcome, _ = self.run_flow(row_flow(), driver, rows_of(4))
        self.assertEqual(outcome, "browser_closed")
        self.assertEqual(self.statuses(), {2: "OK", 3: "Chưa rõ"})

    def test_secret_is_masked_in_errors_and_logs(self):
        flow = {"steps": [{"id": "s0", "do": "loop", "source": "count", "count": 1, "steps": [
            {"id": "s1", "do": "fill", "target": target("#p"), "value": "{{secret:pw}}"},
        ]}]}
        driver = FakeDriver(fail_on={("fill", "#p"): "không điền được giá trị hunter2 vào ô"})
        self.run_flow(flow, driver, secret=lambda name: "hunter2")
        text = json.dumps(self.events, ensure_ascii=False) + (self.dir / "journal.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("hunter2", text)
        self.assertIn("••••", text)

    def test_verify_equal_failure_is_row_error(self):
        flow = {"steps": [{"id": "s0", "do": "loop", "source": "excel", "steps": [
            {"id": "s1", "do": "verify", "target": target("#n"), "equals": "{{C}}"},
        ]}]}
        self.run_flow(flow, FakeDriver(values={"#n": "name2"}), rows_of(2))
        self.assertEqual(self.statuses(), {2: "OK", 3: "Lỗi"})

    def test_verify_url(self):
        flow = {"steps": [{"id": "s1", "do": "verify", "url_not_contains": "/login"}]}
        driver = FakeDriver()
        driver.current_url = "https://example.test/login"
        outcome, message = self.run_flow(flow, driver)
        self.assertEqual(outcome, "failed")
        self.assertIn("/login", message)

    def test_read_saves_to_variable_and_column(self):
        flow = {"steps": [{"id": "s0", "do": "loop", "source": "excel", "steps": [
            {"id": "s1", "do": "read", "target": target("#a"), "save_to_var": "old"},
            {"id": "s2", "do": "read", "target": target("#a"), "save_to_column": "E"},
            {"id": "s3", "do": "fill", "target": target("#b"), "value": "{{var:old}}!"},
        ]}]}
        self.run_flow(flow, FakeDriver(values={"#a": "giá trị"}), rows_of(1))
        self.assertIn(("fill", "#b", "giá trị!"), self.driver.calls)
        record = runner.load_journal(self.dir / "journal.jsonl")
        self.assertEqual(record.written[2], {"E": "giá trị"})

    def test_count_loop_uses_index_and_trial_runs_once(self):
        flow = {"steps": [{"id": "s0", "do": "loop", "source": "count", "count": 3, "steps": [
            {"id": "s1", "do": "goto", "url": "https://example.test/{{i}}"},
        ]}]}
        self.run_flow(flow)
        self.assertIn(("goto", None, "https://example.test/3"), self.driver.calls)
        self.run_flow(flow, mode="trial")
        self.assertEqual(len(self.driver.calls), 1)

    def test_manual_pause_waits_for_user(self):
        flow = {"steps": [{"id": "s1", "do": "manual_pause", "message": "Nhập mã"}]}
        control = ScriptedControl(decisions=["continue"])
        outcome, _ = self.run_flow(flow, control=control)
        self.assertEqual(outcome, "finished")
        self.assertEqual(control.prompts, 1)

    def test_after_loop_failure_keeps_row_results(self):
        driver = FakeDriver(fail_on={("goto", "https://example.test/logout"): "x"})
        outcome, _ = self.run_flow(row_flow(), driver, rows_of(1))
        self.assertEqual(outcome, "after_loop_failed")
        self.assertEqual(self.statuses(), {2: "OK"})

    def test_wait_block_pauses_for_its_seconds_and_announces_them(self):
        control = ScriptedControl()
        outcome, _ = self.run_flow({"steps": [{"id": "s1", "do": "wait", "seconds": 1.5}]}, control=control)
        self.assertEqual(outcome, "finished")
        self.assertEqual(control.waited, [1.5])
        self.assertIn({"type": "waiting", "step": "s1", "seconds": 1.5}, self.events)
        self.assertEqual([event["label"] for event in self.events if event["type"] == "step"], ["Wait 1.5 giây"])

    def test_cancelled_wait_stops_the_run(self):
        control = ScriptedControl()
        control.cancel_waits = True
        flow = {"steps": [
            {"id": "s1", "do": "wait", "seconds": 3},
            {"id": "s2", "do": "goto", "url": "https://example.test/"},
        ]}
        outcome, _ = self.run_flow(flow, control=control)
        self.assertEqual(outcome, "stopped")
        self.assertEqual(self.driver.calls, [])


class WaitCommandTests(unittest.TestCase):
    """The worker's wait: a run's stop means after this row, a check's cancel means now."""

    class QuietPipe:
        closed = False

        def __init__(self):
            self.lines = []

        def poll(self):
            lines, self.lines = self.lines, []
            return lines

    def setUp(self):
        self.pipe = self.QuietPipe()
        self.commands = worker.Commands(self.pipe)
        self.addCleanup(setattr, self.pipe, "closed", True)

    def test_wait_lasts_its_seconds(self):
        started = time.monotonic()
        self.assertTrue(self.commands.wait_seconds(None, 0.3))
        self.assertGreaterEqual(time.monotonic() - started, 0.3)

    def test_cancel_ends_the_wait_early(self):
        self.pipe.lines.append('{"cmd": "cancel"}')
        started = time.monotonic()
        self.assertFalse(self.commands.wait_seconds(None, 30))
        self.assertLess(time.monotonic() - started, 5)

    def test_stop_waits_on_and_stays_queued_for_a_later_manual_step(self):
        self.pipe.lines.append('{"cmd": "stop"}')
        self.assertTrue(self.commands.wait_seconds(None, 0.5))
        self.assertTrue(self.commands.stop_requested)
        self.assertEqual(self.commands.wait_decision(None), "stop")


class WaitValidationTests(unittest.TestCase):
    def errors_for(self, seconds):
        step = {"id": "s1", "do": "wait"}
        if seconds is not None:
            step["seconds"] = seconds
        return [item["message"] for item in flow_module.validate({"steps": [step]}).errors]

    def test_positive_seconds_are_accepted(self):
        self.assertEqual(self.errors_for(3), [])
        self.assertEqual(self.errors_for(0.5), [])

    def test_missing_or_non_positive_seconds_are_rejected(self):
        for seconds in (None, 0, -1, "3", True):
            with self.subTest(seconds=seconds):
                self.assertEqual(self.errors_for(seconds), ["Khối Wait cần số giây lớn hơn 0."])


class FlowTemplateTests(unittest.TestCase):
    def fill_in_blanks(self, flow):
        """What a person does after creating the flow: point the blocks at a real page."""
        for step, _ in flow_module.walk(flow):
            if "target" in step:
                step["target"] = target("#element")
            if step.get("do") == "goto" and not step["url"]:
                step["url"] = "https://example.test/"

    def test_catalog_lists_every_template_with_english_label_and_a_hint(self):
        catalog = flow_templates.catalog()
        self.assertGreaterEqual(len(catalog), 2)
        self.assertLessEqual(len(catalog), 3)
        for item in catalog:
            self.assertTrue(item["label"].isascii(), item["label"])
            self.assertTrue(item["hint"])

    def test_template_is_a_valid_flow_once_its_blanks_are_filled(self):
        for item in flow_templates.catalog():
            with self.subTest(template=item["id"]):
                flow = flow_templates.build(item["id"], "Tên")
                self.assertEqual(flow["name"], "Tên")
                self.fill_in_blanks(flow)
                self.assertEqual(flow_module.validate(flow).errors, [])

    def test_step_ids_are_unique(self):
        for item in flow_templates.catalog():
            with self.subTest(template=item["id"]):
                ids = [step["id"] for step, _ in flow_module.walk(flow_templates.build(item["id"], "x"))]
                self.assertEqual(len(ids), len(set(ids)))

    def test_builds_do_not_share_state(self):
        template_id = flow_templates.catalog()[0]["id"]
        first = flow_templates.build(template_id, "x")
        first["steps"][0]["url"] = "changed"
        self.assertNotEqual(flow_templates.build(template_id, "x")["steps"][0].get("url"), "changed")

    def test_unknown_template_is_refused(self):
        with self.assertRaises(ValueError):
            flow_templates.build("khong_co", "x")


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "j.jsonl"

    def tearDown(self):
        self.folder.cleanup()

    def test_row_without_end_is_unknown_and_cut_line_is_ignored(self):
        journal = runner.Journal(self.path)
        journal.write(type="start", flow_hash="h")
        journal.write(type="row_start", row=2)
        journal.write(type="row_end", row=2, status="OK")
        journal.write(type="row_start", row=3)
        journal.close()
        with self.path.open("a", encoding="utf-8") as file:
            file.write('{"type": "row_e')
        state = runner.load_journal(self.path)
        self.assertEqual(state.statuses, {2: "OK", 3: "Chưa rõ"})
        self.assertEqual(state.meta["flow_hash"], "h")
        self.assertFalse(state.finished)

    def test_row_selection_modes(self):
        rows = rows_of(5)
        previous = {2: "OK", 3: "Lỗi", 4: "Chưa rõ"}
        numbers = lambda chosen: [row.number for row in chosen]
        self.assertEqual(numbers(runner.select_rows(rows, previous, "resume")), [5, 6])
        self.assertEqual(numbers(runner.select_rows(rows, previous, "retry")), [3])
        self.assertEqual(numbers(runner.select_rows(rows, previous, "retry", True)), [3, 4])
        self.assertEqual(numbers(runner.select_rows(rows, {}, "full")), [2, 3, 4, 5, 6])
        self.assertEqual(numbers(runner.select_rows(rows_of(3, skip={2}), {}, "trial")), [3])


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
