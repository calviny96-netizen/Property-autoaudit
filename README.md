# Property Auto Audit — v4.0

Local property matchmaking workspace for Property. It imports WhatsApp `cleaned.json` files per agent, parses each message with deterministic Python rules, stores structured records in PostgreSQL schema `xm`, indexes searchable content in Qdrant collection `xm_rag`, and exposes the results to Hermes Agent.

## Pembaruan v4.0

Major update: ID publik untuk setiap listing dan buyer (`L-AB908`, `B-AB908`), status Ready / On-hold / Sold / Hapus, halaman **Match Terbaru** dengan riwayat tanggal, **Beranda** statistik yang dihitung Python, **Stok Sales** per nomor telepon, pengelompokan listing per pengirim atau nomor telepon, company dengan banyak akun (super admin dapat menambah akun dan mengunci kata kunci), serta tampilan baru yang lebih mudah dipakai di ponsel.

Panduan lengkap, tabel peran, dan langkah upgrade: [docs/UPGRADE_V4.0.md](docs/UPGRADE_V4.0.md).

## Local endpoints

- Web UI: `http://127.0.0.1:9004`
- The same local stack is also available at `http://127.0.0.1:9046` (replaces the temporary sample preview).
- Hermes gateway: `http://127.0.0.1:9002`

## Data isolation

- PostgreSQL schema: `xm`
- Qdrant collection: `xm_rag`
- Local runtime data: `data/`
- Every account owns a private workspace. PostgreSQL records and Qdrant payloads use the workspace namespace in `company_id`; the legacy admin archive keeps `xm`, and new users receive a separate `xm-user-<uuid>` namespace. Every import also carries an `agent_name`.

## Matching defaults

- Category and transaction: hard match
- Land area tolerance: 10%
- Building area tolerance: 20%
- Price tolerance: 10%
- Imported location-index alternatives: at most 4 km, WARM pending buyer confirmation; distance never overrides mandatory constraints.

## Matching workspace update

