"""Real Chrome against the built-in demo site, through the real worker process."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "runtime" / "site"))

DATA = tempfile.mkdtemp(prefix="wh_test_")
os.environ["WEB_HANDLER_DATA"] = DATA
os.environ["WEB_HANDLER_HEADLESS"] = os.environ.get("WEB_HANDLER_HEADLESS", "1")

from web_handler import demo_site, flow as flow_module, paths, runner, secret_store, settings  # noqa: E402
from web_handler.workers import WorkerSession  # noqa: E402

SITE = demo_site.DemoSite()


class Handler(BaseHTTPRequestHandler):
    wbufsize = 64 * 1024

    def log_message(self, *args):
        pass

    def respond(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        status, headers, text = SITE.handle(method, self.path, body, self.headers.get("Cookie"))
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.respond("GET")

    def do_POST(self):
        self.respond("POST")


class IntegrationBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        secret_store.set_secret("demo_user", demo_site.DEMO_USER)
        secret_store.set_secret("demo_password", demo_site.DEMO_PASSWORD)

    @classmethod
    def tearDownClass(cls):
        secret_store.delete_secret("demo_user")
        secret_store.delete_secret("demo_password")
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        SITE.reset()
        SITE.sessions.clear()
        self.folder = tempfile.TemporaryDirectory()
        self.dir = Path(self.folder.name)
        # Each test starts signed out.
        import shutil

        shutil.rmtree(paths.profile_dir(), ignore_errors=True)

    def tearDown(self):
        self.folder.cleanup()

    def run_job(self, flow, excel_path=None, mode="full", previous=None, on_event=None, run_id="run1", timeout=120, include_unknown=False, browser_mode=None):
        flow_path = self.dir / "flow.json"
        flow_module.save_flow(flow_path, flow)
        run_dir = self.dir / run_id
        job = {
            "action": "run", "run_id": run_id, "flow_path": str(flow_path), "excel_path": str(excel_path or ""),
            "mode": mode, "journal_path": str(run_dir / "journal.jsonl"), "screenshot_dir": str(run_dir),
            "include_unknown": include_unknown,
        }
        if browser_mode:
            job["browser_mode"] = browser_mode
        if previous:
            job["previous_journal"] = str(self.dir / previous / "journal.jsonl")
        events = []
        session = WorkerSession(job)
        self.session = session

        def handle(event):
            events.append(event)
            if on_event:
                on_event(event, session)

        session.on_event = handle
        session.start()
        self.assertTrue(session.wait(timeout), "worker did not finish")
        return events, run_dir

    def demo_files(self):
        excel_path = self.dir / "demo.xlsx"
        demo_site.make_demo_workbook(excel_path)
        return demo_site.make_demo_flow(self.base), excel_path

    @staticmethod
    def statuses(events):
        return {event["row"]: event["status"] for event in events if event["type"] == "row_done"}


class InspectorTests(IntegrationBase):
    """The browser kept open while building: it walks to a block and tries its action."""

    @staticmethod
    def target(selector):
        return {"primary": selector, "fallbacks": [], "frames": [], "verified": False}

    def flow_with_login(self):
        target = self.target
        return {
            "version": 1, "name": "Thử", "settings": {"step_timeout_seconds": 3, "navigation_timeout_seconds": 5}, "excel": None,
            "steps": [
                {"id": "s1", "do": "goto", "url": f"{self.base}/demo/login"},
                {"id": "s2", "do": "fill", "target": target("#user"), "value": "{{secret:demo_user}}"},
                {"id": "s3", "do": "fill", "target": target("#password"), "value": "{{secret:demo_password}}"},
                {"id": "s4", "do": "click", "target": target("input.login_button"), "wait_navigation": True},
                {"id": "s5", "do": "goto", "url": f"{self.base}/demo/playground"},
            ],
        }

    def setUp(self):
        super().setUp()
        self.flow_path = self.dir / "flow.json"
        self.events = []
        self.replies = []
        reply_ready = threading.Condition()
        self.reply_ready = reply_ready

        def handle(event):
            with reply_ready:
                self.events.append(event)
                if event.get("type") in ("result", "closed", "finished"):
                    self.replies.append(event)
                reply_ready.notify_all()

        self.inspector = WorkerSession({"action": "inspect"})
        self.inspector.on_event = handle
        self.inspector.start()

    def tearDown(self):
        self.inspector.send({"cmd": "stop"})
        self.assertTrue(self.inspector.wait(20))
        super().tearDown()

    def ask(self, flow, step_ids, selector, kind="click"):
        """Send one check. Returns its result and the blocks that really ran for it."""
        flow_module.save_flow(self.flow_path, flow)
        start = len(self.events)
        self.inspector.send({
            "cmd": "check", "flow_path": str(self.flow_path), "step_ids": step_ids,
            "target": self.target(selector), "kind": kind,
        })
        with self.reply_ready:
            self.assertTrue(self.reply_ready.wait_for(lambda: len(self.replies) > 0, 60), self.events[-5:])
            reply = self.replies.pop()
        return reply, [event["step"] for event in self.events[start:] if event["type"] == "step"]

    def test_only_new_blocks_run_and_a_changed_block_starts_over(self):
        flow = self.flow_with_login()
        reply, ran = self.ask(flow, ["s1", "s2", "s3", "s4"], "#items a.item-link >> nth=0")
        self.assertEqual(reply["count"], 1, reply)
        self.assertEqual(ran, ["s1", "s2", "s3", "s4"])
        self.assertEqual(SITE.login_attempts, 1)

        # Already there: nothing runs again, so nothing is submitted twice.
        reply, ran = self.ask(flow, ["s1", "s2", "s3", "s4"], "#items a.item-link >> nth=0")
        self.assertEqual(ran, [])
        self.assertEqual(SITE.login_attempts, 1)

        reply, ran = self.ask(flow, ["s1", "s2", "s3", "s4", "s5"], "#note", "fill")
        self.assertEqual((reply["count"], reply["problem"]), (1, None), reply)
        self.assertEqual(ran, ["s5"])

        # A password marked verified by an earlier check is no reason to log in again.
        flow["steps"][1]["target"]["verified"] = True
        _, ran = self.ask(flow, ["s1", "s2", "s3", "s4", "s5"], "#note", "fill")
        self.assertEqual(ran, [])

        flow["steps"][4]["url"] = f"{self.base}/demo/items"
        _, ran = self.ask(flow, ["s1", "s2", "s3", "s4", "s5"], "#items")
        self.assertEqual(ran, ["s1", "s2", "s3", "s4", "s5"])
        self.assertEqual(SITE.login_attempts, 2)

    def test_an_if_block_on_the_way_runs_the_branch_the_page_answers(self):
        target = self.target
        login = [
            {"id": "s3", "do": "fill", "target": target("#user"), "value": "{{secret:demo_user}}"},
            {"id": "s4", "do": "fill", "target": target("#password"), "value": "{{secret:demo_password}}"},
            {"id": "s5", "do": "click", "target": target("input.login_button"), "wait_navigation": True},
        ]
        flow = {
            "version": 1, "name": "If", "settings": {"step_timeout_seconds": 3, "navigation_timeout_seconds": 5}, "excel": None,
            "steps": [
                {"id": "s1", "do": "goto", "url": f"{self.base}/demo/"},
                {"id": "s2", "do": "if", "test": "exists", "target": target("#password"), "seconds": 1, "then": login, "else": []},
            ],
        }
        reply, ran = self.ask(flow, ["s1", "s2"], "#items a.item-link >> nth=0")
        self.assertEqual(reply["count"], 1, reply)
        self.assertEqual(ran, ["s1", "s2", "s3", "s4", "s5"])
        self.assertIn({"type": "branch", "step": "s2", "branch": "then"}, self.events)
        # Checking a block inside the branch runs only what comes before it there.
        flow["steps"][0]["url"] = f"{self.base}/demo/logout"
        reply, ran = self.ask(flow, ["s1", "s3"], "#password", "fill")
        self.assertEqual((reply["count"], reply["problem"]), (1, None), reply)
        self.assertEqual(ran, ["s1", "s3"])

    def test_action_is_only_tried_not_carried_out(self):
        flow = self.flow_with_login()
        reply, _ = self.ask(flow, ["s1", "s2", "s3", "s4", "s5"], "#open-popup")
        self.assertEqual((reply["count"], reply["problem"]), (1, None), reply)
        # A real click would have opened the popup, and the field inside would be usable.
        reply, _ = self.ask(flow, ["s1", "s2", "s3", "s4", "s5"], "#popup-input", "fill")
        self.assertEqual(reply["count"], 1)
        self.assertIn("hidden", reply["problem"])

    def test_action_that_cannot_work_on_the_element_is_reported(self):
        flow = self.flow_with_login()
        steps = ["s1", "s2", "s3", "s4", "s5"]
        reply, _ = self.ask(flow, steps, "#open-popup", "fill")
        self.assertIn("not an input", reply["problem"])
        reply, _ = self.ask(flow, steps, "#note", "select")
        self.assertIn("not a dropdown", reply["problem"])
        reply, _ = self.ask(flow, steps, ".same", "click")
        self.assertEqual((reply["count"], reply["problem"]), (2, None), reply)

    def test_failed_block_is_named_and_the_browser_continues_from_there(self):
        flow = self.flow_with_login()
        flow["steps"][2]["target"] = self.target("#khong_co")
        reply, ran = self.ask(flow, ["s1", "s2", "s3", "s4"], "#items")
        self.assertEqual(reply["failed_step"], "s3")
        self.assertEqual(reply["position"], 3)
        self.assertIn("#khong_co", reply["error"])
        self.assertEqual(ran, ["s1", "s2", "s3"])
        self.assertNotIn(demo_site.DEMO_PASSWORD, json.dumps(self.events, ensure_ascii=False))

        flow["steps"][2]["target"] = self.target("#password")
        reply, ran = self.ask(flow, ["s1", "s2", "s3", "s4"], "#items")
        self.assertEqual(reply["count"], 1, reply)
        self.assertEqual(ran, ["s3", "s4"])

    def test_click_that_does_not_navigate_says_so(self):
        flow = self.flow_with_login()
        flow["steps"].append({"id": "s6", "do": "click", "target": self.target("#open-popup"), "wait_navigation": True})
        reply, _ = self.ask(flow, ["s1", "s2", "s3", "s4", "s5", "s6"], "#popup-input", "fill")
        self.assertEqual(reply["failed_step"], "s6")
        self.assertIn("Wait for the page to change", reply["error"])

    def test_excel_block_uses_the_first_usable_row(self):
        excel_path = self.dir / "demo.xlsx"
        demo_site.make_demo_workbook(excel_path)
        flow = demo_site.make_demo_flow(self.base)
        flow["settings"]["step_timeout_seconds"] = 3
        flow_module.save_flow(self.flow_path, flow)
        self.inspector.send({
            "cmd": "check", "flow_path": str(self.flow_path), "excel_path": str(excel_path),
            "step_ids": ["s1", "s2", "s3", "s4", "s5", "s7", "s8"],
            "target": self.target('input[name="SUBMIT"]'), "kind": "click",
        })
        with self.reply_ready:
            self.assertTrue(self.reply_ready.wait_for(lambda: self.replies, 60), self.events[-5:])
            reply = self.replies.pop()
        self.assertEqual((reply["count"], reply["problem"]), (1, None), reply)
        self.assertTrue(reply["url"].endswith("/demo/item/1/edit"), reply)
        # Filled, but Save was only tried.
        self.assertEqual(SITE.items[1]["summary"], "Old name 1")
        self.assertEqual(SITE.saves, 0)

    def test_excel_block_without_a_file_asks_for_one(self):
        flow = demo_site.make_demo_flow(self.base)
        flow_module.save_flow(self.flow_path, flow)
        self.inspector.send({
            "cmd": "check", "flow_path": str(self.flow_path), "step_ids": ["s1", "s7"],
            "target": self.target("#summary"), "kind": "fill",
        })
        with self.reply_ready:
            self.assertTrue(self.reply_ready.wait_for(lambda: self.replies, 60), self.events[-5:])
            reply = self.replies.pop()
        self.assertIn("Select Excel", reply["error"])


class DemoFlowTests(IntegrationBase):
    def test_full_flow_updates_site_and_writes_result_copy(self):
        from openpyxl import load_workbook

        flow, excel_path = self.demo_files()
        from web_handler.excel import file_hash

        before = file_hash(excel_path)
        events, _ = self.run_job(flow, excel_path)
        finished = events[-1]
        self.assertEqual(finished["type"], "finished", events[-3:])
        self.assertEqual(finished["outcome"], "finished", finished)
        self.assertEqual(self.statuses(events), {2: "OK", 3: "OK", 4: "Failed", 5: "Skipped", 6: "OK"})
        self.assertEqual(SITE.items[1]["summary"], "New name 1")
        self.assertEqual(SITE.items[3]["summary"], "Old name 3")
        self.assertEqual(SITE.items[4]["summary"], "Old name 4")
        self.assertEqual(file_hash(excel_path), before)
        workbook = load_workbook(finished["result_file"])
        sheet = workbook["Sheet1"]
        self.assertEqual(sheet["D2"].value, "OK")
        self.assertEqual(sheet["E2"].value, "New name 1")
        self.assertTrue(sheet["D4"].value.startswith("Failed"))
        self.assertTrue(sheet["D5"].value.startswith("Skipped"))
        workbook.close()
        text = json.dumps(events, ensure_ascii=False)
        self.assertNotIn(demo_site.DEMO_PASSWORD, text)

    def test_failed_row_has_a_screenshot(self):
        flow, excel_path = self.demo_files()
        events, run_dir = self.run_job(flow, excel_path)
        failed = next(event for event in events if event["type"] == "row_done" and event["status"] == "Failed")
        self.assertTrue((run_dir / failed["screenshot"]).exists())

    def test_wrong_password_stops_before_any_row_without_retry(self):
        flow, excel_path = self.demo_files()
        secret_store.set_secret("wrong_password", "sai")
        self.addCleanup(secret_store.delete_secret, "wrong_password")
        flow["steps"][2]["value"] = "{{secret:wrong_password}}"
        events, _ = self.run_job(flow, excel_path)
        self.assertEqual(events[-1]["outcome"], "failed")
        self.assertEqual(self.statuses(events), {})
        self.assertEqual(SITE.login_attempts, 1)

    def test_trial_runs_only_one_row(self):
        flow, excel_path = self.demo_files()
        events, _ = self.run_job(flow, excel_path, mode="trial")
        self.assertEqual(self.statuses(events), {2: "OK"})
        self.assertEqual(SITE.saves, 1)

    def test_retry_runs_failed_rows_only_and_keeps_earlier_results(self):
        from openpyxl import load_workbook

        flow, excel_path = self.demo_files()
        self.run_job(flow, excel_path, run_id="first")
        saves = SITE.saves
        events, _ = self.run_job(flow, excel_path, mode="retry", previous="first", run_id="second")
        self.assertEqual(self.statuses(events), {4: "Failed"})
        self.assertEqual(SITE.saves, saves)
        workbook = load_workbook(events[-1]["result_file"])
        self.assertEqual(workbook["Sheet1"]["D2"].value, "OK")
        self.assertEqual(workbook["Sheet1"]["E2"].value, "New name 1")
        workbook.close()

    def test_session_expiring_mid_run_pauses_after_consecutive_failures(self):
        flow, excel_path = self.demo_files()
        flow["settings"]["pause_after_consecutive_failures"] = 2
        flow["settings"]["step_timeout_seconds"] = 2
        flow["excel"]["required"] = ["A"]

        def on_event(event, session):
            if event["type"] == "row_done" and event["row"] == 2:
                SITE.sessions.clear()
            if event["type"] == "paused":
                session.send({"cmd": "stop"})

        events, _ = self.run_job(flow, excel_path, on_event=on_event)
        self.assertEqual(events[-1]["outcome"], "stopped")
        self.assertEqual(self.statuses(events), {2: "OK", 3: "Failed", 4: "Failed"})

    def test_stop_command_finishes_current_row_and_resume_does_the_rest(self):
        flow, excel_path = self.demo_files()

        def on_event(event, session):
            if event["type"] == "row_start" and event["row"] == 2:
                session.send({"cmd": "stop"})

        events, _ = self.run_job(flow, excel_path, on_event=on_event, run_id="first")
        self.assertEqual(events[-1]["outcome"], "stopped")
        self.assertEqual(self.statuses(events), {2: "OK"})
        events, _ = self.run_job(flow, excel_path, mode="resume", previous="first", run_id="second")
        self.assertEqual(sorted(self.statuses(events)), [3, 4, 5, 6])

    def test_worker_killed_mid_run_leaves_row_unknown_and_resume_skips_it(self):
        flow, excel_path = self.demo_files()

        def on_event(event, session):
            if event["type"] == "row_start" and event["row"] == 3:
                session.kill()

        # A demo row can finish before the kill lands, so hold each row open for longer than that.
        slow = json.loads(json.dumps(flow))
        slow["steps"][-1]["steps"].insert(0, {"id": "hold", "do": "wait", "seconds": 2})
        self.run_job(slow, excel_path, on_event=on_event, run_id="first")
        state = runner.load_journal(self.dir / "first" / "journal.jsonl")
        self.assertEqual(state.statuses[2], "OK")
        self.assertEqual(state.statuses[3], "Unclear")
        events, _ = self.run_job(flow, excel_path, mode="resume", previous="first", run_id="second")
        self.assertNotIn(3, self.statuses(events))
        self.assertIn(6, self.statuses(events))


class KeepBrowserTests(IntegrationBase):
    """The tests force a headless browser, so the job's own mode decides here as it would with a window."""

    def setUp(self):
        super().setUp()
        settings.save_keep_browser(True)

    def tearDown(self):
        settings.save_keep_browser(False)
        super().tearDown()

    def flow(self):
        return {"name": "Keep", "steps": [{"id": "s1", "do": "goto", "url": f"{self.base}/demo/login"}]}

    def test_the_worker_stays_with_the_open_browser_until_told_to_stop(self):
        finished = []

        def on_event(event, session):
            if event.get("type") != "finished":
                return
            finished.append(event)
            # Still there well after the 10 seconds a finished run would get, only shorter for the test.
            time.sleep(2)
            finished.append(session.alive())
            session.send({"cmd": "stop"})

        events, _ = self.run_job(self.flow(), on_event=on_event, timeout=60)
        self.assertEqual(finished[0]["outcome"], "finished", finished)
        self.assertTrue(finished[0]["kept_open"])
        self.assertTrue(finished[1], "The worker left without waiting")
        self.assertEqual(events[-1]["type"], "finished")

    def test_a_headless_run_never_keeps_its_browser(self):
        events, _ = self.run_job(self.flow(), browser_mode="headless", timeout=60)
        self.assertEqual(events[-1]["outcome"], "finished")
        self.assertFalse(events[-1]["kept_open"])

    def test_turned_off_the_browser_closes_as_before(self):
        settings.save_keep_browser(False)
        events, _ = self.run_job(self.flow(), timeout=60)
        self.assertFalse(events[-1]["kept_open"])


