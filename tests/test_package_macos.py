"""Install.command and Uninstall.command, on macOS only."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from test_package import FAKE_MAIN, ROOT, SCRATCH, folder, write  # noqa: E402


@unittest.skipUnless(sys.platform == "darwin", "macOS shell scripts")
class MacScriptTests(unittest.TestCase):
    """Install.command and Uninstall.command, with a stand-in app recording what it was asked to do.

    HOME points into the test folder, so the launchers are made and removed there, never in the
    person's own Applications folder.
    """

    def setUp(self):
        self.scratch = folder()
        self.addCleanup(self.scratch.cleanup)
        self.home = Path(self.scratch.name) / "home"
        self.home.mkdir()

    def env(self):
        env = dict(os.environ, HOME=str(self.home))
        # The stand-in app runs on the Python running these tests.
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        env.pop("WEB_HANDLER_NO_PAUSE", None)
        return env

    def run_script(self, script, *arguments):
        return subprocess.run(
            ["/bin/bash", str(script), *arguments], cwd=str(SCRATCH), env=self.env(),
            stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
        )

    def package(self):
        """An unpacked zip, in a folder whose name bash would misread unquoted."""
        source = Path(self.scratch.name) / "Web & Handler $(x) 'zip'"
        for name in ("Start.command", "Install.command", "Uninstall.command", "README.txt", "README.vi.txt"):
            write(source / name)
            shutil.copy2(ROOT / name, source / name)
        write(source / "app" / "main.py", FAKE_MAIN)
        write(source / "app" / "web_handler" / "__pycache__" / "x.pyc")
        write(source / "offline" / "wheels" / "a.whl")
        return source

    def install(self):
        here = Path(self.scratch.name) / "Web & Handler $(x) 'installed'"
        here.mkdir()
        shutil.copy2(ROOT / "Start.command", here / "Start.command")
        shutil.copy2(ROOT / "Uninstall.command", here / "Uninstall.command")
        write(here / "app" / "main.py", FAKE_MAIN)
        write(here / "offline" / "wheels" / "a.whl")
        write(here / "runtime" / "site" / "lib.py")
        write(here / "README.txt")
        write(here / "data" / "flows" / "mine.json")
        return here

    def uninstall(self, here, *arguments):
        return self.run_script(here / "Uninstall.command", "--yes", *arguments)

    def calls(self, here):
        return (here / "calls.txt").read_text(encoding="utf-8").split()

    def launcher(self, name, folder):
        app = self.home / "Applications" / f"{name}.app"
        write(app / "Contents" / "Resources" / "folder", str(folder))
        return app

    def test_install_copies_the_app_and_makes_launchers(self):
        source = self.package()
        target = Path(self.scratch.name) / "target with space"
        result = self.run_script(source / "Install.command", str(target))
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
        self.assertEqual(self.calls(target), ["--install"])
        names = sorted(path.name for path in target.iterdir())
        self.assertEqual(names, [
            "README.txt", "README.vi.txt", "Start.command", "Uninstall.command", "app", "calls.txt", "offline",
        ])
        self.assertFalse((target / "app" / "web_handler" / "__pycache__").exists())
        self.assertTrue(os.access(target / "Start.command", os.X_OK))
        for name, script in (("Web Handler", "Start.command"), ("Uninstall Web Handler", "Uninstall.command")):
            resources = self.home / "Applications" / f"{name}.app" / "Contents" / "Resources"
            self.assertEqual((resources / "folder").read_text(encoding="utf-8"), str(target))
            self.assertEqual((resources / "script").read_text(encoding="utf-8"), script)
            launcher = resources.parent / "MacOS" / "launcher"
            self.assertTrue(os.access(launcher, os.X_OK))
            plist = subprocess.run(["/usr/bin/plutil", "-lint", str(resources.parent / "Info.plist")], capture_output=True)
            self.assertEqual(plist.returncode, 0, plist.stdout)

    def test_installing_again_closes_the_app_and_keeps_the_data(self):
        source = self.package()
        target = Path(self.scratch.name) / "target"
        self.assertEqual(self.run_script(source / "Install.command", str(target)).returncode, 0)
        write(target / "data" / "flows" / "mine.json", "keep me")
        write(target / "app" / "old.py")
        result = self.run_script(source / "Install.command", str(target))
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
        self.assertEqual(self.calls(target), ["--install", "--stop", "--install"])
        self.assertEqual((target / "data" / "flows" / "mine.json").read_text(encoding="utf-8"), "keep me")
        self.assertFalse((target / "app" / "old.py").exists())

    def test_install_refuses_a_folder_holding_other_files(self):
        source = self.package()
        target = Path(self.scratch.name) / "busy"
        write(target / "important.txt")
        result = self.run_script(source / "Install.command", str(target))
        self.assertEqual(result.returncode, 1)
        self.assertEqual([path.name for path in target.iterdir()], ["important.txt"])

    def test_keeps_the_data_and_anything_it_did_not_install(self):
        here = self.install()
        write(here / "notes of mine.txt")
        result = self.uninstall(here)
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
        self.assertEqual(self.calls(here), ["--stop"])
        left = sorted(path.name for path in here.iterdir())
        self.assertEqual(left, ["calls.txt", "data", "notes of mine.txt"])
        self.assertTrue((here / "data" / "flows" / "mine.json").exists())

    def test_deleting_the_data_removes_the_secrets_and_the_folder(self):
        here = self.install()
        result = self.uninstall(here, "--delete-data")
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
        self.assertEqual(self.calls(here), ["--stop", "--forget-secrets"])
        (here / "calls.txt").unlink()
        self.assertEqual(list(here.iterdir()), [])

    def test_the_folder_goes_when_nothing_is_left(self):
        here = self.install()
        write(here / "app" / "main.py", "")  # records nothing
        write(here / ".DS_Store")
        result = self.uninstall(here, "--delete-data")
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
        self.assertFalse(here.exists())

    def test_removes_its_own_launchers_only(self):
        here = self.install()
        mine = self.launcher("Uninstall Web Handler", here)
        other = self.launcher("Web Handler", Path(self.scratch.name) / "another install")
        result = self.uninstall(here)
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
        self.assertFalse(mine.exists())
        self.assertTrue(other.exists())

    def test_nothing_is_removed_when_the_app_cannot_be_closed(self):
        here = self.install()
        write(here / "app" / "main.py", "raise SystemExit(1)")
        result = self.uninstall(here, "--delete-data")
        self.assertEqual(result.returncode, 1)
        self.assertTrue((here / "app" / "main.py").exists())
        self.assertTrue((here / "data" / "flows" / "mine.json").exists())
        self.assertTrue((here / "Uninstall.command").exists())

    def test_refuses_a_folder_that_is_not_an_install(self):
        here = Path(self.scratch.name) / "other"
        write(here / "important.txt")
        shutil.copy2(ROOT / "Uninstall.command", here / "Uninstall.command")
        result = self.uninstall(here, "--delete-data")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(sorted(path.name for path in here.iterdir()), ["Uninstall.command", "important.txt"])

    def test_refuses_the_source_folder(self):
        result = self.uninstall(ROOT, "--delete-data")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"source folder", result.stdout)
        self.assertTrue((ROOT / "Uninstall.command").exists())


if __name__ == "__main__":
    unittest.main()
