"""End-to-end check against a running server (local or Render).

    python scripts\\smoke_api.py --base http://localhost:8000 --file ..\\testset\\docs\\06_no_styles.docx
    python scripts\\smoke_api.py --base http://localhost:8000 --resume <job_id>
    python scripts\\smoke_api.py --base http://localhost:8000 --expect-reject --file a.pdf b.doc ...

Normal mode: upload, poll every 3 s (status / progress / ETA), download chapter 1 as soon as it is
ready while later chapters are still generating (early playback, plus a Range request), ping
/api/health while the job is synthesizing (API responsiveness), wait for the end, download the
full book, check durations with ffprobe, and print a summary.

--resume attaches to an existing job instead of uploading. GET requests are retried on dropped
connections (Render's proxy closes idle keep-alive connections): a fresh connection, backoff 1, 2,
4, 8 s, giving up after 2 minutes. The upload (POST) is never retried.

--expect-reject: upload each file and print the error code and HTTP status (all must be rejected).
"""

import argparse
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent
FINISHED = ("done", "partial", "failed")
RETRYABLE = (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout,
             httpx.ReadError, httpx.WriteError, httpx.PoolTimeout)  # fmt: skip
BACKOFF = (1, 2, 4, 8)
GIVE_UP_SECONDS = 120
T = TypeVar("T")


class JobGone(Exception):
    """The server no longer knows the job (404 after a restart, or 410 expired)."""


