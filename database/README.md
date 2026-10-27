# Backup data aplikasi — 27 Oktober 2026

Backup ini **berisi data**, bukan database kosong. Karena repository publik, seluruh isi backup dienkripsi AES-256-GCM. File `autoaudit-20261027.enc.part-*` adalah potongan berurutan satu arsip; semua potongan wajib tersedia. Kunci 256-bit tidak disimpan di Git dan harus diminta dari pemilik server.

Isi arsip setelah dekripsi:

- `xm-postgresql.dump`: seluruh schema `xm` dan data aplikasinya, format PostgreSQL custom dump.
- `xm-rag.snapshot`: snapshot collection Qdrant `xm_rag`.
- `xm-source-uploads.tar.gz`: file sumber upload.
- `row-counts.json`: jumlah baris untuk verifikasi.

Data snapshot: 3 company, 3 akun, 16 impor, 353.814 pesan mentah, 452.704 dokumen, 54.719 entitas, 26.341 pasangan matching, dan 11 AI review runs. Sesi login, integration keys, dan AI keys sengaja dikosongkan. Referensi `ai_review_runs.key_id` diubah ke NULL. Akun dan data bisnis tetap ada; siapkan ulang key integrasi/AI setelah restore. Environment secrets dan file PDF ekspor sementara tidak disertakan; PDF dapat dibuat ulang dari data.

PostgreSQL diambil lewat `pg_dump` yang konsisten; snapshot Qdrant dan upload diambil terpisah pada hari yang sama, bukan transaksi atomik lintas layanan. Bila ada perubahan saat pengambilan snapshot, jalankan reindex setelah restore. Jangan mempublikasikan kunci atau arsip hasil dekripsi.

## Dekripsi

Jalankan dari root repo. Contoh menggunakan Docker image API sehingga library `cryptography` sudah tersedia. Berkas key harus berisi 64 karakter hex dan dipasang permission `600`.

```bash
sha256sum -c database/SHA256SUMS
mkdir -p release-data/restored
cat database/autoaudit-20261027.enc.part-* > release-data/restored/database.enc
# Salin backup.key dari penyimpanan pribadi ke release-data/restored/backup.key.
chmod 600 release-data/restored/backup.key
docker compose build xm-api
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD:/project" -w /project xm-xm-api \
  python scripts/backup-crypt.py decrypt \
  --input release-data/restored/database.enc \
  --output release-data/restored/database.tar.gz \
  --key-file release-data/restored/backup.key
tar -xzf release-data/restored/database.tar.gz -C release-data/restored
```

Alternatif: jalankan `python3 scripts/backup-crypt.py …` setelah memasang `cryptography` sesuai `api/requirements.txt`. Dekripsi gagal bila kunci salah atau ciphertext berubah; file plaintext tujuan tidak diterbitkan sebelum autentikasi berhasil. Folder `release-data/` diabaikan Git. Simpan kunci di password manager atau backup pribadi terpisah; kehilangan kunci berarti backup tidak bisa dipulihkan.

## Restore ke instalasi tujuan

**Restore mengganti data di instalasi tujuan.** Gunakan instalasi baru, atau backup instalasi tujuan sebelum melanjutkan. Jangan menjalankan langkah ini pada produksi hanya untuk mencoba backup.

1. Isi `.env` dari `.env.example`, sesuaikan password dan konfigurasi server.
2. Jalankan PostgreSQL dan Qdrant (`docker compose up -d postgres qdrant`); hentikan worker/API yang melayani pengguna saat restore.
3. Restore PostgreSQL custom dump menggunakan `pg_restore --no-owner --exit-on-error` ke database tujuan kosong. Untuk schema `xm` yang sudah ada, pemilik instalasi perlu memutuskan penggantiannya terlebih dahulu.
4. Restore snapshot Qdrant melalui snapshot recovery API dengan `priority: snapshot`; contoh mekanismenya ada di `scripts/restore-data.sh`. Script lama tersebut menjatuhkan schema yang ada, jadi gunakan hanya setelah instalasi tujuan dipastikan aman untuk diganti.
5. Ekstrak `xm-source-uploads.tar.gz` ke folder data XM yang dipasang sebagai `/data` (default `data/xm`), sehingga muncul `data/xm/uploads/<company>/…`. Jangan ekstrak ke root repo.
6. Jalankan API/worker/UI, cek `/health`, login menggunakan akun hasil restore, cek hitungan data, buat key integrasi/AI baru, dan buat ulang ekspor. Upload baru dan AI berbayar sebaiknya dijalankan setelah konfigurasi selesai diperiksa.

Untuk validasi PostgreSQL tanpa menyentuh database aktif, gunakan database disposable:

```bash
docker exec postgres-manusai createdb -U postgres autoaudit_restore_check
docker cp release-data/restored/xm-postgresql.dump postgres-manusai:/tmp/autoaudit-restore.dump
docker exec postgres-manusai pg_restore -U postgres \
  -d autoaudit_restore_check --no-owner --exit-on-error /tmp/autoaudit-restore.dump
```

Nama user/database di contoh harus disesuaikan dengan `.env`. Jangan menaruh hasil dekripsi atau `.env` dalam commit.

## Pemeriksaan salinan ini

- PostgreSQL dipulihkan ke database disposable termasuk seluruh index dan foreign key; jumlah baris dicatat di atas.
- Qdrant dipulihkan di container terpisah: status `green`, 452.704 point.
- AES-GCM diuji round-trip dan penolakan ciphertext yang dimodifikasi. Seluruh potongan diperiksa SHA-256, digabung, didekripsi, dan dibandingkan byte demi byte dengan arsip asal.
