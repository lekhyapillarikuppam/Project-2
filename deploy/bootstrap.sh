#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 VM for the audiobook backend (SPEC §14, D28).
# Run as your normal user (it uses sudo). Safe to re-run: every step checks before it changes.
#
#   curl -fsSL https://raw.githubusercontent.com/lekhyapillarikuppam/Project-2/main/deploy/bootstrap.sh -o bootstrap.sh
#   bash bootstrap.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/lekhyapillarikuppam/Project-2.git}"
APP_DIR="${APP_DIR:-$HOME/Project-2}"
SWAP_FILE=/swapfile
SWAP_SIZE_MB=2048

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as your normal user, not root (it calls sudo where needed)." >&2
    exit 1
fi
step() { printf '\n== %s\n' "$*"; }

step "1/6 Docker Engine + compose plugin (Docker's official apt repository)"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    echo "already installed: $(docker --version)"
else
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl git
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    # shellcheck disable=SC1091
    codename="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
sudo systemctl enable --now docker
relogin=false
if ! id -nG "$USER" | grep -qw docker; then
    sudo usermod -aG docker "$USER"
    relogin=true
fi

step "2/6 ${SWAP_SIZE_MB} MB swap file (so docker build fits in 1 GiB of RAM)"
if swapon --show=NAME --noheadings | grep -qx "$SWAP_FILE"; then
    echo "already active"
else
    if [ ! -f "$SWAP_FILE" ]; then
        sudo fallocate -l "${SWAP_SIZE_MB}M" "$SWAP_FILE" || sudo dd if=/dev/zero of="$SWAP_FILE" bs=1M count="$SWAP_SIZE_MB"
        sudo chmod 600 "$SWAP_FILE"
        sudo mkswap "$SWAP_FILE"
    fi
    sudo swapon "$SWAP_FILE"
fi
grep -q "^${SWAP_FILE} " /etc/fstab || echo "${SWAP_FILE} none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
echo "vm.swappiness=10" | sudo tee /etc/sysctl.d/99-audiobook-swappiness.conf >/dev/null
sudo sysctl -q -p /etc/sysctl.d/99-audiobook-swappiness.conf
swapon --show

step "3/6 Firewall (ufw): OpenSSH, 80, 443"
sudo apt-get install -y ufw >/dev/null
sudo ufw allow OpenSSH >/dev/null
sudo ufw allow 80/tcp >/dev/null
sudo ufw allow 443/tcp >/dev/null
sudo ufw --force enable >/dev/null
sudo ufw status

step "4/6 Automatic security updates (unattended-upgrades)"
sudo apt-get install -y unattended-upgrades >/dev/null
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
    | sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null
sudo systemctl enable --now unattended-upgrades >/dev/null
echo "enabled"

step "5/6 Repository in ${APP_DIR}"
if [ -d "${APP_DIR}/.git" ]; then
    echo "already cloned (deploy.sh pulls updates)"
elif [ -e "$APP_DIR" ]; then
    echo "${APP_DIR} exists but is not a git checkout; leaving it alone" >&2
    exit 1
else
    git clone "$REPO_URL" "$APP_DIR"
fi

step "6/6 deploy/.env"
if [ -f "${APP_DIR}/deploy/.env" ]; then
    echo "already exists"
else
    cp "${APP_DIR}/deploy/.env.example" "${APP_DIR}/deploy/.env"
    chmod 600 "${APP_DIR}/deploy/.env"
    echo "created from .env.example"
fi

printf '\nDone.\n'
if $relogin; then
    echo "* You were added to the 'docker' group: log out and back in before the next step."
fi
echo "* Edit API_DOMAIN in ${APP_DIR}/deploy/.env (your <label>.centralindia.cloudapp.azure.com name):"
echo "      cd ${APP_DIR}/deploy && nano .env"
echo "* Then deploy:"
echo "      bash deploy.sh"