- Upload menawarkan mode **Company** (keyword milik company, tanpa membedakan huruf besar/kecil, termasuk tanda tangan pesan) atau **Sales** (nomor kontak utama/alternatif dalam watchlist company). Salah satu sisi pasangan harus termasuk company/sales yang dipantau; sisi lain dicari dari seluruh arsip workspace tersebut. Mode Sales ditolak bila watchlist company kosong, dan tidak memakai nama pengunggah/pengirim sebagai nomor pemilik listing.
- Mode upload tersimpan pada riwayat; mode terakhir yang berhasil diproses menjadi default company untuk proses ulang berikutnya. Company baru mulai pada mode Company tanpa saringan keyword. Aturan keyword/watchlist dibaca kembali saat worker mencocokkan, sehingga aturan yang sudah berubah setelah upload tetap berlaku. File duplikat tidak menambah pesan; bila mode diganti, arsip yang ada diproses ulang sebagai maintenance, tanpa menandai pasangan lama sebagai penemuan upload baru.
- Pengaturan → Umum juga dapat mengganti mode otomatis dan menghitung ulang hasil. Pergantian filter, arah, sales, serta proses ulang menampilkan animasi loading dan skeleton/status proses; animasi mengikuti preferensi pengurangan gerak perangkat.
- After login, choose Buyer → Property or Property → Buyer with all dates selected by default; optional Jakarta calendar presets include this month, this week (Monday–Sunday), last month, and a manual range before opening the matching workspace.
- Both search directions use the company's keywords (company baru mulai tanpa saringan). Super admin dapat memasukkan sampai 20 frasa dipisah baris atau koma; sumber yang cocok dengan salah satu frasa dipakai. Super admin dapat **mengunci** kata kunci; bila tidak dikunci, setiap anggota boleh memakai daftarnya sendiri.
- Select one source card to open its recommendations. On mobile, the source list and recommendations appear as separate views, with a back button to return to the list.
- Property contacts support multiple Indonesian phone numbers separated by commas or newlines, normalized to `62`. Matching uses bubble contact details, including alternate numbers, independently of the uploading agent.
- Hot: score ≥80 only after recognised mandatory constraints pass; uncertain evidence or tolerances cap at 79. Warm: 60–79; Belum cocok: no stored match ≥60. Unrequested factors contribute no points. Multiple status filters can be enabled together.
- Exact complete raw texts are grouped before pagination and in both recommendation directions. Original messages remain stored; occurrence badges expose repeats.
- Local accuracy audit and sample limits: [docs/MATCHING_ACCURACY_AUDIT.md](docs/MATCHING_ACCURACY_AUDIT.md).
- Original-data Caesar feedback loop, regression coverage, and measured search/render checks: [docs/CAESAR_QUALITY_AUDIT.md](docs/CAESAR_QUALITY_AUDIT.md). Repeat the local evidence export with `python audit_quality.py --search caesar --output /tmp/caesar-audit.json` inside the API container; the export contains private source messages.
- Settings and location glossary are stored in PostgreSQL. Save & reprocess queues a durable maintenance job; the worker reparses original messages, updates Qdrant, and recomputes matches. Progress survives leaving/reopening the page.
- Advertising phrases with concrete stock details no longer override listing intent. Contact signatures are extracted separately and excluded from structured locations, searchable matching text, and embeddings.
- Hot/Warm scores always include visible text: 🔥 Hot and 🌡️ Warm. Hot cards have a red border and soft pulsing glow, respecting reduced-motion preferences. PDF badges use rounded red/orange backgrounds; WhatsApp links prefill an Indonesian follow-up message.
- Select individual result pairs (or unmatched sources) to download an A4 portrait PDF (one selected pair per page, 11 pt body text that shrinks only when needed), with the same website logo, Jakarta generation date, and a clickable WhatsApp button only for the recommendation on the right. Maximum 200 report pairs from 50 sources per export.
- Export semua PDF offers one combined PDF or a ZIP containing one PDF per sender/phone group (default). Separate filenames include the sender/contact name, representative phone, source count, and continuous page range, e.g. `01_Ivan-Prayogo_6282226811158_60listing_hal1-574.pdf`. Grouping follows the company’s selected sender/phone setting.
- Backend workflow integration: company-scoped API keys, durable export jobs, per-contact PDF manifests, and authenticated file downloads. See [docs/WORKFLOW_INTEGRATION.md](docs/WORKFLOW_INTEGRATION.md).
- Cocokkan supports independent buyer and listing posting periods in both directions, with Today, rolling one/three months, previous calendar month, and custom ranges. Counts, recommendations and PDF exports follow both periods. Scheduled integration jobs can resolve relative WIB periods and exclude previously delivered pairs after an explicit successful-delivery receipt.
- Regression checks: `python3 -m unittest discover -s api -p 'test_*.py'`.
- Existing local services remain at http://127.0.0.1:9004. The Python/PostgreSQL/Qdrant stack is hosted through Docker Compose; the starter `.openai/hosting.json` has no registered cloud Site.

## Login dan peran

Default akun administrator platform lokal: `admin@autoaudit.id` / `secret123` (ganti lewat `XM_ADMIN_EMAIL` dan `XM_ADMIN_PASSWORD` sebelum instalasi pertama, lalu ganti password dari **Pengaturan → Akun saya**). Password disimpan sebagai PBKDF2 hash dan sesi login berlaku tujuh hari.

Tiga peran:

- **Administrator platform** (`admin`): membuat company, masuk ke company mana pun dari menu **Perusahaan**, dan mengangkat super admin.
- **Super admin company** (`company_admin`): menambah, mengunci, dan mereset akun anggota; mengunggah data; mengatur kata kunci (dan mengunci kata kunci), pengelompokan listing, nomor sales, toleransi, bobot, glosarium, dan indeks lokasi.
- **Anggota** (`user`): mencocokkan, menandai status, mengunduh PDF, dan melihat Beranda, Match Terbaru, serta Stok Sales.

