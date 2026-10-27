#!/usr/bin/env bash
# PM2 keeps this loop alive (and starts it after a reboot via pm2-oem.service);
# the application itself runs in Docker. Never rebuilds or recreates containers (a freshly built image must
# not restart a running job by itself): deploys do that explicitly.
set -u
cd "$(dirname "$0")/.." || exit 1
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
SERVICES="postgres qdrant xm-api xm-worker xm-ui xm-web"
while true; do
  if docker info >/dev/null 2>&1; then
    if ! docker compose up -d --no-build --no-recreate $SERVICES >/dev/null 2>&1; then
      echo "$(date -Is) docker compose up failed; retrying in 30 s"
    elif ! curl --fail --silent --max-time 10 http://127.0.0.1:9004/api/health >/dev/null; then
      # The gateway keeps old container addresses after the API is recreated.
      echo "$(date -Is) gateway not answering; restarting xm-web"
      docker compose restart xm-web >/dev/null 2>&1 || true
    fi
  else
    echo "$(date -Is) waiting for Docker"
  fi
  sleep 30
done
