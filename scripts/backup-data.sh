#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
stamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="$project_dir/release-data/$stamp"
mkdir -p "$backup_dir"

echo "Membuat backup PostgreSQL schema xm..."
docker exec postgres-manusai sh -c 'pg_dump -U "$POSTGRES_USER" -d "${POSTGRES_DB:-postgres}" -n xm -Fc' > "$backup_dir/xm-postgresql.dump"

echo "Membuat snapshot Qdrant collection xm_rag..."
snapshot_name="$(docker exec xm-api python -c "from qdrant import _request; print(_request('POST','/collections/xm_rag/snapshots?wait=true',timeout=300)['result']['name'])")"
docker cp "qdrant-rag:/qdrant/snapshots/xm_rag/$snapshot_name" "$backup_dir/$snapshot_name"

if [[ -d "$project_dir/data/xm/uploads" ]]; then
  echo "Mengarsipkan file sumber cleaned.json..."
  tar -C "$project_dir/data/xm" -czf "$backup_dir/xm-source-uploads.tar.gz" uploads
fi

(
  cd "$backup_dir"
  find . -maxdepth 1 -type f ! -name SHA256SUMS.txt -print0 | sort -z | xargs -0 shasum -a 256 > SHA256SUMS.txt
)

echo "Backup selesai: $backup_dir"
ls -lh "$backup_dir"