Akun terkunci menampilkan layar kosong dengan latar blur dan tautan WhatsApp admin. Hak akses ditegakkan di server pada setiap permintaan.

## Backup lengkap

Gunakan `scripts/backup-data.sh` untuk membuat dump PostgreSQL, snapshot Qdrant `xm_rag`, serta arsip file sumber. Panduan pemulihan tersedia di `docs/BACKUP_RESTORE.md`. Backup data sengaja tidak dilacak Git karena berisi percakapan dan nomor kontak.

## Upgrade

Versi sekarang: `4.0.0`. Lihat [docs/UPGRADE_V4.0.md](docs/UPGRADE_V4.0.md) untuk memperbarui instalasi berjalan tanpa kehilangan data. Catatan versi sebelumnya: [v3.1](docs/UPGRADE_V3.1.md), [v3.0](docs/UPGRADE_V3.0.md).


## Admin and mobile update (v3.0, diperbarui di v4.0)

- Migration adds `role` and `is_locked` to existing users without replacing matching data. The configured bootstrap admin is promoted once and receives the configured password; subsequent restarts preserve the password and active sessions.
- Akun dikelola dari **Tim & Akses** (super admin company) atau **Perusahaan** (administrator platform). Password dan email yang diubah mencabut sesi akun itu. Kunci akun berlaku pada permintaan berikutnya; UI memeriksa status akun tiap 5 detik dan saat jendela difokuskan.
- Hanya super admin (dan administrator platform) yang dapat mengubah pengaturan company, upload, glosarium, indeks lokasi, dan penghitungan ulang. Akun terkunci hanya dapat melihat sesinya dan keluar. Hak akses ditegakkan di API, termasuk untuk permintaan langsung.


## Isolated user workspaces

From **Panel Admin**, choose **Data & setting** on an account. The settings drawer identifies the selected email; matching, JSON imports, glossary CSVs, location data, default searches, tolerances, and weights all belong to that account. **Workspace saya** returns to the admin's own archive. Users can only read and match their own data.

Admin requests carry a selected user ID, checked against the authenticated role on the server. Each request and worker job gets its own workspace context. PostgreSQL queries, uploaded-file directories, Qdrant searches/payloads, result caches, calendar counts, exports, and maintenance jobs are scoped to that workspace. A normal user cannot switch owner by changing a request header or submitting another account's document ID. The selected workspace stays local to the browser view, so admin tabs can manage different users independently.

The existing archive and its settings remain in the admin workspace; nothing is automatically copied to a new company. Upload the appropriate source JSON and location data from that company's **Unggah Data** and **Pengaturan** pages. Schema upgrades preserve existing records and add ownership for accounts, glossary entries, and jobs.

`api/test_user_isolation.py` exercises real HTTP requests against a disposable PostgreSQL database: identical uploads in different accounts, settings/glossary/location independence, forbidden owner switches and foreign document IDs, cached and uncached results, exports, worker ownership, reindexing, and concurrent requests. Run the regression suite with `XM_TEST_DATABASE_URL` pointing only to an isolated test database.

PDF layout regressions: install `api/requirements-test.txt` and run `python3 -m unittest discover -s api -p test_report.py`. These tests inspect the generated PDF for A4 size, page count, complete long text, default font size, and right-column WhatsApp links.

## API administratif dan backup berisi data

- [Panduan API administratif](docs/API_ADMINISTRATIF.md): login as company, upload, custom matching, watchlist, ZIP/PDF, AI Review.
- [Seluruh endpoint](docs/API_ENDPOINTS.md) dan [OpenAPI untuk Postman](docs/openapi.json).
- [Backup database terenkripsi](database/README.md): data aplikasi dan indeks, beserta cara pemulihan. Kunci tidak disimpan di Git.
