"""Memory readings for the periodic job log: process RSS and the container's cgroup usage.
Standard library only (no psutil). Every reader returns None when the value is not available."""

import sys
from pathlib import Path

CGROUP = Path("/sys/fs/cgroup")
MB = 1024 * 1024


def process_rss_mb() -> float | None:
    """Current resident memory of this process."""
    try:
        if sys.platform.startswith("linux"):
            pages = int(Path("/proc/self/statm").read_text().split()[1])
            import os

            return pages * os.sysconf("SC_PAGE_SIZE") / MB
        if sys.platform == "win32":
            import ctypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
                    (name, ctypes.c_size_t)
                    for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                 "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                                 "PagefileUsage", "PeakPagefileUsage")
                ]  # fmt: skip

            counters = Counters(cb=ctypes.sizeof(Counters))
            get_process = ctypes.windll.kernel32.GetCurrentProcess
            get_process.restype = ctypes.c_void_p
            get_info = ctypes.windll.psapi.GetProcessMemoryInfo
            get_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
            if get_info(get_process(), ctypes.byref(counters), counters.cb):
                return counters.WorkingSetSize / MB
    except (OSError, ValueError, IndexError, AttributeError):
        return None
    return None


def _read_bytes(path: Path) -> int | None:
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def cgroup_memory_mb() -> tuple[float | None, float | None]:
    """(memory.current, anon) of the container in MB. `anon` excludes the page cache, which the
    kernel reclaims under pressure (MP3 files written by the job inflate memory.current)."""
    current = _read_bytes(CGROUP / "memory.current")  # cgroup v2
    stat = CGROUP / "memory.stat"
    if current is None:  # cgroup v1
        current = _read_bytes(CGROUP / "memory" / "memory.usage_in_bytes")
        stat = CGROUP / "memory" / "memory.stat"
    anon = None
    try:
        for line in stat.read_text().splitlines():
            key, _, value = line.partition(" ")
            if key in ("anon", "rss"):  # v2 / v1
                anon = int(value)
                break
    except (OSError, ValueError):
        pass
    return (current / MB if current is not None else None, anon / MB if anon is not None else None)


def memory_summary() -> str:
    rss = process_rss_mb()
    current, anon = cgroup_memory_mb()
    parts = [f"process RSS {rss:.0f} MB" if rss is not None else "process RSS n/a"]
    if current is not None:
        parts.append(f"cgroup memory.current {current:.0f} MB")
    if anon is not None:
        parts.append(f"anon {anon:.0f} MB")
    return ", ".join(parts)
