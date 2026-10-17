"""End-to-end check against a running server (local or Render).

    python scripts\\smoke_api.py --base http://localhost:8000 --file ..\\testset\\docs\\06_no_styles.docx
    python scripts\\smoke_api.py --base http://localhost:8000 --expect-reject --file a.pdf b.doc ...

Normal mode: upload, poll every 3 s (status / progress / ETA), download chapter 1 as soon as it is
ready while later chapters are still generating (early playback, plus a Range request), ping
/api/health while the job is synthesizing (API responsiveness), wait for the end, download the
full book, check durations with ffprobe, and print a summary.

--expect-reject: upload each file and print the error code and HTTP status (all must be rejected).
"""

import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent
FINISHED = ("done", "partial", "failed")
ACTIVE = ("queued", "extracting", "synthesizing")


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


def wake(client: httpx.Client) -> None:
    start = time.perf_counter()
    r = client.get("/api/health", timeout=120)
    r.raise_for_status()
    took = time.perf_counter() - start
    note = " (server was asleep or starting)" if took > 3 else ""
    print(f"health: {r.json()} in {took:.2f} s{note}")


def upload(client: httpx.Client, path: Path) -> httpx.Response:
    with path.open("rb") as fh:
        return client.post("/api/upload", files={"file": (path.name, fh, "application/octet-stream")}, timeout=300)


def download(client: httpx.Client, url: str, target: Path, headers: dict | None = None) -> httpx.Response:
    with client.stream("GET", url, headers=headers or {}, timeout=600) as r:
        r.raise_for_status()
        with target.open("wb") as fh:
            for piece in r.iter_bytes():
                fh.write(piece)
    return r


def run_rejects(client: httpx.Client, files: list[Path]) -> int:
    failures = 0
    print(f"{'file':<28} {'HTTP':>4}  code")
    for path in files:
        r = upload(client, path)
        if r.status_code == 202:
            failures += 1
            print(f"{path.name:<28} {r.status_code:>4}  ACCEPTED (expected a rejection) job {r.json()['job_id']}")
            continue
        error = r.json().get("error", {})
        print(f"{path.name:<28} {r.status_code:>4}  {error.get('code')}: {error.get('message')}")
    return 1 if failures else 0


def ping_health(client: httpx.Client, count: int, results: list[float]) -> None:
    client.get("/api/health", timeout=30)  # untimed: opens the connection (DNS / connect not measured)
    for _ in range(count):
        start = time.perf_counter()
        client.get("/api/health", timeout=30).raise_for_status()
        results.append((time.perf_counter() - start) * 1000)
        time.sleep(0.15)


