# Upgrade XM v1.2

Tag Git: `XM-V1.2`. Versi aplikasi: `1.2.0`. Versi sebelumnya: `XM-V1.1`.

## Isi rilis

- Pemeriksaan syarat wajib sebelum pemberian skor HOT; perbaikan ekstraksi jenis aset, harga, lokasi, ukuran, kamar dan lantai.
- Pengelompokan teks identik, cache hasil matching, dan optimasi render/pencarian.
- Impor indeks lokasi melalui CSV untuk alternatif dalam radius maksimum 4 km, dengan syarat wajib tetap berlaku.
- Filter tanggal tunggal/rentang, batas tanggal berdasarkan data tersedia, badge jumlah data, dan waktu posting rekomendasi.
- Tes regresi dan audit: lihat `CAESAR_QUALITY_AUDIT.md` dan `MATCHING_ACCURACY_AUDIT.md`.

## Database tetap terpisah

Git membawa source code, `api/schema.sql`, dokumentasi, dan contoh uji tanpa bagian kontak. Git tidak membawa isi database operasional, snapshot Qdrant, file unggahan, hasil audit mentah, atau `.env`. Folder `data/`, `release-data/`, dan `tmp/` diabaikan Git.

Pada instalasi yang sudah memiliki data, pertahankan database, volume, dan konfigurasi yang ada. Tidak perlu restore atau mengunggah ulang database untuk upgrade ini. Isi glosarium dan indeks jarak adalah data konfigurasi di database: perubahan kode tidak otomatis menyalin data konfigurasi lokal ke server lain. Bila indeks jarak belum tersedia di tujuan, impor CSV melalui pengaturan administrator, secara terpisah dari Git.

Pada instalasi baru yang membutuhkan data lama, pemindahan data tetap menggunakan backup privat sesuai `BACKUP_RESTORE.md`.

## Langkah upgrade instalasi yang sudah berjalan

1. Tunggu pekerjaan impor/pemrosesan yang sedang berjalan selesai. Buat backup lokal dengan `scripts/backup-data.sh` sesuai panduan backup.
2. Ambil rilis dan pertahankan konfigurasi serta volume data yang ada:

   ```bash
   git fetch origin --tags
   git switch --detach XM-V1.2
   docker compose build xm-api xm-worker xm-ui
   docker compose up -d --no-deps xm-api xm-worker xm-ui
   ```

3. Tunggu log API menunjukkan `Application startup complete`, lalu restart gateway web agar alamat container terbaru terbaca:

   ```bash
   docker compose logs --tail=30 xm-api
   docker compose restart xm-web
   ```

   Perintah ini mempertahankan PostgreSQL dan Qdrant yang sudah berjalan; tidak membuat ulang service database bersama. Sesuaikan akses jaringan dan konfigurasi pada instalasi tujuan bila berbeda dari Compose ini.

4. API menerapkan `api/schema.sql` saat startup. Tambahan tabel cache `document_groups`, `document_group_members`, `group_matches`, dan `workspace_cache_state` memakai `CREATE TABLE IF NOT EXISTS`; bukan penggantian schema `xm` atau penghapusan pesan asli.
5. Antrekan pemrosesan ulang tanpa mengubah pengaturan yang sudah tersimpan:

   ```bash
   docker compose exec xm-api python -c 'from workspace import rebuild_index; print(rebuild_index())'
   ```

   Worker membaca ulang pesan asli, memperbarui indeks Qdrant, menghitung matching, dan membangun cache grup. Pantau status dari pengaturan administrator atau `docker compose logs -f xm-worker`. Bila sedang mengedit pengaturan, tombol **Simpan & proses ulang pencocokan** juga mengantrekan pekerjaan yang sama; tombol ini tidak aktif jika tidak ada perubahan pengaturan. Tunggu pekerjaan selesai sebelum menilai hasil. Jangan restart API/worker ketika pekerjaan ini berjalan. Hasil dan waktu respons selama pemrosesan belum mewakili kondisi akhir.
6. Periksa pencarian, kedua arah matching, tanggal, dan rekomendasi. Audit lokal dapat diulang dengan:

   ```bash
   docker compose exec xm-api python audit_quality.py --search caesar --output /tmp/caesar-audit.json
   ```

   Berkas audit mengandung pesan sumber privat: jangan commit atau unggah sebagai aset rilis publik. Lolos pemeriksaan aturan bukan bukti akurasi manusia untuk seluruh pasar.

## Pemulihan

Simpan backup sebelum upgrade. Mengembalikan kode saja tidak mengembalikan hasil ekstraksi/matching yang telah dihitung ulang. Jika perlu memulihkan data persis sebelum upgrade, gunakan backup sesuai `BACKUP_RESTORE.md`; restore mengganti schema dan collection tujuan.

Push Git/tag ini tidak melakukan deployment otomatis yang dikendalikan aplikasi. Infrastruktur eksternal yang memasang pemicu deployment Git harus ditinjau sesuai pengaturan instalasinya.
