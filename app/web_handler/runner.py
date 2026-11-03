"""Runs a flow against a browser driver, row by row, and keeps a resumable journal."""
from dataclasses import dataclass, field
import json
from pathlib import Path
import time

from . import flow as flow_module
from . import i18n, templating
from .browser_errors import BrowserClosed, ElementError
from .i18n import t

STATUS_OK = "OK"
STATUS_FAILED = "Failed"
STATUS_SKIPPED = "Skipped"
STATUS_UNKNOWN = "Unclear"
# Journals written before the interface had two languages.
OLD_STATUSES = {
    "L\u1ed7i": STATUS_FAILED,
    "B\u1ecf qua": STATUS_SKIPPED,
    "Ch\u01b0a r\u00f5": STATUS_UNKNOWN,
}


class StepError(Exception):
    pass


class RunStopped(Exception):
    pass


class Journal:
    """One JSON line per event, flushed at once, so a crash loses nothing finished."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a", encoding="utf-8")

    def write(self, **record):
        self.file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


@dataclass
class JournalState:
    meta: dict = field(default_factory=dict)
    statuses: dict = field(default_factory=dict)
    messages: dict = field(default_factory=dict)
    written: dict = field(default_factory=dict)
    finished: bool = False


def load_journal(path):
    state = JournalState()
    started = set()
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return state
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue  # The last line may be cut off by a crash.
        kind = record.get("type")
        if kind == "start":
            state.meta = record
        elif kind == "row_start":
            started.add(record["row"])
        elif kind == "row_end":
            started.discard(record["row"])
            state.statuses[record["row"]] = OLD_STATUSES.get(record["status"], record["status"])
            # Saved in the language of that run, which may not be the one in use now.
            state.messages[record["row"]] = i18n.retranslate(record.get("message", ""))
            state.written[record["row"]] = record.get("written", {})
        elif kind == "finish":
            state.finished = True
    for row in started:
        state.statuses[row] = STATUS_UNKNOWN
        state.messages[row] = t("This row was running when the app stopped; it may already be written.")
    return state


def select_rows(rows, previous, mode, include_unknown=False):
    """Choose which rows this run works on. `previous` maps row number to status."""
    if mode == "resume":
        chosen = [row for row in rows if row.number not in previous]
    elif mode == "retry":
        chosen = [row for row in rows if previous.get(row.number) == STATUS_FAILED]
    else:
        chosen = list(rows)
    if include_unknown:
        wanted = {row.number for row in chosen}
        wanted |= {number for number, status in previous.items() if status == STATUS_UNKNOWN}
        chosen = [row for row in rows if row.number in wanted]
    if mode == "trial":
        for row in chosen:
            if not row.skip_reason:
                return [row]
        return []
    return chosen


class Runner:
    def __init__(
        self, flow, driver, emit, control, get_secret=None, rows=None, writer=None,
        journal=None, screenshot_dir=None, mode="full", previous=None,
    ):
        self.flow = flow
        self.driver = driver
        self.emit = emit
        self.control = control
        self.writer = writer
        self.journal = journal
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        self.rows = rows or []
        self.mode = mode
        self.previous = previous
        self.context = templating.Context(get_secret)
        settings = flow.get("settings") or {}
        self.step_timeout = settings.get("step_timeout_seconds", 15)
        self.navigation_timeout = settings.get("navigation_timeout_seconds", 60)
        self.pause_after = settings.get("pause_after_consecutive_failures", 3)
        self.counts = {STATUS_OK: 0, STATUS_FAILED: 0, STATUS_SKIPPED: 0, STATUS_UNKNOWN: 0}
        self.written = {}
        # Where the run is, so blocks inside an "If" block report like the ones around it.
        self.stage = None
        self.row_key = None
        self.current = None

    # Running one step

    def run_step(self, step):
        kind = step["do"]
        render = lambda name: templating.render(step.get(name), self.context)
        target = step.get("target")
        if kind == "goto":
            status = self.driver.goto(render("url"), self.navigation_timeout)
            if status and status >= 400:
                self.emit_log("warn", t("The page returned HTTP {status}.", status=status))
        elif kind == "login":
            selectors = {name: step.get(f"{name}_selector") or None for name in ("account", "password", "submit")}
            found = self.driver.login(render("account"), render("password"), selectors, self.step_timeout, self.navigation_timeout)
            if not found:
                self.emit_log("warn", t("There is no login form on this page, so the block assumes you are already logged in."))
        elif kind == "fill":
            self.driver.fill(target, render("value"), self.step_timeout)
        elif kind == "click":
            self.driver.click(
                target, self.step_timeout,
                self.navigation_timeout if step.get("wait_navigation") else None,
            )
        elif kind == "select":
            self.driver.select(target, render("value"), self.step_timeout)
        elif kind == "check":
            self.driver.check(target, bool(step.get("checked", True)), self.step_timeout)
        elif kind == "read":
            self.store_read(step, self.driver.read(target, self.step_timeout))
        elif kind == "ocr":
            text = self.driver.ocr(target, self.step_timeout)
            case = step.get("text_case")
            self.store_read(step, text.upper() if case == "upper" else text.lower() if case == "lower" else text)
        elif kind == "wait_for":
            self.driver.wait_for(target, self.step_timeout)
        elif kind == "wait":
            self.hold(step, step["seconds"])
        elif kind == "verify":
            self.verify(step, render)
        elif kind == "if":
            self.run_if(step, render)
        elif kind == "manual_pause":
            self.wait_for_user(step.get("message") or t("Do this by hand, then click Continue."))
        else:
            raise StepError(t("Unknown block type {kind}.", kind=repr(kind)))
        for fallback in self.driver.take_fallback_notices():
            self.emit_log("warn", t("Using a fallback way to find the element: {fallback}", fallback=fallback))
        if step.get("delay_after"):
            self.hold(step, step["delay_after"])

    def hold(self, step, seconds):
        self.emit({"type": "waiting", "step": step.get("id"), "seconds": seconds})
        if not self.control.wait_seconds(self.driver, seconds):
            raise RunStopped()

    def store_read(self, step, value):
        if step.get("save_to_var"):
            self.context.variables[step["save_to_var"]] = value
        column = step.get("save_to_column")
        if column:
            self.written[column] = value
            if self.writer and self.context.row is not None:
                self.writer.set_value(self.context.row, column, value)

    def verify(self, step, render):
        url = self.driver.url()
        if step.get("url_contains"):
            expected = render("url_contains")
            if expected not in url:
                raise StepError(t("The URL does not contain '{expected}'.", expected=expected))
        if step.get("url_not_contains"):
            expected = render("url_not_contains")
            if expected in url:
                raise StepError(t("The URL still contains '{expected}'.", expected=expected))
        if step.get("equals") not in (None, ""):
            expected = render("equals")
            actual = self.driver.read(step["target"], self.step_timeout)
            if actual != expected:
                raise StepError(t("Expected '{expected}', found '{actual}'.", expected=expected, actual=actual))
        if step.get("contains") not in (None, ""):
            expected = render("contains")
            actual = self.driver.read(step["target"], self.step_timeout)
            if expected not in actual:
                raise StepError(t("'{actual}' does not contain '{expected}'.", expected=expected, actual=actual))

    def condition_holds(self, step, render):
        """Asks the page, and never fails the row: a missing element is only an answer."""
        test = step.get("test")
        seconds = step.get("seconds") or flow_module.IF_WAIT_SECONDS
        if test == "exists":
            holds = self.driver.appears(step["target"], seconds)
        elif test in ("equals", "contains"):
            expected = render("value")
            try:
                actual = self.driver.read(step["target"], seconds)
            except ElementError:
                actual = None
            holds = actual is not None and (actual == expected if test == "equals" else expected in actual)
        elif test == "url_contains":
            holds = render("value") in self.driver.url()
        else:
            raise StepError(t('The "{name}" block has no condition.', name=t(flow_module.STEP_NAMES["if"])))
        return holds != bool(step.get("negate"))

    def run_if(self, step, render):
        holds = self.condition_holds(step, render)
        branch = "then" if holds else "else"
        event = {"type": "branch", "step": step.get("id"), "branch": branch}
        if self.row_key is not None:
            event["row"] = self.row_key
        self.emit(event)
        for child in step.get(branch) or []:
            self.announce(child)
            self.run_step(child)

    # Helpers

    def announce(self, step):
        self.current = step
        event = {"type": "step", "stage": self.stage, "step": step.get("id"), "label": flow_module.step_label(step)}
        if self.row_key is not None:
            event["row"] = self.row_key
        self.emit(event)

    def emit_log(self, level, text):
        self.emit({"type": "log", "level": level, "text": self.context.mask(text)})

    def wait_for_user(self, message):
        self.driver.show()
        self.emit({"type": "paused", "reason": self.context.mask(message)})
        decision = self.control.wait_decision(self.driver)
        self.emit({"type": "resumed", "decision": decision})
        if decision != "continue":
            raise RunStopped()

    def screenshot(self, name):
        if not self.screenshot_dir:
            return None
        try:
            path = self.screenshot_dir / f"{name}.png"
            self.driver.screenshot(path)
            return path.name
        except Exception:
            return None

    def run_plain_steps(self, steps, stage):
        self.stage = stage
        self.row_key = None
        for step in steps:
            if self.control.stop_requested:
                raise RunStopped()
            self.announce(step)
            self.run_step(step)

    # Running the rows

    def iterations(self, loop):
        if loop.get("source") == "excel":
            for index, row in enumerate(self.rows, 1):
                yield index, row
        else:
            total = 1 if self.mode == "trial" else int(loop.get("count", 1))
            for index in range(1, total + 1):
                yield index, None

    def total_iterations(self, loop):
        if loop is None:
            return 0
        if loop.get("source") == "excel":
            return len(self.rows)
        return 1 if self.mode == "trial" else int(loop.get("count", 1))

    def finish_row(self, key, row, status, message="", screenshot=None):
        self.counts[status] += 1
        message = self.context.mask(message)
        if self.journal:
            self.journal.write(
                type="row_end", row=key, status=status, message=message,
                written=self.written, screenshot=screenshot,
            )
        if self.writer and row is not None:
            text = t(status) if not message else f"{t(status)}: {message}"
            self.writer.set_status(row.number, text)
            self.writer.row_finished()
        self.emit({
            "type": "row_done", "row": key, "status": status,
            "message": message, "screenshot": screenshot,
        })

    def run_iteration(self, index, row, loop):
        key = row.number if row else index
        self.written = {}
        if row and row.skip_reason:
            self.finish_row(key, row, STATUS_SKIPPED, row.skip_reason)
            return None
        self.context.index = index
        self.context.row = row.number if row else None
        self.context.columns = row.values if row else {}
        if self.journal:
            self.journal.write(type="row_start", row=key)
        self.emit({"type": "row_start", "row": key, "index": index})
        self.stage = "loop"
        self.row_key = key
        self.current = None
        try:
            for step in loop.get("steps", []):
                self.announce(step)
                self.run_step(step)
        except BrowserClosed:
            self.finish_row(key, row, STATUS_UNKNOWN, t("The browser was closed partway through."))
            raise
        except RunStopped:
            self.finish_row(key, row, STATUS_UNKNOWN, t("Stopped during a wait."))
            raise
        except Exception as error:
            # The block inside an "If" block that failed, not the "If" block holding it.
            current = self.current
            label = flow_module.step_label(current) if current else ""
            message = f"{label}: {self.describe(error)}"
            shot = self.screenshot(f"row_{key}")
            self.finish_row(key, row, STATUS_FAILED, message, shot)
            return False
        self.finish_row(key, row, STATUS_OK)
        return True

    def describe(self, error):
        text = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
        return self.context.mask(text)[:300]

    def run_loop(self, loop):
        consecutive = 0
        for index, row in self.iterations(loop):
            if self.control.stop_requested:
                raise RunStopped()
            outcome = self.run_iteration(index, row, loop)
            if outcome is False:
                consecutive += 1
                if self.pause_after and consecutive >= self.pause_after:
                    self.wait_for_user(
                        t("{count} rows failed in a row. The login may have expired or the page may have changed.", count=consecutive)
                    )
                    consecutive = 0
            elif outcome is True:
                consecutive = 0

    def run(self):
        steps = self.flow.get("steps", [])
        loop = flow_module.find_loop(self.flow)
        loop_position = steps.index(loop) if loop else len(steps)
        outcome = "finished"
        message = ""
        self.emit({"type": "started", "total": self.total_iterations(loop)})
        try:
            try:
                self.run_plain_steps(steps[:loop_position], "before")
            except (RunStopped, BrowserClosed):
                raise
            except Exception as error:
                # Nothing has been touched yet, so nothing is worth continuing for.
                self.screenshot("before_loop")
                raise StepError(t("A block before the loop failed, so the run stopped: {error}", error=self.describe(error))) from error
            if loop:
                self.run_loop(loop)
                try:
                    self.run_plain_steps(steps[loop_position + 1:], "after")
                except (RunStopped, BrowserClosed):
                    raise
                except Exception as error:
                    self.screenshot("after_loop")
                    self.emit_log("error", t("A block after the loop failed: {error}", error=self.describe(error)))
                    outcome = "after_loop_failed"
                    message = self.describe(error)
        except RunStopped:
            outcome = "stopped"
        except BrowserClosed:
            outcome = "browser_closed"
            message = t("The browser was closed partway through.")
        except StepError as error:
            outcome = "failed"
            message = self.context.mask(str(error))
        return outcome, message
