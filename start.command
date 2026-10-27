#!/bin/zsh

# XM Property Matchmaker launcher for macOS.
# Safe to run repeatedly: existing containers are left running and stopped
# containers are started without deleting volumes or importing data again.

set -u

SCRIPT_DIR="${0:A:h}"
APP_URL="http://127.0.0.1:9004/"
HEALTH_URL="http://127.0.0.1:9004/api/health"

cd "$SCRIPT_DIR" || {
  echo "Tidak dapat membuka folder XM Agent: $SCRIPT_DIR"
  read -r "?Tekan Enter untuk menutup..."
  exit 1
}

echo "Memulai XM Property Matchmaker..."

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI tidak ditemukan. Pastikan Docker Desktop sudah terpasang."
  read -r "?Tekan Enter untuk menutup..."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker belum aktif. Membuka Docker Desktop..."
  open -a "Docker" >/dev/null 2>&1 || true

  attempt=0
  until docker info >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if (( attempt >= 60 )); then
      echo "Docker belum siap setelah 2 menit. Periksa Docker Desktop lalu jalankan file ini lagi."
      read -r "?Tekan Enter untuk menutup..."
      exit 1
    fi
    sleep 2
  done
fi

# PostgreSQL dan Qdrant adalah layanan bersama yang sudah ada di komputer ini.
# Nyalakan bila ditemukan tetapi sedang berhenti; jangan membuat atau mengubah datanya.
for dependency in postgres-manusai qdrant-rag; do
  if docker container inspect "$dependency" >/dev/null 2>&1; then
    if [[ "$(docker inspect -f '{{.State.Running}}' "$dependency")" != "true" ]]; then
      echo "Menyalakan $dependency..."
      docker start "$dependency" >/dev/null || exit 1
    fi
  else
    echo "Peringatan: container $dependency tidak ditemukan."
  fi
done

echo "Menyalakan seluruh container XM..."
if ! docker compose up -d; then
  echo "Gagal menyalakan stack XM. Pesan Docker di atas dapat dipakai untuk diagnosis."
  read -r "?Tekan Enter untuk menutup..."
  exit 1
fi

echo "Menunggu Web UI siap..."
attempt=0
until curl --fail --silent --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if (( attempt >= 45 )); then
    echo "Container aktif, tetapi Web UI belum siap. Cek dengan: docker compose ps"
    read -r "?Tekan Enter untuk menutup..."
    exit 1
  fi
  sleep 2
done

echo "XM Property Matchmaker siap: $APP_URL"
open "$APP_URL"
echo "Jendela ini boleh ditutup; container akan tetap berjalan."
sleep 3
