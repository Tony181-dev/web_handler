"""Build dist/WebHandlerProbe.zip. Run on the system it is for, Windows or macOS, with internet access."""
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

PYTHON_RELEASE = "20261026"
PYTHON_FULL_VERSION = "3.12.16"
# For each system a package is built for: its python-build-standalone name, the SHA-256 from the
# release's SHA256SUMS, and the wheels pip should pick for it. macOS 14 is the oldest that the
# Apple silicon wheels of numpy and onnxruntime support, macOS 13 on Intel.
TARGETS = {
    "windows-x86_64": {
        "python": "x86_64-pc-windows-msvc",
        "sha256": "590dfcbe3a34c07301e6332e13e2188b98655cd072b08afa45a7998dc4c22bc5",
        "wheels": "win_amd64",
    },
    "macos-arm64": {
        "python": "aarch64-apple-darwin",
        "sha256": "fbf4afef53c2a6b72bb741bc06d5671a9dfaf87bd84e5aa73ac9b3d0d589f14e",
        "wheels": "macosx_14_0_arm64",
    },
    "macos-x86_64": {
        "python": "x86_64-apple-darwin",
        "sha256": "a962e47df522889eaa7ff0d6a56203f1079f68aabb65ffdbe8455f362bc769f3",
        "wheels": "macosx_13_0_x86_64",
    },
}

ROOT = Path(__file__).resolve().parents[1]
PROBE_DIR = ROOT / "probe"
REQUIREMENTS = ROOT / "tools" / "probe_requirements.txt"
CACHE_DIR = ROOT / "build" / "cache"
DIST_DIR = ROOT / "dist"
PACKAGE_NAME = "WebHandlerProbe"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def host_target():
    """The system this machine builds for: its own."""
    if sys.platform == "win32":
        return "windows-x86_64"
    if sys.platform == "darwin":
        return "macos-arm64" if platform.machine() == "arm64" else "macos-x86_64"
    raise SystemExit("Packages are built on Windows or macOS.")


def is_windows(target):
    return target.startswith("windows")


def python_archive_name(target):
    return f"cpython-{PYTHON_FULL_VERSION}+{PYTHON_RELEASE}-{TARGETS[target]['python']}-install_only.tar.gz"


def download_python(target):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    name = python_archive_name(target)
    archive = CACHE_DIR / name
    if not archive.exists():
        print(f"Downloading {name}...")
        partial = archive.with_name(archive.name + ".part")
        url = (
            "https://github.com/astral-sh/python-build-standalone/releases/download/"
            f"{PYTHON_RELEASE}/{name.replace('+', '%2B')}"
        )
        urllib.request.urlretrieve(url, partial)
        partial.replace(archive)
    actual = sha256(archive)
    if actual != TARGETS[target]["sha256"]:
        archive.unlink()
        raise RuntimeError(f"Python archive checksum mismatch: {actual}")
    print(f"Python archive verified: {actual}")
    return archive


def pack_python(archive, offline, target):
    """Put Python in the package: a zip for Start.bat to unpack, the release tar as it is on macOS.

    A tar keeps the links and run permissions the macOS Python needs, and macOS has tar built in.
    """
    if is_windows(target):
        repack_python(archive, offline / "python.zip")
    else:
        shutil.copy2(archive, offline / "python.tar.gz")


def repack_python(archive, destination):
    """Convert the release archive to the zip the package format expects."""
    with tempfile.TemporaryDirectory(dir=CACHE_DIR) as folder:
        with tarfile.open(archive) as tar:
            tar.extractall(folder, filter="data")
        extracted = Path(folder)
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path in sorted((extracted / "python").rglob("*")):
                if path.is_file():
                    bundle.write(path, path.relative_to(extracted).as_posix())


def download_wheels(destination, target):
    subprocess.run(
        [
            sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
            "--only-binary=:all:", "--platform", TARGETS[target]["wheels"], "--python-version", "3.12",
            "--implementation", "cp", "--abi", "cp312",
            "--dest", str(destination), "-r", str(REQUIREMENTS),
        ],
        check=True,
    )


def write_lf(source, destination, executable=False):
    """A script or text for macOS: bash cannot run a script with Windows line endings."""
    text = source.read_text(encoding="utf-8").replace("\r\n", "\n")
    with destination.open("w", encoding="utf-8", newline="") as file:
        file.write(text)
    if executable:
        destination.chmod(0o755)


def write_crlf(source, destination):
    text = source.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
    encoding = "utf-8-sig" if source.suffix == ".txt" else "utf-8"
    with destination.open("w", encoding=encoding, newline="") as file:
        file.write(text)


def build():
    target = host_target()
    archive = download_python(target)
    package = DIST_DIR / PACKAGE_NAME
    if package.exists():
        shutil.rmtree(package)
    offline = package / "offline"
    (offline / "wheels").mkdir(parents=True)

    print("Packing Python...")
    pack_python(archive, offline, target)
    print("Downloading libraries...")
    download_wheels(offline / "wheels", target)

    shutil.copy2(PROBE_DIR / "probe.py", package / "probe.py")
    if is_windows(target):
        write_crlf(PROBE_DIR / "Probe.bat", package / "Probe.bat")
        write_crlf(PROBE_DIR / "README.txt", package / "README.txt")
    else:
        write_lf(PROBE_DIR / "Probe.command", package / "Probe.command", executable=True)
        write_lf(PROBE_DIR / "README.txt", package / "README.txt")
    shutil.copy2(REQUIREMENTS, offline / "requirements.txt")

    print("Creating the zip file...")
    output = shutil.make_archive(str(DIST_DIR / PACKAGE_NAME), "zip", DIST_DIR, PACKAGE_NAME)
    size_mb = Path(output).stat().st_size / (1024 * 1024)
    print(f"Done: {output} ({size_mb:.0f} MB)")
    print(f"SHA-256: {sha256(Path(output))}")


if __name__ == "__main__":
    build()