class IfBlockTests(IntegrationBase):
    """The case it was made for: log in only when the site still asks for it."""

    def flow(self):
        password = {"primary": "input[type=password]", "fallbacks": [], "frames": [], "verified": True}
        login = {"do": "login", "account": demo_site.DEMO_USER, "password": "{{secret:demo_password}}"}
        return {
            "name": "t", "settings": {"step_timeout_seconds": 3, "navigation_timeout_seconds": 20},
            "steps": [
                {"id": "a", "do": "loop", "source": "count", "count": 2, "steps": [
                    {"id": "b", "do": "goto", "url": f"{self.base}/demo/"},
                    {"id": "c", "do": "if", "test": "exists", "target": password, "seconds": 1,
                     "then": [{"id": "d", **login}], "else": []},
                    {"id": "e", "do": "verify", "url_contains": "/demo/items"},
                ]},
            ],
        }

    def test_logs_in_the_first_time_and_skips_it_once_logged_in(self):
        events, _ = self.run_job(self.flow())
        self.assertEqual(events[-1]["outcome"], "finished", events[-5:])
        self.assertEqual(self.statuses(events), {1: "OK", 2: "OK"})
        branches = [(event["row"], event["branch"]) for event in events if event["type"] == "branch"]
        self.assertEqual(branches, [(1, "then"), (2, "else")])
        self.assertEqual(SITE.login_attempts, 1)


