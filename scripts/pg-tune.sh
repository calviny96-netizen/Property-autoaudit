#!/usr/bin/env bash
# One-off PostgreSQL tuning for the shared postgres-manusai container (scheduled for a quiet hour).
# Waits until no import/maintenance/export job is running, applies the settings with
# ALTER SYSTEM (persisted in the data directory), restarts PostgreSQL and verifies them.
# If PostgreSQL does not come back, the new settings are removed again and it is restarted.
set -u
cd "$(dirname "$0")/.." || exit 1
LOG="$HOME/.cache/property-autoaudit/pg-tune.log"
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
PSQL=(docker exec postgres-manusai psql -U postgres -At)
log() { echo "$(date -Is) $*"; }

log "start"
for _ in $(seq 1 120); do
  busy=$("${PSQL[@]}" -c "SELECT (SELECT count(*) FROM xm.maintenance_jobs WHERE status='processing')
    + (SELECT count(*) FROM xm.imports WHERE status IN ('queued','processing'))
    + (SELECT count(*) FROM xm.export_jobs WHERE status='processing')" 2>/dev/null || echo 1)
  [ "$busy" = "0" ] && break
  log "jobs still running ($busy); waiting 60 s"
  sleep 60
done
[ "$busy" = "0" ] || { log "still busy after 2 hours; not changing anything"; exit 1; }

"${PSQL[@]}" -c "ALTER SYSTEM SET shared_buffers = '1GB'" \
             -c "ALTER SYSTEM SET max_wal_size = '2GB'" \
             -c "ALTER SYSTEM SET random_page_cost = 1.1" || { log "ALTER SYSTEM failed"; exit 1; }
log "settings written; restarting postgres-manusai"
docker restart postgres-manusai >/dev/null

ready=0
for _ in $(seq 1 60); do
  docker exec postgres-manusai pg_isready -U postgres >/dev/null 2>&1 && { ready=1; break; }
  sleep 2
done
if [ "$ready" != 1 ]; then
  log "PostgreSQL did not start; rolling back"
  docker stop postgres-manusai >/dev/null 2>&1
  docker run --rm -v "$PWD/data/postgres:/var/lib/postgresql/data" postgres:16 \
    sh -c "sed -i -e '/shared_buffers/d' -e '/max_wal_size/d' -e '/random_page_cost/d' /var/lib/postgresql/data/pgdata/postgresql.auto.conf"
  docker start postgres-manusai >/dev/null
  exit 1
fi
log "PostgreSQL ready: shared_buffers=$("${PSQL[@]}" -c 'SHOW shared_buffers') max_wal_size=$("${PSQL[@]}" -c 'SHOW max_wal_size') random_page_cost=$("${PSQL[@]}" -c 'SHOW random_page_cost')"
# Fresh connections everywhere; the gateway re-resolves the API.
docker restart xm-api xm-worker >/dev/null && docker restart xm-web >/dev/null
sleep 5
log "health: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9004/api/health)"
log "done"
