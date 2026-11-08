import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "probe"))

import probe
from probe import Check, CheckResult


def result(check_id, status="PASS", detail=""):
    return CheckResult(check_id, check_id, status, detail)


def all_pass():
    return [
        result(check_id)
        for check_id in (
            "portable_python", "write", "local_server", "file_dialog", "chrome_found",
            "install", "imports", "browser", "recording", "excel", "credentials",
        )
    ]


def with_status(check_id, status):
    return [
        result(item.id, status) if item.id == check_id else item for item in all_pass()
    ]


class SanitizeTests(unittest.TestCase):
    def test_replaces_personal_values_case_insensitively(self):
        text = r"Lỗi ở C:\USERS\alice\app trên máy ALICE-PC, người dùng Alice"
        replacements = {
            r"C:\Users\alice": "<HOME>",
            "ALICE-PC": "<MÁY>",
            "alice": "<NGƯỜI DÙNG>",
        }
        cleaned = probe.sanitize(text, replacements)
        self.assertNotIn("alice", cleaned.lower())
        self.assertIn(r"<HOME>\app", cleaned)
        self.assertIn("<MÁY>", cleaned)

    def test_longest_value_is_replaced_first(self):
        cleaned = probe.sanitize("D:/x/alice/y", {"alice": "<U>", "D:/x/alice": "<D>"})
        self.assertEqual(cleaned, "<D>/y")

    def test_empty_values_are_ignored(self):
        self.assertEqual(probe.sanitize("abc", {"": "<X>"}), "abc")

    def test_slash_variants_are_replaced(self):
        cleaned = probe.sanitize("C:/Users/alice/app", {r"C:\Users\alice": "<HOME>"})
        self.assertEqual(cleaned, "<HOME>/app")


class DecideTests(unittest.TestCase):
    def test_all_pass(self):
        self.assertEqual(probe.decide(all_pass())[0], "ALL_PASS")

    def test_info_results_do_not_change_the_decision(self):
        results = all_pass() + [result("system_python", "INFO")]
        self.assertEqual(probe.decide(results)[0], "ALL_PASS")

    def test_portable_python_failure_requires_rebuild(self):
        self.assertEqual(
            probe.decide(with_status("portable_python", "FAIL"))[0],
            "REBUILD_FOR_SYSTEM_PYTHON",
        )

    def test_rebuild_takes_priority_over_playwright_failure(self):
        results = [
            result("portable_python", "FAIL"),
            result("install", "FAIL"),
            result("browser", "SKIP"),
        ]
        self.assertEqual(probe.decide(results)[0], "REBUILD_FOR_SYSTEM_PYTHON")

    def test_playwright_failures_are_reported_as_blocked(self):
        for check_id in ("install", "imports", "browser", "recording"):
            with self.subTest(check_id):
                self.assertEqual(
                    probe.decide(with_status(check_id, "FAIL"))[0],
                    "PLAYWRIGHT_BLOCKED",
                )

    def test_other_failures_need_review(self):
        for check_id in ("write", "local_server", "file_dialog", "chrome_found", "excel", "credentials"):
            with self.subTest(check_id):
                self.assertEqual(
                    probe.decide(with_status(check_id, "FAIL"))[0], "NEEDS_REVIEW"
                )

    def test_skipped_required_check_is_never_all_pass(self):
        self.assertNotEqual(probe.decide(with_status("excel", "SKIP"))[0], "ALL_PASS")


class RunChecksTests(unittest.TestCase):
    def test_exception_becomes_failure_and_later_checks_still_run(self):
        def boom():
            raise RuntimeError("hỏng")

        results = probe.run_checks([
            Check("a", "A", boom),
            Check("b", "B", lambda: ("PASS", "ổn")),
        ])
        self.assertEqual([item.status for item in results], ["FAIL", "PASS"])
        self.assertIn("hỏng", results[0].detail)

    def test_dependent_check_is_skipped_when_requirement_fails(self):
        called = []
        results = probe.run_checks([
            Check("a", "A", lambda: ("FAIL", "lỗi")),
            Check("b", "B", lambda: called.append("b") or ("PASS", ""), requires=("a",)),
            Check("c", "C", lambda: called.append("c") or ("PASS", ""), requires=("b",)),
        ])
        self.assertEqual([item.status for item in results], ["FAIL", "SKIP", "SKIP"])
        self.assertEqual(called, [])

    def test_detail_is_sanitized_and_shortened(self):
        secret_home = str(Path.home())
        results = probe.run_checks([
            Check("a", "A", lambda: ("FAIL", secret_home + "/" + "x" * 5000)),
        ])
        self.assertNotIn(secret_home, results[0].detail)
        self.assertLessEqual(len(results[0].detail), probe.MAX_DETAIL + 20)