def run_job(client: httpx.Client, path: Path, out_dir: Path, poll: float, pings: int) -> int:
    start = time.perf_counter()
    r = upload(client, path)
    if r.status_code != 202:
        print(f"upload rejected: HTTP {r.status_code} {r.text}")
        return 1
    job_id = r.json()["job_id"]
    job_dir = out_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    print(f"uploaded {path.name} in {time.perf_counter() - start:.1f} s -> job {job_id}")

    synth_started: float | None = None
    early: dict | None = None
    latencies: list[float] = []
    pinger: threading.Thread | None = None
    job: dict = {}
    while True:
        r = client.get(f"/api/jobs/{job_id}", timeout=60)
        r.raise_for_status()
        job = r.json()
        now = time.perf_counter()
        progress = job["progress"]
        status = job["status"]
        if status == "synthesizing" and synth_started is None:
            synth_started = now
        pct = 100 * progress["chars_done"] / progress["chars_total"] if progress["chars_total"] else 0.0
        states = "".join({"pending": ".", "generating": ">", "ready": "R", "failed": "X"}[c["status"]]
                         for c in job["chapters"])  # fmt: skip
        print(
            f"[{fmt_seconds(now - start):>7}] {status:<12} chapters {progress['chapters_done']}/"
            f"{progress['chapters_total']} [{states}] text {pct:5.1f}% ETA {fmt_seconds(progress['eta_seconds'])}",
            flush=True,
        )

        if status == "synthesizing" and pinger is None and pings:
            pinger = threading.Thread(target=ping_health, args=(httpx.Client(base_url=str(client.base_url)), pings,
                                                                latencies), daemon=True)  # fmt: skip
            pinger.start()

        chapters = job["chapters"]
        if early is None and chapters and chapters[0]["status"] == "ready":
            still_working = [c["index"] for c in chapters[1:] if c["status"] in ("pending", "generating")]
            target = job_dir / "early_ch_001.mp3"
            t0 = time.perf_counter()
            resp = download(client, f"/api/jobs/{job_id}/chapters/1/audio", target)
            ranged = client.get(f"/api/jobs/{job_id}/chapters/1/audio", headers={"Range": "bytes=1000-1999"})
            early = {
                "at": now - start,
                "bytes": target.stat().st_size,
                "seconds": time.perf_counter() - t0,
                "still_working": still_working,
                "range": f"{ranged.status_code} {ranged.headers.get('content-range')} ({len(ranged.content)} bytes)",
                "content_type": resp.headers.get("content-type"),
            }
            print(
                f"          early playback: chapter 1 downloaded ({early['bytes']:,} bytes, "
                f"{resp.headers.get('content-type')}) while chapters {still_working or 'none'} were still "
                f"pending/generating; Range request -> {early['range']}",
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
    resp = download(client, f"/api/jobs/{job_id}/download/full", full_path)
    full_seconds = time.perf_counter() - t0
    disposition = resp.headers.get("content-disposition", "")
    ch1_path = job_dir / "ch_001.mp3"
    download(client, f"/api/jobs/{job_id}/download/1", ch1_path)

    api_total = sum(c["duration_seconds"] for c in ready)
    full_probe = ffprobe_duration(full_path)
    expected_full = api_total + 2.0 * (len(ready) - 1)
    ch1_probe = ffprobe_duration(ch1_path)
    ch1_api = job["chapters"][0]["duration_seconds"] or 0.0
    # Each 2 s silence clip carries ~0.06 s of MP3 frame padding.
    full_ok = abs(full_probe - expected_full) <= 1.0 + 0.1 * len(ready)
    ch1_ok = abs(ch1_probe - ch1_api) <= 1.0

    chars = job["progress"]["chars_total"]
    synth_time = end - (synth_started or start)
    print(f"{'#':>3}  {'title':<44} {'status':<8} {'duration':>8}")
    for c in job["chapters"]:
        print(f"{c['index']:>3}  {c['title'][:44]:<44} {c['status']:<8} {fmt_seconds(c['duration_seconds']):>8}")
    print()
    print(f"status:            {job['status']} (engine {job['engine_used']})")
    print(f"total time:        {fmt_seconds(end - start)} ({end - start:.1f} s, upload to finished)")
    print(f"synthesis time:    {fmt_seconds(synth_time)} ({synth_time:.1f} s, from first 'synthesizing' poll)")
    print(f"characters:        {chars:,}")
    print(f"chars/second:      {chars / synth_time:.1f} (server TTS_CONCURRENCY, default 4)" if synth_time else "")
    print(f"audio:             {fmt_seconds(api_total)} ({api_total:.1f} s) in {len(ready)} chapters")
    if early:
        print(
            f"early playback:    chapter 1 playable at {fmt_seconds(early['at'])}, "
            f"{'while chapters ' + str(early['still_working']) + ' were still pending/generating' if early['still_working'] else 'but no later chapter was still generating'}"
        )
        print(f"range request:     {early['range']}")
    print(
        f"full book:         {full_path.stat().st_size:,} bytes in {full_seconds:.1f} s; ffprobe {full_probe:.1f} s "
        f"vs chapters + gaps {expected_full:.1f} s -> {'OK' if full_ok else 'MISMATCH'}"
    )
    print(f"                   {disposition}")
    print(f"chapter 1:         ffprobe {ch1_probe:.1f} s vs API {ch1_api:.1f} s -> {'OK' if ch1_ok else 'MISMATCH'}")
    if latencies:
        print(
            f"/api/health:       {len(latencies)} pings while synthesizing: max {max(latencies):.0f} ms, "
            f"median {sorted(latencies)[len(latencies) // 2]:.0f} ms -> {'OK' if max(latencies) < 200 else 'SLOW'}"
        )
    print(f"files:             {job_dir}")
    return 0 if full_ok and ch1_ok and early else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end API smoke test.")
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--file", type=Path, nargs="+", required=True)
    parser.add_argument("--expect-reject", action="store_true", help="every file must be rejected")
    parser.add_argument("--out", type=Path, default=BACKEND_DIR / "storage" / "smoke")
    parser.add_argument("--poll", type=float, default=3.0)
    parser.add_argument("--health-pings", type=int, default=20, help="pings while synthesizing (0 = off)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    with httpx.Client(base_url=args.base) as client:
        wake(client)
        if args.expect_reject:
            return run_rejects(client, args.file)
        if len(args.file) != 1:
            parser.error("give exactly one --file (or use --expect-reject)")
        return run_job(client, args.file[0], args.out, args.poll, args.health_pings)


if __name__ == "__main__":
    sys.exit(main())
