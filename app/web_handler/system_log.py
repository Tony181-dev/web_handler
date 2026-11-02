"""What the app has been doing, shown behind the Log button and kept across restarts.

Only block labels and already masked messages reach it, never the values filled into pages.
"""
from datetime import datetime
import json
import threading

from . import i18n, paths

MAX_LINES = 5000
MAX_RETURNED = 1000


class SystemLog:
    def __init__(self):
        self.lock = threading.Lock()
        self.lines = []
        self.last_id = 0
        self._load()

    @property
    def path(self):
        return paths.data_dir() / "system.log"

    def _load(self):
        try:
            saved = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for text in saved[-MAX_LINES:]:
            try:
                line = json.loads(text)
            except ValueError:
                continue  # The last line may be cut off by a crash.
            self.last_id += 1
            self.lines.append({**line, "id": self.last_id})

    def _rewrite(self):
        text = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in self.lines)
        self.path.write_text(text, encoding="utf-8")

    def add(self, level, text):
        with self.lock:
            self.last_id += 1
            line = {"id": self.last_id, "time": datetime.now().isoformat(sep=" ", timespec="seconds"), "level": level, "text": str(text)}
            self.lines.append(line)
            try:
                if len(self.lines) > MAX_LINES * 2:
                    self.lines = self.lines[-MAX_LINES:]
                    self._rewrite()
                else:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("a", encoding="utf-8") as file:
                        file.write(json.dumps(line, ensure_ascii=False) + "\n")
            except OSError:
                pass  # The log must never stop the work it describes.

    def after(self, last_seen):
        with self.lock:
            found = [line for line in self.lines if line["id"] > last_seen][-MAX_RETURNED:]
        # Each line is kept in the language of its time, which may not be the one in use now.
        return [{**line, "text": i18n.retranslate(line["text"])} for line in found]

    def clear(self):
        with self.lock:
            self.lines = []
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
