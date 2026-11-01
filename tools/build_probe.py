"""Build dist/WebHandlerProbe.zip. Run on a Windows machine with internet access."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

PYTHON_RELEASE = "20261026"
PYTHON_FULL_VERSION = "3.12.16"
PYTHON_ARCHIVE = (
    f"cpython-{PYTHON_FULL_VERSION}+{PYTHON_RELEASE}-x86_64-pc-windows-msvc-install_only.tar.gz"
)
PYTHON_SHA256 = "590dfcbe3a34c07301e6332e13e2188b98655cd072b08afa45a7998dc4c22bc5"
PYTHON_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{PYTHON_RELEASE}/{PYTHON_ARCHIVE.replace('+', '%2B')}"
)

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


def download_python():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    archive = CACHE_DIR / PYTHON_ARCHIVE
    if not archive.exists():
        print(f"Downloading {PYTHON_ARCHIVE}...")
        partial = archive.with_name(archive.name + ".part")
        urllib.request.urlretrieve(PYTHON_URL, partial)
        partial.replace(archive)
    actual = sha256(archive)
    if actual != PYTHON_SHA256:
        archive.unlink()
        raise RuntimeError(f"Python archive checksum mismatch: {actual}")
    print(f"Python archive verified: {actual}")
    return archive


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


def download_wheels(destination):
    subprocess.run(
        [
            sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
            "--only-binary=:all:", "--platform", "win_amd64", "--python-version", "3.12",
            "--implementation", "cp", "--abi", "cp312",
            "--dest", str(destination), "-r", str(REQUIREMENTS),
        ],
        check=True,
    )


def write_crlf(source, destination):
    text = source.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
    encoding = "utf-8-sig" if source.suffix == ".txt" else "utf-8"
    with destination.open("w", encoding=encoding, newline="") as file:
        file.write(text)


def build():
    if sys.platform != "win32":
        raise SystemExit("The probe package must be built on Windows.")

    archive = download_python()
    package = DIST_DIR / PACKAGE_NAME
    if package.exists():
        shutil.rmtree(package)
    offline = package / "offline"
    (offline / "wheels").mkdir(parents=True)

    print("Packing Python...")
    repack_python(archive, offline / "python.zip")
    print("Downloading libraries...")
    download_wheels(offline / "wheels")

    shutil.copy2(PROBE_DIR / "probe.py", package / "probe.py")
    write_crlf(PROBE_DIR / "Probe.bat", package / "Probe.bat")
    write_crlf(PROBE_DIR / "README.txt", package / "README.txt")
    shutil.copy2(REQUIREMENTS, offline / "requirements.txt")

    print("Creating the zip file...")
    output = shutil.make_archive(str(DIST_DIR / PACKAGE_NAME), "zip", DIST_DIR, PACKAGE_NAME)
    size_mb = Path(output).stat().st_size / (1024 * 1024)
    print(f"Done: {output} ({size_mb:.0f} MB)")
    print(f"SHA-256: {sha256(Path(output))}")


if __name__ == "__main__":
    build()
