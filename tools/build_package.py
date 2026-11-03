"""Build dist/WebHandler-<version>.zip and dist/WebHandler-Setup-<version>.exe.

Run on a Windows machine with internet access and Inno Setup 6.
Use --no-chromium for a smaller package that relies on Chrome being installed.
Use --no-installer to build only the zip.
"""
import argparse
import filecmp
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_probe  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "tools" / "requirements.txt"
# Every wheel the package ships, pinned with its SHA-256: pip refuses a file that differs,
# so a library replaced upstream cannot slip into a build. Made from REQUIREMENTS by --update-lock.
LOCK = ROOT / "tools" / "requirements.lock"
CACHE_DIR = ROOT / "build" / "cache"
STAGING_DIR = ROOT / "build" / "package"
DIST_DIR = ROOT / "dist"
PACKAGE_NAME = "WebHandler"


def version():
    namespace = {}
    exec((ROOT / "app" / "web_handler" / "__init__.py").read_text(encoding="utf-8"), namespace)
    return namespace["__version__"]


def pip_download(destination, *arguments):
    subprocess.run(
        [
            sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
            "--only-binary=:all:", "--platform", "win_amd64", "--python-version", "3.12",
            "--implementation", "cp", "--abi", "cp312",
            "--dest", str(destination), *arguments,
        ],
        check=True,
    )


def download_wheels(destination):
    pip_download(destination, "--require-hashes", "--no-deps", "-r", str(LOCK))


def lock_line(wheel):
    """name==version with the wheel's hash, from a wheel file name such as numpy-2.5.4-cp312-...whl."""
    name, wheel_version = wheel.name.split("-")[:2]
    return f"{name}=={wheel_version} \\\n    --hash=sha256:{build_probe.sha256(wheel)}"


def update_lock():
    """Resolve REQUIREMENTS again and pin what it gives. Review the changes before building with them."""
    with tempfile.TemporaryDirectory() as folder:
        pip_download(Path(folder), "-r", str(REQUIREMENTS))
        wheels = sorted(Path(folder).glob("*.whl"), key=lambda path: path.name.lower())
        lines = [lock_line(wheel) for wheel in wheels]
    LOCK.write_text(
        "# Made by tools/build_package.py --update-lock from requirements.txt. Do not edit by hand.\n"
        + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(f"Pinned {len(lines)} wheels in {LOCK}")


def playwright_site():
    """Playwright for downloading Chromium: the app's own copy, or a private one on a fresh checkout."""
    site = ROOT / "runtime" / "site"
    if (site / "playwright").is_dir():
        return site
    tools = CACHE_DIR / "tools"
    if not (tools / "playwright").is_dir():
        subprocess.run(
            [
                sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                "--require-hashes", "--target", str(tools), "-r", str(LOCK),
            ],
            check=True,
        )
    return tools


def chromium_zip(destination):
    """Download Chromium with the bundled Playwright, once, and pack only what is needed."""
    browsers = CACHE_DIR / "browsers"
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(browsers))
    env["PYTHONPATH"] = str(playwright_site())
    subprocess.run([sys.executable, "-m", "playwright", "install", "--no-shell", "chromium"], check=True, env=env)
    folders = sorted(browsers.glob("chromium-*"))
    if not folders:
        raise RuntimeError("Chromium was not downloaded.")
    source = folders[-1]
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(source.rglob("*")):
            if path.is_file() and not path.name.startswith("INSTALLATION_COMPLETE"):
                bundle.write(path, path.relative_to(source.parent).as_posix())


def copy_app(package):
    def ignore(folder, names):
        return [name for name in names if name == "__pycache__" or name.endswith(".pyc")]

    shutil.copytree(ROOT / "app", package / "app", ignore=ignore)


def refresh_folder(source, target):
    """Update an unpacked copy in place, which may be running and holds someone's data and runtime.

    Only changed files are written: cmd.exe misbehaves when a running Start.bat is rewritten.
    """
    for path in source.rglob("*"):
        destination = target / path.relative_to(source)
        if path.is_file() and not (destination.exists() and filecmp.cmp(path, destination, shallow=False)):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def find_inno_setup():
    folders = [os.environ.get(name) for name in ("LOCALAPPDATA", "ProgramFiles(x86)", "ProgramFiles")]
    candidates = [Path(folder) for folder in folders if folder]
    candidates = [candidates[0] / "Programs", *candidates[1:]] if os.environ.get("LOCALAPPDATA") else candidates
    for folder in candidates:
        compiler = folder / "Inno Setup 6" / "ISCC.exe"
        if compiler.is_file():
            return compiler
    return None


def build_installer(package, compiler):
    script = STAGING_DIR / "installer.iss"
    shutil.copy2(ROOT / "tools" / "installer.iss", script)
    subprocess.run(
        [
            str(compiler), "/Q", f"/DAppVersion={version()}", f"/DSourceDir={package}",
            f"/DOutputDir={DIST_DIR}", str(script),
        ],
        check=True,
    )
    return DIST_DIR / f"{PACKAGE_NAME}-Setup-{version()}.exe"


def build(with_chromium, with_installer):
    if sys.platform != "win32":
        raise SystemExit("The package must be built on Windows.")
    compiler = find_inno_setup() if with_installer else None
    if with_installer and compiler is None:
        raise SystemExit(
            "Inno Setup 6 was not found. Install it from https://jrsoftware.org/isdl.php, or use --no-installer."
        )
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    build_probe.CACHE_DIR = CACHE_DIR
    archive = build_probe.download_python()
    package = STAGING_DIR / PACKAGE_NAME
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    offline = package / "offline"
    (offline / "wheels").mkdir(parents=True)

    print("Packing Python...")
    build_probe.repack_python(archive, offline / "python.zip")
    print("Downloading libraries...")
    download_wheels(offline / "wheels")
    # The machine installing it checks every wheel against these hashes too.
    shutil.copy2(LOCK, offline / "requirements.txt")
    if with_chromium:
        print("Packing Chromium...")
        chromium_zip(offline / "chromium.zip")

    copy_app(package)
    build_probe.write_crlf(ROOT / "Start.bat", package / "Start.bat")
    build_probe.write_crlf(ROOT / "README.txt", package / "README.txt")
    build_probe.write_crlf(ROOT / "README.vi.txt", package / "README.vi.txt")
    build_probe.write_crlf(ROOT / "Install.bat", package / "Install.bat")

    print("Creating the zip file...")
    name = f"{PACKAGE_NAME}-{version()}"
    outputs = [Path(shutil.make_archive(str(DIST_DIR / name), "zip", STAGING_DIR, PACKAGE_NAME))]
    if compiler:
        print("Creating the installer...")
        outputs.append(build_installer(package, compiler))
    refresh_folder(package, DIST_DIR / PACKAGE_NAME)
    shutil.rmtree(STAGING_DIR)
    for output in outputs:
        size_mb = output.stat().st_size / (1024 * 1024)
        print(f"Done: {output} ({size_mb:.0f} MB)")
        print(f"SHA-256: {build_probe.sha256(output)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-chromium", action="store_true")
    parser.add_argument("--no-installer", action="store_true")
    parser.add_argument("--update-lock", action="store_true", help="pin the libraries again from requirements.txt, then stop")
    options = parser.parse_args()
    if options.update_lock:
        update_lock()
    else:
        build(not options.no_chromium, not options.no_installer)
