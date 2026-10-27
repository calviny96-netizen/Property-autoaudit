# Validasi perubahan API — 27 Oktober 2026

Dilakukan pada source aplikasi, tanpa deployment produksi dan tanpa panggilan AI berbayar.

- TypeScript: `npx tsc --noEmit` lulus.
- Build: `npm run build` lulus.
- 25 tes: `test_daily_api`, `test_internal_api`, `test_integration`, dan `test_jev_review.AnswerTests` lulus.
- 31 tes pada pemeriksaan akhir: `test_daily_api`, `test_export_combined`, `test_export_selection`, `test_matching_periods` lulus. Beberapa tes bertumpang tindih dengan kelompok pertama.
- Tes HTTP/worker memakai PostgreSQL disposable; mencakup scope company, session company header, watchlist, preview, export ZIP/PDF, combined directions, pilihan kelompok, polling, pagination, missing/foreign IDs, repeat downloads, dan batas keputusan 40%.
- Default buyer/listing diuji dengan tanggal WIB yang dibekukan; nilai tanggal eksplisit/semua tanggal tetap dipertahankan.
- Backup PostgreSQL dan snapshot Qdrant dipulihkan ke layanan disposable. Kunci/sesi dikeluarkan dari PostgreSQL; referensi key riwayat AI dinolkan agar relasi tetap valid.
- Pemulihan Qdrant: `green`, 452.704 point. Enkripsi AES-GCM diuji round-trip dan tamper rejection.

UI yang diubah hanya nilai awal form AI Review dan pilihan preset tanggal; build/typecheck lulus. Pemeriksaan visual browser belum dilakukan. Seluruh perubahan lain yang sudah ada di workspace ikut dimuat dalam snapshot source repo; hasil ini bukan klaim bahwa seluruh fitur aplikasi telah diuji menyeluruh.