class OcrBlockTests(IntegrationBase):
    def ocr_flow(self, selector="#captcha"):
        flow, _ = self.demo_files()
        target = {"primary": selector, "fallbacks": [], "frames": [], "verified": True}
        box = {"primary": "#note", "fallbacks": [], "frames": [], "verified": True}
        flow = {"name": "t", "settings": {"step_timeout_seconds": 5, "navigation_timeout_seconds": 20}, "steps": flow["steps"][:5]}
        flow["steps"] += [
            {"id": "a", "do": "goto", "url": f"{self.base}/demo/playground"},
            {"id": "b", "do": "ocr", "target": target, "save_to_var": "code", "text_case": "upper"},
            {"id": "c", "do": "fill", "target": box, "value": "{{var:code}}"},
            {"id": "d", "do": "verify", "target": box, "equals": demo_site.DEMO_CAPTCHA},
        ]
        return flow

    def test_reads_the_image_on_the_page_and_saves_no_file(self):
        events, run_dir = self.run_job(self.ocr_flow())
        self.assertEqual(events[-1]["outcome"], "finished", events[-1])
        self.assertEqual([path.name for path in run_dir.rglob("*") if path.suffix in (".png", ".jpg", ".jpeg")], [])
        temporary = [path.name for path in paths.data_dir().glob("*") if path.suffix in (".png", ".jpg", ".jpeg")]
        self.assertEqual(temporary, [])

    def test_missing_image_fails_the_block(self):
        events, _ = self.run_job(self.ocr_flow("#no-such-image"))
        self.assertEqual(events[-1]["outcome"], "failed")
        self.assertIn("no-such-image", events[-1]["message"])


