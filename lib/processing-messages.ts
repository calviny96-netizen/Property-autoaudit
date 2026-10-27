/**
 * Sentences shown while a background job runs (reprocess or upload). The product rule is an English UI,
 * but these were requested in Indonesian: short, no em-dashes, exactly 50 so the rotation is predictable.
 * The sentence shown is `(elapsed seconds / ROTATE_EVERY_S) mod 50`, so it keeps advancing between polls.
 */
export const ROTATE_EVERY_S = 2.5;

export const PROCESSING_MESSAGES: readonly string[] = [
  'Memulai proses pencocokan…',
  'Mencari data listing…',
  'Mencari data buyer…',
  'Mengambil data dari grup…',
  'Melakukan analisa pesan…',
  'Memisahkan pesan yang berisi beberapa konteks…',
  'Mengenali tujuan jual atau sewa…',
  'Membaca jenis unit: rumah, apartemen, ruko, gudang…',
  'Mencocokkan lokasi dan cluster…',
  'Menghitung harga per meter…',
  'Memeriksa legalitas SHM / HGB…',
  'Mencari tower dan tipe unit apartemen…',
  'Menimbang toleransi harga…',
  'Membandingkan luas tanah dan bangunan…',
  'Mengelompokkan posting yang sama…',
  'Menandai buyer yang menolak lokasi lain…',
  'Menyusun skor Hot, Warm, dan Custom…',
  'Menyiapkan alasan rekomendasi…',
  'Membaca budget dan kisaran harga…',
  'Mengenali singkatan dari kamus perusahaan…',
  'Mencari nomor kontak di tanda tangan pesan…',
  'Memeriksa kata kunci perusahaan…',
  'Mencocokkan nomor sales di watchlist…',
  'Menghitung jarak antar cluster…',
  'Mencari alternatif di cluster sekitar…',
  'Memeriksa area layanan per jenis unit…',
  'Membedakan jual, sewa, atau keduanya…',
  'Membaca hadap rumah dan arah…',
  'Mencatat syarat wajib dari buyer…',
  'Menyaring pesan yang bukan buyer atau listing…',
  'Mengenali pesan yang dikirim ulang…',
  'Menyusun kembali indeks pencarian…',
  'Menghitung kemiripan kalimat…',
  'Mencari pasangan yang paling cocok…',
  'Memeriksa status stok: ready, on hold, sold…',
  'Membaca lantai dan posisi unit…',
  'Mencatat cara bayar: KPR atau cash…',
  'Mencocokkan ukuran kavling…',
  'Mengelompokkan listing per pengirim…',
  'Membaca pesan dari beberapa chat…',
  'Mencari harga sewa per tahun…',
  'Menghubungkan pesan dengan ID publik…',
  'Mengecek data yang sudah pernah diproses…',
  'Menyusun ringkasan per buyer…',
  'Menghitung ulang semua pasangan…',
  'Menyimpan hasil pencocokan…',
  'Memperbarui angka di halaman Home…',
  'Menyiapkan daftar Recent matches…',
  'Hampir selesai, merapikan hasil…',
  'Sedikit lagi, tinggal pengecekan akhir…',
];

/** Which sentence to show after `elapsedSeconds` of work. */
export function processingMessage(elapsedSeconds: number) {
  const index = Math.floor(Math.max(0, elapsedSeconds) / ROTATE_EVERY_S) % PROCESSING_MESSAGES.length;
  return PROCESSING_MESSAGES[index];
}
