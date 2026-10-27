# Integrasi backend Property → Workflow Builder

Backend menyediakan job export PDF per kelompok tanpa browser. Workflow Builder
memulai job sesuai jadwal WIB, mengecek status, mengunduh setiap PDF, lalu
mengirimnya melalui GOWA. Backend Property tidak mengirim WhatsApp.

Dokumen ini menjelaskan kontrak backend yang tersedia di repo. Deploy backend
API dan worker diperlukan sebelum endpoint tersedia di domain produksi. Node
Property dan routing penerima per file di Workflow Builder perlu ditambahkan
secara terpisah.

## Alamat dan autentikasi

Di web/nginx, semua alamat API memakai prefix `/api`. Jika mengakses FastAPI
langsung (port 8000), hilangkan prefix `/api`. Gunakan satu base URL agar
`download_url` relatif dari manifest dapat diselesaikan ke host yang sama.

Admin company atau admin platform membuat token melalui sesi login yang aktif:

```http
POST /api/integration/keys
Content-Type: application/json
Cookie: xm_session=<sesi-admin>

{"name":"Workflow Builder"}
```

Respons `201` berisi `id`, `token`, `scope: "property:export"`, `company_id`,
`name`, dan `created_at`. Simpan `token` di penyimpanan kredensial workflow.
Token mentah hanya dikembalikan saat pembuatan; database menyimpan hash SHA-256.
UI tersedia di **Pengaturan → Integrasi workflow** untuk admin company dan admin platform. Isi nama koneksi, klik **Buat token**, lalu **Salin token**. Setelah disimpan ke kredensial workflow, tekan **Sudah disimpan**. Tab ini juga menampilkan URL API, daftar token, waktu pemakaian terakhir, dan tombol pencabutan dengan konfirmasi. Anggota tim tidak dapat mengakses pengelolaan token.

- `GET /api/integration/keys`: daftar metadata token company, tanpa token mentah.
- `DELETE /api/integration/keys/{id}`: cabut token; perlu sesi admin.
- Token integrasi tidak memberi akses ke `/workspace`, pengaturan, atau API admin.
- Token terikat company dan tidak boleh memakai header `X-XM-User-Id` untuk
  mengganti workspace. Admin platform boleh memilih workspace saat membuat key
  melalui mekanisme sesi admin yang sudah ada.
- Token ditolak jika dicabut, pembuatnya terkunci, atau pembuatnya kehilangan
  hak admin company. Untuk rotasi, buat token baru dan cabut token lama.

Endpoint job dan file menggunakan header berikut pada **setiap request**:

```http
Authorization: Bearer <token-integrasi>
```

Sesi browser saja tidak dapat mengakses endpoint job integrasi.

## 1. Buat job export

```http
POST /api/integration/exports
Authorization: Bearer <token-integrasi>
Content-Type: application/json

{
  "request_id": "workflow-42:2026-10-09:08:00",
  "direction": "property",
  "group_by": "phone",
  "date_from": "2026-10-08",
  "date_to": "2026-10-08",
  "time_from": "00:00",
  "time_to": "23:59",
  "statuses": "hot,warm",
  "stock_status": "ready",
  "search": "",
  "phones": "",
  "public_id": "",
  "recipients": {}
}
```

Respons `202`:

```json
{
  "job_id": "<uuid-job>",
  "request_id": "workflow-42:2026-10-09:08:00",
  "status": "queued",
  "group_by": "phone",
  "direction": "property",
  "progress": {},
  "files": [],
  "error": null,
  "status_url": "/api/integration/exports/<uuid-job>",
  "poll_after_seconds": 3
}
```

`request_id` wajib dan berfungsi sebagai idempotency key dalam satu company.
Gunakan identitas workflow dan kejadian jadwal, bukan UUID baru pada tiap retry.
Payload yang sama mengembalikan job yang sama; ID yang sama dengan filter atau
mapping penerima berbeda menghasilkan `409`. Job gagal/kedaluwarsa tidak
otomatis dibuat ulang saat ID yang sama dipanggil. Gunakan ID percobaan baru
untuk sengaja membuat export baru.

Pilihan:

