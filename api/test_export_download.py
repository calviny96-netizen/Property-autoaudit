"""Download packaging regressions using real PDF parts, without a database."""
import json
import tempfile
import unittest
import uuid
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from fastapi import HTTPException
from pypdf import PdfReader, PdfWriter
from export_all import download


class ExportDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.token = uuid.uuid4()
        self.patch = patch('export_all.folder', return_value=self.folder)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        for index, lengths in enumerate(([2, 1], [2])):
            for offset, count in enumerate(lengths):
                writer = PdfWriter()
                for _ in range(count):
                    writer.add_blank_page(width=595, height=842)
                writer.write(self.folder / f'{index:05d}-{offset:08d}.pdf')
            metadata = dict(key=['~ Ivan Prayogo', '6281333120202'][index],
                            title='unused', group_by=['sender', 'phone'][index],
                            contact_name=['Ivan', 'Yovita'][index], phone=['6282226811158', '6281333120202'][index],
                            sources=[60, 37][index], direction='property', first_page=[1, 4][index])
            (self.folder / f'{index:05d}.json').write_text(json.dumps(metadata))

    def test_grouped_zip_has_separate_named_pdfs_each_numbered_from_page_one(self):
        response = download(self.token, output='grouped')
        self.assertEqual(response.media_type, 'application/zip')
        with ZipFile(BytesIO(response.body)) as archive:
            # Every person's file starts at page 1, whatever first_page an older client sent.
            self.assertEqual(archive.namelist(), [
                '01_Ivan-Prayogo_6282226811158_60listing_hal1-3.pdf',
                '02_Yovita_6281333120202_37listing_hal1-2.pdf',
            ])
            files = [PdfReader(BytesIO(archive.read(name))) for name in archive.namelist()]
            self.assertEqual([len(pdf.pages) for pdf in files], [3, 2])
            self.assertEqual([[page.extract_text().strip() for page in pdf.pages] for pdf in files], [
                [f'Property Matchmaker | Hal. {n} dari 3' for n in (1, 2, 3)],
                [f'Property Matchmaker | Hal. {n} dari 2' for n in (1, 2)]])

    def test_single_pdf_still_merges_every_part(self):
        response = download(self.token)
        self.assertEqual(response.media_type, 'application/pdf')
        pages = PdfReader(BytesIO(response.body)).pages
        self.assertEqual(len(pages), 5)
        # Page numbers restart with every person's section group.
        self.assertEqual([page.extract_text().strip() for page in pages],
                         [f'Property Matchmaker | Hal. {n} dari {m}' for n, m in ((1, 3), (2, 3), (3, 3), (1, 2), (2, 2))])

    def test_missing_group_metadata_fails_instead_of_misnaming_files(self):
        (self.folder / '00000.json').unlink()
        with self.assertRaises(HTTPException) as error:
            download(self.token, output='grouped')
        self.assertEqual(error.exception.status_code, 409)

    def test_buyer_filename_and_missing_contact_fallback(self):
        path = self.folder / '00000.json'
        metadata = json.loads(path.read_text())
        metadata.update(group_by='phone', contact_name='', phone='', direction='buyer')
        path.write_text(json.dumps(metadata))
        with ZipFile(BytesIO(download(self.token, output='grouped').body)) as archive:
            self.assertEqual(archive.namelist()[0], '01_Tanpa-nama_Tanpa-nomor_60buyer_hal1-3.pdf')
