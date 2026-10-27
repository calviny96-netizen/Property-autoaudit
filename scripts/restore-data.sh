#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Pemakaian: scripts/restore-data.sh /path/ke/folder-backup"
  exit 1
fi

backup_dir="$(cd "$1" && pwd)"
postgres_dump="$backup_dir/xm-postgresql.dump"
qdrant_snapshot="$(find "$backup_dir" -maxdepth 1 -type f -name '*.snapshot' -print -quit)"

if [[ ! -f "$postgres_dump" || -z "$qdrant_snapshot" ]]; then
  echo "Backup PostgreSQL atau snapshot Qdrant tidak ditemukan."
  exit 1
fi

echo "Memulihkan PostgreSQL schema xm..."
docker exec postgres-manusai sh -c 'psql -U "$POSTGRES_USER" -d "${POSTGRES_DB:-postgres}" -c "DROP SCHEMA IF EXISTS xm CASCADE"'
docker cp "$postgres_dump" postgres-manusai:/tmp/xm-postgresql.dump
docker exec postgres-manusai sh -c 'pg_restore -U "$POSTGRES_USER" -d "${POSTGRES_DB:-postgres}" --no-owner /tmp/xm-postgresql.dump'

snapshot_name="$(basename "$qdrant_snapshot")"
docker exec qdrant-rag mkdir -p /qdrant/snapshots/xm_rag
docker cp "$qdrant_snapshot" "qdrant-rag:/qdrant/snapshots/xm_rag/$snapshot_name"
docker exec xm-api python -c "from qdrant import _request; print(_request('PUT','/collections/xm_rag/snapshots/recover',{'location':'file:///qdrant/snapshots/xm_rag/$snapshot_name','priority':'snapshot'},timeout=300))"

if [[ -f "$backup_dir/xm-source-uploads.tar.gz" ]]; then
  project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  mkdir -p "$project_dir/data/xm"
  tar -C "$project_dir/data/xm" -xzf "$backup_dir/xm-source-uploads.tar.gz"
fi

echo "Restore selesai. Restart layanan XM sebelum digunakan."
