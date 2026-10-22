#!/usr/bin/env bash
# Deploy spaceclaw to the server and restart the service.
#
# Usage: ./deploy.sh
#
# Env:
#   SPACECLAW_HOST   ssh destination (default: spaceclaw, a ~/.ssh/config alias)
#   SPACECLAW_URL    public URL to health-check (default: https://spaceclaw.net)
#   SKIP_TESTS=1     don't run the test suite first
set -euo pipefail

HOST="${SPACECLAW_HOST:-spaceclaw}"
URL="${SPACECLAW_URL:-https://spaceclaw.net}"
REMOTE_DIR="spaceclaw"

cd "$(dirname "$0")"

if [[ "${SKIP_TESTS:-}" != "1" ]]; then
    echo "==> Running tests"
    uv run pytest -q
fi

echo "==> Syncing code to $HOST:~/$REMOTE_DIR"
# Excluded paths are also protected from --delete, so the server's own
# secrets.json and database are never touched.
rsync -az --delete \
    --exclude .git --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
    --exclude .DS_Store --exclude '*.db*' --exclude config/secrets.json \
    ./ "$HOST:$REMOTE_DIR/"

echo "==> Installing dependencies and restarting"
ssh "$HOST" REMOTE_DIR="$REMOTE_DIR" bash -s <<'EOF'
set -euo pipefail
cd ~/"$REMOTE_DIR"
~/.local/bin/uv sync --frozen --no-dev --quiet

if ! cmp -s deploy/spaceclaw.service /etc/systemd/system/spaceclaw.service; then
    echo "    updating systemd unit"
    sudo cp deploy/spaceclaw.service /etc/systemd/system/spaceclaw.service
    sudo systemctl daemon-reload
fi

sudo systemctl restart spaceclaw

for _ in $(seq 20); do
    if systemctl is-active --quiet spaceclaw && ss -tln | grep -q ':23 '; then
        echo "    spaceclaw is running"
        exit 0
    fi
    sleep 0.5
done
echo "    spaceclaw failed to start:" >&2
journalctl -u spaceclaw -n 30 --no-pager >&2
exit 1
EOF

echo "==> Checking $URL"
curl -fsS -o /dev/null --max-time 15 "$URL"
echo "==> Deployed"