class Api:
    """httpx client whose idempotent GETs survive dropped connections."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.client = httpx.Client(base_url=base, timeout=60)
        self.retries = 0

    def _fresh(self) -> None:
        self.client.close()
        self.client = httpx.Client(base_url=self.base, timeout=60)

    def retrying(self, what: str, call: Callable[[httpx.Client], T]) -> T:
        start = time.monotonic()
        attempt = 0
        while True:
            try:
                return call(self.client)
            except RETRYABLE as e:
                waited = time.monotonic() - start
                if waited >= GIVE_UP_SECONDS:
                    raise RuntimeError(f"{what}: still failing after {waited:.0f} s ({type(e).__name__}: {e})") from e
                delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
                attempt += 1
                self.retries += 1
                print(f"          ! {what}: {type(e).__name__}: {e or 'connection dropped'}; "
                      f"retrying on a fresh connection in {delay} s", flush=True)  # fmt: skip
                time.sleep(delay)
                self._fresh()

    def get(self, path: str, **kwargs) -> httpx.Response:  # noqa: ANN003
        return self.retrying(f"GET {path}", lambda c: c.get(path, **kwargs))

    def download(self, path: str, target: Path, headers: dict | None = None) -> httpx.Response:
        def fetch(c: httpx.Client) -> httpx.Response:
            with c.stream("GET", path, headers=headers or {}, timeout=600) as r:
                if r.status_code >= 400:
                    r.read()
                    return r
                with target.open("wb") as fh:  # rewritten from the start on a retry
                    for piece in r.iter_bytes():
                        fh.write(piece)
                return r

        r = self.retrying(f"GET {path}", fetch)
        if r.status_code >= 400:
            raise RuntimeError(f"GET {path}: HTTP {r.status_code} {r.text}")
        return r

    def close(self) -> None:
        self.client.close()


def fmt_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    m, s = divmod(int(round(seconds)), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def wake(api: Api) -> None:
    start = time.perf_counter()
    r = api.retrying("GET /api/health", lambda c: c.get("/api/health", timeout=120))
    r.raise_for_status()
    took = time.perf_counter() - start
    note = " (server was asleep or starting)" if took > 3 else ""
    print(f"health: {r.json()} in {took:.2f} s{note}")


def upload(api: Api, path: Path) -> httpx.Response:
    with path.open("rb") as fh:
        return api.client.post(
            "/api/upload", files={"file": (path.name, fh, "application/octet-stream")}, timeout=300
        )


def run_rejects(api: Api, files: list[Path]) -> int:
    failures = 0
    print(f"{'file':<28} {'HTTP':>4}  code")
    for path in files:
        r = upload(api, path)
        if r.status_code == 202:
            failures += 1
            print(f"{path.name:<28} {r.status_code:>4}  ACCEPTED (expected a rejection) job {r.json()['job_id']}")
            continue
        error = r.json().get("error", {})
        print(f"{path.name:<28} {r.status_code:>4}  {error.get('code')}: {error.get('message')}")
    return 1 if failures else 0


def ping_health(base: str, count: int, results: list[float], errors: list[str]) -> None:
    with httpx.Client(base_url=base, timeout=30) as client:
        try:
            client.get("/api/health")  # untimed: opens the connection (DNS / connect not measured)
        except httpx.HTTPError as e:
            errors.append(f"{type(e).__name__}: {e}")
        for _ in range(count):
            start = time.perf_counter()
            try:
                client.get("/api/health").raise_for_status()
                results.append((time.perf_counter() - start) * 1000)
            except httpx.HTTPError as e:
                errors.append(f"{type(e).__name__}: {e}")
            time.sleep(0.15)


def get_job(api: Api, job_id: str) -> dict:
    r = api.get(f"/api/jobs/{job_id}")
    if r.status_code in (404, 410):
        raise JobGone(r.json().get("error", {}).get("code", str(r.status_code)))
    r.raise_for_status()
    return r.json()


def run_job(api: Api, job_id: str, out_dir: Path, poll: float, pings: int, start: float) -> int:
    job_dir = out_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    synth_first: tuple[float, int] | None = None  # (time, chars_done) at the first 'synthesizing' poll
    early: dict | None = None
    latencies: list[float] = []
    ping_errors: list[str] = []
    pinger: threading.Thread | None = None
    job: dict = {}
    while True:
        try:
            job = get_job(api, job_id)
        except JobGone as e:
            print(
                f"\njob {job_id} is gone ({e}). The server no longer knows it: it probably restarted "
                "(redeploy, crash or out of memory; check the Render events/logs). Jobs are not "
                "restored after a restart (D18), so upload the document again."
            )
            return 2
        now = time.perf_counter()
        progress = job["progress"]
        status = job["status"]
        if status == "synthesizing" and synth_first is None:
            synth_first = (now, progress["chars_done"])
        pct = 100 * progress["chars_done"] / progress["chars_total"] if progress["chars_total"] else 0.0
        states = "".join({"pending": ".", "generating": ">", "ready": "R", "failed": "X"}[c["status"]]
                         for c in job["chapters"])  # fmt: skip
        print(
            f"[{fmt_seconds(now - start):>7}] {status:<12} chapters {progress['chapters_done']}/"
            f"{progress['chapters_total']} [{states}] text {pct:5.1f}% ETA {fmt_seconds(progress['eta_seconds'])}",
            flush=True,
        )

        if status == "synthesizing" and pinger is None and pings:
            pinger = threading.Thread(target=ping_health, args=(api.base, pings, latencies, ping_errors), daemon=True)
            pinger.start()

        chapters = job["chapters"]
        if early is None and chapters and chapters[0]["status"] == "ready":
            still_working = [c["index"] for c in chapters[1:] if c["status"] in ("pending", "generating")]
            target = job_dir / "early_ch_001.mp3"
            t0 = time.perf_counter()
            resp = api.download(f"/api/jobs/{job_id}/chapters/1/audio", target)
            ranged = api.get(f"/api/jobs/{job_id}/chapters/1/audio", headers={"Range": "bytes=1000-1999"})
            early = {
                "at": now - start,
                "bytes": target.stat().st_size,
                "seconds": time.perf_counter() - t0,
                "still_working": still_working,
                "range": f"{ranged.status_code} {ranged.headers.get('content-range')} ({len(ranged.content)} bytes)",
            }
            print(
                f"          early playback: chapter 1 downloaded ({early['bytes']:,} bytes, "
                f"{resp.headers.get('content-type')}, {early['seconds']:.1f} s) while chapters "
                f"{still_working or 'none'} were still pending/generating; Range -> {early['range']}",
                flush=True,
            )

        if status in FINISHED:
            break
        time.sleep(poll)

    end = time.perf_counter()
    if pinger is not None:
        pinger.join(timeout=60)

    print()
    if job["status"] == "failed":
        print(f"job failed: {job['error']}")
        return 1

    ready = [c for c in job["chapters"] if c["status"] == "ready"]
    full_path = job_dir / "full.mp3"
    t0 = time.perf_counter()
    resp = api.download(f"/api/jobs/{job_id}/download/full", full_path)
    full_seconds = time.perf_counter() - t0
    disposition = resp.headers.get("content-disposition", "")
    first = ready[0]
    first_path = job_dir / f"ch_{first['index']:03d}.mp3"
    api.download(f"/api/jobs/{job_id}/download/{first['index']}", first_path)

    api_total = sum(c["duration_seconds"] for c in ready)
    full_probe = ffprobe_duration(full_path)
    expected_full = api_total + 2.0 * (len(ready) - 1)
    first_probe = ffprobe_duration(first_path)
    first_api = first["duration_seconds"] or 0.0
    # Each 2 s silence clip carries ~0.06 s of MP3 frame padding.
    full_ok = abs(full_probe - expected_full) <= 1.0 + 0.1 * len(ready)
    first_ok = abs(first_probe - first_api) <= 1.0

    chars_total = job["progress"]["chars_total"]
    print(f"{'#':>3}  {'title':<44} {'status':<8} {'duration':>8}")
    for c in job["chapters"]:
        print(f"{c['index']:>3}  {c['title'][:44]:<44} {c['status']:<8} {fmt_seconds(c['duration_seconds']):>8}")
    print()
    print(f"status:            {job['status']} (engine {job['engine_used']})")
    print(f"total time:        {fmt_seconds(end - start)} ({end - start:.1f} s, this client's start to finished)")
    if synth_first:
        synth_time = end - synth_first[0]
        chars_seen = chars_total - synth_first[1]
        print(f"synthesis time:    {fmt_seconds(synth_time)} ({synth_time:.1f} s, from the first 'synthesizing' poll)")
        if synth_time > 0:
            print(f"chars/second:      {chars_seen / synth_time:.1f} ({chars_seen:,} chars synthesized while watched)")
    print(f"characters:        {chars_total:,}")
    print(f"audio:             {fmt_seconds(api_total)} ({api_total:.1f} s) in {len(ready)} chapters")
    if early:
        working = early["still_working"]
        while_text = f"while chapters {working} were still pending/generating" if working else "(no later chapter left)"
        print(f"early playback:    chapter 1 playable at {fmt_seconds(early['at'])}, {while_text}")
        print(f"range request:     {early['range']}")
    print(
        f"full book:         {full_path.stat().st_size:,} bytes in {full_seconds:.1f} s; ffprobe {full_probe:.1f} s "
        f"vs chapters + gaps {expected_full:.1f} s -> {'OK' if full_ok else 'MISMATCH'}"
    )
    print(f"                   {disposition}")
    print(f"chapter {first['index']}:         ffprobe {first_probe:.1f} s vs API {first_api:.1f} s -> "
          f"{'OK' if first_ok else 'MISMATCH'}")  # fmt: skip
    if latencies:
        print(
            f"/api/health:       {len(latencies)} pings while synthesizing: max {max(latencies):.0f} ms, "
            f"median {sorted(latencies)[len(latencies) // 2]:.0f} ms -> {'OK' if max(latencies) < 200 else 'SLOW'}"
            + (f"; {len(ping_errors)} failed" if ping_errors else "")
        )
    print(f"GET retries:       {api.retries}")
    print(f"files:             {job_dir}")
    return 0 if full_ok and first_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end API smoke test.")
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--file", type=Path, nargs="+")
    parser.add_argument("--resume", metavar="JOB_ID", help="attach to an existing job instead of uploading")
    parser.add_argument("--expect-reject", action="store_true", help="every file must be rejected")
    parser.add_argument("--out", type=Path, default=BACKEND_DIR / "storage" / "smoke")
    parser.add_argument("--poll", type=float, default=3.0)
    parser.add_argument("--health-pings", type=int, default=20, help="pings while synthesizing (0 = off)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if not args.resume and not args.file:
        parser.error("give --file or --resume")

    api = Api(args.base)
    try:
        wake(api)
        if args.expect_reject:
            return run_rejects(api, args.file)
        start = time.perf_counter()
        if args.resume:
            job_id = args.resume
            print(f"attached to job {job_id}")
        else:
            if len(args.file) != 1:
                parser.error("give exactly one --file (or use --expect-reject)")
            r = upload(api, args.file[0])
            if r.status_code != 202:
                print(f"upload rejected: HTTP {r.status_code} {r.text}")
                return 1
            job_id = r.json()["job_id"]
            print(f"uploaded {args.file[0].name} in {time.perf_counter() - start:.1f} s -> job {job_id}")
            print(f"          (if this client dies, re-attach with: --resume {job_id})")
        return run_job(api, job_id, args.out, args.poll, args.health_pings, start)
    finally:
        api.close()


if __name__ == "__main__":
    sys.exit(main())