| Field | Nilai/perilaku |
| --- | --- |
| `direction` | `property` (default: listing → buyer) atau `buyer` |
| `group_by` | `phone` atau `sender`; jika kosong, setting company dibekukan saat job dibuat |
| `statuses` | `hot`, `warm`, `unmatched`, atau gabungan dipisahkan koma; default `hot,warm` |
| `stock_status` | Filter status sumber: `ready`, `on_hold`, `sold`, `deleted`, atau gabungan; default `ready` |
| `date_from`, `date_to` | Tanggal posting sumber; kosong berarti tidak dibatasi tanggal |
| `buyer_date_from`, `buyer_date_to` | Rentang tanggal posting buyer, pada kedua arah pencocokan |
| `listing_date_from`, `listing_date_to` | Rentang tanggal posting listing, pada kedua arah pencocokan |
| `buyer_period`, `listing_period` | Periode relatif untuk jadwal; diubah menjadi tanggal WIB saat job dibuat |
| `delivery_scope` | Identitas aliran report/audiens yang stabil; jika diisi, hanya pasangan yang belum dikonfirmasi terkirim masuk report |
| `time_from`, `time_to` | Jam batas pada tanggal tersebut, mengikuti perilaku filter Cocokkan |
| `phones` | Filter nomor listing, dipisahkan koma; dipakai pada arah `property` |
| `search`, `public_id` | Filter teks/ID seperti Cocokkan; `search` kosong berarti tanpa filter keyword |
| `recipients` | Mapping kunci kelompok yang persis sama → nomor WhatsApp tujuan |
| `recipient_names` | Mapping kunci kelompok → nama penerima GOWA, misalnya `Ivan P - Konig` |

Rekomendasi target mengikuti perilaku export Cocokkan: target yang masih Ready.
Batas 15.000 halaman berlaku per PDF kelompok. Job kosong selesai dengan
`files: []`; workflow dapat mencatat “tidak ada hasil” tanpa mengirim pesan.

### Periode buyer dan listing yang terpisah

Pada Cocokkan, pilih **Tanggal posting buyer** dan **Tanggal posting listing**
secara independen. Nilainya tetap mengikuti jenis data ketika arah dibalik.
Jumlah Hot/Warm, kelompok sales, rekomendasi, PDF pilihan, dan export semua
memakai kedua rentang yang sama. Target yang tidak mempunyai posting dalam
rentang terpilih tidak ditampilkan. Repost dalam rentang tetap ditemukan
meskipun ada salinan yang lebih baru di luar rentang.

Untuk jadwal 09.37 WIB, buyer hari ini dibandingkan stok tiga bulan terakhir:

```json
{
  "request_id": "konig:2026-10-09:09:37",
  "direction": "property",
  "group_by": "phone",
  "buyer_period": {"mode": "today"},
  "listing_period": {"mode": "last_months", "amount": 3},
  "delivery_scope": "konig-daily",
  "statuses": "hot,warm",
  "stock_status": "ready",
  "recipient_names": {"6282226811158": "Ivan P - Konig"}
}
```

Kebalikannya: gunakan `listing_period: {"mode":"today"}` dan
`buyer_period: {"mode":"last_months","amount":1}`. Pilih `direction` sesuai
kelompok penerima yang ingin dibuat. Semua tanggal dihitung pada kalender WIB.
Pada 9 Oktober 2026, `last_months` dengan `amount: 1` berarti 9 September–9
Oktober; `previous_month` berarti 1–30 September. `amount: 3` berarti 9 Juli–9
Oktober. Batas hari awal dan akhir ikut dihitung. Pilihan lain: `all`,
`last_days` (termasuk hari ini), dan `custom` dengan `date_from`/`date_to`.

Jangan campur periode relatif dengan tanggal absolut untuk jenis data yang
sama, atau dengan `date_from`/`date_to` lama. Job membekukan periode saat
dibuat; retry dengan `request_id` yang sama tetap memakai periode pertama,
meskipun melewati tengah malam. Respons job memuat tanggal hasil di `periods`.

