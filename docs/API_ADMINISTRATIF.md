# API administratif Property AutoAudit

Base URL web: `https://<domain>/api`. Backend FastAPI langsung: `http://<host>:8000` tanpa `/api`.
Semua contoh menggunakan placeholder, bukan kredensial produksi. [Katalog endpoint](API_ENDPOINTS.md) mencantumkan seluruh route; [OpenAPI](openapi.json) berisi tipe, field, dan response lengkap untuk diimpor ke Postman.

## Login, company ID, dan login as

```bash
export BASE='https://<domain>/api'
curl --fail-with-body -c cookies.txt "$BASE/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"email":"<email>","password":"<password>"}'
curl --fail-with-body -b cookies.txt "$BASE/auth/me"
curl --fail-with-body -b cookies.txt "$BASE/company/settings"
```

`company/settings` mengembalikan `company_id`. Admin platform dapat mengambil seluruh company melalui `GET /admin/companies`, termasuk `company_id`, `owner_id`, dan anggota.

Untuk **login as company**, tetap gunakan sesi admin dan kirim `X-XM-Company-Id: <company_id>` pada setiap request. Tidak membuat sesi palsu atau mengganti password. Alternatif lama: `X-XM-User-Id: <owner_id>`. Bila kedua header dipakai, targetnya harus sama. Hanya admin platform dapat memilih company lain; company admin/anggota tidak dapat berpindah company. Request tanpa header kembali ke company akun login.

```bash
curl --fail-with-body -b cookies.txt "$BASE/company/settings" \
  -H 'X-XM-Company-Id: <company_id>'
```

`POST /auth/logout` mengakhiri sesi. Hak anggota tetap mengikuti fitur yang diberikan admin company. Jangan commit `cookies.txt`.

## Pilihan autentikasi otomatisasi

| Metode | Kegunaan | Pemilihan company |
| --- | --- | --- |
| Cookie sesi login | Semua fitur sesuai izin akun, termasuk tim dan konfigurasi | Header company hanya untuk admin platform |
| `Bearer xm_internal_…` | Administrasi matching lintas company, upload, watchlist, ekspor, AI Review | Wajib `X-XM-Company-Id`; daftar company/OpenAPI boleh tanpa header |
| `Bearer xm_wf_…`, scope `property:export` | Preview, antrean ekspor, daftar job, PDF/ZIP, delivery receipt | Terikat company pembuat token |
| `Bearer xm_wf_…`, scope `property:upload` | Upload dan status impor integrasi | Terikat company pembuat token |

Internal key diatur server melalui `XM_INTERNAL_API_KEY`, bukan melalui API. Route administrasi akun, password, deploy, dan pengelolaan kredensial tidak dibuka untuk internal key. Allowlist definitif ada di `api/internal_api.py`.

Token workflow dibuat dengan sesi admin: `POST /integration/keys`, body `{"name":"Daily export","scope":"property:export"}`. Token mentah hanya ditampilkan sekali. Cabut lewat `DELETE /integration/keys/{id}`. Route `/integration/imports` dan `/integration/exports` menggunakan bearer, bukan cookie saja.

## Upload dan pemrosesan

```bash
curl --fail-with-body -b cookies.txt "$BASE/imports" \
  -H 'X-XM-Company-Id: <company_id>' \
  -F 'file=@cleaned.json' -F 'agent_name=Daily import'
```

Upload memakai JSON ekspor chat yang didukung importer, bukan ZIP sembarang. Respons `202` berarti masuk antrean, belum selesai. `GET /imports` menampilkan progres. Untuk workflow: `POST /integration/imports` dengan multipart yang sama, lalu `GET /integration/imports/{import_id}`. API dan worker harus berjalan. Impor duplikat mengikuti pemeriksaan importer.

## Matching dengan custom filter

1. `GET /workspace`: daftar buyer (`direction=buyer`) atau listing (`direction=property`). Filter: `search`, `phones` (nomor dipisahkan koma), `statuses=hot,warm,custom,unmatched`, `stock_status=ready,on_hold,sold`, `public_id`, `offset`, dan tanggal posting kedua sisi.
2. `POST /workspace/recommendations`: pasangan untuk maksimal 50 source ID per request.
3. `POST /export/selection`: ekspor pasangan yang dipilih secara eksplisit, maksimal 200 pasangan, lintas kedua arah.

Gunakan filter tanggal yang sama pada daftar, rekomendasi, preview ekspor, dan job ekspor:

