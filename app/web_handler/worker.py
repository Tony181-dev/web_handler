"""Browser work in its own process. Reads JSON commands on stdin, writes events on stdout.

The first line says what to do. Later lines are commands such as stop or continue.
Secrets are read here, directly from Credential Manager, and never cross the pipe.
"""
import ctypes
import json
import msvcrt
import os
import queue
import sys
import threading
import time
import traceback

from . import browser as browser_module
from . import excel, flow as flow_module, html_reader, i18n, recorder as recorder_module, runner, secret_store, templating
from .browser_errors import BrowserClosed, ElementError
from .i18n import t

EVENT_PREFIX = "@@"


def emit(event):
    sys.stdout.write(EVENT_PREFIX + json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class PipeLines:
    """Reads lines from stdin without ever leaving a read pending.

    While a read waits on a pipe, Windows blocks every call that queries that
    handle, and DLLs do so as they load. A thread sitting in a read therefore
    hangs the process for good on the next import of a compiled module.
    """

    def __init__(self):
        self.handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        self.buffer = b""
        self.closed = False

    def _available(self):
        count = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.PeekNamedPipe(
            ctypes.c_void_p(self.handle), None, 0, None, ctypes.byref(count), None
        )
        if not ok:
            self.closed = True
            return 0
        return count.value

    def poll(self):
        """Return the complete lines that have arrived so far."""
        available = self._available()
        if available:
            self.buffer += os.read(sys.stdin.fileno(), available)
        *lines, self.buffer = self.buffer.split(b"\n")
        return [line.decode("utf-8", "replace") for line in lines]

    def read_first_line(self):
        while True:
            lines = self.poll()
            if lines:
                # Commands sent right after the job must not be lost.
                self.buffer = b"".join(line.encode("utf-8") + b"\n" for line in lines[1:]) + self.buffer
                return lines[0]
            if self.closed:
                raise EOFError(t("No job was received."))
            time.sleep(0.02)


class Commands:
    """Polls stdin on a thread, because the browser library must stay on the main one."""

    def __init__(self, pipe):
        self.pipe = pipe
        self.queue = queue.Queue()
        self.stop_requested = False
        self.closed = False
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while not self.pipe.closed:
            for line in self.pipe.poll():
                try:
                    command = json.loads(line)
                except ValueError:
                    continue
                if command.get("cmd") == "stop":
                    self.stop_requested = True
                self.queue.put(command)
            time.sleep(0.05)
        # The app is gone, so finish the current row and leave.
        self.closed = True
        self.stop_requested = True
        self.queue.put({"cmd": "stop"})

    def next(self, driver=None, timeout=0.2):
        """Wait briefly for a command, keeping the browser's events flowing."""
        try:
            return self.queue.get_nowait()
        except queue.Empty:
            pass
        if driver is not None:
            driver.idle(int(timeout * 1000))
        else:
            time.sleep(timeout)
        return None

    def wait_decision(self, driver):
        while True:
            command = self.next(driver)
            if command and command.get("cmd") in ("continue", "stop", "cancel"):
                return "continue" if command["cmd"] == "continue" else "stop"

    def wait_seconds(self, driver, seconds):
        """Returns False if a check was cancelled. A run's stop means after this row, so it waits on."""
        deadline = time.monotonic() + seconds
        held = []
        try:
            while (left := deadline - time.monotonic()) > 0:
                command = self.next(driver, min(left, 0.2))
                if command and command.get("cmd") == "cancel":
                    return False
                if command:
                    held.append(command)
            return True
        finally:
            for command in held:
                self.queue.put(command)


def run_flow(job, commands):
    flow = flow_module.load_flow(job["flow_path"])
    if job.get("excel_settings"):
        flow["excel"] = {**(flow.get("excel") or {}), **job["excel_settings"]}
    loop = flow_module.find_loop(flow)
    uses_excel = bool(loop and loop.get("source") == "excel")
    previous_statuses = {}
    previous_written = {}
    if job.get("previous_journal"):
        state = runner.load_journal(job["previous_journal"])
        previous_statuses = state.statuses
        previous_written = state.written

    rows = []
    writer = None
    if uses_excel:
        settings = flow["excel"]
        rows = excel.read_rows(
            job["excel_path"], settings["sheet"], settings.get("start_row", 2),
            settings.get("end_row"), settings.get("required") or (), flow_module.stop_column(flow),
        )
        rows = runner.select_rows(rows, previous_statuses, job.get("mode", "full"), job.get("include_unknown", False))
        writer = excel.ResultWriter(job["excel_path"], settings["sheet"], settings.get("status_column"), job["run_id"])
        for number, status in previous_statuses.items():
            if job.get("mode") in ("resume", "retry"):
                writer.set_status(number, status)
            for column, value in (previous_written.get(number) or {}).items():
                writer.set_value(number, column, value)

    journal = runner.Journal(job["journal_path"])
    journal.write(
        type="start", flow_hash=flow_module.flow_hash(flow), mode=job.get("mode", "full"),
        excel_hash=excel.file_hash(job["excel_path"]) if uses_excel else None,
    )
    if job.get("mode") in ("resume", "retry"):
        for number, status in previous_statuses.items():
            selected = {row.number for row in rows}
            if number not in selected:
                journal.write(
                    type="row_end", row=number, status=status,
                    message=t("(from the previous run)"), written=previous_written.get(number) or {},
                )

    driver = browser_module.ChromeDriver()
    outcome = "failed"
    message = ""
    result_file = None
    instance = None
    try:
        driver.open()
        emit({"type": "browser_open"})
        instance = runner.Runner(
            flow, driver, emit, commands, get_secret=secret_store.get_secret, rows=rows,
            writer=writer, journal=journal, screenshot_dir=job.get("screenshot_dir"),
            mode=job.get("mode", "full"),
        )
        outcome, message = instance.run()
    except Exception as error:
        message = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
        if instance is not None:
            message = instance.context.mask(message)
    finally:
        if writer is not None:
            try:
                result_file = str(writer.save())
            except Exception as error:
                emit({"type": "log", "level": "error", "text": t("Could not save the result file: {error}", error=error)})
            writer.close()
        journal.write(type="finish", outcome=outcome, message=message, result_file=result_file)
        journal.close()
        driver.close()
    emit({
        "type": "finished", "outcome": outcome, "message": message, "result_file": result_file,
        "counts": instance.counts if instance else {},
    })
    return 0


def run_inspector(job, commands):
    """A browser kept open to check elements and read pages while building a flow."""
    driver = browser_module.ChromeDriver()
    driver.open()
    emit({"type": "ready"})
    progress = BrowserProgress()
    while True:
        if not driver.is_open():
            emit({"type": "closed"})
            break
        try:
            command = commands.next(driver, 0.15)
        except BrowserClosed:
            emit({"type": "closed"})
            break
        if command is None:
            continue
        action = command.get("cmd")
        if action == "stop":
            break
        if action not in ("check", "scan"):
            continue
        try:
            result = inspect(driver, commands, command, progress)
        except BrowserClosed:
            emit({"type": "closed"})
            break
        except Exception as error:
            result = {"error": str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__}
        emit({"type": "result", **result})
        if commands.stop_requested:
            break
    driver.close()
    return 0


class BrowserProgress:
    """How far the open browser has got, so checking the next block only runs what is new.

    Starting over would log in again, and a site that is already logged in has no login form.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.done = []
        self.url = None
        self.context = templating.Context(secret_store.get_secret)


def step_signature(step, row):
    target = {key: value for key, value in (step.get("target") or {}).items() if key != "verified"}
    return json.dumps({**step, "target": target, "row": row.number if row else None}, sort_keys=True)


def needs_excel_row(step):
    for field in flow_module.TEMPLATE_FIELDS:
        if any(kind in ("column", "row") for kind, _ in templating.references(step.get(field))):
            return True
    return False


def first_usable_row(flow, excel_path):
    settings = flow.get("excel") or {}
    if not excel_path or not settings.get("sheet"):
        raise ValueError(t("Blocks in the loop need Excel data. Click Select Excel first."))
    rows = excel.read_rows(
        excel_path, settings["sheet"], settings.get("start_row", 2),
        settings.get("end_row"), settings.get("required") or (), flow_module.stop_column(flow),
    )
    for row in rows:
        if not row.skip_reason:
            return row
    raise ValueError(t("There are no Excel rows to run in the chosen range."))


def run_up_to(driver, commands, command, progress):
    """Really run the blocks before the one being inspected. Returns a result if it stops early."""
    flow = flow_module.load_flow(command["flow_path"])
    by_id = {step["id"]: step for step, _ in flow_module.walk(flow)}
    steps = [by_id[step_id] for step_id in command.get("step_ids") or []]
    loop = flow_module.find_loop(flow)
    in_loop = {step["id"] for step in loop["steps"]} if loop else set()
    row = None
    if loop and loop.get("source") == "excel" and any(step["id"] in in_loop and needs_excel_row(step) for step in steps):
        row = first_usable_row(flow, command.get("excel_path"))

    signatures = [step_signature(step, row) for step in steps]
    if signatures[:len(progress.done)] != progress.done or driver.url() != progress.url:
        progress.reset()
    instance = runner.Runner(flow, driver, emit, commands, get_secret=secret_store.get_secret)
    instance.context = progress.context
    for index, step in enumerate(steps):
        if index < len(progress.done):
            continue
        pending = commands.next(driver, 0.01)
        if pending and pending.get("cmd") in ("cancel", "stop"):
            return {"cancelled": True}
        label = flow_module.step_label(step)
        emit({"type": "step", "step": step["id"], "label": instance.context.mask(label), "index": index + 1, "total": len(steps)})
        if step["id"] in in_loop:
            instance.context.index = 1
            if row:
                instance.context.row = row.number
                instance.context.columns = row.values
        try:
            instance.run_step(step)
        except runner.RunStopped:
            progress.url = driver.url()
            return {"cancelled": True}
        except BrowserClosed:
            raise
        except Exception as error:
            progress.url = driver.url()
            return {
                "failed_step": step["id"], "position": index + 1,
                "error": f"{instance.context.mask(label)}: {instance.describe(error)}",
            }
        progress.done.append(signatures[index])
        progress.url = driver.url()
    return None


def inspect(driver, commands, command, progress):
    if command.get("url"):
        progress.reset()
        driver.goto(command["url"], 60)
    else:
        stopped = run_up_to(driver, commands, command, progress)
        if stopped:
            return stopped
    return inspect_target(driver, command) if command["cmd"] == "check" else scan_page(driver)


def inspect_target(driver, command):
    target = command["target"]
    deadline = time.monotonic() + 5
    count = driver.count(target)
    while count == 0 and time.monotonic() < deadline:
        driver.idle(150)
        count = driver.count(target)
    if count >= 1:
        driver.highlight(target)
    problem = driver.action_problem(target, command.get("kind")) if count == 1 else None
    return {"count": count, "problem": problem, "url": driver.url()}


def scan_page(driver):
    elements = []
    for document in driver.scan():
        for element in html_reader.read_page(document["html"]):
            element["frames"] = document["frames"]
            elements.append(element)
    return {"elements": elements, "url": driver.url()}


def run_recording(job, commands):
    recorder = recorder_module.Recorder(emit)
    driver = browser_module.ChromeDriver()
    driver.open(on_new_context=recorder.attach)
    emit({"type": "ready"})
    if job.get("url"):
        try:
            driver.goto(job["url"], 60)
        except BrowserClosed:
            raise
        except Exception as error:
            emit({"type": "recorder_warning", "text": t("Could not open the link: {error}", error=str(error).splitlines()[0])})
    try:
        while driver.is_open():
            command = commands.next(driver, 0.15)
            if command and command.get("cmd") == "stop":
                break
    except BrowserClosed:
        pass
    emit({"type": "finished", "outcome": "recorded", **recorder.result()})
    driver.close()
    return 0


def run_self_test(job, commands):
    from .selftest import run

    emit({"type": "selftest", **run(job["test"])})
    return 0


ACTIONS = {
    "run": run_flow,
    "inspect": run_inspector,
    "record": run_recording,
    "selftest": run_self_test,
}


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pipe = PipeLines()
    job = json.loads(pipe.read_first_line())
    i18n.use(job.get("language"))
    commands = Commands(pipe)
    try:
        return ACTIONS[job["action"]](job, commands)
    except Exception as error:
        text = str(error).strip()
        emit({
            "type": "finished", "outcome": "failed",
            "message": text.splitlines()[0] if text else type(error).__name__,
            "detail": traceback.format_exc(limit=3),
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
