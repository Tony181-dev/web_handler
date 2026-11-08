"""Build the release for the system this runs on, with internet access.

- Windows, with Inno Setup 6: dist/WebHandler-<version>.zip and dist/WebHandler-Setup-<version>.exe.
- macOS: dist/WebHandler-<version>-macOS-<arm64 or x86_64>.zip, for Macs of the same kind as this one.
A finished build leaves only the new release in dist: the zip, the Setup exe on Windows and the unpacked
WebHandler folder, whose data and runtime are kept. A failed build leaves dist as it was.
Use --no-chromium for a smaller package that relies on Chrome being installed.
Use --no-installer to build only the zip. macOS has no Setup exe, so there it is always only the zip.
"""
import argparse
import filecmp
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_probe  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "tools" / "requirements.txt"
# Every wheel the package ships, pinned with its SHA-256: pip refuses a file that differs,
# so a library replaced upstream cannot slip into a build. Made from REQUIREMENTS by --update-lock,
# one for each system, as the wheels differ.
LOCKS = {
    "windows-x86_64": ROOT / "tools" / "requirements.lock",
    "macos-arm64": ROOT / "tools" / "requirements-macos-arm64.lock",
    "macos-x86_64": ROOT / "tools" / "requirements-macos-x86_64.lock",
}
CACHE_DIR = ROOT / "build" / "cache"
STAGING_DIR = ROOT / "build" / "package"
DIST_DIR = ROOT / "dist"
PACKAGE_NAME = "WebHandler"
# Made on the machine by the app itself, never by a build: someone's flows and sessions, and the
# installed Python and libraries. Refreshing the unpacked copy leaves them alone.
KEPT_IN_PACKAGE = {"data", "runtime"}


def version():
    namespace = {}
    exec((ROOT / "app" / "web_handler" / "__init__.py").read_text(encoding="utf-8"), namespace)
    return namespace["__version__"]


def pip_download(destination, target, *arguments):
    subprocess.run(
        [
            sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
            "--only-binary=:all:", "--platform", build_probe.TARGETS[target]["wheels"], "--python-version", "3.12",
            "--implementation", "cp", "--abi", "cp312",
            "--dest", str(destination), *arguments,
        ],
        check=True,
    )


def download_wheels(destination, target):
    pip_download(destination, target, "--require-hashes", "--no-deps", "-r", str(LOCKS[target]))


def lock_line(wheel):
    """name==version with the wheel's hash, from a wheel file name such as numpy-2.5.4-cp312-...whl."""
    name, wheel_version = wheel.name.split("-")[:2]
    return f"{name}=={wheel_version} \\\n    --hash=sha256:{build_probe.sha256(wheel)}"


def update_lock(target):
    """Resolve REQUIREMENTS again and pin what it gives. Review the changes before building with them."""
    lock = LOCKS[target]
    with tempfile.TemporaryDirectory() as folder:
        pip_download(Path(folder), target, "-r", str(REQUIREMENTS))
        wheels = sorted(Path(folder).glob("*.whl"), key=lambda path: path.name.lower())
        lines = [lock_line(wheel) for wheel in wheels]
    lock.write_text(
        f"# Made by tools/build_package.py --update-lock --target {target} from requirements.txt. Do not edit by hand.\n"
        + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(f"Pinned {len(lines)} wheels in {lock}")


def build_python(archive, target):
    """The package's own Python, unpacked once for the build: Playwright runs on the Python it was made for."""
    folder = CACHE_DIR / f"python-{target}"
    if not folder.is_dir():
        partial = CACHE_DIR / f"python-{target}.part"
        shutil.rmtree(partial, ignore_errors=True)
        with tarfile.open(archive) as tar:
            tar.extractall(partial, filter="tar")
        partial.replace(folder)
    if build_probe.is_windows(target):
        return folder / "python" / "python.exe"
    return folder / "python" / "bin" / "python3"


def playwright_site(python, target):
    """Playwright for downloading Chromium, installed from the lock for the build alone."""
    tools = CACHE_DIR / f"tools-{target}"
    if not (tools / "playwright").is_dir():
        subprocess.run(
            [
                str(python), "-m", "pip", "install", "--isolated", "--disable-pip-version-check",
                "--require-hashes", "--target", str(tools), "-r", str(LOCKS[target]),
            ],
            check=True,
        )
    return tools


def chromium_archive(destination, python, target):
    """Download Chromium with the bundled Playwright, once, and pack only what is needed.

    A zip for Windows. A tar for macOS: the app bundle holds links and run permissions a zip loses.
    """
    browsers = CACHE_DIR / f"browsers-{target}"
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(browsers), PYTHONNOUSERSITE="1")
    env["PYTHONPATH"] = str(playwright_site(python, target))
    subprocess.run([str(python), "-m", "playwright", "install", "--no-shell", "chromium"], check=True, env=env)
    folders = sorted(browsers.glob("chromium-*"))
    if not folders:
        raise RuntimeError("Chromium was not downloaded.")
    source = folders[-1]

    def wanted(path):
        return not path.name.startswith("INSTALLATION_COMPLETE")

    if build_probe.is_windows(target):
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path in sorted(source.rglob("*")):
                if path.is_file() and wanted(path):
                    bundle.write(path, path.relative_to(source.parent).as_posix())
        return
    with tarfile.open(destination, "w:gz", compresslevel=6) as bundle:
        bundle.add(source, source.name, filter=lambda info: info if wanted(Path(info.name)) else None)