class LoginBlockTests(IntegrationBase):
    def login_flow(self, password="{{secret:demo_password}}", **fields):
        return {
            "name": "t", "settings": {"step_timeout_seconds": 3, "navigation_timeout_seconds": 20},
            "steps": [
                {"id": "a", "do": "goto", "url": f"{self.base}/demo/login"},
                {"id": "b", "do": "login", "account": demo_site.DEMO_USER, "password": password, **fields},
                {"id": "c", "do": "verify", "url_contains": "/demo/items"},
            ],
        }

    def test_finds_the_form_by_itself_and_logs_in(self):
        events, _ = self.run_job(self.login_flow())
        self.assertEqual(events[-1]["outcome"], "finished", events[-1])
        self.assertEqual(SITE.login_attempts, 1)

    def test_selectors_can_be_given_by_hand(self):
        flow = self.login_flow(account_selector="#user", password_selector="#password", submit_selector="input.login_button")
        events, _ = self.run_job(flow)
        self.assertEqual(events[-1]["outcome"], "finished", events[-1])

    def test_wrong_password_fails_and_the_password_stays_hidden(self):
        secret_store.set_secret("wrong_password", "not-the-password")
        self.addCleanup(secret_store.delete_secret, "wrong_password")
        events, _ = self.run_job(self.login_flow(password="{{secret:wrong_password}}"))
        self.assertEqual(events[-1]["outcome"], "failed")
        self.assertIn("login form is still there", events[-1]["message"])
        self.assertNotIn("not-the-password", json.dumps(events, ensure_ascii=False))

    def test_when_already_logged_in_the_block_moves_on(self):
        flow = self.login_flow()
        flow["steps"] += [
            {"id": "d", "do": "goto", "url": f"{self.base}/demo/items"},
            {"id": "e", "do": "login", "account": demo_site.DEMO_USER, "password": "{{secret:demo_password}}"},
            {"id": "f", "do": "verify", "url_contains": "/demo/items"},
        ]
        events, _ = self.run_job(flow)
        self.assertEqual(events[-1]["outcome"], "finished", events[-1])
        self.assertEqual(SITE.login_attempts, 1)
        self.assertTrue(any("already logged in" in event.get("text", "") for event in events))

    def test_special_characters_in_a_secret_reach_the_page_unchanged(self):
        value = "p@$$w0rd!#%^&*()_+-=[]{}|;:'\",.<>/?`~\\ {{A}} \\1 \u00e0"
        secret_store.set_secret("me@mail.com / test", value)
        self.addCleanup(secret_store.delete_secret, "me@mail.com / test")
        flow = self.login_flow()
        flow["steps"] += [
            {"id": "d", "do": "goto", "url": f"{self.base}/demo/item/2/edit"},
            {"id": "e", "do": "fill", "target": {"primary": "#summary", "fallbacks": [], "frames": [], "verified": True}, "value": "{{secret:me@mail.com / test}}"},
            {"id": "f", "do": "click", "target": {"primary": "input[name=\"SUBMIT\"]", "fallbacks": [], "frames": [], "verified": True}, "wait_navigation": True},
        ]
        events, _ = self.run_job(flow)
        self.assertEqual(events[-1]["outcome"], "finished", events[-1])
        self.assertEqual(SITE.items[2]["summary"], value)