Filter tanggal posting bukan tanggal pertama kali buyer diimpor. Jika hanya
memilih `today` pada 09.37, buyer yang masuk setelah jam itu belum tercakup;
gunakan rentang yang mencakup sejak run terakhir atau jadwal tambahan agar
data tersebut tidak terlewat.

## 2. Tunggu status dan baca manifest

```http
GET /api/integration/exports/<uuid-job>
Authorization: Bearer <token-integrasi>
```

Status: `queued`, `processing`, `completed`, `failed`, `cancelled`, `expired`.
Poll sesuai `poll_after_seconds`. Saat `processing`, `progress` berisi
`groups_total`, `groups_done`, `pages_done`, dan `pages_estimated`. Angka estimasi
berasal dari rencana awal; halaman aktual bisa berubah jika data berubah saat
export berlangsung. Saat gagal, baca `error` dan hentikan pengiriman.

Saat selesai, tiap item `files` berbentuk:

```json
{
  "file_id": "<uuid-file>",
  "group_key": "6282226811158",
  "group_name": "Ivan Prayogo · +62 822-2681-1158",
  "contact_name": "Ivan Prayogo",
  "contact_phone": "6282226811158",
  "recipient_phone": "6282226811158",
  "recipient_name": "Ivan P - Konig",
  "recipient_status": "ready",
  "recipient_reason": "group_phone",
  "filename": "01_Ivan-Prayogo_6282226811158_60listing_hal1-574.pdf",
  "mime": "application/pdf",
  "size_bytes": 123456,
  "source_count": 60,
  "source_type": "listing",
  "page_count": 574,
  "first_page": 1,
  "last_page": 574,
  "download_url": "/api/integration/exports/<uuid-job>/files/<uuid-file>"
}
```

Setiap PDF diberi nomor halaman sendiri mulai dari 1 ("Hal. N dari M"), termasuk
halaman pembuka kelompok, jadi `first_page` selalu 1 dan `last_page` sama dengan
`page_count`. Nomor pasangan ("No. N") juga mulai dari 1 di setiap PDF.
`source_count` adalah jumlah listing/buyer sumber, bukan jumlah pasangan.

Aturan penerima:

- Mapping `recipients[group_key]` yang valid selalu diprioritaskan:
  `recipient_reason: "mapping"`.
- Kelompok `phone` memakai nomor kunci kelompok yang valid:
  `recipient_reason: "group_phone"`.
- Nomor kosong/tidak valid: `recipient_phone: null`,
  `recipient_status: "needs_review"`, alasan `missing_phone`.
- Kelompok `sender` wajib punya mapping eksplisit. Contoh:
  `"recipients": {"~ Ivan Prayogo": "082226811158"}`. Tanpa mapping, alasan
  `sender_mapping_required` dan status `needs_review`.
- `contact_phone` hanya metadata listing. Jangan gunakan sebagai fallback tujuan
  untuk kelompok pengirim. Nomor penerima dinormalisasi ke format `628…`.

## 3. Download PDF dan kirim dari workflow

```http
GET /api/integration/exports/<uuid-job>/files/<uuid-file>
Authorization: Bearer <token-integrasi>
```

Respons biner `application/pdf`, dengan `Content-Disposition` nama file.
File tetap tersedia untuk download ulang/retry sampai `expires_at`. Default
masa simpan 7 hari, diatur melalui `XM_INTEGRATION_EXPORT_DAYS` pada API dan worker.
File kedaluwarsa mengembalikan `410`; file belum selesai `409`; file/job company
lain `404`.

`download_url` adalah alamat **privat**, bukan URL publik yang langsung bisa
dikirim ke GOWA sebagai `file_url`. Workflow harus mengunduhnya dengan Bearer
terlebih dahulu, menyimpan sebagai artifact/file, lalu mengirim PDF tersebut
melalui multipart GOWA `/send/file`.

Node pengiriman membaca `files[]`, hanya memproses `recipient_status == "ready"`,
dan memakai `recipient_phone` item tersebut. Gunakan `filename` dan `mime`
item yang sama untuk lampiran. Pertahankan pasangan metadata-file; jangan
menebak penerima dari nama file atau posisi daftar. Jangan mengirim ZIP untuk
mode per kontak.