def copy_app(package):
    def ignore(folder, names):
        return [name for name in names if name == "__pycache__" or name.endswith(".pyc")]

    shutil.copytree(ROOT / "app", package / "app", ignore=ignore)


def remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def refresh_folder(source, target):
    """Make an unpacked copy match the new package, in place: it may be running and holds someone's data.

    Only changed files are written: cmd.exe misbehaves when a running Start.bat is rewritten.
    Files the new package no longer has are removed, except the folders in KEPT_IN_PACKAGE.
    """
    for path in source.rglob("*"):
        destination = target / path.relative_to(source)
        if path.is_file() and not (destination.exists() and filecmp.cmp(path, destination, shallow=False)):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    remove_extra(source, target, KEPT_IN_PACKAGE)


def remove_extra(source, target, keep=()):
    """Remove what is in target but not in source. Kept folders are not even looked into."""
    for path in target.iterdir():
        if path.name in keep:
            continue
        counterpart = source / path.name
        if not counterpart.exists():
            remove(path)
        elif path.is_dir() and not path.is_symlink() and counterpart.is_dir():
            remove_extra(counterpart, path)


def clear_dist(keep):
    """Remove earlier releases and other leftovers from dist, so it holds only what this build made."""
    for path in DIST_DIR.iterdir():
        if path.name not in keep:
            try:
                remove(path)
            except OSError as error:
                raise SystemExit(f"Could not remove {path}: {error}. Close what is using it and build again.")


def find_inno_setup():
    folders = [os.environ.get(name) for name in ("LOCALAPPDATA", "ProgramFiles(x86)", "ProgramFiles")]
    candidates = [Path(folder) for folder in folders if folder]
    candidates = [candidates[0] / "Programs", *candidates[1:]] if os.environ.get("LOCALAPPDATA") else candidates
    for folder in candidates:
        compiler = folder / "Inno Setup 6" / "ISCC.exe"
        if compiler.is_file():
            return compiler
    return None


def build_installer(package, compiler, output_dir):
    script = STAGING_DIR / "installer.iss"
    shutil.copy2(ROOT / "tools" / "installer.iss", script)
    subprocess.run(
        [
            str(compiler), "/Q", f"/DAppVersion={version()}", f"/DSourceDir={package}",
            f"/DOutputDir={output_dir}", str(script),
        ],
        check=True,
    )
    return output_dir / f"{PACKAGE_NAME}-Setup-{version()}.exe"


def copy_scripts(package, target):
    """The files a person opens, with the line endings their system needs."""
    if build_probe.is_windows(target):
        for name in ("Start.bat", "README.txt", "README.vi.txt", "Install.bat", "Uninstall.bat"):
            build_probe.write_crlf(ROOT / name, package / name)
        return
    for name in ("Start.command", "Install.command", "Uninstall.command"):
        build_probe.write_lf(ROOT / name, package / name, executable=True)
    for name in ("README.txt", "README.vi.txt"):
        build_probe.write_lf(ROOT / name, package / name)


def release_name(target):
    if build_probe.is_windows(target):
        return f"{PACKAGE_NAME}-{version()}"
    return f"{PACKAGE_NAME}-{version()}-macOS-{target.split('-', 1)[1]}"


def build(with_chromium, with_installer):
    target = build_probe.host_target()
    with_installer = with_installer and build_probe.is_windows(target)
    compiler = find_inno_setup() if with_installer else None
    if with_installer and compiler is None:
        raise SystemExit(
            "Inno Setup 6 was not found. Install it from https://jrsoftware.org/isdl.php, or use --no-installer."
        )
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    build_probe.CACHE_DIR = CACHE_DIR
    archive = build_probe.download_python(target)
    package = STAGING_DIR / PACKAGE_NAME
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    offline = package / "offline"
    (offline / "wheels").mkdir(parents=True)

    print("Packing Python...")
    build_probe.pack_python(archive, offline, target)
    print("Downloading libraries...")
    download_wheels(offline / "wheels", target)
    # The machine installing it checks every wheel against these hashes too.
    shutil.copy2(LOCKS[target], offline / "requirements.txt")
    if with_chromium:
        print("Packing Chromium...")
        name = "chromium.zip" if build_probe.is_windows(target) else "chromium.tar.gz"
        chromium_archive(offline / name, build_python(archive, target), target)

    copy_app(package)
    copy_scripts(package, target)

    # Made next to the package first: dist only changes once everything is built.
    made = STAGING_DIR / "release"
    made.mkdir()
    print("Creating the zip file...")
    name = release_name(target)
    built = [Path(shutil.make_archive(str(made / name), "zip", STAGING_DIR, PACKAGE_NAME))]
    if compiler:
        print("Creating the installer...")
        built.append(build_installer(package, compiler, made))

    print("Clearing the dist folder...")
    clear_dist(keep={PACKAGE_NAME})
    outputs = [Path(shutil.move(str(path), str(DIST_DIR / path.name))) for path in built]
    (DIST_DIR / PACKAGE_NAME).mkdir(exist_ok=True)
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
    parser.add_argument("--target", choices=sorted(LOCKS), help="the system whose lock --update-lock makes (default: this one)")
    options = parser.parse_args()
    if options.update_lock:
        update_lock(options.target or build_probe.host_target())
    else:
        build(not options.no_chromium, not options.no_installer)
