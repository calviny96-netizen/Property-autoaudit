# Upgrade XM v3.1

Tag Git: `XM-V3.1`. Versi aplikasi/API: `3.1.0`.

## Perubahan ekspor PDF

- Ukuran kertas A4 portrait (210 × 297 mm).
- Kedua kolom tetap dicetak: data sumber di kiri dan rekomendasi di kanan.
- Satu pilihan listing/pasangan per halaman, termasuk teks panjang dan pilihan tanpa pasangan.
- Teks isi menggunakan 11 pt secara default. Ukuran font setiap kolom mengecil otomatis hanya jika dibutuhkan agar seluruh teks muat di halaman yang sama.
- Tombol WhatsApp hijau hanya ada di kolom rekomendasi sebelah kanan. Tidak ditampilkan bila rekomendasi tidak memiliki nomor kontak atau belum ada pasangan.
- Logo, tanggal Jakarta, label Hot/Warm, nomor halaman, dan pemisahan data per akun tetap digunakan.

## Upgrade dari v3.0

Rilis ini tidak mengubah struktur atau kepemilikan data. Pertahankan konfigurasi dan volume yang ada. Setelah pekerjaan impor/pemrosesan selesai:

```bash
git fetch origin --tags
git switch --detach XM-V3.1
docker compose build xm-api xm-worker xm-ui
docker compose up -d --no-deps xm-api xm-worker xm-ui
docker compose restart xm-web
```

Tidak perlu upload ulang atau menghitung ulang pencocokan. Ekspor berikutnya langsung menggunakan format baru. Untuk upgrade dari versi sebelum v3.0, ikuti langkah migrasi workspace di [UPGRADE_V3.0.md](UPGRADE_V3.0.md).

## Verifikasi

Tes PDF memeriksa ukuran A4, jumlah halaman, font default 11 pt, pengecilan font untuk teks panjang tanpa halaman lanjutan, dan tautan WhatsApp hanya di sisi kanan pada kedua arah pencocokan. Sampel normal, panjang, dan tanpa pasangan dirender serta diperiksa secara visual. Tes backend mencakup isolasi per user yang sudah tersedia sejak v3.0.
