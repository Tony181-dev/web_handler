"""The release build's handling of dist, shared by every system. The install scripts are tested per system."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "app"))

import build_package  # noqa: E402

# Batch files run from the Windows temp folder get quarantined by some antivirus programs.
SCRATCH = ROOT / "build" / "test_package"


def write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def folder():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=SCRATCH)


class RefreshFolderTests(unittest.TestCase):
    def test_the_copy_matches_the_package_but_keeps_data_and_runtime(self):
        with folder() as name:
            source, target = Path(name) / "package", Path(name) / "copy"
            write(source / "Start.bat", "new")
            write(source / "app" / "main.py", "same")
            write(source / "app" / "web_handler" / "flow.py", "flow")
            write(target / "Start.bat", "old")
            write(target / "app" / "main.py", "same")
            write(target / "app" / "web_handler" / "gone.py")
            write(target / "app" / "old_folder" / "inside.py")
            write(target / "Build.bat")
            write(target / "data" / "flows" / "mine.json", "keep me")
            write(target / "runtime" / "python" / "python.exe", "keep me")
            unchanged = (target / "app" / "main.py").stat().st_mtime_ns

            build_package.refresh_folder(source, target)

            self.assertEqual((target / "Start.bat").read_text(encoding="utf-8"), "new")
            self.assertEqual((target / "app" / "web_handler" / "flow.py").read_text(encoding="utf-8"), "flow")
            self.assertEqual((target / "app" / "main.py").stat().st_mtime_ns, unchanged)
            self.assertFalse((target / "app" / "web_handler" / "gone.py").exists())
            self.assertFalse((target / "app" / "old_folder").exists())
            self.assertFalse((target / "Build.bat").exists())
            self.assertEqual((target / "data" / "flows" / "mine.json").read_text(encoding="utf-8"), "keep me")
            self.assertTrue((target / "runtime" / "python" / "python.exe").exists())


class ClearDistTests(unittest.TestCase):
    def test_only_the_unpacked_copy_is_left(self):
        with folder() as name:
            dist = Path(name) / "dist"
            write(dist / "WebHandler" / "data" / "flows" / "mine.json")
            write(dist / "WebHandler-0.1.13.zip")
            write(dist / "WebHandler-Setup-0.1.13.exe")
            write(dist / "WebHandlerProbe" / "probe.py")
            with mock.patch.object(build_package, "DIST_DIR", dist):
                build_package.clear_dist(keep={"WebHandler"})
            self.assertEqual([path.name for path in dist.iterdir()], ["WebHandler"])
            self.assertTrue((dist / "WebHandler" / "data" / "flows" / "mine.json").exists())


class ForgetSecretsTests(unittest.TestCase):
    def test_every_saved_secret_is_removed(self):
        import main
        from web_handler import secret_store

        with mock.patch.object(secret_store, "names", return_value=["login", "other"]), \
                mock.patch.object(secret_store, "delete_secret") as delete, \
                mock.patch.object(main, "say"):
            self.assertEqual(main.forget_secrets(), 0)
        self.assertEqual([call.args[0] for call in delete.call_args_list], ["login", "other"])

    def test_a_failure_is_reported(self):
        import main
        from web_handler import secret_store

        with mock.patch.object(secret_store, "names", return_value=["login"]), \
                mock.patch.object(secret_store, "delete_secret", side_effect=RuntimeError("locked")), \
                mock.patch.object(main, "say") as say:
            self.assertEqual(main.forget_secrets(), 1)
        self.assertIn("locked", say.call_args.args[0])


FAKE_MAIN = """import sys
from pathlib import Path
with open(Path(__file__).resolve().parents[1] / "calls.txt", "a", encoding="utf-8") as calls:
    calls.write(" ".join(sys.argv[1:]) + "\\n")
"""


if __name__ == "__main__":
    unittest.main()