Catat hasil pengiriman berdasarkan `(job_id, file_id, recipient_phone)` di
workflow. Idempotency export mencegah job duplikat, tetapi tidak menggantikan
pencatatan pengiriman WhatsApp. Jika respons GOWA timeout setelah request
terkirim, hasilnya belum pasti; jangan langsung menganggap pesan tidak terkirim.

### Konfirmasi pengiriman dan hindari pasangan berulang

Setelah GOWA mengonfirmasi pengiriman berhasil, catat receipt untuk file itu:

```http
POST /api/integration/exports/<uuid-job>/files/<uuid-file>/delivered
Authorization: Bearer <token-integrasi>
Content-Type: application/json

{"delivery_id":"<id-pengiriman-GOWA>"}
```

Endpoint ini memerlukan `delivery_scope`, file completed yang belum expired,
dan penerima `ready`. Retry receipt idempotent. Manifest kemudian menyertakan
`delivered_at` dan `delivery_id`; consumer melewati file yang sudah terkirim.
Download atau pembuatan PDF tidak menandai pasangan sebagai terkirim.

Job baru dengan scope yang sama mengecualikan pasangan buyer–listing yang
telah dikonfirmasi. ID pasangan memakai entitas yang stabil, sehingga repost,
perhitungan ulang, perubahan arah, dan kedaluwarsa PDF tidak mengulang pasangan
tersebut. Stok yang sama dengan buyer berbeda tetap masuk sebagai pasangan
baru. Scope terpisah digunakan untuk audiens/aliran report yang berbeda.
Report dengan scope hanya mendukung Hot/Warm, bukan sumber tanpa pasangan.

Jalankan satu aliran pengiriman secara berurutan. Receipt tidak membatalkan PDF
yang sudah terlanjur disiapkan job lain sebelum receipt dicatat. Consumer tetap
harus menyimpan status GOWA per file dan menangani timeout dengan rekonsiliasi,
agar retry setelah pengiriman berhasil tetapi sebelum receipt tidak mengirim
ulang. Backend Property tidak memanggil GOWA atau menjamin exactly-once delivery.

## Pembatalan, worker, dan deployment

`DELETE /api/integration/exports/{job_id}` membatalkan job queued/processing.
Job completed tidak dihapus oleh endpoint ini. Pembatalan diperiksa di antara
bagian render; hasil parsial tidak dipublikasikan sebagai file siap kirim.

Jalankan API dan `python worker.py` dengan database serta volume `/data` yang
sama. Compose sudah mengatur:

- `XM_EXPORT_DIR=/data/export-parts`
- `XM_INTEGRATION_EXPORT_DIR=/data/integration-exports`
- `XM_INTEGRATION_EXPORT_DAYS=7` (dapat diubah)

Skema diterapkan oleh `ensure_schema()` saat startup API/worker. Job tersimpan
di PostgreSQL. Worker memproses import dan maintenance dahulu, lalu export.
Session advisory lock mencegah dua worker mengambil job yang sama. Jika worker
mati, job `processing` dapat diambil kembali dan dirender ulang dari awal;
progress mungkin kembali ke nol. Tidak ada pengiriman WhatsApp oleh worker ini.
File expired dibersihkan oleh worker dan metadata job tetap ada untuk idempotensi.

Jadwal dan webhook tetap menjadi tanggung jawab Workflow Builder. Backend ini
menggunakan pola request/poll/download; tidak memanggil webhook eksternal.

## Tambahan API administratif (27 Oktober 2026)

Job workflow kini mendukung `combined:true` untuk kedua arah per orang.
`POST /integration/exports/plan` memberikan group keys untuk pemilihan sales/pengirim;
`GET /integration/exports` menampilkan riwayat job company.
`GET /integration/exports/{job_id}/download?output=zip|pdf` mengunduh seluruh atau beberapa
kelompok, dengan query `group_keys` yang dapat diulang. PDF per file tetap tersedia dan
semua download dapat diulang sampai expiry. Lihat [panduan administratif](API_ADMINISTRATIF.md)
untuk login as company, watchlist, filter tanggal, dan AI Review.
