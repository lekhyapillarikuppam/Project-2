#!/usr/bin/env bash
# Deploy or update the backend on the VM: git pull -> build -> restart -> HTTPS health check.
# A restart empties the job list and storage (D18): avoid deploying while someone is converting.
set -euo pipefail
cd "$(dirname "$0")"

HEALTH_TIMEOUT_SECONDS=120

if [ ! -f .env ]; then
    echo "deploy/.env is missing: cp .env.example .env and set API_DOMAIN" >&2
    exit 1
fi
API_DOMAIN="$(grep -E '^API_DOMAIN=' .env | tail -n1 | cut -d= -f2- | tr -d "\"' \r")"
case "$API_DOMAIN" in
    "" | *"<"* | *">"*)
        echo "Set API_DOMAIN in deploy/.env first (now: '${API_DOMAIN}')" >&2
        exit 1
        ;;
esac

echo "== git pull"
git pull --ff-only

echo "== docker compose build"
docker compose build

echo "== docker compose up -d"
docker compose up -d --remove-orphans

echo "== waiting for https://${API_DOMAIN}/api/health (up to ${HEALTH_TIMEOUT_SECONDS} s; the first run also gets the certificate)"
deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS))
healthy=false
while [ "$SECONDS" -lt "$deadline" ]; do
    if body="$(curl -fsS --max-time 5 "https://${API_DOMAIN}/api/health" 2>/dev/null)"; then
        echo "healthy: ${body}"
        healthy=true
        break
    fi
    sleep 5
done

echo "== docker compose ps"
docker compose ps
echo "== last 30 log lines"
docker compose logs --tail 30

if ! $healthy; then
    echo "Health check did not pass within ${HEALTH_TIMEOUT_SECONDS} s. Check the logs above," >&2
    echo "that ports 80/443 are open in the Azure network security group, and that API_DOMAIN" >&2
    echo "matches the VM's DNS name." >&2
    exit 1
fi
docker image prune -f >/dev/null
echo "Deployed."
