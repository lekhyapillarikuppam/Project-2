# Deployment: Azure VM (Docker + Caddy)

The backend runs on one Azure for Students **B2ats v2** VM (2 vCPU, 1 GiB RAM, Ubuntu 24.04) as two
containers (SPEC §14, D28):

- **api**: the backend image built from `backend/Dockerfile` (one uvicorn worker). It is not
  published on the host; only Caddy reaches it. Job files live in the `jobs` Docker volume. Memory
  limit 900 MB.
- **caddy**: HTTPS on ports 80/443 with an automatic Let's Encrypt certificate for `API_DOMAIN`,
  reverse-proxying to `api:8000`.

## Files

| File | What it does |
|---|---|
| `docker-compose.yml` | The two services, volumes, health check, memory limit, log rotation |
| `Caddyfile` | HTTPS site for `{$API_DOMAIN}`: 55 MB upload limit, no compression, long timeouts for audio |
| `.env.example` | Settings template; copied to `.env` (gitignored, never commit it) |
| `bootstrap.sh` | One-time VM setup: Docker, 2 GB swap file, firewall (ufw: SSH, 80, 443), automatic security updates, clone, `.env` |
| `deploy.sh` | Every update: `git pull` → build → `up -d` → waits for `https://$API_DOMAIN/api/health` |

## First setup (once)

```bash
ssh <user>@<API_DOMAIN>
curl -fsSL https://raw.githubusercontent.com/lekhyapillarikuppam/Project-2/main/deploy/bootstrap.sh -o bootstrap.sh
bash bootstrap.sh
# log out and back in (docker group), then:
cd ~/Project-2/deploy && nano .env      # set API_DOMAIN
bash deploy.sh
```

The Azure network security group must allow inbound 80 and 443 (and 22 from your IP only).

## Update to the latest commit

```bash
cd ~/Project-2/deploy && bash deploy.sh
```

A restart empties all jobs (they are not restored, D18), so don't deploy while a conversion is
running. The first build takes several minutes on the 1 GiB VM (swap is used); later builds reuse
the cached dependency layer.

## Everyday commands (run in `~/Project-2/deploy`)

| Task | Command |
|---|---|
| Follow the API logs (per-chapter timing, memory every 30 s) | `docker compose logs -f api` |
| Caddy logs (certificates, proxy errors) | `docker compose logs -f caddy` |
| Status and health | `docker compose ps` |
| Memory and CPU now | `docker stats --no-stream` |
| Stop everything | `docker compose down` (keeps volumes and the certificate) |
| Start again | `docker compose up -d` |
| Restart only the API | `docker compose restart api` |
| Disk use of images / volumes | `docker system df` |

`docker stats` shows the api container's memory including the page cache from MP3 files it has
written; the app's own memory log (`Job … memory: process RSS …, anon …`) shows the part that
really counts.

Do not run `docker compose down -v`: `-v` deletes the `caddy_data` volume with the certificate, and
Let's Encrypt limits how often a certificate can be issued again.
