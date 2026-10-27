# Upgrade XM v3.0

Tag Git: `XM-V3.0`. Versi aplikasi: `3.0.0`.

## Isi rilis

- Panel admin untuk membuat akun, mengubah email/password, serta mengunci dan membuka akun.
- Workspace terisolasi per akun: JSON, PostgreSQL, Qdrant, glosarium, indeks lokasi, default pencarian, toleransi, bobot, cache, dan ekspor.
- Admin memilih **Panel Admin → Data & setting** pada akun tujuan sebelum mengunggah data atau mengubah setting. User hanya membaca setting miliknya.
- Beberapa frasa default pencarian, label temperatur dengan teks, stroke merah dan glow lembut untuk Hot.
- Tampilan mobile memilih listing dahulu, lalu membuka rekomendasi dan kembali ke daftar.

## Model isolasi dan data lama

Workspace dibuat otomatis saat akun dibuat. Semua akun menggunakan schema PostgreSQL `xm` dan collection Qdrant `xm_rag` yang sama, dengan namespace `company_id` berbeda. Server memeriksa hak akses dan menerapkan namespace untuk setiap permintaan, pencarian, cache, file upload, dan pekerjaan worker. Ini isolasi logis per akun, bukan schema/database/collection fisik baru atau PostgreSQL RLS.

Arsip lama dan setting tetap dimiliki workspace admin `xm`. Akun lain memiliki namespace `xm-user-<uuid>`, setting awal independen dan data kosong; data lama tidak disalin otomatis. Password akun admin yang sudah dipromosikan tetap dipertahankan saat restart. Bootstrap admin lokal adalah `admin@autoaudit.id`; konfigurasi instalasi tersedia melalui `XM_ADMIN_EMAIL` dan `XM_ADMIN_PASSWORD`.

## Upgrade instalasi berjalan

1. Tunggu impor dan pemrosesan ulang selesai. Buat backup privat sesuai [BACKUP_RESTORE.md](BACKUP_RESTORE.md).
2. Pertahankan `.env`, database, dan volume data yang ada, lalu jalankan:

   ```bash
   git fetch origin --tags
   git switch --detach XM-V3.0
   docker compose build xm-api xm-worker xm-ui
   docker compose stop xm-worker
   docker compose up -d --no-deps xm-api
   docker compose logs --tail=30 xm-api
   ```

3. Setelah API menunjukkan `Application startup complete`, migrasi tambahan sudah diterapkan. Jalankan worker dan UI baru bersama agar seluruh pemrosesan menggunakan namespace akun:

   ```bash
   docker compose up -d --no-deps xm-worker xm-ui
   docker compose restart xm-web
   ```

4. Login admin dan periksa arsip lama. Buat akun, pilih **Data & setting**, upload JSON/glosarium/lokasi miliknya, kemudian tunggu pemrosesan selesai. Login sebagai user untuk memeriksa data dan setting yang terlihat.

Jangan jalankan worker versi lama bersama API versi baru. Upgrade ini mempertahankan data dan tidak memerlukan pemrosesan ulang seluruh arsip. Pemulihan ke versi lama harus memakai backup yang sesuai karena kode lama tidak memiliki pembatasan workspace per akun.

## Verifikasi rilis

62 tes backend lulus, termasuk isolasi melalui permintaan HTTP, PostgreSQL, proses impor/reindex, filter Qdrant, cache, ekspor, penolakan ID milik akun lain, dan permintaan bersamaan. TypeScript, lint pada komponen yang diubah, serta build produksi lulus. Uji lokal menggunakan dua akun sementara memastikan unggahan pada satu akun tidak muncul pada akun lainnya dan arsip admin tetap utuh.

Git hanya membawa kode, migrasi, dan dokumentasi. Database operasional, file unggahan, kredensial lokal, serta akun/data QA tidak termasuk rilis. Push tag tidak mengatur deployment server lain; upgrade server dilakukan terpisah.
