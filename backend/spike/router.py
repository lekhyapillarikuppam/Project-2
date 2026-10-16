"""GET /spike/{name}: runs one whitelisted spike measurement in a subprocess (Phase 2b only).

Mounted by app/main.py only when SPIKE_ENABLED=true. No arguments come from the request: the
name picks one fixed command line.
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parent.parent
TIMEOUT_SECONDS = 600
STDERR_TAIL = 2000

logger = logging.getLogger(__name__)
router = APIRouter()


def spike_commands() -> dict[str, list[str]]:
    models = get_settings().piper_model_full_path.parent
    medium = str(models / "en_US-lessac-medium.onnx")
    low = str(models / "en_US-lessac-low.onnx")
    return {
        "memory": ["memory"],
        "edge": ["edge"],
        "piper-medium-arena": ["piper", "--model", medium, "--arena", "on"],
        "piper-medium-noarena": ["piper", "--model", medium, "--arena", "off"],
        "piper-low-noarena": ["piper", "--model", low, "--arena", "off"],
        "extract": ["extract", "--pages", "200"],
    }


@router.get("/spike/{name}")
def run_spike(name: str) -> JSONResponse:
    commands = spike_commands()
    if name not in commands:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": f"Unknown spike. Choose one of: {', '.join(commands)}"}},
        )
    argv = [sys.executable, "-m", "spike.run", *commands[name]]
    logger.info("Spike %s: %s", name, " ".join(argv))
    try:
        proc = subprocess.run(argv, cwd=BACKEND_DIR, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as e:
        stderr = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return JSONResponse(
            {"name": name, "timeout": True, "killed": True, "exit_code": None, "result": None,
             "stderr_tail": stderr[-STDERR_TAIL:]}
        )  # fmt: skip
    result = None
    lines = proc.stdout.strip().splitlines()
    if lines:
        try:
            result = json.loads(lines[-1])
        except json.JSONDecodeError:
            result = {"raw_stdout": proc.stdout[-STDERR_TAIL:]}
    # A negative code is the signal number (-9 = SIGKILL); 137 = 128 + 9 from a shell/runtime.
    killed = proc.returncode < 0 or proc.returncode == 137
    return JSONResponse(
        {
            "name": name,
            "exit_code": proc.returncode,
            "killed": killed,
            "timeout": False,
            "result": result,
            "stderr_tail": proc.stderr[-STDERR_TAIL:],
        }
    )
