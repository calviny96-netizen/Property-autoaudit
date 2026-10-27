# Backup dan restore data XM

Repository menyimpan source code dan definisi skema di `api/schema.sql`. Data operasional disimpan sebagai tiga berkas backup terpisah agar repository Git tetap ringan:

- `xm-postgresql.dump`: seluruh schema `xm`, termasuk bubble chat, dokumen hasil parsing, match, glosarium, bobot, akun, dan preferensi;
- `xm_rag-*.snapshot`: seluruh vector dan payload collection Qdrant `xm_rag`;
- `xm-source-uploads.tar.gz`: file `cleaned.json` asli yang pernah diunggah.

## Membuat backup

Pastikan container `postgres-manusai`, `qdrant-rag`, dan `xm-api` sedang berjalan, lalu jalankan:

```bash
chmod +x scripts/backup-data.sh scripts/restore-data.sh
scripts/backup-data.sh
```

Hasil dibuat di `release-data/<timestamp>/` bersama checksum SHA-256. Folder ini sengaja diabaikan Git karena berisi percakapan WhatsApp dan nomor telepon.

## Memulihkan backup

Jalankan stack PostgreSQL, Qdrant, dan XM terlebih dahulu. Kemudian:

```bash
scripts/restore-data.sh release-data/<timestamp>
docker compose restart xm-api xm-worker xm-ui xm-web
```

Restore mengganti seluruh schema PostgreSQL `xm` dan collection Qdrant `xm_rag`. Gunakan hanya pada instalasi baru atau setelah membuat backup instalasi tujuan.

## Publikasi

Jangan commit backup data ke Git. Bila repository bersifat private, berkas backup dapat ditambahkan sebagai release assets. Untuk repository public, simpan backup di lokasi private atau enkripsi dahulu karena isinya mencakup pesan dan nomor kontak.
