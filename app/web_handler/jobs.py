"""Runs, recordings and the inspector browser, as seen by the server.

Only one of them may use the browser profile at a time.
"""
from datetime import datetime
import json
import secrets
import threading
import time
from pathlib import Path

from . import flow as flow_module, i18n, paths, runner, settings
from .i18n import t
from .workers import WorkerSession

# Windows ends a closing console's processes after 5 seconds. macOS would wait, but runs end alike on both.
SHUTDOWN_GRACE_SECONDS = 4
STOP_NOW_GRACE_SECONDS = 3
# A worker that has reported its end but cannot close its browser must not block the next run.
WORKER_EXIT_SECONDS = 10


class EventLog:
    """Events kept in memory, so a page that reconnects can catch up."""

    def __init__(self):
        self.events = []
        self.condition = threading.Condition()
        self.closed = False

    def add(self, event):
        with self.condition:
            self.events.append(event)
            self.condition.notify_all()

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()

    def wait_after(self, index, timeout):
        with self.condition:
            if len(self.events) <= index and not self.closed:
                self.condition.wait(timeout)
            return self.events[index:], self.closed


class Run:
    def __init__(self, run_id, flow_id, flow_name, excel_path, mode):
        self.run_id = run_id
        self.flow_id = flow_id
        self.flow_name = flow_name
        self.excel_path = excel_path
        self.mode = mode
        self.log = EventLog()
        self.session = None
        self.state = "running"
        self.stopped_now = False
        # The run has ended, and its window is left to the person until they close it.
        self.kept_open = False
        self.started_at = datetime.now().isoformat(timespec="seconds")

    @property
    def folder(self):
        return paths.runs_dir() / self.run_id

    def save_meta(self, extra=None):
        data = {
            "run_id": self.run_id, "flow_id": self.flow_id, "flow_name": self.flow_name,
            "excel_path": self.excel_path, "mode": self.mode, "started_at": self.started_at,
            **(extra or {}),
        }
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "run.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def new_run_id():
    return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(2)


def describe_event(event):
    """The log line for a worker event, as (level, text), or None if it is not worth one."""
    kind = event.get("type")
    prefix = t("Row {row}: ", row=event["row"]) if event.get("row") else ""
    if kind == "browser_open":
        return "info", t("Opened the browser.")
    if kind == "step":
        return "info", prefix + event.get("label", "")
    if kind == "branch":
        if event.get("branch") == "then":
            return "info", prefix + t('The condition of the "If" block is met, so the Yes branch runs.')
        return "info", prefix + t('The condition of the "If" block is not met, so the No branch runs.')
    if kind == "row_done":
        message = f": {event['message']}" if event.get("message") else ""
        level = "error" if event.get("status") == runner.STATUS_FAILED else "info"
        return level, f"{prefix}{t(event.get('status', ''))}{message}"
    if kind == "paused":
        return "warn", t("Paused: {reason}", reason=event.get("reason", ""))
    if kind == "resumed":
        return "info", t("Continuing.") if event.get("decision") == "continue" else t("Stopped as requested.")
    if kind == "log":
        return event.get("level", "info"), event.get("text", "")
    if kind == "finished":
        message = f": {event['message']}" if event.get("message") else ""
        return ("info" if event.get("outcome") in ("finished", "stopped") else "error"), t("Ended ({outcome}){message}", outcome=event.get("outcome"), message=message)
    return None


def describe_result(event):
    if event.get("cancelled"):
        return "info", t("Check: stopped as requested.")
    if event.get("error"):
        return "error", t("Check: {detail}", detail=event["error"])
    if event.get("problem"):
        return "error", t("Check: {detail}", detail=event["problem"])
    if "count" in event:
        return ("info" if event["count"] == 1 else "error"), t("Check: matched {count} element(s) at {url}", count=event["count"], url=event.get("url", ""))
    return "info", t("Page scan: found {count} element(s) at {url}", count=len(event.get("elements") or []), url=event.get("url", ""))


