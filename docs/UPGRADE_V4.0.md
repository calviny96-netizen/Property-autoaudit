# Upgrade XM v4.0 — Major Update

Versi aplikasi/API: `4.0.0`. Rilis ini menambah ID publik, status listing/buyer, riwayat "Match Terbaru", dashboard statistik, pemantauan stok sales, model company dengan banyak akun, dan mendesain ulang seluruh tampilan.

## Ringkasan fitur

| # | Permintaan | Hasil di aplikasi |
|---|------------|-------------------|
| 1 | ID untuk tiap listing dan buyer | ID publik seperti `L-AB908` (listing) dan `B-AB908` (buyer). Dua huruf + tiga angka, otomatis bertambah panjang (`L-ABC123`, …) sehingga cukup untuk jutaan data. Huruf `I` dan `O` tidak dipakai agar tidak tertukar dengan 1 dan 0. ID dapat dicari, disalin dengan satu ketukan, dan tercetak di PDF. |
| 2 | Log "recent match found" | Menu **Match Terbaru**. Setiap upload JSON yang menghasilkan pasangan baru dicatat permanen. Dua arah (Buyer → Listing dan Listing → Buyer), pilihan Hari ini / Kemarin / Minggu ini / Minggu lalu / Bulan ini / tanggal pilihan, dan **Hot + Warm terpilih secara default**. Ada lencana jumlah match yang belum dilihat. |
| 3 | Tandai Delete / Sold / On-hold / Ready | Menu ⋮ pada setiap kartu (juga untuk beberapa sekaligus lewat **Pilih beberapa**). Standarnya Ready. Selain Ready tidak direkomendasikan lagi, tetapi bisa dilihat lewat filter status dan dikembalikan. Hapus bersifat lunak (dapat dipulihkan). Setiap perubahan punya tombol **Urungkan**. |
| 4 | Pengelompokan listing | Pengaturan company: kelompokkan listing menurut **pengirim pesan** (standar) atau **nomor telepon di pesan**. Berlaku di Cocokkan dan grafik "Listing per sales". |
| 5 | Dashboard statistik (Python) | **Beranda**: demand buyer minggu ini, bulan ini, dan periode pilihan; dibandingkan dengan periode sebelumnya; grafik Chart.js; peluang (dicari banyak, stok sedikit). Semua angka dihitung di server oleh kode Python (`api/dashboard.py`). |
| 6 | Log stok per nomor sales | Menu **Stok Sales**. Masukkan banyak nomor dipisah koma (level company). Jumlah listing Ready / On-hold / Sold per nomor dicatat otomatis setiap upload dan setiap perubahan status. |
| 7 | Super admin company | Peran baru `company_admin`: menambah/mengunci/mereset akun anggota, mengunggah data, dan **mengunci kata kunci pencarian** sehingga anggota tidak dapat mengubahnya. |
| 8 | Perbaikan UI/UX | Tampilan baru dengan HeroUI v3, Magic UI, animasi Lottie dan Chart.js; huruf lebih besar, target sentuh ≥ 44 px, navigasi bawah di ponsel, dan bahasa yang sederhana. |

## Peran dan hak akses

| Peran | Siapa | Boleh |
|-------|-------|-------|
| `admin` | Administrator platform (pemilik aplikasi) | Semua company: membuat company, masuk ke company mana pun, menambah/mengubah akun termasuk mengangkat super admin. |
| `company_admin` | Super admin sebuah company | Di company-nya: tim, upload, kata kunci (+ kunci), pengelompokan, nomor sales, toleransi/bobot/glosarium/lokasi. |
| `user` | Anggota | Cocokkan, tandai status, unduh PDF, lihat Beranda / Match Terbaru / Stok Sales, ubah kata kunci sendiri bila tidak dikunci. |

Hak akses diperiksa di server untuk setiap permintaan (`api/access.py`), bukan hanya disembunyikan di tampilan. Kunci kata kunci ditegakkan di server: bila terkunci, parameter pencarian dari anggota diabaikan.

Akun lama tidak diubah haknya: admin lama tetap administrator platform dan pemilik workspace `xm`; akun biasa lama tetap `user` di workspace-nya sendiri. Untuk menjadikannya super admin, administrator platform membuka **Perusahaan → Masuk & kelola → Tim & Akses → Kelola** lalu memilih peran Super admin.

## Perubahan perilaku yang perlu diketahui

- **Company baru** dimulai tanpa kata kunci (semua pesan dipakai). Super admin dapat mengaktifkan saringan di Pengaturan → Umum. Company lama mempertahankan kata kuncinya.
- **Cocokkan** kini menampilkan Hot + Warm secara default untuk pengguna yang belum pernah memilih. Pilihan yang sudah tersimpan tidak berubah.
- Listing/buyer berstatus **On-hold, Sold, atau Dihapus** tidak muncul di daftar standar, tidak muncul sebagai rekomendasi, dan tidak dihitung pada angka kartu Hot/Warm. Endpoint `/agent/search` dan `/agent/matches` (untuk bot) hanya memberi data Ready dan menyertakan ID.
- **Match Terbaru** hanya mencatat pasangan baru dari **upload**. Menghitung ulang karena mengubah toleransi, bobot, glosarium, atau lokasi tidak dianggap match baru. Pasangan yang sudah ada saat upgrade ini ditandai sebagai riwayat (baseline), bukan berita.
- Teks yang **identik** tetap dianggap satu listing/buyer (satu kartu, satu ID), sama seperti sebelumnya. ID dan status melekat pada teks itu, sehingga tetap sama walau pencocokan dihitung ulang.
- Endpoint manajemen akun pindah: `/auth/users…` → `/team/users…` (dalam company) dan `/admin/companies…` (administrator platform).