class ElementTests(IntegrationBase):
    def login_steps(self):
        flow, _ = self.demo_files()
        return flow["steps"][:5]

    def flow_with(self, steps):
        return {"name": "t", "settings": {"step_timeout_seconds": 3, "navigation_timeout_seconds": 20}, "steps": self.login_steps() + steps}

    @staticmethod
    def target(selector, fallbacks=(), frames=()):
        return {"primary": selector, "fallbacks": list(fallbacks), "frames": list(frames), "verified": True}

    def test_iframe_popup_dropdown_checkbox_and_late_element(self):
        steps = [
            {"id": "a", "do": "goto", "url": f"{self.base}/demo/playground"},
            {"id": "b", "do": "click", "target": self.target("#open-popup")},
            {"id": "c", "do": "fill", "target": self.target("#popup-input"), "value": "trong popup"},
            {"id": "d", "do": "click", "target": self.target("#popup-close")},
            {"id": "e", "do": "fill", "target": self.target("#frame-input", frames=["#inner"]), "value": "trong khung"},
            {"id": "f", "do": "click", "target": self.target("#frame-button", frames=["#inner"])},
            {"id": "g", "do": "verify", "target": self.target("#frame-result", frames=["#inner"]), "equals": "Clicked"},
            {"id": "h", "do": "click", "target": self.target("#delayed")},
            {"id": "i", "do": "wait_for", "target": self.target("#late-text")},
            {"id": "j", "do": "goto", "url": f"{self.base}/demo/item/2/edit"},
            {"id": "k", "do": "select", "target": self.target("#priority"), "value": "High"},
            {"id": "l", "do": "check", "target": self.target("#done"), "checked": True},
            {"id": "m", "do": "click", "target": self.target("input[name='SUBMIT']"), "wait_navigation": True},
        ]
        events, _ = self.run_job(self.flow_with(steps))
        self.assertEqual(events[-1]["outcome"], "finished", events[-1])
        self.assertEqual(SITE.items[2]["priority"], "High")
        self.assertTrue(SITE.items[2]["done"])

    def test_duplicate_element_fails_instead_of_guessing(self):
        steps = [
            {"id": "a", "do": "goto", "url": f"{self.base}/demo/playground"},
            {"id": "b", "do": "click", "target": self.target("button.same")},
        ]
        events, _ = self.run_job(self.flow_with(steps))
        self.assertEqual(events[-1]["outcome"], "failed")
        self.assertIn("matches 2 places", events[-1]["message"])

    def test_fallback_is_used_when_primary_is_gone_and_logged(self):
        steps = [
            {"id": "a", "do": "goto", "url": f"{self.base}/demo/playground"},
            {"id": "b", "do": "fill", "target": self.target("#ember_khong_con", ["input[name='auto-field']"]), "value": "x"},
        ]
        events, _ = self.run_job(self.flow_with(steps))
        self.assertEqual(events[-1]["outcome"], "finished", events[-1])
        self.assertTrue(any("fallback" in event.get("text", "") for event in events))

    def test_fallback_matching_many_is_not_used(self):
        steps = [
            {"id": "a", "do": "goto", "url": f"{self.base}/demo/playground"},
            {"id": "b", "do": "click", "target": self.target("#khong_co", ["button.same"])},
        ]
        events, _ = self.run_job(self.flow_with(steps))
        self.assertEqual(events[-1]["outcome"], "failed")

    def test_count_loop_uses_index(self):
        steps = [{"id": "z", "do": "loop", "source": "count", "count": 2, "steps": [
            {"id": "a", "do": "goto", "url": f"{self.base}/demo/item/{{{{i}}}}/edit"},
            {"id": "b", "do": "fill", "target": self.target("#summary"), "value": "Lần {{i}}"},
            {"id": "c", "do": "click", "target": self.target("input[name='SUBMIT']"), "wait_navigation": True},
        ]}]
        events, _ = self.run_job(self.flow_with(steps))
        self.assertEqual(events[-1]["outcome"], "finished", events[-1])
        self.assertEqual(SITE.items[2]["summary"], "Lần 2")


