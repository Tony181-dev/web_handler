"""The local HTTP server behind the UI. Starts on the standard library alone."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import mimetypes
import os
from pathlib import Path
import secrets
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__, child_processes, file_dialog, flow as flow_module, flow_templates, i18n, paths
from .i18n import t
from .demo_site import DEMO_PASSWORD, DEMO_USER, DemoSite, make_demo_flow, make_demo_workbook
from .jobs import JobManager
from .setup_manager import SetupManager
from .system_log import SystemLog

DEMO_FLOW_ID = "demo"
PING_ATTEMPTS = 3
MAX_BODY_BYTES = 30 * 1024 * 1024
SERVER_FILE = "server.json"


class Server(ThreadingHTTPServer):
    # A page load opens a dozen connections at once. Past this queue Windows refuses them,
    # scripts fail to load and the app stays blank.
    request_queue_size = 128


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class App:
    def __init__(self, key):
        self.key = key
        self.port = None
        self.log = SystemLog()
        self.log.add("info", t("Web Handler {version} started.", version=__version__))
        self.jobs = JobManager(self.log)
        self.setup = SetupManager(self.log)
        self.demo = DemoSite()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    # Flows

    def list_flows(self):
        folder = paths.flows_dir()
        if not folder.is_dir():
            return []
        flows = []
        for path in folder.glob("*.json"):
            # The sample flow has its own place on the home page.
            if path.stem == DEMO_FLOW_ID:
                continue
            try:
                flow = flow_module.load_flow(path)
            except (OSError, ValueError):
                continue
            flows.append({
                "id": path.stem, "name": flow.get("name") or path.stem,
                "steps": sum(1 for _ in flow_module.walk(flow)),
                "modified": path.stat().st_mtime,
            })
        return sorted(flows, key=lambda item: item["modified"], reverse=True)

    def create_flow(self, name, template=None):
        flow_id = flow_module.new_flow_id()
        if template:
            try:
                flow = flow_templates.build(template, name)
            except ValueError as error:
                raise ApiError(str(error)) from error
        else:
            flow = flow_module.new_flow(name or t("New flow"))
        flow_module.save_flow(flow_module.flow_path(flow_id), flow)
        return {"id": flow_id, "flow": flow}

    def read_flow(self, flow_id):
        path = flow_module.flow_path(flow_id)
        if not path.is_file():
            raise ApiError(t("Flow not found."), 404)
        return flow_module.load_flow(path)

    def duplicate_flow(self, flow_id):
        flow = self.read_flow(flow_id)
        flow["name"] = t("{name} (copy)", name=flow.get("name", ""))
        new_id = flow_module.new_flow_id()
        flow_module.save_flow(flow_module.flow_path(new_id), flow)
        return {"id": new_id}

    def make_demo(self):
        """Create the sample flow, workbook and secrets for the built-in demo site."""
        from . import secret_store

        secret_store.set_secret("demo_user", DEMO_USER)
        secret_store.set_secret("demo_password", DEMO_PASSWORD)
        self.demo.reset()
        excel_path = paths.data_dir() / "demo" / "example_data.xlsx"
        make_demo_workbook(excel_path)
        flow_module.save_flow(flow_module.flow_path(DEMO_FLOW_ID), make_demo_flow(self.base_url))
        return {"id": DEMO_FLOW_ID, "excel_path": str(excel_path), "url": f"{self.base_url}/demo/"}

    # Excel

    def excel_info(self, path):
        from . import excel

        paths.activate_site()
        try:
            return excel.sheet_info(path)
        except excel.ExcelError as error:
            raise ApiError(str(error)) from error

    # Checking before a run

    def check_context(self, flow, excel_path):
        from . import excel, secret_store

        settings = flow.get("excel") or {}
        loop = flow_module.find_loop(flow)
        columns = None
        nonempty = set()
        excel_error = None
        if loop and loop.get("source") == "excel":
            if not excel_path:
                excel_error = t("No Excel file selected.")
            elif settings.get("sheet"):
                try:
                    info = excel.sheet_info(excel_path)
                    sheet = next((item for item in info["sheets"] if item["name"] == settings["sheet"]), None)
                    if sheet is None:
                        excel_error = t("There is no sheet '{name}'.", name=settings["sheet"])
                    else:
                        columns = {item["column"] for item in sheet["columns"]} | set(_letters_written(flow))
                        if settings.get("status_column"):
                            columns.add(settings["status_column"])
                        wanted = set(_letters_written(flow))
                        if settings.get("status_column"):
                            wanted.add(settings["status_column"])
                        nonempty = excel.nonempty_columns(excel_path, settings["sheet"], wanted, settings.get("start_row", 2))
                except excel.ExcelError as error:
                    excel_error = str(error)
        known = set(secret_store.names())
        return flow_module.CheckContext(
            secret_exists=lambda name: name in known and secret_store.exists(name),
            columns=columns, nonempty_columns=nonempty, excel_error=excel_error,
        )

    def preflight(self, body):
        from . import excel, runner, templating

        flow = self.read_flow(body["flow_id"])
        excel_path = body.get("excel_path") or ""
        context = self.check_context(flow, excel_path)
        issues = flow_module.validate(flow, context)
        result = issues.as_dict()
        loop = flow_module.find_loop(flow)
        settings = flow.get("excel") or {}
        mode = body.get("mode", "full")
        previous = {}
        previous_state = None
        if body.get("previous_run"):
            previous_state = runner.load_journal(paths.runs_dir() / body["previous_run"] / "journal.jsonl")
            previous = previous_state.statuses
        total = 1 if not loop else None
        urls = [step.get("url", "") for step, _ in flow_module.walk(flow) if step.get("do") == "goto"]
        skipped = 0
        if loop and loop.get("source") == "count":
            total = 1 if mode == "trial" else int(loop.get("count") or 1)
        elif loop and not result["errors"] and excel_path:
            stop_column = flow_module.stop_column(flow)
            rows = excel.read_rows(excel_path, settings["sheet"], settings.get("start_row", 2), settings.get("end_row"), settings.get("required") or (), stop_column)
            if not settings.get("end_row"):
                result["stop_column"] = stop_column or ""
            chosen = runner.select_rows(rows, previous, mode, body.get("include_unknown", False))
            skipped = sum(1 for row in chosen if row.skip_reason)
            total = len(chosen) - skipped
            urls = _expand_urls(flow, chosen[:50], urls, templating)
            if total == 0:
                result["errors"].append({"step": None, "message": t("There are no rows to run in the chosen range.")})
        if previous_state is not None:
            current_flow = flow_module.flow_hash(flow)
            if previous_state.meta.get("flow_hash") not in (None, current_flow):
                result["changed"] = t("The flow has changed since the previous run.")
            if excel_path and previous_state.meta.get("excel_hash") not in (None, excel.file_hash(excel_path)):
                result["changed"] = t("The Excel file has changed since the previous run.")
        result["total"] = total
        result["skipped"] = skipped
        result["hosts"] = flow_module.hosts_of(flow, [url for url in urls if "{{" not in url])
        written = list(_letters_written(flow))
        if settings.get("status_column") and loop and loop.get("source") == "excel":
            written.insert(0, settings["status_column"])
        result["columns"] = written
        result["trial_note"] = mode == "trial"
        return result

    def start_run(self, body):
        settings = None
        run = self.jobs.start_run(
            body["flow_id"], body.get("excel_path") or "", body.get("mode", "full"), settings,
            body.get("previous_run"), body.get("include_unknown", False),
        )
        return {"run_id": run.run_id}

    def file_for_run(self, run_id, name):
        summary = self.jobs.run_summary(run_id)
        if summary is None:
            raise ApiError(t("Run not found."), 404)
        folder = (paths.runs_dir() / run_id).resolve()
        path = (folder / name).resolve()
        if path.parent != folder or path.suffix.lower() != ".png" or not path.is_file():
            raise ApiError(t("File not found."), 404)
        return path


def _letters_written(flow):
    for step, _ in flow_module.walk(flow):
        if step.get("do") == "read" and step.get("save_to_column"):
            yield step["save_to_column"]


def _expand_urls(flow, rows, urls, templating):
    expanded = [url for url in urls if "{{" not in url]
    for row in rows:
        if row.skip_reason:
            continue
        context = templating.Context()
        context.row = row.number
        context.columns = row.values
        context.index = 1
        for url in urls:
            if "{{" in url:
                try:
                    expanded.append(templating.render(url, context))
                except templating.TemplateError:
                    pass
    return expanded


class Handler(BaseHTTPRequestHandler):
    # Headers and body must leave in one write. Sent separately, about 1 in 200
    # responses lost the body on a machine with a network filter.
    wbufsize = 64 * 1024
    protocol_version = "HTTP/1.0"
    app: App = None

    def log_message(self, *args):
        pass

    # Security

    def host_is_allowed(self):
        host = self.headers.get("Host", "")
        return host in (f"127.0.0.1:{self.app.port}", f"localhost:{self.app.port}")

    def origin_is_allowed(self):
        origin = self.headers.get("Origin")
        return origin is None or origin in (f"http://127.0.0.1:{self.app.port}", f"http://localhost:{self.app.port}")

    def has_key(self, query):
        supplied = query.get("key", [None])[0]
        if supplied and hmac.compare_digest(supplied, self.app.key):
            return True
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "wh_key" and hmac.compare_digest(value, self.app.key):
                return True
        return False

    # Responses

    def send_bytes(self, status, body, content_type, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status=200):
        self.send_bytes(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ApiError(t("The data sent is too large."), 413)
        return self.rfile.read(length) if length else b""

    def read_json(self):
        body = self.read_body()
        return json.loads(body.decode("utf-8")) if body else {}

    # Routing

    def dispatch(self, method):
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)
        if not self.host_is_allowed():
            return self.send_json({"error": t("Invalid host.")}, 403)
        if path == "/demo" or path.startswith("/demo/"):
            return self.serve_demo(method)
        if not self.origin_is_allowed():
            return self.send_json({"error": t("Invalid origin.")}, 403)
        if not self.has_key(query):
            return self.send_bytes(403, t("The session key is missing. Open the app with Start.bat.").encode("utf-8"), "text/plain; charset=utf-8")
        try:
            if path == "/":
                return self.serve_index()
            if path.startswith("/ui/"):
                return self.serve_ui(path)
            if path.startswith("/api/"):
                return self.serve_api(method, path, query)
            raise ApiError(t("Not found."), 404)
        except ApiError as error:
            self.app.log.add("error", str(error))
            self.send_json({"error": str(error)}, error.status)
        except (BrokenPipeError, ConnectionError):
            pass
        except Exception as error:
            text = str(error).strip()
            message = text.splitlines()[0] if text else type(error).__name__
            self.app.log.add("error", message)
            self.send_json({"error": message}, 500)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def serve_demo(self, method):
        body = self.read_body().decode("utf-8", "replace") if method == "POST" else ""
        status, headers, text = self.app.demo.handle(method, self.path, body, self.headers.get("Cookie"))
        self.send_bytes(status, text.encode("utf-8"), "text/html; charset=utf-8", headers)

    def serve_index(self):
        body = (paths.UI_DIR / "index.html").read_bytes()
        cookie = f"wh_key={self.app.key}; Path=/; HttpOnly; SameSite=Strict"
        self.send_bytes(200, body, "text/html; charset=utf-8", {"Set-Cookie": cookie})

    def serve_ui(self, path):
        target = (paths.UI_DIR / path[len("/ui/"):]).resolve()
        if paths.UI_DIR.resolve() not in target.parents or not target.is_file():
            raise ApiError(t("Not found."), 404)
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind.endswith("javascript"):
            kind += "; charset=utf-8"
        self.send_bytes(200, target.read_bytes(), kind)

    def serve_api(self, method, path, query):
        app = self.app
        segments = path.strip("/").split("/")[1:]
        head = segments[0] if segments else ""
        rest = segments[1:]

        if head == "ping" and method == "GET":
            return self.send_json({"ok": True, "version": __version__})
        if head == "log":
            return self.api_log(method, query)
        if head == "setup":
            return self.api_setup(method, rest)
        if head == "dialog" and method == "POST":
            return self.send_json({"path": file_dialog.pick_excel_file()})

        if head == "language":
            return self.api_language(method)

        paths.activate_site()
        if head == "flows":
            return self.api_flows(method, rest)
        if head == "demo" and method == "POST":
            return self.send_json(app.make_demo())
        if head == "active":
            return self.api_active(method, rest)
        if head == "excel" and method == "POST":
            return self.send_json(app.excel_info(self.read_json().get("path", "")))
        if head == "html" and method == "POST":
            return self.api_html()
        if head == "secrets":
            return self.api_secrets(method, rest)
        if head == "preflight" and method == "POST":
            return self.send_json(app.preflight(self.read_json()))
        if head == "runs":
            return self.api_runs(method, rest, query)
        if head == "inspect":
            return self.api_inspect(method, rest)
        if head == "record":
            return self.api_record(method, rest, query)
        raise ApiError(t("Not found."), 404)

    def api_active(self, method, rest):
        jobs = self.app.jobs
        if method == "GET" and not rest:
            return self.send_json({"job": jobs.active_job()})
        if method == "POST" and rest == ["stop"]:
            jobs.stop_active()
            return self.send_json({"ok": True})
        raise ApiError(t("Not found."), 404)

    def api_language(self, method):
        if method == "GET":
            return self.send_json({"language": i18n.language()})
        if method == "PUT":
            try:
                i18n.save(self.read_json().get("language"))
            except ValueError as error:
                raise ApiError(str(error)) from error
            # The demo site holds its sample text in the language it was created in.
            self.app.demo.reset()
            return self.send_json({"language": i18n.language()})
        raise ApiError(t("Not found."), 404)

    def api_log(self, method, query):
        if method == "GET":
            after = query.get("after", ["0"])[0]
            return self.send_json({"lines": self.app.log.after(int(after) if after.isdigit() else 0)})
        if method == "DELETE":
            self.app.log.clear()
            return self.send_json({"ok": True})
        raise ApiError(t("Not found."), 404)

    def api_setup(self, method, rest):
        setup = self.app.setup
        if method == "GET" and not rest:
            return self.send_json(setup.status())
        if method == "POST" and rest == ["install"]:
            return self.send_json({"started": setup.start_install()})
        if method == "POST" and rest == ["test"]:
            return self.send_json({"started": setup.start_self_tests()})
        raise ApiError(t("Not found."), 404)

    def api_flows(self, method, rest):
        app = self.app
        if not rest:
            if method == "GET":
                return self.send_json({"flows": app.list_flows(), "templates": flow_templates.catalog()})
            if method == "POST":
                body = self.read_json()
                return self.send_json(app.create_flow(body.get("name"), body.get("template")))
        elif len(rest) == 1:
            flow_id = rest[0]
            if method == "GET":
                return self.send_json({"id": flow_id, "flow": app.read_flow(flow_id)})
            if method == "PUT":
                flow_module.save_flow(flow_module.flow_path(flow_id), self.read_json())
                return self.send_json({"ok": True})
            if method == "DELETE":
                flow_module.flow_path(flow_id).unlink(missing_ok=True)
                return self.send_json({"ok": True})
        elif len(rest) == 2 and rest[1] == "duplicate" and method == "POST":
            return self.send_json(app.duplicate_flow(rest[0]))
        raise ApiError(t("Not found."), 404)

    def api_html(self):
        from . import html_reader

        body = self.read_json()
        html = body.get("html") or ""
        if body.get("mode") == "page":
            return self.send_json({"elements": html_reader.read_page(html)})
        return self.send_json({"element": html_reader.read_element(html)})

    def api_secrets(self, method, rest):
        from . import secret_store

        if method == "GET" and not rest:
            return self.send_json({"names": secret_store.names()})
        if method == "POST" and not rest:
            body = self.read_json()
            if not body.get("value"):
                raise ApiError(t("Please enter a value."))
            try:
                secret_store.set_secret(body.get("name", ""), body["value"])
            except ValueError as error:
                raise ApiError(str(error)) from error
            return self.send_json({"ok": True})
        if method == "DELETE" and len(rest) == 1:
            secret_store.delete_secret(unquote(rest[0]))
            return self.send_json({"ok": True})
        raise ApiError(t("Not found."), 404)

    def api_runs(self, method, rest, query):
        app = self.app
        if not rest:
            if method == "GET":
                flow_id = query.get("flow", [None])[0]
                return self.send_json({"runs": app.jobs.list_runs(flow_id)})
            if method == "POST":
                try:
                    return self.send_json(app.start_run(self.read_json()))
                except RuntimeError as error:
                    raise ApiError(str(error), 409) from error
        run_id = rest[0] if rest else ""
        if not run_id.replace("_", "").isalnum():
            raise ApiError(t("Invalid run ID."), 404)
        if len(rest) == 1 and method == "GET":
            summary = app.jobs.run_summary(run_id)
            if summary is None:
                raise ApiError(t("Run not found."), 404)
            return self.send_json(summary)
        if len(rest) == 2 and rest[1] == "events" and method == "GET":
            run = app.jobs.runs.get(run_id)
            if run is None:
                raise ApiError(t("This run is no longer in memory."), 404)
            return self.stream_events(run.log)
        if len(rest) == 2 and rest[1] == "command" and method == "POST":
            command = self.read_json().get("cmd")
            if command not in ("stop", "continue"):
                raise ApiError(t("Invalid command."))
            try:
                app.jobs.command(run_id, command)
            except RuntimeError as error:
                raise ApiError(str(error), 409) from error
            return self.send_json({"ok": True})
        if len(rest) == 2 and rest[1] == "open" and method == "POST":
            summary = app.jobs.run_summary(run_id)
            result = summary and summary.get("result_file")
            if not result or not Path(result).is_file():
                raise ApiError(t("There is no result file."), 404)
            os.startfile(result)
            return self.send_json({"ok": True})
        if len(rest) == 3 and rest[1] == "files" and method == "GET":
            path = app.file_for_run(run_id, rest[2])
            return self.send_bytes(200, path.read_bytes(), "image/png")
        raise ApiError(t("Not found."), 404)

    def api_inspect(self, method, rest):
        jobs = self.app.jobs
        if method == "POST" and not rest:
            body = self.read_json()
            if body.get("flow_id"):
                body["flow_path"] = str(flow_module.flow_path(body.pop("flow_id")))
            try:
                jobs.start_inspection(body)
            except RuntimeError as error:
                raise ApiError(str(error), 409) from error
            return self.send_json({"ok": True})
        if method == "GET" and rest == ["events"]:
            if jobs.inspection_log is None:
                raise ApiError(t("There has been no check yet."), 404)
            return self.stream_events(jobs.inspection_log)
        if method == "POST" and rest == ["command"]:
            command = self.read_json().get("cmd")
            if command not in ("continue", "cancel"):
                raise ApiError(t("Invalid command."))
            jobs.inspection_command(command)
            return self.send_json({"ok": True})
        raise ApiError(t("Not found."), 404)

    def api_record(self, method, rest, query):
        jobs = self.app.jobs
        if method == "POST" and not rest:
            try:
                jobs.start_recording(self.read_json().get("url"))
            except RuntimeError as error:
                raise ApiError(str(error), 409) from error
            return self.send_json({"ok": True})
        if method == "POST" and rest == ["stop"]:
            jobs.stop_recording()
            return self.send_json({"ok": True})
        if method == "GET" and rest == ["events"]:
            if jobs.recording_log is None:
                raise ApiError(t("There is no recording yet."), 404)
            return self.stream_events(jobs.recording_log)
        raise ApiError(t("Not found."), 404)

    def stream_events(self, log):
        last = self.headers.get("Last-Event-ID")
        index = int(last) + 1 if last and last.isdigit() else 0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.flush()
        while True:
            events, closed = log.wait_after(index, 10)
            for event in events:
                payload = json.dumps(event, ensure_ascii=False)
                self.wfile.write(f"id: {index}\ndata: {payload}\n\n".encode("utf-8"))
                index += 1
            if not events:
                self.wfile.write(b": keep-alive\n\n")
            self.wfile.flush()
            if closed and not events:
                self.wfile.write(b"event: end\ndata: {}\n\n")
                self.wfile.flush()
                return


def load_saved_address():
    try:
        return json.loads((paths.data_dir() / SERVER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_address(port, key):
    folder = paths.data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    pid, started = child_processes.current_process()
    (folder / SERVER_FILE).write_text(
        json.dumps({"port": port, "key": key, "pid": pid, "started": started}), encoding="utf-8",
    )


def running_instance():
    """Return the URL of an app already running on this data folder, if there is one."""
    import urllib.request

    saved = load_saved_address()
    if not saved.get("port") or not saved.get("key"):
        return None
    url = f"http://127.0.0.1:{saved['port']}/"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # Several tries: a network filter can drop a local answer, and a working app must not look hung.
    for _ in range(PING_ATTEMPTS):
        try:
            with opener.open(f"{url}api/ping?key={saved['key']}", timeout=2) as response:
                if json.loads(response.read().decode("utf-8")).get("ok"):
                    return f"{url}?key={saved['key']}"
        except Exception:
            pass
    return None


def stop_saved_instance():
    """End the app recorded for this data folder, along with its workers and their browser."""
    saved = load_saved_address()
    if not saved.get("pid") or not saved.get("started"):
        return True
    return child_processes.end_process(saved["pid"], saved["started"])


def create_server():
    """Bind to 127.0.0.1. The last port is reused so links and the demo flow keep working."""
    saved = load_saved_address()
    key = saved.get("key") or secrets.token_urlsafe(24)
    app = App(key)
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = None
    if saved.get("port"):
        try:
            server = Server(("127.0.0.1", saved["port"]), handler)
        except OSError:
            server = None
    if server is None:
        server = Server(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    app.port = server.server_address[1]
    save_address(app.port, key)
    return server, app