class JobManager:
    def __init__(self, log):
        self.log = log
        self.lock = threading.RLock()
        self.runs = {}
        self.active = None
        self.inspector = None
        self.inspection_log = None
        self.recording = None
        self.recording_log = None

    def _let_finished_run_close(self):
        run = self.active
        if run is None or run.session is None or run.state == "running" or not run.session.alive():
            return
        if run.kept_open:
            # Only one browser can use the profile, and the person has asked for something new.
            self.log.add("info", t("Closed the browser kept open from the last run."))
            run.session.send({"cmd": "stop"})
        if not run.session.wait(WORKER_EXIT_SECONDS):
            run.session.kill()
            run.session.wait(STOP_NOW_GRACE_SECONDS)

    def browser_in_use(self):
        self._let_finished_run_close()
        if self.active is not None and self.active.session and self.active.session.alive():
            return t("A flow is running.")
        if self.recording is not None and self.recording.alive():
            return t("Actions are being recorded.")
        return None

    # Running a flow

    def start_run(self, flow_id, excel_path, mode, excel_settings=None, previous_run=None, include_unknown=False):
        with self.lock:
            busy = self.browser_in_use()
            if busy:
                raise RuntimeError(busy)
            self.close_inspector()
            path = flow_module.flow_path(flow_id)
            flow = flow_module.load_flow(path)
            run = Run(new_run_id(), flow_id, flow.get("name", ""), excel_path, mode)
            run.save_meta()
            job = {
                "action": "run", "run_id": run.run_id, "flow_path": str(path),
                "excel_path": excel_path, "mode": mode, "excel_settings": excel_settings,
                "journal_path": str(run.folder / "journal.jsonl"),
                "screenshot_dir": str(run.folder),
                "include_unknown": include_unknown,
                "browser_mode": settings.browser_mode(),
            }
            if previous_run:
                job["previous_journal"] = str(paths.runs_dir() / previous_run / "journal.jsonl")
            session = WorkerSession(job)
            run.session = session
            session.on_event = lambda event: self._on_run_event(run, event)
            session.on_exit = lambda code: self._on_run_exit(run, code)
            self.runs[run.run_id] = run
            self.active = run
            self.log.add("info", t("Started running '{name}' (mode {mode}).", name=run.flow_name, mode=mode))
            session.start()
            return run

    def _on_run_event(self, run, event):
        line = describe_event(event)
        if line:
            self.log.add(*line)
        if event.get("type") == "finished":
            run.state = event.get("outcome") or "finished"
            run.save_meta({"outcome": run.state, "result_file": event.get("result_file"), "message": event.get("message")})
            run.kept_open = bool(event.get("kept_open")) and not run.stopped_now
            if run.kept_open:
                self.log.add("info", t("The browser stays open. Close it when you are done."))
            elif event.get("kept_open"):
                # Stop now means everything, the window too.
                run.session.send({"cmd": "stop"})
            if not run.kept_open:
                timer = threading.Timer(WORKER_EXIT_SECONDS, run.session.kill)
                timer.daemon = True
                timer.start()
        run.log.add(event)

    def _on_run_exit(self, run, code):
        if run.state == "running" and run.stopped_now:
            run.state = "stopped"
            run.save_meta({"outcome": "stopped"})
            run.log.add({"type": "finished", "outcome": "stopped", "message": ""})
        elif run.state == "running":
            run.state = "crashed"
            tail = "\n".join(run.session.output_tail[-5:])
            event = {
                "type": "finished", "outcome": "crashed",
                "message": t("The run stopped suddenly. Click Resume to finish it.") + (f"\n{tail}" if tail else ""),
            }
            run.save_meta({"outcome": "crashed"})
            self.log.add("error", event["message"])
            run.log.add(event)
        run.log.close()

    def active_job(self):
        """What is using the browser now, so a page can always lead back to it or stop it."""
        with self.lock:
            run = self.active
            if run is not None and run.session is not None and run.session.alive() and run.state == "running":
                return {"kind": "run", "run_id": run.run_id, "flow_id": run.flow_id, "flow_name": run.flow_name}
            if self.recording is not None and self.recording.alive():
                return {"kind": "recording"}
            return None

    def browser_state(self):
        """running while a run uses the browser, kept while its window is left open after it, or None."""
        with self.lock:
            run = self.active
            if run is None or run.session is None or not run.session.alive():
                return None
            if run.state == "running":
                return "running"
            return "kept" if run.kept_open else None

    def close_kept_browser(self):
        with self.lock:
            if self.browser_state() == "kept":
                self._let_finished_run_close()

    def stop_active(self):
        """Stop whatever is running, now. It gets a moment to save, and is ended if that is not enough."""
        with self.lock:
            run = self.active
            session = run.session if run is not None and run.session is not None and run.session.alive() else None
            if session is not None:
                run.stopped_now = True
            elif self.recording is not None and self.recording.alive():
                session = self.recording
        if session is None:
            return
        session.send({"cmd": "cancel"})
        session.send({"cmd": "stop"})
        if not session.wait(STOP_NOW_GRACE_SECONDS):
            session.kill()
            session.wait(STOP_NOW_GRACE_SECONDS)

    def command(self, run_id, command):
        run = self.runs.get(run_id)
        if run is None or run.session is None or not run.session.alive():
            raise RuntimeError(t("This run has ended."))
        run.session.send({"cmd": command})

    def run_summary(self, run_id):
        folder = paths.runs_dir() / run_id
        try:
            meta = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        state = runner.load_journal(folder / "journal.jsonl")
        counts = {}
        for status in state.statuses.values():
            counts[status] = counts.get(status, 0) + 1
        run = self.runs.get(run_id)
        running = bool(run and run.session and run.session.alive())
        return {
            **meta, "message": i18n.retranslate(meta.get("message", "")), "counts": counts, "running": running,
            "rows": [
                {"row": row, "status": status, "message": state.messages.get(row, "")}
                for row, status in sorted(state.statuses.items())
            ],
        }

    def list_runs(self, flow_id=None, limit=20):
        folder = paths.runs_dir()
        if not folder.is_dir():
            return []
        found = []
        for path in sorted(folder.iterdir(), key=lambda item: item.name, reverse=True):
            if path.is_dir():
                summary = self.run_summary(path.name)
                if summary and (flow_id is None or summary.get("flow_id") == flow_id):
                    found.append(summary)
                    if len(found) >= limit:
                        break
        return found

    # Recording

    def start_recording(self, url):
        with self.lock:
            busy = self.browser_in_use()
            if busy:
                raise RuntimeError(busy)
            self.close_inspector()
            self.recording_log = EventLog()
            session = WorkerSession({"action": "record", "url": url})
            log = self.recording_log
            session.on_event = lambda event: self._on_recording_event(log, event)
            session.on_exit = lambda code: log.close()
            self.recording = session
            self.log.add("info", t("Started recording at {url}.", url=url or t("a blank page")))
            session.start()
            return session

    def _on_recording_event(self, log, event):
        if event.get("type") == "recorder_warning":
            self.log.add("warn", t("Recording: {detail}", detail=event.get("text", "")))
        elif event.get("type") == "finished":
            self.log.add("info", t("Recording ended: {count} block(s).", count=len(event.get("entries") or [])))
        log.add(event)

    def stop_recording(self):
        with self.lock:
            if self.recording and self.recording.alive():
                self.recording.send({"cmd": "stop"})

    # The inspector browser

    def _ensure_inspector(self):
        if self.inspector is not None and self.inspector.alive():
            return self.inspector
        busy = self.browser_in_use()
        if busy:
            raise RuntimeError(busy)
        session = WorkerSession({"action": "inspect"})
        session.on_event = self._on_inspector_event
        session.on_exit = lambda code: self._on_inspector_exit(session)
        self.inspector = session
        session.start()
        return session

    def start_inspection(self, command):
        """Send one check or scan to the inspector. Its progress and result arrive in inspection_log."""
        with self.lock:
            if self.inspection_log is not None and not self.inspection_log.closed:
                raise RuntimeError(t("A check on the live page is already running."))
            session = self._ensure_inspector()
            self.inspection_log = EventLog()
            self.log.add("info", t("Check: running up to the block on the live page.") if command.get("cmd") == "check" else t("Scanning the open page."))
            session.send({**command, "language": i18n.language()})

    def inspection_command(self, command):
        with self.lock:
            if self.inspector is not None and self.inspector.alive():
                self.inspector.send({"cmd": command})

    def _on_inspector_event(self, event):
        kind = event.get("type")
        if kind in ("closed", "finished"):
            self._finish_inspection(event.get("message"))
        elif kind in ("step", "branch", "paused", "resumed", "log", "result"):
            line = describe_result(event) if kind == "result" else describe_event(event)
            if line:
                self.log.add(*line)
            log = self.inspection_log
            if log is not None and not log.closed:
                log.add(event)
                if kind == "result":
                    log.close()

    def _on_inspector_exit(self, session):
        if session is self.inspector:
            self._finish_inspection(None)

    def _finish_inspection(self, message):
        log = self.inspection_log
        if log is not None and not log.closed:
            text = message or t("The browser was closed. Please click again.")
            self.log.add("error", t("Check: {detail}", detail=text))
            log.add({"type": "result", "error": text})
            log.close()

    def close_inspector(self):
        session = self.inspector
        self.inspector = None
        if session is not None and session.alive():
            session.send({"cmd": "stop"})
            if not session.wait(10):
                session.kill()

    def shutdown(self, grace_seconds=SHUTDOWN_GRACE_SECONDS):
        """Let the workers finish up and save. Whatever is still going dies with this process."""
        run = self.active.session if self.active is not None else None
        sessions = [session for session in (self.inspector, self.recording, run) if session is not None and session.alive()]
        for session in sessions:
            # Cancel ends a Wait block at once: nobody will see the row finish, but the result file gets saved.
            session.send({"cmd": "cancel"})
            session.send({"cmd": "stop"})
        deadline = time.monotonic() + grace_seconds
        for session in sessions:
            session.wait(max(0, deadline - time.monotonic()))