class RecorderTests(IntegrationBase):
    def record(self, actions):
        """Record what `actions(page, recorder, context)` does in a fresh browser. Returns the entries."""
        from playwright.sync_api import sync_playwright

        from web_handler import recorder as recorder_module

        recorder = recorder_module.Recorder(lambda event: None)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context()
            recorder.attach(context)
            page = context.new_page()
            actions(page, recorder, context)
            page.wait_for_timeout(400)
            browser.close()
        return recorder.result()["entries"]

    def log_in(self, page):
        page.goto(f"{self.base}/demo/login")
        page.fill("#user", demo_site.DEMO_USER)
        page.fill("#password", demo_site.DEMO_PASSWORD)
        page.click("input.login_button")
        page.wait_for_url("**/demo/items")

    def test_logging_in_with_the_enter_key_records_the_button(self):
        def actions(page, recorder, context):
            page.goto(f"{self.base}/demo/login")
            page.fill("#user", demo_site.DEMO_USER)
            page.fill("#password", demo_site.DEMO_PASSWORD)
            page.press("#password", "Enter")
            page.wait_for_url("**/demo/items")

        entries = self.record(actions)
        steps = [entry["step"] for entry in entries]
        self.assertEqual([step["do"] for step in steps], ["goto", "fill", "fill", "click"], steps)
        self.assertTrue(steps[3]["wait_navigation"])
        self.assertIn("login", steps[3]["target"]["primary"].lower())

    def test_editing_a_field_again_keeps_one_block_with_the_last_value(self):
        def actions(page, recorder, context):
            self.log_in(page)
            page.goto(f"{self.base}/demo/item/2/edit")
            page.fill("#summary", "First try")
            page.click("h2")
            page.fill("#summary", "Second try")
            page.click("h2")

        steps = [entry["step"] for entry in self.record(actions)]
        fills = [step for step in steps if step["do"] == "fill" and step["target"]["primary"] == "#summary"]
        self.assertEqual([step["value"] for step in fills], ["Second try"])
        # Clicking a heading is not a click to repeat.
        self.assertEqual([step for step in steps if step["do"] == "click" and "h2" in step["target"]["primary"]], [])

    def test_typing_and_stopping_at_once_keeps_the_last_field(self):
        def actions(page, recorder, context):
            self.log_in(page)
            page.goto(f"{self.base}/demo/item/2/edit")
            page.click("#priority")
            page.locator("#summary").press_sequentially("Not left yet")
            typed.append(page.input_value("#summary"))
            recorder.flush_pending(context)

        typed = []
        steps = [entry["step"] for entry in self.record(actions)]
        fills = [step for step in steps if step["do"] == "fill" and step["target"]["primary"] == "#summary"]
        self.assertIn("Not left yet", typed[0])
        self.assertEqual([step["value"] for step in fills], typed)

    def test_a_field_only_clicked_into_records_nothing_when_stopping(self):
        def actions(page, recorder, context):
            self.log_in(page)
            page.goto(f"{self.base}/demo/item/2/edit")
            page.click("#summary")
            recorder.flush_pending(context)

        steps = [entry["step"] for entry in self.record(actions)]
        self.assertEqual([step for step in steps if step["do"] == "fill" and step["target"]["primary"] == "#summary"], [])

    def test_a_link_opening_a_new_tab_is_a_plain_click(self):
        def actions(page, recorder, context):
            self.log_in(page)
            page.goto(f"{self.base}/demo/playground")
            with context.expect_page() as popup:
                page.click("#new-tab")
            popup.value.wait_for_load_state()

        steps = [entry["step"] for entry in self.record(actions)]
        click = next(step for step in steps if step["do"] == "click" and step["target"]["primary"] == "#new-tab")
        self.assertFalse(click["wait_navigation"])
        self.assertEqual(steps[-1], click, "The new tab's page must not become an Open link block")

    def test_recording_flow_creates_steps_without_password(self):
        from playwright.sync_api import sync_playwright

        from web_handler import recorder as recorder_module

        events = []
        recorder = recorder_module.Recorder(events.append)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context()
            recorder.attach(context)
            page = context.new_page()
            recorder.attach_page(page)
            page.goto(f"{self.base}/demo/login")
            page.fill("#user", demo_site.DEMO_USER)
            page.fill("#password", demo_site.DEMO_PASSWORD)
            page.click("input.login_button")
            page.wait_for_url("**/demo/items")
            page.click("a.item-link >> nth=1")
            page.wait_for_url("**/demo/item/2/edit")
            page.fill("#summary", "Tên do người gõ")
            page.select_option("#priority", label="High")
            page.check("#done")
            page.click("input[name='SUBMIT']")
            page.wait_for_url("**/demo/item/2")
            page.goto(f"{self.base}/demo/playground")
            page.frame_locator("#inner").locator("#frame-input").fill("trong khung")
            page.frame_locator("#inner").locator("#frame-button").click()
            page.wait_for_timeout(300)
            browser.close()

        result = recorder.result()
        steps = [entry["step"] for entry in result["entries"]]
        kinds = [step["do"] for step in steps]
        self.assertEqual(kinds[:4], ["goto", "fill", "fill", "click"], kinds)
        self.assertEqual(steps[0]["url"], f"{self.base}/demo/login")
        self.assertEqual(steps[1]["target"]["primary"], "#user")
        self.assertEqual(steps[2]["value"], "")
        self.assertTrue(result["entries"][2]["password"])
        self.assertTrue(steps[3]["wait_navigation"])
        self.assertIn("select", kinds)
        self.assertIn("check", kinds)
        select = next(step for step in steps if step["do"] == "select")
        self.assertEqual(select["value"], "High")
        typed = next(step for step in steps if step.get("value") == "Tên do người gõ")
        self.assertEqual(typed["target"]["primary"], "#summary")
        self.assertTrue(typed["target"]["verified"])
        in_frame = next(step for step in steps if step.get("value") == "trong khung")
        self.assertEqual(in_frame["target"]["frames"], ['iframe[name="inner-frame"]'])
        frame_click = next(step for step in steps if step["do"] == "click" and "frame-button" in step["target"]["primary"])
        self.assertEqual(frame_click["target"]["frames"], ['iframe[name="inner-frame"]'])
        text = json.dumps([result, events], ensure_ascii=False)
        self.assertNotIn(demo_site.DEMO_PASSWORD, text)

    def test_recorded_steps_replay_in_a_real_run(self):
        from playwright.sync_api import sync_playwright

        from web_handler import recorder as recorder_module

        recorder = recorder_module.Recorder(lambda event: None)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context()
            recorder.attach(context)
            page = context.new_page()
            recorder.attach_page(page)
            page.goto(f"{self.base}/demo/login")
            page.fill("#user", demo_site.DEMO_USER)
            page.fill("#password", demo_site.DEMO_PASSWORD)
            page.click("input.login_button")
            page.wait_for_url("**/demo/items")
            # Nobody types an address within a redirect's time of the page loading.
            page.wait_for_timeout(1700)
            page.goto(f"{self.base}/demo/item/5/edit")
            page.fill("#summary", "Phát lại")
            page.click("input[name='SUBMIT']")
            page.wait_for_url("**/demo/item/5")
            page.wait_for_timeout(200)
            browser.close()
        entries = recorder.result()["entries"]
        steps = [entry["step"] for entry in entries]
        # What a person does after recording: choose the password the recording left empty.
        for entry in entries:
            if entry.get("password"):
                entry["step"]["value"] = "{{secret:demo_password}}"
        SITE.reset()
        SITE.sessions.clear()
        events, _ = self.run_job({"name": "r", "settings": {"step_timeout_seconds": 5}, "steps": steps})
        self.assertEqual(events[-1]["outcome"], "finished", events[-1])
        self.assertEqual(SITE.items[5]["summary"], "Phát lại")


if __name__ == "__main__":
    unittest.main()
