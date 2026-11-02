"""The real app: real server, real worker, real Chrome, with the UI driven like a person."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "runtime" / "site"))

DATA = tempfile.mkdtemp(prefix="wh_ui_test_")
os.environ["WEB_HANDLER_DATA"] = DATA
os.environ["WEB_HANDLER_HEADLESS"] = "1"

from playwright.sync_api import expect, sync_playwright  # noqa: E402

from web_handler import i18n, paths, secret_store  # noqa: E402

SHOTS = Path(os.environ.get("WEB_HANDLER_SHOTS", "")) if os.environ.get("WEB_HANDLER_SHOTS") else None
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
VIETNAMESE_LETTERS = re.compile("[À-ỹ]")
# Flow names and elements found on a scanned page are the user's own words, not labels.
BUTTON_LABELS = "button:not(.name):not(.element-row), a.btn, .tool, [role=tab], summary"


def start_app():
    return subprocess.Popen(
        [sys.executable, str(ROOT / "app" / "main.py"), "--no-browser"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=paths.child_env(),
        encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )


def read_address():
    deadline = time.monotonic() + 20
    path = Path(DATA) / "server.json"
    while time.monotonic() < deadline:
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            with NO_PROXY.open(f"http://127.0.0.1:{saved['port']}/api/ping?key={saved['key']}", timeout=2):
                return saved
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("App did not start.")


class AppTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = start_app()
        saved = read_address()
        cls.port = saved["port"]
        cls.key = saved["key"]
        cls.url = f"http://127.0.0.1:{cls.port}/?key={cls.key}"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(cls.app.pid)], capture_output=True)
        for name in ("demo_user", "demo_password", "ui_test"):
            secret_store.delete_secret(name)
        shutil.rmtree(DATA, ignore_errors=True)

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1400, "height": 860})
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [], "JavaScript errors on the page")

    def shot(self, name):
        if SHOTS:
            SHOTS.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)

    def new_flow(self, name="Thử"):
        self.page.click("#new-flow")
        self.page.locator(".modal input").fill(name)
        self.page.click("[data-template=blank]")
        expect(self.page.locator("#canvas")).to_be_visible()

    def drag_tool(self, kind, target_selector=None):
        tool = self.page.locator(f".tool[data-kind={kind}]").bounding_box()
        target = self.page.locator(target_selector or "#canvas").bounding_box()
        self.page.mouse.move(tool["x"] + 20, tool["y"] + 10)
        self.page.mouse.down()
        self.page.mouse.move(target["x"] + target["width"] - 30, target["y"] + target["height"] - 30, steps=8)
        self.page.mouse.up()

    def lasso(self, first, last):
        one = self.page.locator(first).bounding_box()
        two = self.page.locator(last).bounding_box()
        self.page.mouse.move(one["x"] + 4, one["y"] - 6)
        self.page.mouse.down()
        self.page.mouse.move(two["x"] + two["width"] + 4, two["y"] + two["height"] + 6, steps=8)
        self.page.mouse.up()

    def saved_flow(self, flow_id=None):
        flow_id = flow_id or self.page.url.rsplit("/", 1)[-1]
        self.page.wait_for_function("!['Not saved', 'Saving...'].includes(document.querySelector('.save-state').textContent)")
        return json.loads((Path(DATA) / "flows" / f"{flow_id}.json").read_text(encoding="utf-8"))

    def assert_buttons_are_english(self):
        labels = self.page.locator(BUTTON_LABELS).all_inner_texts()
        self.assertTrue(labels, "No buttons found on this screen")
        self.assertEqual([label for label in labels if VIETNAMESE_LETTERS.search(label)], [])


class DemoTests(AppTestCase):
    def run_demo(self):
        self.page.click("#open-demo")
        expect(self.page.locator("#excel-name")).to_contain_text("example_data.xlsx")
        self.shot("builder_demo")

    def test_full_demo_flow(self):
        self.run_demo()
        self.page.click("#run-full")
        expect(self.page.locator("#confirm-total")).to_contain_text("4")
        expect(self.page.locator("#confirm-total")).to_contain_text("1 row(s) with missing data skipped")
        expect(self.page.locator("#confirm-hosts")).to_contain_text(f"127.0.0.1:{self.port}")
        self.shot("confirm")
        self.assert_buttons_are_english()
        self.page.click("[data-action=confirm-run]")
        expect(self.page.locator("#run-stop")).to_have_text("Stop after this row")
        self.assert_buttons_are_english()
        expect(self.page.locator("#run-headline")).to_have_text("Finished.", timeout=90000)
        expect(self.page.locator("[data-count='OK']")).to_have_text("3")
        expect(self.page.locator("[data-count='Failed']")).to_have_text("1")
        expect(self.page.locator("[data-count='Skipped']")).to_have_text("1")
        expect(self.page.locator("#run-rows tr[data-row='4']")).to_contain_text("view-summary")
        expect(self.page.locator("#result-file")).to_contain_text("example_data_result_")
        self.assertNotIn("null", self.page.locator("#run-page").inner_text())
        self.shot("finished")
        self.assert_buttons_are_english()

        # A row's screenshot loads through the app.
        self.page.locator("#run-rows details summary").click()
        image = self.page.locator("#run-rows img.shot")
        expect(image).to_be_visible()
        self.page.wait_for_function("document.querySelector('#run-rows img.shot').naturalWidth > 0")

        # Retrying only touches the failed row.
        first_run = self.page.url
        self.page.click("#run-retry")
        expect(self.page.locator("#confirm-total")).to_have_text("1")
        self.page.click("[data-action=confirm-run]")
        self.page.wait_for_url(lambda url: url != first_run)
        expect(self.page.locator("#run-stop")).to_have_count(0, timeout=60000)
        expect(self.page.locator("[data-count='Failed']")).to_have_text("1", timeout=60000)
        expect(self.page.locator("#run-headline")).to_have_text("Finished.", timeout=60000)

        # The finished run can be opened again from its link.
        address = self.page.url
        self.page.goto(self.url)
        self.page.goto(address)
        expect(self.page.locator("#run-headline")).to_have_text("Finished.")

    def test_trial_warns_it_writes_for_real(self):
        self.run_demo()
        self.page.click("#run-trial")
        expect(self.page.locator(".modal .banner.warn")).to_contain_text("for real")
        expect(self.page.locator("#confirm-total")).to_have_text("1")
        self.page.click("[data-action=cancel]")
        expect(self.page.locator(".modal")).to_have_count(0)

    def test_diagram_shows_demo_with_loop(self):
        self.run_demo()
        expect(self.page.locator(".loop-frame .node")).to_have_count(5)
        expect(self.page.locator("#canvas .node")).to_have_count(10)
        expect(self.page.locator("#row-count")).to_have_text("Runs until column A ends (first empty cell)")

    def test_row_count_message_follows_the_end_row(self):
        self.run_demo()
        expect(self.page.locator("#end-row")).to_have_value("")
        self.page.click("#run-full")
        expect(self.page.locator("#confirm-stop")).to_have_text("Runs until column A ends (first empty cell)")
        self.page.click("[data-action=cancel]")
        self.page.fill("#end-row", "4")
        expect(self.page.locator("#row-count")).to_have_text("up to 3 run(s)")
        self.page.click("#run-full")
        expect(self.page.locator("#confirm-total")).to_have_text("3")
        expect(self.page.locator("#confirm-stop")).to_have_count(0)
        self.page.click("[data-action=cancel]")
        self.page.fill("#end-row", "")
        expect(self.page.locator("#row-count")).to_contain_text("column A ends")

    def test_clear_all_also_forgets_excel_but_keeps_the_file(self):
        self.run_demo()
        workbook = Path(self.page.evaluate("window.currentEditor.excelPath"))
        self.page.click("#clear-all")
        expect(self.page.locator(".modal")).to_contain_text("10 block(s)")
        self.page.click("[data-action=confirm]")
        expect(self.page.locator("#canvas .node")).to_have_count(0)
        expect(self.page.locator("#excel-name")).to_have_text("No Excel file selected")
        self.page.wait_for_timeout(800)
        self.assertIsNone(self.saved_flow()["excel"])
        self.assertTrue(workbook.exists())

        self.page.reload()
        expect(self.page.locator("#excel-name")).to_have_text("No Excel file selected")
        expect(self.page.locator("#clear-all")).to_be_disabled()


class BuilderTests(AppTestCase):
    def build_three(self):
        self.new_flow()
        for kind in ("goto", "fill", "click"):
            self.drag_tool(kind)
        expect(self.page.locator("#canvas .node")).to_have_count(3)

    def test_drag_blocks_and_paste_html_then_autosave(self):
        self.build_three()
        self.page.locator("#canvas .node").nth(1).click()
        self.page.fill("#paste-element", '<input id="summary" name="s" type="text">')
        expect(self.page.locator("#target-selector")).to_have_value("#summary")
        expect(self.page.locator("#verified-badge")).to_contain_text("Not checked")
        self.page.locator("[data-field=value]").fill("{{C}}")
        expect(self.page.locator("#canvas .node").nth(1)).to_contain_text("#summary")
        self.page.wait_for_timeout(800)
        flow = self.saved_flow()
        self.assertEqual(flow["steps"][1]["target"]["primary"], "#summary")
        self.assertEqual(flow["steps"][1]["value"], "{{C}}")
        self.assertNotIn('id="summary"', json.dumps(flow))

    def test_action_suggestion_can_switch_block_type(self):
        self.build_three()
        self.page.locator("#canvas .node").nth(1).click()
        self.page.fill("#paste-element", '<input type="submit" name="SUBMIT" value="Save">')
        self.page.click("#switch-action")
        expect(self.page.locator("#canvas .node").nth(1)).to_contain_text("Click")

    def test_clear_all_asks_first_then_empties_the_flow(self):
        self.new_flow("Giữ tên")
        expect(self.page.locator("#clear-all")).to_be_disabled()
        for kind in ("goto", "fill", "click"):
            self.drag_tool(kind)
        self.lasso("#canvas .node >> nth=1", "#canvas .node >> nth=2")
        self.page.click("#make-loop")

        self.page.click("#clear-all")
        expect(self.page.locator(".modal")).to_contain_text("3 block(s)")
        self.assert_buttons_are_english()
        self.page.get_by_role("button", name="Cancel").click()
        expect(self.page.locator("#canvas .node")).to_have_count(3)

        self.page.click("#clear-all")
        self.page.click("[data-action=confirm]")
        expect(self.page.locator("#canvas .node")).to_have_count(0)
        expect(self.page.locator(".loop-frame")).to_have_count(0)
        expect(self.page.locator(".canvas-empty")).to_be_visible()
        expect(self.page.locator("#clear-all")).to_be_disabled()
        self.page.wait_for_timeout(800)
        flow = self.saved_flow()
        self.assertEqual(flow["steps"], [])
        self.assertEqual(flow["name"], "Giữ tên")

        # The emptied flow keeps working.
        self.page.locator(".tool[data-kind=goto]").click()
        expect(self.page.locator("#canvas .node")).to_have_count(1)
        expect(self.page.locator("#clear-all")).to_be_enabled()

    def test_buttons_are_english_on_every_block_and_tab(self):
        self.page.click("#nav-secrets")
        self.page.fill("#secret-name", "ui_test")
        self.page.fill("#secret-value", "x")
        self.page.click("#secret-save")
        expect(self.page.locator(".modal table.data")).to_contain_text("ui_test")
        self.assert_buttons_are_english()
        self.page.click("[data-action=close]")
        self.assert_buttons_are_english()

        self.new_flow()
        kinds = ("goto", "fill", "click", "select", "check", "read", "wait_for", "verify", "manual_pause", "wait", "login")
        for kind in kinds:
            self.page.locator(f".tool[data-kind={kind}]").click()
            self.assert_buttons_are_english()
        self.page.locator("#canvas .node").nth(1).click()
        for tab in ("element", "page", "live"):
            self.page.click(f"[data-tab={tab}]")
            self.assert_buttons_are_english()
        self.page.click("[data-tab=element]")
        self.page.fill("#paste-element", '<input type="submit" name="SUBMIT" value="Save">')
        expect(self.page.locator("#switch-action")).to_have_text("Change to Click")
        self.page.evaluate("window.currentEditor.selectMany(['s8', 's9'])")
        expect(self.page.locator("#make-loop")).to_be_enabled()
        self.assert_buttons_are_english()
        self.page.click("#make-loop")
        self.assert_buttons_are_english()
        self.page.click(".brand")
        expect(self.page.locator(".flow-item")).not_to_have_count(0)
        self.assert_buttons_are_english()

    def test_click_on_palette_appends_block(self):
        self.new_flow()
        self.page.locator(".tool[data-kind=goto]").click()
        expect(self.page.locator("#canvas .node")).to_have_count(1)

    def drag_until_over(self, kind, x, y):
        tool = self.page.locator(f".tool[data-kind={kind}]").bounding_box()
        self.page.mouse.move(tool["x"] + 20, tool["y"] + 10)
        self.page.mouse.down()
        self.page.mouse.move(x - 5, y, steps=4)
        self.page.mouse.move(x, y, steps=2)
        return self.page.locator(".drop-marker").bounding_box()

    def test_marker_stays_on_the_row_when_dropping_after_its_last_block(self):
        self.new_flow()
        for _ in range(8):
            self.page.locator(".tool[data-kind=wait]").click()
        nodes = self.page.locator("#canvas .node")
        last_in_row = next(
            index for index in range(nodes.count() - 1)
            if nodes.nth(index + 1).bounding_box()["y"] > nodes.nth(index).bounding_box()["y"]
        )
        end = nodes.nth(last_in_row).bounding_box()
        canvas = self.page.locator("#canvas").bounding_box()
        for x in (end["x"] + end["width"] - 10, canvas["x"] + canvas["width"] - 8):
            marker = self.drag_until_over("fill", x, end["y"] + 20)
            self.assertGreaterEqual(marker["x"], end["x"] + end["width"])
            self.assertAlmostEqual(marker["y"], end["y"], delta=2)
            # Released outside the canvas, the drag adds nothing.
            self.page.mouse.move(canvas["x"] - 60, canvas["y"] + 500)
            self.page.mouse.up()
        expect(self.page.locator("#canvas .node")).to_have_count(8)
        self.drag_until_over("fill", end["x"] + end["width"] - 10, end["y"] + 20)
        self.page.mouse.up()
        steps = self.page.evaluate("window.currentEditor.flow.steps.map(step => step.do)")
        self.assertEqual(steps.index("fill"), last_in_row + 1)

    def test_marker_stays_beside_the_block_in_front_of_a_loop(self):
        self.page.click("#new-flow")
        self.page.click("[data-template=login_excel]")
        verify = self.page.locator("#canvas [data-lane=top] > .item:not(.block) .node").last
        expect(verify).to_be_visible()
        box = verify.bounding_box()
        marker = self.drag_until_over("wait", box["x"] + box["width"] + 40, box["y"] + 20)
        self.assertGreaterEqual(marker["x"], box["x"] + box["width"])
        self.assertAlmostEqual(marker["y"], box["y"], delta=2)
        self.page.mouse.up()
        kinds = self.page.evaluate("window.currentEditor.flow.steps.map(step => step.do)")
        self.assertEqual(kinds[-3:], ["login", "wait", "loop"])

    def test_login_block_asks_for_account_and_a_saved_password(self):
        self.addCleanup(secret_store.delete_secret, "login_ui_test")
        self.page.click("#nav-secrets")
        self.page.fill("#secret-name", "login_ui_test")
        self.page.fill("#secret-value", "x")
        self.page.click("#secret-save")
        expect(self.page.locator(".modal")).to_contain_text("login_ui_test")
        self.page.click("[data-action=close]")
        self.new_flow()
        self.page.locator(".tool[data-kind=login]").click()
        node = self.page.locator("#canvas .node").first
        expect(node).to_have_class(re.compile("invalid"))
        self.page.click("#run-full")
        expect(self.page.locator("#issues")).to_contain_text("has no account")
        expect(self.page.locator("#issues")).to_contain_text("has no password")
        self.page.click("[data-action=close]")
        self.page.locator("[data-field=account]").fill("me@mail.com")
        self.assertEqual(self.page.locator("[data-field=password]").count(), 0)
        self.page.select_option("[data-name=password]", "login_ui_test")
        expect(node).not_to_have_class(re.compile("invalid"))
        expect(node).to_contain_text("me@mail.com")
        self.shot("login_block")
        step = self.saved_flow()["steps"][0]
        self.assertEqual((step["account"], step["password"]), ("me@mail.com", "{{secret:login_ui_test}}"))

    def test_secret_name_and_value_accept_special_characters_and_can_be_deleted(self):
        name = "me@mail.com / test"
        value = "p@$$w0rd!#%^&*()_+-=[]{}|;:'\",.<>/?`~\\ {{A}} \u00e0"
        self.page.click("#nav-secrets")
        self.page.fill("#secret-name", name)
        self.page.fill("#secret-value", value)
        self.page.click("#secret-save")
        row = self.page.locator(".modal tr", has_text=name)
        expect(row).to_have_count(1)
        self.assertEqual(secret_store.get_secret(name), value)
        row.locator("button.danger").click()
        expect(self.page.locator(".modal tr", has_text=name)).to_have_count(0)
        self.assertIsNone(secret_store.get_secret(name))

    def test_loop_block_in_the_palette_adds_an_empty_loop_about_one_row_high(self):
        self.new_flow()
        tool = self.page.locator(".tool[data-kind=loop]")
        self.assertEqual(tool.evaluate("el => getComputedStyle(el).borderTopStyle"), "dashed")
        self.page.locator(".tool[data-kind=goto]").click()
        node_height = self.page.locator("#canvas .node").first.bounding_box()["height"]
        tool.click()
        frame = self.page.locator(".loop-frame")
        expect(frame).to_have_count(1)
        self.assertLessEqual(self.page.locator("[data-lane=loop]").bounding_box()["height"], node_height + 14)
        flow = self.saved_flow()
        self.assertEqual([step["do"] for step in flow["steps"]], ["goto", "loop"])
        self.assertEqual(flow["steps"][1]["steps"], [])

    def test_only_one_loop_block_can_be_added(self):
        self.new_flow()
        self.page.locator(".tool[data-kind=loop]").click()
        self.page.locator(".tool[data-kind=loop]").click()
        expect(self.page.locator(".loop-frame")).to_have_count(1)
        expect(self.page.locator(".toast.bad")).to_contain_text("only one loop")

    def test_loop_block_can_be_dragged_to_a_position(self):
        self.new_flow()
        for _ in range(2):
            self.page.locator(".tool[data-kind=wait]").click()
        second = self.page.locator("#canvas .node").nth(1).bounding_box()
        self.drag_until_over("loop", second["x"] + 5, second["y"] + 20)
        self.page.mouse.up()
        kinds = self.page.evaluate("window.currentEditor.flow.steps.map(step => step.do)")
        self.assertEqual(kinds, ["wait", "loop", "wait"])

    def test_empty_loop_takes_dragged_blocks_and_grows_when_full(self):
        self.new_flow()
        self.page.locator(".tool[data-kind=loop]").click()
        frame = self.page.locator(".loop-frame")
        height = frame.bounding_box()["height"]
        for count in range(1, 8):
            self.drag_tool("wait", "[data-lane=loop]")
            expect(self.page.locator("[data-lane=loop] .node")).to_have_count(count)
        self.assertGreater(frame.bounding_box()["height"], height + 40)
        loop = self.saved_flow()["steps"][0]
        self.assertEqual(len(loop["steps"]), 7)
        lane = self.page.locator("[data-lane=loop]").bounding_box()
        for node in self.page.locator("[data-lane=loop] .node").all():
            box = node.bounding_box()
            self.assertLessEqual(box["y"] + box["height"], lane["y"] + lane["height"] + 1)

    def drag_sidebar(self, offset):
        handle = self.page.locator("#inspector-resize").bounding_box()
        self.page.mouse.move(handle["x"] + handle["width"] / 2, 400)
        self.page.mouse.down()
        self.page.mouse.move(handle["x"] + handle["width"] / 2 + offset, 400, steps=5)
        self.page.mouse.up()
        return self.page.locator("#inspector").bounding_box()["width"]

    def test_sidebar_resizes_from_its_size_up_to_forty_percent_more(self):
        self.new_flow()
        start = self.page.locator("#inspector").bounding_box()["width"]
        self.assertAlmostEqual(self.drag_sidebar(60), start, delta=1, msg="Cannot go below its normal size")
        self.assertAlmostEqual(self.drag_sidebar(-50), start + 50, delta=2)
        self.assertAlmostEqual(self.drag_sidebar(-800), start * 1.4, delta=1)
        self.shot("sidebar_wide")
        canvas = self.page.locator("#canvas").bounding_box()
        self.assertAlmostEqual(canvas["x"] + canvas["width"], self.page.locator("#inspector").bounding_box()["x"], delta=1)
        self.page.reload()
        expect(self.page.locator("#inspector")).to_be_visible()
        self.assertAlmostEqual(self.page.locator("#inspector").bounding_box()["width"], start * 1.4, delta=1)
        self.assertAlmostEqual(self.drag_sidebar(800), start, delta=1)

    def test_sidebar_resizes_with_the_keyboard(self):
        self.new_flow()
        start = self.page.locator("#inspector").bounding_box()["width"]
        self.page.locator("#inspector-resize").focus()
        self.page.keyboard.press("ArrowLeft")
        self.assertGreater(self.page.locator("#inspector").bounding_box()["width"], start)

    def test_secret_fields_show_example_names(self):
        self.page.click("#nav-secrets")
        expect(self.page.locator("#secret-name")).to_have_attribute("placeholder", "your_account")
        expect(self.page.locator("#secret-value")).to_have_attribute("placeholder", "your_password")

    def test_reorder_by_dragging(self):
        self.build_three()
        nodes = self.page.locator("#canvas .node")
        first = nodes.nth(0).bounding_box()
        last = nodes.nth(2).bounding_box()
        self.page.mouse.move(last["x"] + 30, last["y"] + 20)
        self.page.mouse.down()
        self.page.mouse.move(first["x"] + 4, first["y"] + 20, steps=8)
        self.page.mouse.up()
        expect(nodes.nth(0)).to_contain_text("Click")

    def test_lasso_makes_loop_and_second_loop_is_refused(self):
        self.build_three()
        nodes = "#canvas .node"
        self.lasso(f"{nodes} >> nth=1", f"{nodes} >> nth=2")
        expect(self.page.locator(".selection-bar")).to_contain_text("Selected blocks: 2")
        self.shot("lasso")
        self.page.click("#make-loop")
        expect(self.page.locator(".loop-frame .node")).to_have_count(2)
        self.shot("loop")

        self.drag_tool("goto")
        expect(self.page.locator("#canvas .node")).to_have_count(4)
        self.page.evaluate("window.currentEditor.selectMany(['s1', 's5'])")
        expect(self.page.locator("#loop-problem")).to_contain_text("one loop")
        expect(self.page.locator("#make-loop")).to_be_disabled()

    def test_lasso_rejects_non_adjacent_blocks(self):
        self.new_flow()
        # A narrow window forces a wrap, so a box can catch blocks 1 and 3 but not 2.
        self.page.set_viewport_size({"width": 700, "height": 800})
        for kind in ("goto", "fill", "click", "read"):
            self.page.locator(f".tool[data-kind={kind}]").click()
        editor = "window.currentEditor"
        self.page.evaluate(f"{editor}.selectMany(['s1', 's3'])")
        expect(self.page.locator("#loop-problem")).to_contain_text("next to each other")
        expect(self.page.locator("#make-loop")).to_be_disabled()

    def test_remove_loop_keeps_its_blocks(self):
        self.build_three()
        self.lasso("#canvas .node >> nth=0", "#canvas .node >> nth=2")
        self.page.click("#make-loop")
        self.page.click("#remove-loop")
        expect(self.page.locator(".loop-frame")).to_have_count(0)
        expect(self.page.locator("#canvas .node")).to_have_count(3)

    def test_drag_into_loop(self):
        self.build_three()
        self.lasso("#canvas .node >> nth=0", "#canvas .node >> nth=1")
        self.page.click("#make-loop")
        self.drag_tool("verify", ".loop-frame [data-lane=loop]")
        expect(self.page.locator(".loop-frame .node")).to_have_count(3)

    def test_delete_with_keyboard_does_not_fire_while_typing(self):
        self.build_three()
        self.page.locator("#canvas .node").nth(0).click()
        self.page.locator("[data-field=url]").fill("https://example.test")
        self.page.locator("[data-field=url]").press("Delete")
        expect(self.page.locator("#canvas .node")).to_have_count(3)
        self.page.locator("#canvas .node").nth(0).click()
        self.page.locator("#canvas .node").nth(0).focus()
        self.page.keyboard.press("Delete")
        expect(self.page.locator("#canvas .node")).to_have_count(2)

    def test_incomplete_flow_is_blocked_with_reasons(self):
        self.build_three()
        self.page.click("#run-full")
        expect(self.page.locator(".modal")).to_contain_text("Cannot run yet")
        expect(self.page.locator("#issues")).to_contain_text("has no link")
        expect(self.page.locator("[data-action=confirm-run]")).to_have_count(0)

    def test_count_loop_has_run_count_on_the_bar(self):
        self.build_three()
        self.lasso("#canvas .node >> nth=1", "#canvas .node >> nth=2")
        self.page.click("#make-loop")
        self.page.select_option("#loop-source", "count")
        self.page.fill("#run-count", "7")
        expect(self.page.locator(".loop-head")).to_contain_text("7 time(s)")
        self.page.wait_for_timeout(800)
        loop = next(step for step in self.saved_flow()["steps"] if step["do"] == "loop")
        self.assertEqual(loop["count"], 7)
        self.assertEqual(loop["source"], "count")

    def test_scan_pasted_page_lists_elements(self):
        self.build_three()
        self.page.locator("#canvas .node").nth(1).click()
        self.page.click("[data-tab=page]")
        self.page.fill("#paste-page", "<html><body><label for='a'>Tên</label><input id='a'><button>Lưu</button></body></html>")
        rows = self.page.locator(".element-row")
        expect(rows).to_have_count(2)
        self.shot("page_scan")
        rows.nth(0).click()
        expect(self.page.locator("#target-selector")).to_have_value("#a")

    def test_secret_values_are_never_shown_again(self):
        self.page.click("#nav-secrets")
        self.page.fill("#secret-name", "ui_test")
        self.page.fill("#secret-value", "gia-tri-bi-mat")
        self.page.click("#secret-save")
        # The name may be listed already from an earlier test, so wait for this save itself.
        expect(self.page.locator(".toast")).to_contain_text("Saved")
        expect(self.page.locator(".modal table.data")).to_contain_text("ui_test")
        self.assertNotIn("gia-tri-bi-mat", self.page.content())
        self.assertEqual(secret_store.get_secret("ui_test"), "gia-tri-bi-mat")
        stored = (Path(DATA) / "secrets.json").read_text(encoding="utf-8")
        self.assertNotIn("gia-tri-bi-mat", stored)

    def test_recording_from_the_ui(self):
        self.new_flow()
        self.page.click("#start-record")
        self.page.locator(".modal input").fill(f"http://127.0.0.1:{self.port}/demo/login")
        self.page.click("[data-action=confirm]")
        expect(self.page.locator("#record-live")).to_contain_text("Open http", timeout=60000)
        self.assert_buttons_are_english()
        self.page.click("[data-action=stop-record]")
        expect(self.page.locator("#record-review")).to_contain_text("Open", timeout=30000)
        self.shot("record_review")
        self.assert_buttons_are_english()
        self.page.click("[data-action=accept-record]")
        expect(self.page.locator("#canvas .node")).to_have_count(1)

    def test_check_target_on_the_real_page(self):
        self.new_flow()
        self.page.locator(".tool[data-kind=goto]").click()
        self.page.locator("[data-field=url]").fill(f"http://127.0.0.1:{self.port}/demo/login")
        self.page.locator(".tool[data-kind=fill]").click()
        self.page.fill("#target-selector", "#user")
        self.page.click("#check-target")
        expect(self.page.locator("#check-result")).to_contain_text("exactly 1 element", timeout=60000)
        expect(self.page.locator("#verified-badge")).to_contain_text("Checked")
        self.page.fill("#target-selector", "#khong_co")
        expect(self.page.locator("#verified-badge")).to_contain_text("Not checked")
        self.page.click("#check-target")
        expect(self.page.locator("#check-result")).to_contain_text("Matched 0", timeout=30000)

    def test_panel_has_no_iframe_or_fallback_details(self):
        self.new_flow()
        self.page.locator(".tool[data-kind=click]").click()
        self.page.fill("#paste-element", '<button id="save" name="save" class="primary">Lưu</button>')
        expect(self.page.locator("#target-selector")).to_have_value("#save")
        panel = self.page.locator("#inspector")
        self.assertNotIn("Iframe", panel.inner_text())
        self.assertNotIn("Fallback", panel.inner_text())
        expect(self.page.locator("#check-url")).to_have_count(0)

        # Typing another element drops what was found for the old one.
        self.page.fill("#target-selector", "#other")
        self.page.wait_for_timeout(800)
        target = self.saved_flow()["steps"][0]["target"]
        self.assertEqual(target["fallbacks"], [])
        self.assertEqual(target["frames"], [])

    def test_value_source_separates_text_and_excel(self):
        self.new_flow()
        self.page.locator(".tool[data-kind=fill]").click()
        expect(self.page.locator("[data-mode=text]")).to_have_class("on")
        self.page.locator("[data-field=value]").fill("Nội dung cố định")

        self.page.click("[data-mode=excel]")
        expect(self.page.locator("[data-field=value]")).to_have_count(0)
        expect(self.page.locator("#column-preview")).to_contain_text("C is column C")
        expect(self.page.locator("#needs-loop")).to_be_visible()
        self.page.locator("[data-column=value]").fill("c")
        expect(self.page.locator("[data-column=value]")).to_have_value("C")
        self.page.wait_for_timeout(800)
        self.assertEqual(self.saved_flow()["steps"][0]["value"], "{{C}}")

        # Coming back to the block shows where its value comes from.
        self.page.locator(".tool[data-kind=click]").click()
        self.page.locator("#canvas .node").nth(0).click()
        expect(self.page.locator("[data-mode=excel]")).to_have_class("on")
        expect(self.page.locator("[data-column=value]")).to_have_value("C")

        self.page.click("[data-mode=text]")
        expect(self.page.locator("[data-field=value]")).to_have_value("")

    def test_links_and_values_from_excel_stay_together_row_by_row(self):
        from openpyxl import Workbook, load_workbook

        login = f"http://127.0.0.1:{self.port}/demo/login"
        folder = Path(DATA) / "links"
        folder.mkdir(exist_ok=True)
        path = folder / "links.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Link", "Nội dung", "Đã đọc"])
        for number in (1, 2, 3):
            sheet.append([f"{login}?hang={number}", f"Giá trị {number}"])
        # The run ends at the first empty link, whatever comes after it.
        sheet.append([None, "Sau ô trống"])
        sheet.append([f"{login}?hang=5", "Không được chạy"])
        workbook.save(path)
        workbook.close()

        self.new_flow()
        self.page.locator(".tool[data-kind=goto]").click()
        self.page.click("[data-mode=excel]")
        self.page.locator("[data-column=url]").fill("A")
        self.page.locator(".tool[data-kind=fill]").click()
        self.page.fill("#target-selector", "#user")
        self.page.click("[data-mode=excel]")
        self.page.locator("[data-column=value]").fill("B")
        self.page.locator(".tool[data-kind=read]").click()
        self.page.fill("#target-selector", "#user")
        self.page.select_option("[data-field=read-mode]", "column")
        self.page.locator("[data-field=save_to_column]").fill("C")
        expect(self.page.locator("#canvas .node").nth(0)).to_contain_text("Excel, column A")

        self.lasso("#canvas .node >> nth=0", "#canvas .node >> nth=2")
        self.page.click("#make-loop")
        # Blocks that use a column make the loop follow Excel without being asked.
        expect(self.page.locator("#loop-source")).to_have_value("excel")
        self.page.evaluate("path => window.currentEditor.loadExcel(path)", str(path))
        expect(self.page.locator("#row-count")).to_have_text("Runs until column A ends (first empty cell)")

        self.page.click("#run-full")
        expect(self.page.locator("#confirm-total")).to_contain_text("3")
        self.page.click("[data-action=confirm-run]")
        expect(self.page.locator("#run-headline")).to_have_text("Finished.", timeout=90000)
        expect(self.page.locator("[data-count='OK']")).to_have_text("3")
        result = load_workbook(self.page.locator("#result-file").inner_text())
        self.assertEqual([result.active[f"C{row}"].value for row in (2, 3, 4)], ["Giá trị 1", "Giá trị 2", "Giá trị 3"])
        self.assertIsNone(result.active["C6"].value)
        result.close()


class LiveCheckTests(AppTestCase):
    def demo_block(self, step_id):
        self.page.click("#open-demo")
        expect(self.page.locator("#excel-name")).to_contain_text("example_data.xlsx")
        self.page.locator(f"#canvas .node[data-step={step_id}]").click()

    def test_check_runs_the_blocks_before_it_without_doing_the_action(self):
        # The form behind the login blocks: this could never be found before.
        self.demo_block("s9")
        expect(self.page.locator("#check-target")).to_have_css("background-color", "rgb(15, 143, 120)")
        self.page.click("#check-target")
        modal = self.page.locator(".modal")
        expect(modal).to_contain_text("input.login_button")
        self.shot("check_confirm")
        self.page.click("[data-action=confirm]")
        expect(self.page.locator("#check-result")).to_contain_text("exactly 1 element", timeout=60000)
        expect(self.page.locator("#verified-badge")).to_contain_text("Checked")
        self.shot("check_ok")

        # The next block would really press Save, so that is asked about first, and can be declined.
        self.page.locator("#canvas .node[data-step=s10]").click()
        self.page.click("#check-target")
        expect(modal).to_contain_text('input[name="SUBMIT"]')
        self.page.get_by_role("button", name="Cancel").click()
        expect(self.page.locator("#check-target")).to_be_enabled()
        expect(self.page.locator("#check-result")).to_have_count(0)

    def test_failed_block_before_the_target_is_named_and_marked(self):
        self.demo_block("s2")
        self.page.fill("#target-selector", "#khong_co")
        self.page.locator("#canvas .node[data-step=s4]").click()
        self.page.click("#check-target")
        expect(self.page.locator("#check-result")).to_contain_text("Stopped at block 2", timeout=60000)
        expect(self.page.locator("#check-result")).to_contain_text("#khong_co")
        expect(self.page.locator("#canvas .node[data-step=s2]")).to_have_class(re.compile("failed"))
        expect(self.page.locator("#verified-badge")).to_contain_text("Checked")
        self.shot("check_failed")

    def test_incomplete_block_stops_before_opening_anything(self):
        self.new_flow()
        for kind in ("goto", "select", "fill"):
            self.page.locator(f".tool[data-kind={kind}]").click()
        self.page.fill("#target-selector", "#user")
        self.page.click("#check-target")
        expect(self.page.locator("#check-result")).to_contain_text("Block 1 (Open link)")
        expect(self.page.locator("#canvas .node[data-step=s1]")).to_have_class(re.compile("failed"))

    def test_manual_block_waits_for_continue_and_can_be_stopped(self):
        self.new_flow()
        self.page.locator(".tool[data-kind=goto]").click()
        self.page.locator("[data-field=url]").fill(f"http://127.0.0.1:{self.port}/demo/login")
        self.page.locator(".tool[data-kind=manual_pause]").click()
        self.page.locator("[data-field=message]").fill("Nhập mã xác thực")
        self.page.locator(".tool[data-kind=fill]").click()
        self.page.fill("#target-selector", "#user")

        self.page.click("#check-target")
        expect(self.page.locator(".modal")).to_contain_text("Nhập mã xác thực", timeout=60000)
        self.assert_buttons_are_english()
        self.page.click("[data-action=inspect-stop]")
        expect(self.page.locator("#check-result")).to_contain_text("Stopped", timeout=30000)

        self.page.click("#check-target")
        expect(self.page.locator("#inspect-continue")).to_be_visible(timeout=30000)
        self.page.click("#inspect-continue")
        expect(self.page.locator("#check-result")).to_contain_text("exactly 1 element", timeout=30000)


class RunHighlightTests(AppTestCase):
    def node(self, step_id):
        return self.page.locator(f"#run-flow .node[data-step={step_id}]")

    def test_blocks_light_up_reset_each_round_and_failed_block_turns_red(self):
        self.page.click("#open-demo")
        expect(self.page.locator("#excel-name")).to_contain_text("example_data.xlsx")
        self.page.click("#run-full")
        self.page.click("[data-action=confirm-run]")
        # Row 4 waits 10 seconds on the block that fails, so this moment can be looked at.
        self.page.wait_for_selector("#run-rows tr[data-row='3']", timeout=60000)
        expect(self.node("s10")).to_have_class(re.compile("running"), timeout=30000)
        expect(self.node("s7")).to_have_class(re.compile("lit"))
        expect(self.node("s9")).to_have_class(re.compile("lit"))
        expect(self.node("s11")).not_to_have_class(re.compile("lit|running"))
        expect(self.node("s1")).to_have_class(re.compile("lit"))
        self.shot("run_lit")
        expect(self.node("s10")).to_have_class(re.compile("failed"), timeout=30000)
        self.shot("run_row_failed")
        expect(self.page.locator("#run-headline")).to_have_text("Finished.", timeout=60000)
        # The last round ended well, so everything is back to normal.
        expect(self.page.locator("#run-flow .node.lit")).to_have_count(0)
        expect(self.page.locator("#run-flow .node.running")).to_have_count(0)
        expect(self.page.locator("#run-flow .node.failed")).to_have_count(0, timeout=5000)

    def test_block_that_stops_the_run_stays_red(self):
        self.page.click("#open-demo")
        expect(self.page.locator("#excel-name")).to_contain_text("example_data.xlsx")
        self.page.locator("#canvas .node[data-step=s3]").click()
        self.page.fill("#target-selector", "#khong_co")
        self.page.wait_for_timeout(800)
        self.page.click("#run-full")
        self.page.click("[data-action=confirm-run]")
        expect(self.page.locator("#run-headline")).to_have_text("Stopped by an error.", timeout=60000)
        expect(self.node("s3")).to_have_class(re.compile("failed"))
        expect(self.node("s2")).to_have_class(re.compile("lit"))
        expect(self.node("s4")).not_to_have_class(re.compile("lit|running|failed"))
        self.shot("run_stopped_red")


class SystemLogTests(AppTestCase):
    def open_log(self):
        self.page.locator(".log-toggle:visible").click()
        expect(self.page.locator("#log-panel")).to_be_visible()

    def test_log_button_sits_above_select_excel_and_clears(self):
        self.page.click("#open-demo")
        expect(self.page.locator("#excel-name")).to_contain_text("example_data.xlsx")
        button = self.page.locator(".log-toggle:visible").bounding_box()
        excel = self.page.locator("#pick-excel").bounding_box()
        self.assertLessEqual(button["y"] + button["height"], excel["y"])
        self.assertLess(abs(button["x"] - excel["x"]), 40)
        self.assertLess(excel["y"] - (button["y"] + button["height"]), 40)
        self.shot("log_button")

        self.open_log()
        expect(self.page.locator("#log-lines")).to_contain_text("started")
        self.assert_buttons_are_english()
        self.shot("log_panel")
        self.page.click("#log-clear")
        expect(self.page.locator("#log-empty")).to_be_visible()
        expect(self.page.locator("#log-lines div")).to_have_count(0)
        self.assertFalse((Path(DATA) / "system.log").exists())

        # New lines keep arriving after a clear.
        self.page.click("#log-close")
        expect(self.page.locator("#log-panel")).to_have_count(0)
        self.open_log()
        self.page.click("#run-full")
        self.page.click("[data-action=confirm-run]")
        expect(self.page.locator("#log-lines")).to_contain_text("Started running", timeout=30000)
        expect(self.page.locator("#run-headline")).to_have_text("Finished.", timeout=90000)
        expect(self.page.locator("#log-lines")).to_contain_text("Row 4")
        expect(self.page.locator("#log-lines")).to_contain_text("Ended")
        expect(self.page.locator("#log-lines .error").first).to_contain_text("Failed")
        self.shot("log_run")

        # A filled password never reaches the log, on screen or on disk.
        self.assertNotIn("demo-password", self.page.locator("#log-lines").inner_text())
        self.assertNotIn("demo-password", (Path(DATA) / "system.log").read_text(encoding="utf-8"))
        self.assertNotIn("New name 1", (Path(DATA) / "system.log").read_text(encoding="utf-8"))

    def test_log_is_available_on_other_screens(self):
        self.open_log()
        expect(self.page.locator("#log-panel")).to_be_visible()
        self.page.click("#nav-setup")
        expect(self.page.locator("#log-panel")).to_be_visible()


class SetupAndSecurityTests(AppTestCase):
    def test_setup_screen(self):
        self.page.click("#nav-setup")
        expect(self.page.locator("[data-item=libraries]")).to_contain_text("Pass", timeout=60000)
        expect(self.page.locator("[data-item=browser]")).to_contain_text("Pass")
        self.page.click("#setup-test")
        expect(self.page.locator("[data-item=recording]")).to_contain_text("Pass", timeout=120000)
        expect(self.page.locator("[data-item=browser_control]")).to_contain_text("Pass")
        expect(self.page.locator("[data-item=credentials]")).to_contain_text("Pass")
        self.assertNotIn("null", self.page.locator("#setup-page").inner_text())
        self.shot("setup")
        self.assert_buttons_are_english()

    def status_of(self, path, headers=None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        try:
            with NO_PROXY.open(request, timeout=10) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    def test_requests_without_key_are_refused(self):
        self.assertEqual(self.status_of("/"), 403)
        self.assertEqual(self.status_of("/api/flows"), 403)
        self.assertEqual(self.status_of(f"/api/flows?key=sai"), 403)
        self.assertEqual(self.status_of(f"/api/flows?key={self.key}"), 200)

    def test_foreign_host_and_origin_are_refused(self):
        self.assertEqual(self.status_of(f"/api/flows?key={self.key}", {"Host": "evil.test"}), 403)
        self.assertEqual(self.status_of(f"/api/flows?key={self.key}", {"Origin": "http://evil.test"}), 403)

    def test_ui_paths_cannot_leave_the_ui_folder(self):
        self.assertIn(self.status_of(f"/ui/..%2f..%2fserver.py?key={self.key}"), (403, 404))
        self.assertIn(self.status_of(f"/ui/../../server.py?key={self.key}"), (403, 404))

    def test_many_connections_at_once_are_all_served(self):
        results = []
        address = f"http://127.0.0.1:{self.port}/api/ping?key={self.key}"

        def ping():
            try:
                with NO_PROXY.open(address, timeout=20) as response:
                    results.append(response.status)
            except Exception as error:
                results.append(str(error))

        threads = [threading.Thread(target=ping) for _ in range(60)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results, [200] * 60)

    def test_second_start_reuses_running_server(self):
        second = start_app()
        output, _ = second.communicate(timeout=30)
        self.assertEqual(second.returncode, 0, output)
        self.assertIn("already running", output)
        self.assertEqual(json.loads((Path(DATA) / "server.json").read_text())["port"], self.port)

    def test_home_lists_flows_and_flow_can_be_deleted(self):
        self.new_flow("Sẽ xóa")
        self.page.click(".brand")
        row = self.page.locator(".flow-item", has_text="Sẽ xóa")
        expect(row).to_have_count(1)
        row.locator("button.danger").click()
        self.page.click("[data-action=confirm]")
        expect(self.page.locator(".flow-item", has_text="Sẽ xóa")).to_have_count(0)

    def test_sample_flow_opens_directly_and_stays_out_of_the_list(self):
        self.new_flow("Của tôi")
        self.page.click(".brand")
        self.page.click("#open-demo")
        expect(self.page.locator("#excel-name")).to_contain_text("example_data.xlsx")
        expect(self.page.locator("#canvas .node").first).to_be_visible()
        self.page.click(".brand")
        expect(self.page.locator(".flow-item", has_text="Của tôi")).to_have_count(1)
        expect(self.page.locator(".flow-item[data-flow=demo]")).to_have_count(0)
        self.assertNotIn("Quy trình mẫu:", self.page.locator("#view").inner_text())

    def test_new_flow_from_template_creates_the_blocks_at_once(self):
        self.page.click("#new-flow")
        self.shot("new_flow_templates")
        self.assert_buttons_are_english()
        self.assertEqual(self.page.locator("[data-template]").count(), 4)
        self.page.click("[data-template=login_excel]")
        expect(self.page.locator("#canvas .node").first).to_be_visible()
        expect(self.page.locator(".modal")).to_have_count(0)
        self.shot("template_login_excel")
        flow = self.saved_flow()
        self.assertEqual(flow["name"], "Login + Excel rows")
        self.assertEqual([step["do"] for step in flow["steps"]], ["goto", "login", "loop"])
        self.assertEqual(flow["steps"][1]["password"], "{{secret:your_password}}")
        self.assertEqual(flow["excel"]["required"], ["A", "B"])
        self.assertEqual([step["do"] for step in flow["steps"][-1]["steps"]], ["goto", "fill", "click"])
        # A blank block says what it is for, and its form opens without trouble.
        expect(self.page.locator("#canvas .node").nth(0)).to_contain_text("Login page link")
        self.page.locator("#canvas .node").nth(3).click()
        expect(self.page.locator("#target-selector")).to_be_visible()

    def test_template_uses_the_typed_name(self):
        self.page.click("#new-flow")
        self.page.locator(".modal input").fill("Đọc giá")
        self.page.click("[data-template=read_excel]")
        expect(self.page.locator("#canvas .node").first).to_be_visible()
        self.assertEqual(self.saved_flow()["name"], "Đọc giá")

    def test_double_click_on_a_template_creates_one_flow(self):
        folder = Path(DATA) / "flows"
        before = len(list(folder.glob("*.json")))
        self.page.click("#new-flow")
        # Both clicks reach the page before the first answer comes back.
        self.page.evaluate("const b = document.querySelector('[data-template=repeat_clicks]'); b.click(); b.click();")
        expect(self.page.locator("#canvas .node").first).to_be_visible()
        self.page.wait_for_timeout(500)
        self.assertEqual(len(list(folder.glob("*.json"))), before + 1)

    def test_enter_in_the_name_box_creates_a_blank_flow(self):
        self.page.click("#new-flow")
        self.page.locator(".modal input").fill("Trống")
        self.page.locator(".modal input").press("Enter")
        expect(self.page.locator("#canvas")).to_be_visible()
        flow = self.saved_flow()
        self.assertEqual((flow["name"], flow["steps"]), ("Trống", []))


class WaitBlockTests(AppTestCase):
    def test_block_defaults_to_three_seconds_and_takes_a_typed_value(self):
        self.new_flow()
        self.page.locator(".tool[data-kind=wait]").click()
        node = self.page.locator("#canvas .node").first
        expect(node).to_contain_text("Wait")
        expect(node).to_contain_text("3 s")
        expect(self.page.locator("#wait-seconds")).to_have_value("3")
        self.page.fill("#wait-seconds", "1.5")
        expect(node).to_contain_text("1.5 s")
        self.assertEqual(self.saved_flow()["steps"][0]["seconds"], 1.5)
        self.page.fill("#wait-seconds", "")
        expect(node).to_contain_text("3 s")
        self.assertEqual(self.saved_flow()["steps"][0]["seconds"], 3)

    def test_run_counts_down_on_the_block(self):
        self.new_flow()
        self.page.locator(".tool[data-kind=goto]").click()
        self.page.locator("[data-field=url]").fill(f"http://127.0.0.1:{self.port}/demo/login")
        self.page.locator(".tool[data-kind=wait]").click()
        self.page.fill("#wait-seconds", "4")
        self.saved_flow()
        self.page.click("#run-full")
        self.page.click("[data-action=confirm-run]")
        node = self.page.locator("#run-flow .node[data-step=s2]")
        expect(node).to_contain_text("left", timeout=60000)
        expect(self.page.locator("#run-current")).to_contain_text("left")
        self.shot("wait_countdown")
        first = int(re.search(r"(\d+) s left", node.inner_text()).group(1))
        expect(node).not_to_contain_text(f"{first} s left", timeout=3000)
        expect(self.page.locator("#run-headline")).to_have_text("Finished.", timeout=30000)


class RunningFlowTests(AppTestCase):
    def start_long_run(self):
        self.new_flow()
        self.page.locator(".tool[data-kind=goto]").click()
        self.page.locator("[data-field=url]").fill(f"http://127.0.0.1:{self.port}/demo/login")
        self.page.locator(".tool[data-kind=wait]").click()
        self.page.fill("#wait-seconds", "300")
        self.saved_flow()
        self.page.click("#run-full")
        self.page.click("[data-action=confirm-run]")
        expect(self.page.locator("#run-flow .node[data-step=s2]")).to_contain_text("left", timeout=60000)

    def test_leaving_a_running_flow_keeps_a_way_back_and_a_way_to_stop(self):
        self.start_long_run()
        run_url = self.page.url
        expect(self.page.locator("#active-job")).to_be_hidden()
        self.page.click("text=Back to flow")
        expect(self.page.locator("#canvas")).to_be_visible()
        expect(self.page.locator("#active-job")).to_be_visible(timeout=10000)
        self.assert_buttons_are_english()

        # Run does not just refuse: it offers both ways out.
        self.page.click("#run-full")
        self.page.click("[data-action=open-active]")
        expect(self.page.locator("#run-headline")).to_be_visible()
        self.assertEqual(self.page.url, run_url)

        self.page.click(".brand")
        self.page.click("#active-job")
        self.page.click("[data-action=stop-active]")
        expect(self.page.locator("#active-job")).to_be_hidden(timeout=30000)

        self.new_flow("Sau khi dừng")
        self.page.locator(".tool[data-kind=goto]").click()
        self.page.locator("[data-field=url]").fill(f"http://127.0.0.1:{self.port}/demo/login")
        self.saved_flow()
        self.page.click("#run-full")
        self.page.click("[data-action=confirm-run]")
        expect(self.page.locator("#run-headline")).to_have_text("Finished.", timeout=60000)

    def test_reloading_the_run_page_still_shows_the_run_and_stop_now_works(self):
        self.start_long_run()
        self.page.reload()
        expect(self.page.locator("#run-flow")).to_be_visible()
        self.page.click("#run-stop-now")
        expect(self.page.locator("#run-headline")).to_have_text("Stopped as requested.", timeout=30000)


class FinderTests(AppTestCase):
    def test_how_to_find_it_shares_a_box_with_the_other_ways(self):
        self.new_flow()
        self.page.locator(".tool[data-kind=click]").click()
        self.page.fill("#paste-element", '<button id="save" name="save" class="primary">Save</button>')
        box = self.page.locator("#finder-box")
        expect(box.locator("#target-selector")).to_be_visible()
        expect(box.locator(".candidate").first).to_be_visible()
        self.assertGreater(box.locator(".candidate").count(), 1)
        self.shot("finder_box")
        box.locator(".candidate").nth(1).click()
        chosen = self.page.locator("#finder-box .candidate.on code").inner_text()
        expect(self.page.locator("#target-selector")).to_have_value(chosen)


class ToolHelpTests(AppTestCase):
    def test_hovering_a_block_explains_it_and_leaving_removes_it(self):
        self.new_flow()
        help_box = self.page.locator("#tool-help")
        expect(help_box).to_have_count(0)
        self.page.locator(".tool[data-kind=fill]").hover()
        expect(help_box).to_be_visible()
        expect(help_box).to_contain_text("Fills a value")
        self.shot("tool_help")
        self.page.locator(".tool[data-kind=wait]").hover()
        expect(help_box).to_have_count(1)
        expect(help_box).to_contain_text("Pauses")
        self.page.locator("#canvas").hover()
        expect(help_box).to_have_count(0)

    def test_every_block_and_action_in_the_column_has_help(self):
        self.new_flow()
        help_box = self.page.locator("#tool-help")
        tools = self.page.locator(".tool")
        seen = set()
        for index in range(tools.count()):
            tools.nth(index).hover()
            expect(help_box).to_be_visible()
            seen.add(help_box.inner_text())
        self.assertEqual(len(seen), tools.count())
        for selector in ("#start-record", "#clear-all"):
            # A disabled button gets no pointer events, so give Clear all something to clear.
            self.page.locator(".tool[data-kind=goto]").click()
            self.page.locator(selector).hover()
            expect(help_box).to_be_visible()

    def test_help_stays_on_screen_and_does_not_get_in_the_way_of_dragging(self):
        self.new_flow()
        tool = self.page.locator(".tool[data-kind=manual_pause]")
        tool.hover()
        box = self.page.locator("#tool-help").bounding_box()
        self.assertLessEqual(box["y"] + box["height"], self.page.viewport_size["height"])
        self.assertGreaterEqual(box["x"], tool.bounding_box()["x"] + tool.bounding_box()["width"])
        self.page.mouse.down()
        expect(self.page.locator("#tool-help")).to_have_count(0)
        self.page.mouse.up()

    def test_keyboard_focus_shows_help(self):
        self.new_flow()
        self.page.locator(".tool[data-kind=click]").focus()
        expect(self.page.locator("#tool-help")).to_contain_text("Clicks")
        self.page.locator("#flow-name").focus()
        expect(self.page.locator("#tool-help")).to_have_count(0)


class LanguageTests(AppTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vi = json.loads((ROOT / "app" / "web_handler" / "ui" / "lang" / "vi.json").read_text(encoding="utf-8"))
        cls.long_english = [
            key for key, value in cls.vi.items()
            if key != value and "{" not in key and len(key.split()) >= 3
        ]

    def tearDown(self):
        self.set_language("en")
        super().tearDown()

    def set_language(self, language):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/language?key={self.key}", method="PUT",
            data=json.dumps({"language": language}).encode("utf-8"), headers={"Content-Type": "application/json"},
        )
        with NO_PROXY.open(request, timeout=10):
            pass

    def choose(self, language):
        self.page.select_option("#language", language)
        expect(self.page.locator("html")).to_have_attribute("lang", language)

    def assert_all_english(self, screen):
        # Each language keeps its own name in the picker.
        text = self.page.locator("body").inner_text().replace("Ti\u1ebfng Vi\u1ec7t", "")
        self.assertEqual(VIETNAMESE_LETTERS.findall(text), [], f"{screen}: {text[:600]}")

    def assert_all_vietnamese(self, screen):
        labels = self.page.locator(BUTTON_LABELS).all_inner_texts()
        left = [label for label in labels if label.strip() in self.vi and self.vi[label.strip()] != label.strip()]
        self.assertEqual(left, [], f"{screen}: buttons still in English")
        text = self.page.locator("body").inner_text()
        left = [key for key in self.long_english if key in text]
        self.assertEqual(left, [], f"{screen}: text still in English")

    def walk_screens(self, check):
        check("home")
        self.page.click("#new-flow")
        expect(self.page.locator("[data-template=blank]")).to_be_visible()
        check("new flow dialog")
        self.page.locator(".modal input").fill("Flow one")
        self.page.click("[data-template=login_excel]")
        expect(self.page.locator("#canvas .node").first).to_be_visible()
        check("builder")
        self.page.locator(".tool[data-kind=wait_for]").hover()
        self.shot(f"builder_{self.page.locator('html').get_attribute('lang')}")
        self.page.locator("#canvas .node").nth(3).click()
        check("fill panel")
        for tab in ("element", "page", "live"):
            self.page.click(f"[data-tab={tab}]")
            check(f"tab {tab}")
        self.page.click("[data-tab=element]")
        for kind in ("goto", "login", "click", "select", "check", "read", "wait_for", "wait", "verify", "manual_pause"):
            self.page.locator(f".tool[data-kind={kind}]").click()
            check(f"{kind} panel")
            # Pressing a block hides its help until the pointer comes back.
            self.page.locator("#flow-name").hover()
            self.page.locator(f".tool[data-kind={kind}]").hover()
            expect(self.page.locator("#tool-help")).to_be_visible()
            check(f"{kind} help")
        self.page.click("#run-full")
        expect(self.page.locator(".modal")).to_be_visible()
        check("issues dialog")
        self.page.click("[data-action=close]")
        self.page.click("#nav-secrets")
        expect(self.page.locator(".modal")).to_be_visible()
        check("secrets")
        self.page.click("[data-action=close]")
        self.page.click("#nav-setup")
        expect(self.page.locator("#setup-table")).to_be_visible(timeout=60000)
        check("setup")
        self.page.click(".brand")
        expect(self.page.locator("#open-demo")).to_be_visible()
        self.page.click("#open-demo")
        expect(self.page.locator("#excel-name")).to_contain_text("example_data.xlsx")
        check("sample flow")
        self.page.click("#run-full")
        expect(self.page.locator("[data-action=confirm-run]")).to_be_visible()
        check("confirm run")
        self.page.click("[data-action=confirm-run]")
        expect(self.page.locator("#run-counts")).to_be_visible()
        self.page.wait_for_selector("#result-file", timeout=120000)
        check("finished run")
        self.page.locator(".btn.log-toggle").first.click()
        expect(self.page.locator("#log-panel")).to_be_visible()

    def test_app_starts_in_english_and_every_screen_is_english(self):
        expect(self.page.locator("html")).to_have_attribute("lang", "en")
        expect(self.page.locator("#language")).to_have_value("en")
        self.walk_screens(self.assert_all_english)
        self.assert_all_english("log")

    def test_vietnamese_covers_every_screen(self):
        expect(self.page.locator("#new-flow")).to_be_visible()
        # Lines already in the log stay in the language they were written in.
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/log?key={self.key}", method="DELETE")
        with NO_PROXY.open(request, timeout=10):
            pass
        self.choose("vi")
        expect(self.page.locator("#new-flow")).to_have_text(self.vi["+ New flow"])
        self.walk_screens(self.assert_all_vietnamese)
        self.assert_all_vietnamese("log")

    def test_choice_is_remembered_after_reloading(self):
        expect(self.page.locator("#new-flow")).to_be_visible()
        self.choose("vi")
        self.page.reload()
        expect(self.page.locator("#language")).to_have_value("vi")
        expect(self.page.locator("#new-flow")).to_have_text(self.vi["+ New flow"])
        self.choose("en")
        expect(self.page.locator("#new-flow")).to_have_text("+ New flow")

    def test_results_and_messages_from_a_run_follow_the_language(self):
        from openpyxl import load_workbook

        expect(self.page.locator("#new-flow")).to_be_visible()
        self.choose("vi")
        expect(self.page.locator("#open-demo")).to_be_visible()
        self.page.click("#open-demo")
        expect(self.page.locator("#excel-name")).to_contain_text("example_data.xlsx")
        self.page.click("#run-full")
        self.page.click("[data-action=confirm-run]")
        expect(self.page.locator("#run-headline")).to_have_text(self.vi["Finished."], timeout=90000)
        expect(self.page.locator("[data-count='Failed']")).to_have_text("1")
        path = self.page.locator("#result-file").inner_text()
        workbook = load_workbook(path)
        sheet = workbook["Sheet1"]
        self.assertEqual(sheet["A1"].value, self.vi["Item ID"])
        self.assertTrue(sheet["D4"].value.startswith(self.vi["Failed"]))
        self.assertTrue(sheet["D5"].value.startswith(self.vi["Skipped"]))
        workbook.close()


if __name__ == "__main__":
    unittest.main()
