"""The release build's handling of dist, and the uninstaller."""
import os
from pathlib import Path
import shutil
import subprocess
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


@unittest.skipUnless(sys.platform == "win32", "Windows batch files")
class UninstallTests(unittest.TestCase):
    """Uninstall.bat on an installed copy whose app is a stand-in recording what it was asked to do."""

    def install(self, parent):
        # A name cmd would misread unquoted.
        here = Path(parent) / "Web & Handler (x86)"
        here.mkdir()
        shutil.copy2(ROOT / "Start.bat", here / "Start.bat")
        shutil.copy2(ROOT / "Uninstall.bat", here / "Uninstall.bat")
        write(here / "app" / "main.py", FAKE_MAIN)
        write(here / "offline" / "wheels" / "a.whl")
        write(here / "runtime" / "site" / "lib.py")
        write(here / "README.txt")
        write(here / "data" / "flows" / "mine.json")
        return here

    def uninstall(self, here, *arguments):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        # The stand-in app runs on the Python running these tests.
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        return subprocess.run(
            [os.environ.get("COMSPEC", "cmd.exe"), "/c", "call", str(here / "Uninstall.bat"), "--yes", *arguments],
            cwd=str(SCRATCH), env=env, capture_output=True, timeout=120,
        )

    def calls(self, here):
        return (here / "calls.txt").read_text(encoding="utf-8").split()

    def test_keeps_the_data_and_anything_it_did_not_install(self):
        with folder() as parent:
            here = self.install(parent)
            write(here / "notes of mine.txt")
            result = self.uninstall(here)
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
            self.assertEqual(self.calls(here), ["--stop"])
            left = sorted(path.name for path in here.iterdir())
            self.assertEqual(left, ["calls.txt", "data", "notes of mine.txt"])
            self.assertTrue((here / "data" / "flows" / "mine.json").exists())

    def test_deleting_the_data_removes_the_secrets_and_the_folder(self):
        with folder() as parent:
            here = self.install(parent)
            result = self.uninstall(here, "--delete-data")
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
            self.assertEqual(self.calls(here), ["--stop", "--forget-secrets"])
            (here / "calls.txt").unlink()
            # Only the stand-in's own record was left, so the folder itself is gone once it is.
            self.assertEqual(list(here.iterdir()), [])

    def test_the_folder_goes_when_nothing_is_left(self):
        with folder() as parent:
            here = self.install(parent)
            write(here / "app" / "main.py", "")  # records nothing
            result = self.uninstall(here, "--delete-data")
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
            self.assertFalse(here.exists())

    def test_removes_its_own_shortcuts_only(self):
        programs = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        mine = programs / "Uninstall Web Handler.lnk"
        other = programs / "Web Handler.lnk"
        if mine.exists():
            self.skipTest("Web Handler is installed on this machine with its own uninstall shortcut.")
        other_before = other.read_bytes() if other.exists() else None
        powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        with folder() as parent:
            here = self.install(parent)
            subprocess.run(
                [str(powershell), "-NoProfile", "-Command",
                 "$l = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LINK); "
                 "$l.TargetPath = $env:TARGET; $l.Save()"],
                env=dict(os.environ, LINK=str(mine), TARGET=str(here / "Uninstall.bat")), check=True,
            )
            try:
                result = self.uninstall(here)
                self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
                self.assertFalse(mine.exists())
            finally:
                mine.unlink(missing_ok=True)
        self.assertEqual(other.read_bytes() if other.exists() else None, other_before)

    def test_nothing_is_removed_when_the_app_cannot_be_closed(self):
        with folder() as parent:
            here = self.install(parent)
            write(here / "app" / "main.py", "raise SystemExit(1)")
            result = self.uninstall(here, "--delete-data")
            self.assertEqual(result.returncode, 1)
            self.assertTrue((here / "app" / "main.py").exists())
            self.assertTrue((here / "data" / "flows" / "mine.json").exists())
            self.assertTrue((here / "Uninstall.bat").exists())

    def test_refuses_a_folder_that_is_not_an_install(self):
        with folder() as parent:
            here = Path(parent) / "other"
            write(here / "important.txt")
            shutil.copy2(ROOT / "Uninstall.bat", here / "Uninstall.bat")
            result = self.uninstall(here, "--delete-data")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(sorted(path.name for path in here.iterdir()), ["Uninstall.bat", "important.txt"])

    def test_refuses_the_source_folder(self):
        result = self.uninstall(ROOT, "--delete-data")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"source folder", result.stdout)
        self.assertTrue((ROOT / "Uninstall.bat").exists())

    def test_hands_over_to_the_setup_uninstaller(self):
        with folder() as parent:
            here = self.install(parent)
            # A stand-in that only leaves a mark, in place of Inno Setup's uninstaller.
            shutil.copy2(Path(os.environ["SystemRoot"]) / "System32" / "where.exe", here / "unins000.exe")
            result = self.uninstall(here, "--delete-data")
            self.assertEqual(result.returncode, 0)
            self.assertIn(b"Setup exe", result.stdout)
            self.assertTrue((here / "app" / "main.py").exists())
            self.assertFalse((here / "calls.txt").exists())


if __name__ == "__main__":
    unittest.main()