```json
{
  "direction": "buyer",
  "ids": ["<source_document_uuid>"],
  "target_status": ["ready"],
  "buyer_date_from": "2026-10-26",
  "buyer_date_to": "2026-10-27",
  "listing_date_from": "2026-07-27",
  "listing_date_to": "2026-10-27"
}
```

Tanggal menggunakan kalender WIB, inklusif kedua ujung. UUID dokumen diambil dari response workspace; ID tampilan seperti `B-AA000` dipakai untuk pencarian `public_id`, bukan sebagai UUID pasangan.

`GET/PUT /settings` mengelola aturan mesin matching; `GET/PUT /search-default` mengelola filter pencarian company. Filter request tidak mengubah konfigurasi tersimpan. `GET /entities/lookup?q=…` membantu mencari ID; `POST /entities/status` dengan `{"ids":["<id>"],"status":"on_hold","note":"Follow up"}` mengubah status entitas.

## Sales watchlist

| Endpoint | Fungsi / body |
| --- | --- |
| `GET /stock/overview` | Daftar sales tracked, label, dan stock |
| `POST /stock/tracked/item` | Tambah `{"phone":"081234567890","label":"Nama sales"}` |
| `PATCH /stock/tracked/item` | Edit `{"original_phone":"6281234567890","phone":"081234567890","label":"Nama baru"}` |
| `DELETE /stock/tracked/item?phone=6281234567890` | Hapus satu sales dari watchlist; log lama tetap ada |
| `PUT /stock/tracked` | Ganti daftar seluruhnya; gunakan add item untuk penambahan biasa |
| `POST /stock/tracked/parse` | Parse teks daftar kontak; lihat skema OpenAPI |
| `POST /stock/snapshot` | Ambil snapshot stock |
| `GET /stock/log`, `GET /stock/export` | Riwayat dan ekspor stock |

Untuk laporan per sales watchlist: ambil nomor dari overview, gunakan `group_by: "phone"` dan `phones` berisi nomor pilihan. Untuk laporan per pengirim: gunakan `group_by: "sender"` dan `group_keys` dari preview. Jangan menebak raw key pengirim; raw key dapat berbeda dari nama tampilan.

## Ekspor ZIP/PDF per sales atau pengirim

### Pilihan pasangan tertentu, langsung download

`POST /export/selection`, sesi atau internal key:

```json
{
  "pairs": [
    {"direction":"buyer","source_id":"<buyer_document_uuid>","target_id":"<listing_document_uuid>"}
  ],
  "output": "grouped"
}
```

`grouped` mengelompokkan PDF menurut setting company; dapat berupa satu PDF jika hanya satu orang. `single` menghasilkan satu PDF gabungan. Gunakan job di bawah jika membutuhkan ZIP secara eksplisit, termasuk untuk satu orang.

### Ekspor besar melalui antrean

1. `POST /integration/exports/plan`: kirim filter, baca `groups[].key`, judul, jumlah pasangan, dan halaman.
2. `POST /integration/exports`: kirim filter yang sama, tambahkan `request_id` unik dan `group_keys` pilihan. `202` mengembalikan `job_id`.
3. Poll `GET /integration/exports/{job_id}` sesuai `poll_after_seconds` sampai `completed`, `failed`, `cancelled`, atau `expired`.
4. Unduh ZIP, PDF gabungan, atau PDF seorang dari manifest.

```json
{
  "request_id": "daily-sales-2026-10-27-01",
  "direction": "property",
  "combined": true,
  "group_by": "phone",
  "phones": "6281234567890,6282233744657",
  "statuses": "hot,warm,custom",
  "stock_status": "ready",
  "buyer_period": {"mode":"last_days","amount":2},
  "listing_period": {"mode":"last_months","amount":3}
}
```

`combined:true` menggabungkan Listing → Buyer dan Buyer → Listing per orang. `false` memakai `direction` saja. `group_keys:null` atau field tidak dikirim berarti semua kelompok yang lolos filter; `[]` berarti tidak ada kelompok. `buyer_period`/`listing_period` dibekukan ketika job dibuat; retry dengan `request_id` yang sama tidak bergeser ke tanggal baru. Jangan gabungkan relative period dan tanggal eksplisit untuk sisi yang sama.