class ProcessTests(unittest.TestCase):
    def test_returns_output_and_exit_code(self):
        outcome = probe.run_with_timeout(
            [sys.executable, "-c", "print('xin chào'); raise SystemExit(3)"],
            timeout=30,
            env=probe.child_env(None),
        )
        self.assertEqual(outcome.returncode, 3)
        self.assertFalse(outcome.timed_out)
        self.assertIn("xin chào", outcome.output)

    def test_missing_program_is_reported_not_raised(self):
        outcome = probe.run_with_timeout(["khong-co-chuong-trinh-nay-xyz"], timeout=5)
        self.assertIsNone(outcome.returncode)
        self.assertTrue(outcome.output)

    def test_timeout_kills_process_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            pid_file = Path(folder) / "grandchild.pid"
            code = (
                "import subprocess, sys, time, pathlib\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
                f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
                "print('đã khởi động', flush=True)\n"
                "time.sleep(120)\n"
            )
            started = time.monotonic()
            outcome = probe.run_with_timeout([sys.executable, "-c", code], timeout=5)
            elapsed = time.monotonic() - started

            self.assertTrue(outcome.timed_out)
            self.assertLess(elapsed, 30)
            self.assertIn("đã khởi động", outcome.output)
            grandchild_pid = pid_file.read_text()
            time.sleep(1)
            command = (
                ["tasklist", "/FI", f"PID eq {grandchild_pid}", "/NH"] if sys.platform == "win32"
                else ["/bin/ps", "-p", grandchild_pid, "-o", "pid="]
            )
            listing = subprocess.run(command, capture_output=True, text=True).stdout
            self.assertNotIn(grandchild_pid, listing)

    def test_parse_child_output_uses_last_marker_line(self):
        output = (
            "tiếng ồn\n"
            f"{probe.RESULT_MARKER}{json.dumps({'ok': False})}\n"
            f"{probe.RESULT_MARKER}{json.dumps({'ok': True, 'detail': 'x'})}\n"
            "tiếng ồn cuối\n"
        )
        self.assertEqual(probe.parse_child_output(output), {"ok": True, "detail": "x"})

    def test_parse_child_output_without_marker(self):
        self.assertIsNone(probe.parse_child_output("không có gì"))

    def test_parse_child_output_with_broken_json(self):
        self.assertIsNone(probe.parse_child_output(probe.RESULT_MARKER + "{hỏng"))


class LocalServerTests(unittest.TestCase):
    class FlakyOpener:
        def __init__(self, failures):
            self.failures = failures
            self.calls = 0

        def open(self, url, timeout):
            self.calls += 1
            if self.calls <= self.failures:
                raise TimeoutError("timed out")
            return io.BytesIO(b"noi dung")

    def test_fetch_returns_body_and_retry_count(self):
        opener = self.FlakyOpener(failures=2)
        self.assertEqual(probe.fetch_with_retry(opener, "u", 3, 1), (b"noi dung", 2))

    def test_fetch_gives_up_after_all_attempts(self):
        opener = self.FlakyOpener(failures=3)
        with self.assertRaises(TimeoutError):
            probe.fetch_with_retry(opener, "u", 3, 1)
        self.assertEqual(opener.calls, 3)

    def test_response_is_buffered_into_one_write(self):
        # Separate writes for headers and body stall on machines with a network filter.
        with probe.ProbeSite() as site:
            handler = site.server.RequestHandlerClass
            self.assertGreaterEqual(handler.wbufsize, 64 * 1024)

    def test_response_has_content_length(self):
        with probe.ProbeSite() as site:
            opener = probe.urllib.request.build_opener(probe.urllib.request.ProxyHandler({}))
            with opener.open(site.url, timeout=10) as response:
                body = response.read()
                self.assertEqual(int(response.headers["Content-Length"]), len(body))
                self.assertIn("main-button", body.decode("utf-8"))

    def test_check_reports_success(self):
        status, detail = probe.check_local_server()
        self.assertEqual(status, "PASS")
        self.assertIn(str(probe.SERVER_ROUNDS), detail)


class SystemPythonTests(unittest.TestCase):
    def test_running_interpreter_is_not_reported_when_path_has_no_python(self):
        # CreateProcess also searches the parent's folder, which once made the
        # bundled Python look like a Python installed on the machine.
        with mock.patch.dict(probe.os.environ, {"PATH": ""}):
            status, detail = probe.check_system_python()
        self.assertEqual(status, "INFO")
        self.assertIn("Không tìm thấy", detail)


class ReportTests(unittest.TestCase):
    def render(self):
        results = [result("browser", "FAIL", "chi tiết lỗi"), result("excel", "SKIP")]
        return probe.render_report(
            results, probe.decide(results), {"Hệ điều hành": "Windows-10"}
        )

    def test_report_shows_decision_results_and_meta(self):
        text = self.render()
        self.assertIn("PLAYWRIGHT_BLOCKED", text)
        self.assertIn("[FAIL] browser", text)
        self.assertIn("chi tiết lỗi", text)
        self.assertIn("[SKIP] excel", text)
        self.assertIn("Windows-10", text)

    def test_report_is_written_with_bom_for_notepad(self):
        with tempfile.TemporaryDirectory() as folder:
            path = probe.write_report("xin chào", Path(folder) / "report.txt")
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertEqual(path.read_text(encoding="utf-8-sig"), "xin chào")

    def test_report_falls_back_when_target_is_unwritable(self):
        with tempfile.TemporaryDirectory() as folder:
            # A directory at the target path makes the first write fail.
            blocked = Path(folder) / "probe_report_test.txt"
            blocked.mkdir()
            path = probe.write_report("xin chào", blocked)
            try:
                self.assertNotEqual(path, blocked)
                self.assertEqual(path.read_text(encoding="utf-8-sig"), "xin chào")
            finally:
                path.unlink()

    def test_console_output_survives_legacy_encoding(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252")
        probe.safe_print("Kiểm tra môi trường", stream)
        stream.flush()
        self.assertTrue(raw.getvalue())


if __name__ == "__main__":
    unittest.main()
