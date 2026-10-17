# Phase 3b — Move the backend to an Azure VM

Read first: `CLAUDE.md`, `docs/DECISIONS.md` (**D28** is new; D14, D19, D26 changed), `docs/SPEC.md` **revision 6** §5, §10, §11 (polling + connection check), §14.
No frontend in this phase. Nothing in the pipeline / TTS / API behaviour changes except what is listed here.

## Facts from the Render runs (for context, don't re-investigate)
- No crash and no OOM: job 06 finished on the server ("done in 235.3 s, 172.2 chars/s, engine edge"); 01_novel was queued behind it (FIFO) and started at 07:48:38.
- Both smoke runs died client-side with `httpx.RemoteProtocolError: Server disconnected without sending a response` (a reused keep-alive connection dropped by the proxy).
- Render free = 0.15 CPU; progress stalled 15–25 s at a time. That plus the 15-minute sleep is why we move (D28).

## 1. Code changes (work on any host)
1. **smoke_api.py robustness**: retry idempotent GETs on `RemoteProtocolError`, `ConnectError`, `ReadTimeout`, `ReadError` with a fresh connection and backoff 1/2/4/8 s, giving up after 2 min; clearly report a 404 after a restart ("job disappeared — server restarted?"); add `--resume <job_id>`.
2. **Timing logs** (INFO) per chapter: synthesis time, assembly time, number and total time of ffmpeg/ffprobe calls.
3. **Faster assembly on small CPUs**: our MP3s are CBR 48 kbps, 24 kHz mono, no Xing/ID3 header. Compute each part's duration from its MPEG frames (count frames × 576 / 24000, or size-based for CBR — pick the exact one and justify) instead of one ffprobe per part. Test: computed duration matches ffprobe within 50 ms for real edge chunks and for silence clips. Keep one ffprobe of the final chapter file as the cross-check (warn if > 1 s apart). Add `-threads 1 -nostdin -loglevel error` to every ffmpeg call.
4. **Memory log**: while a job runs, every 30 s log process RSS and cgroup `memory.current` (if readable) at INFO.
5. Measure locally before/after on `01_novel.pdf` (chars/s and assembly seconds per chapter), plus one Docker run limited with `--cpus=2 --memory=1g --memory-swap=1g` to mimic the Azure VM.

## 2. Deployment files (new folder `deploy/`)
- `deploy/docker-compose.yml`:
  - `api`: builds `../backend` (build arg `INCLUDE_PIPER` from env, default `false`), env from `deploy/.env` (gitignored), named volume for `/app/storage`, `restart: unless-stopped`, not published to the host (only Caddy reaches it), a `healthcheck` on `/api/health`, memory limit 900m.
  - `caddy`: `caddy:2` image (pin an exact version), ports 80 and 443, volumes for its data/config, `restart: unless-stopped`.
- `deploy/Caddyfile`: `{$API_DOMAIN}` → `reverse_proxy api:8000`; `request_body { max_size 55MB }`; gzip off for audio; long read/write timeouts for streaming.
- `deploy/.env.example`: `API_DOMAIN=<label>.centralindia.cloudapp.azure.com`, `TTS_FALLBACK=none`, `TTS_MAX_RETRIES=5`, `TTS_CONCURRENCY=4`, `CORS_ORIGINS=http://localhost:5173`, `LOG_LEVEL=INFO`, `INCLUDE_PIPER=false`, plus any other variables the app needs.
- `deploy/bootstrap.sh` (run once on a fresh Ubuntu 24.04 VM with sudo; must be safe to re-run):
  - install Docker Engine + compose plugin from Docker's official apt repo; add the user to the `docker` group;
  - create a 2 GB swap file (`/swapfile`, persistent in fstab, `vm.swappiness=10`) — mainly so `docker build` doesn't run out of memory on 1 GiB;
  - `ufw`: allow OpenSSH, 80, 443; enable;
  - enable `unattended-upgrades`;
  - clone the repo to `~/Project-2` if missing;
  - copy `deploy/.env.example` to `deploy/.env` if missing and tell the user to edit `API_DOMAIN`.
- `deploy/deploy.sh` (run on the VM for every update): `git pull` → `docker compose build` → `docker compose up -d` → wait for `https://$API_DOMAIN/api/health` (up to 2 min) → print `docker compose ps` and the last 30 log lines. Exit non-zero if health never passes.
- `deploy/README.md`: what each file does, how to update, how to view logs (`docker compose logs -f api`), how to stop/start, how to check memory (`docker stats --no-stream`).
- `.gitignore`: add `deploy/.env`.
- Remove Render-specific wording from code comments and docs (the Dockerfile header etc.). Keep `docs/spike_results.md` as history.

Run `pytest -q`, commit `Phase 3b: Azure VM deployment (Docker + Caddy), faster assembly, robust smoke script`, push.

## 3. Stop and hand over
Print, for the user to do on the VM after their own portal setup (they will have an SSH command from me):
```
ssh <user>@<API_DOMAIN>
curl -fsSL https://raw.githubusercontent.com/lekhyapillarikuppam/Project-2/main/deploy/bootstrap.sh -o bootstrap.sh && bash bootstrap.sh
# log out and back in (docker group), then:
cd ~/Project-2/deploy && nano .env      # set API_DOMAIN
bash deploy.sh
```
(If the repo is private, tell the user exactly how to clone it instead — a GitHub fine-grained read-only token, or a deploy key — and adjust bootstrap.sh to skip cloning when the folder already exists.)

Then, on the laptop:
```
python scripts\smoke_api.py --base https://<API_DOMAIN> --file ..\testset\docs\06_no_styles.docx
python scripts\smoke_api.py --base https://<API_DOMAIN> --file ..\testset\docs\01_novel.pdf
```
and on the VM during the Alice run: `docker stats --no-stream` a few times.

Wait for the user's results.

## 4. After the results
- Append an "Azure VM" section to `docs/spike_results.md` (chars/s, per-chapter assembly time, peak memory, any edge retries) with the projected time for a 100,000-word book.
- **Optional Piper re-test on the VM** (only if memory during the Alice run stayed under ~350 MB): rebuild with `INCLUDE_PIPER=true`, run `narrate.py --engine piper --chapters 1` inside the container with `PIPER_MODEL_PATH=/app/models/en_US-lessac-low.onnx`, `PIPER_DISABLE_ARENA=true`, `PIPER_MAX_CHARS=300`, and report peak memory (container limit 900m). Recommend whether to enable `TTS_FALLBACK=piper`; do not change it yourself.
- Commit, push, and tell the user it is safe to delete the Render service.
Do not start Phase 4.