| Download | Hasil |
| --- | --- |
| `GET /integration/exports/{job_id}/download?output=zip` | ZIP berisi satu PDF per orang |
| `GET /integration/exports/{job_id}/download?output=pdf` | Satu PDF gabungan |
| `GET /integration/exports/{job_id}/download?output=zip&group_keys=<key1>&group_keys=<key2>` | Hanya kelompok yang dipilih |
| `GET /integration/exports/{job_id}/files/{file_id}` | Satu PDF orang, sesuai `files[].download_url` |

Gunakan URL encoding untuk raw group keys. Setiap download tetap perlu autentikasi. File dapat diunduh ulang sampai kedaluwarsa (default tujuh hari). `GET /integration/exports?limit=50&offset=0` menampilkan job company; `DELETE /integration/exports/{job_id}` membatalkan job pending/processing.

`request_id` sama + filter sama mengembalikan job yang sama. Filter berbeda dengan `request_id` sama menghasilkan `409`. PDF gabungan maksimal 15.000 halaman; pilih ZIP atau kelompok lebih sedikit jika melebihi batas. Untuk penjadwalan/pengiriman, lihat [WORKFLOW_INTEGRATION.md](WORKFLOW_INTEGRATION.md). Download tidak menandai file sudah terkirim.

Alternatif ekspor sesi yang sudah tersedia: `POST /export/all/plan` → `POST /export/all/part` untuk setiap kelompok/offset/section → `GET /export/all/{token}/download?output=single|grouped`. Jalur lama menghapus bagian sementara setelah download; job integrasi lebih cocok untuk retry otomatisasi.

## AI Review

Default UI dan API tanpa filter eksplisit:

- Buyer: kemarin dan hari ini (WIB).
- Listing: tiga bulan kalender terakhir sampai hari ini.
- `max_pairs:150`, `directions:"both"`, status Hot/Warm/Custom.
- Model Jev `typesafe/jev-1.13` (preset Jev yang sudah tersedia di aplikasi). UI kembali memilih default ini saat membuka form, bukan model terakhir.
- Probabilitas rekomendasi Jev `<0.40`: reject; `0.40–<0.70`: unsure; `>=0.70`: recommend.

Angka ini adalah probabilitas kecocokan/rekomendasi dari **decision model Jev**, bukan confidence atas keputusan model teks lain. Aturan baru memakai versi cache `jev-d2`; hasil lama tidak diubah secara retroaktif. Review tidak mengubah status buyer/listing menjadi deleted. Export approved tetap hanya memasukkan recommend (termasuk override), bukan unsure.

| Endpoint | Fungsi |
| --- | --- |
| `GET /ai/models` | Model dan default |
| `POST /ai/review-runs/estimate` | Estimasi pasangan/biaya; `{}` memakai default |
| `POST /ai/review-runs` | Mulai review; `{}` memakai default dan key company |
| `GET /ai/review-runs` | Riwayat run |
| `GET /ai/review-runs/{id}` | Progres |
| `GET /ai/review-runs/{id}/results` | Hasil/filter keputusan; lihat OpenAPI |
| `POST /ai/review-runs/{id}/cancel` | Batalkan |
| `PUT /ai/reviews/{id}/override` | Override keputusan, body sesuai OpenAPI |
| `POST /ai/reviews/lookup` | Hasil review untuk pasangan tertentu |
| `GET /ai/review-runs/{id}/export?output=grouped` | PDF/ZIP hasil disetujui |

Tanggal eksplisit tetap boleh diganti. Kirim kedua field tanggal suatu sisi sebagai `""` untuk semua tanggal; menghilangkan kedua field memakai default sisi tersebut. Key AI dikonfigurasi platform admin melalui `/ai/keys`; internal key tidak mendapat akses ke pengelolaan rahasia. Menjalankan review memerlukan worker serta key AI aktif dan dapat menimbulkan biaya provider.

## Administrasi lain dan error

Sesi admin juga dapat mengelola `/team/users`, fitur anggota, `/company/settings`, `/glossary`, `/location-index`, reprocess/index, serta administrasi company melalui `/admin/companies`. Detail seluruh endpoint ada di katalog dan OpenAPI. Izin endpoint mengikuti middleware; adanya endpoint di katalog bukan izin bagi semua token.

`401`: autentikasi salah/kedaluwarsa. `403`: scope/izin salah. `404`: objek tidak ditemukan di company terpilih. `409`: konflik/hasil belum siap. `410`: export kedaluwarsa. `422`: validasi payload. Periksa JSON `detail`; jangan retry otomatis untuk kesalahan validasi. Untuk job async, simpan `job_id` dan poll status; respons queued bukan bukti PDF sudah tersedia.
