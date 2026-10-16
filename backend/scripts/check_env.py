"""Phase 0 environment check. Run from backend/:  python scripts/check_env.py

Checks Python version, required packages, ffmpeg/ffprobe, and a tiny edge-tts synthesis.
Exits with code 1 if anything required is missing.
"""

import asyncio
import importlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OK = "[ OK ]"
FAIL = "[FAIL]"
WARN = "[WARN]"

failures = 0


def report(ok: bool, label: str, detail: str = "", required: bool = True) -> None:
    global failures
    tag = OK if ok else (FAIL if required else WARN)
    print(f"{tag} {label}" + (f" — {detail}" if detail else ""))
    if not ok and required:
        failures += 1


def check_python() -> None:
    v = sys.version_info
    ok = (v.major, v.minor) in {(3, 11), (3, 12)}
    report(ok, "Python 3.11 or 3.12", f"found {v.major}.{v.minor}.{v.micro}")


def check_packages() -> None:
    packages = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "multipart": "python-multipart",
        "pydantic_settings": "pydantic-settings",
        "fitz": "pymupdf",
        "docx": "python-docx",
        "edge_tts": "edge-tts",
        "num2words": "num2words",
        "rapidfuzz": "rapidfuzz",
        "pytest": "pytest",
        "httpx": "httpx",
    }
    for module, pip_name in packages.items():
        try:
            importlib.import_module(module)
            report(True, f"package {pip_name}")
        except ImportError as e:
            report(False, f"package {pip_name}", str(e))

    # Piper is the fallback engine; not fatal in Phase 0
    try:
        importlib.import_module("piper")
        report(True, "package piper-tts")
    except ImportError as e:
        report(False, "package piper-tts (fallback, needed by Phase 2)", str(e), required=False)


def check_binary(name: str) -> None:
    path = shutil.which(name)
    if not path:
        report(False, f"{name} on PATH", "not found")
        return
    try:
        out = subprocess.run([name, "-version"], capture_output=True, text=True, timeout=15)
        first = out.stdout.splitlines()[0] if out.stdout else path
        report(out.returncode == 0, f"{name} on PATH", first)
    except Exception as e:  # noqa: BLE001
        report(False, f"{name} on PATH", str(e))


async def _edge_tts_sample(target: Path) -> None:
    import edge_tts

    communicate = edge_tts.Communicate("Environment check. Hello from the audiobook generator.", "en-US-AriaNeural")
    await communicate.save(str(target))


def check_edge_tts() -> None:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "check.mp3"
            asyncio.run(_edge_tts_sample(target))
            size = target.stat().st_size if target.exists() else 0
            report(size > 1000, "edge-tts synthesis (needs internet)", f"{size} bytes")
    except Exception as e:  # noqa: BLE001
        report(False, "edge-tts synthesis (needs internet)", repr(e))


def main() -> None:
    print("Audiobook Generator — environment check\n")
    check_python()
    check_packages()
    check_binary("ffmpeg")
    check_binary("ffprobe")
    check_edge_tts()
    print()
    if failures:
        print(f"{failures} required check(s) failed.")
        sys.exit(1)
    print("All required checks passed.")


if __name__ == "__main__":
    main()