## Cara upgrade instalasi berjalan

Pertahankan `.env`, database, dan volume yang ada. Migrasi skema berjalan otomatis dan aman diulang; data lama tidak diubah atau dihapus. Pada salinan data produksi (±38 ribu dokumen, 13 ribu teks unik, 46 ribu pasangan) migrasi pertama memerlukan sekitar 20 detik.

1. Tunggu import/pemrosesan yang sedang berjalan selesai, lalu buat backup (lihat [BACKUP_RESTORE.md](BACKUP_RESTORE.md)):

   ```bash
   scripts/backup-data.sh
   ```

2. Ambil kode versi ini, lalu bangun ulang dan jalankan:

   ```bash
   docker compose build xm-api xm-worker xm-ui
   docker compose stop xm-worker
   docker compose up -d --no-deps xm-api
   docker compose logs --tail=30 xm-api      # tunggu "Application startup complete"
   docker compose up -d --no-deps xm-worker xm-ui
   docker compose restart xm-web
   ```

   Saat API pertama kali berjalan ia membuat tabel baru, memberi ID kepada setiap teks unik, dan menandai pasangan lama sebagai riwayat. Jangan menjalankan worker lama bersama API baru.

3. Buka `http://127.0.0.1:9004`, login sebagai administrator, dan periksa: Beranda menampilkan jumlah buyer/listing, kartu di **Cocokkan** memiliki ID, dan **Perusahaan** menampilkan semua company.

4. Beri tahu super admin tiap company agar mengatur **Pengaturan → Umum** (kata kunci dan kuncinya) serta **Pantau Sales**.

### Kembali ke versi lama

Skema baru hanya menambah tabel dan kolom, sehingga versi 3.x masih dapat membaca data lama. Namun akun dengan peran `company_admin` tidak dikenal versi lama, dan status Sold/On-hold/Dihapus tidak berlaku di sana. Untuk pemulihan penuh, pakai backup dari langkah 1.

## Arsitektur singkat

- **Entity** (`xm.entities`): satu teks lengkap yang unik per company dan jenis (buyer/listing). Memiliki `public_id`, `status`, dan menjadi kunci riwayat. `document_groups` membawa `public_id`, `status`, dan hitungan Hot/Warm yang hanya menghitung pasangan berstatus Ready.
- **Riwayat match** (`xm.match_events`): satu baris per pasangan entity, dengan `found_at`, skor awal dan terakhir, asal (`import`, `recompute`, `baseline`) dan upload pemicunya.
- **Stok sales** (`xm.tracked_sales`, `xm.stock_log`): nomor yang dipantau dan catatan otomatis.
- **Pengaturan company** (`xm.app_preferences`): nama, kata kunci, kunci, dan pengelompokan listing.
- Penulisan cache grup dan perubahan status memakai kunci advisory `9042028` agar perubahan status tidak berselisih dengan penghitungan ulang.
- Layanan UI (`docker/ui-nginx.conf`) mengompres file dengan gzip (skrip utama dari 861 kB menjadi sekitar 247 kB) dan menyimpan file build berhash di cache peramban, sehingga halaman terbuka lebih cepat di ponsel; `index.html` selalu diperiksa ulang agar rilis baru langsung terpakai. Proxy `docker/nginx.conf` ikut mengompres respons JSON.
- Frontend: Vite/vinext (static export), HeroUI v3, Tailwind v4, Magic UI (`components/magicui`), Chart.js, Lottie (`lib/lottie/*.json`, dibuat oleh `scripts/generate-lottie.py`).

## Verifikasi rilis

- Backend: `python3 -m unittest discover -s api -p 'test_*.py'` dengan `XM_TEST_DATABASE_URL` mengarah ke database uji terpisah. Mencakup ID (termasuk kecocokan fungsi SQL dan Python), status, riwayat match, kunci kata kunci, pengelompokan, stok sales, dashboard, matriks hak akses, dan isolasi antar company melalui permintaan HTTP.
- Uji skala (database uji terpisah, data sintetis): 400.000 dokumen mendapat ID dalam sekitar 284 detik (sekali saja, saat migrasi atau unggahan pertama yang sangat besar); menambah 50.000 dokumen baru ke arsip itu memerlukan sekitar 74 detik. ID berjalan `L-AA000` … `L-KZ999` untuk 240.000 listing, tanpa tabrakan. Unggahan biasa (ribuan pesan) hanya memberi ID pada pesan baru, sehingga berlangsung dalam hitungan detik di worker. ID lengkap (mis. `AB908`) dicari lewat indeks; mengetik sebagian ID memakai pencarian "mengandung" yang memerlukan sekitar 0,35 detik pada 450.000 entitas.
- Frontend: `npx tsc --noEmit`, `npm run lint`, dan `npm run build` bersih. Alur login, unggah, Match Terbaru, status, stok, pengaturan, dan tim diuji di browser pada lebar desktop dan ponsel (375 px).
