import io
import unittest
from unittest.mock import patch
from pypdf import PdfWriter
import library
from app import app


def pdf(title, pages=5):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    writer.add_metadata({'/Title': title})
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


class LibraryTests(unittest.TestCase):
    def test_wrong_title_and_volume_rejected(self):
        for title in ('Kayıp Ruhlar Cenneti', 'Kayıp Ruhlar Kıraathanesi'):
            self.assertFalse(library.title_matches('Kayıp Ruhlar Şehri', title))
        self.assertFalse(library.title_matches('Zaman Çarkı 2', 'Zaman Çarkı 1'))
        self.assertFalse(library.title_matches('Zaman Çarkı 2', 'Zaman Çarkı 20'))
        self.assertTrue(library.title_matches('İnsancıklar', 'Insanciklar'))

    def test_real_pdf_and_wrong_pdf(self):
        self.assertEqual(library.inspect_document(pdf('Insanciklar'), 'İnsancıklar')['pages'], 5)
        with self.assertRaises(ValueError):
            library.inspect_document(pdf('Other Book'), 'İnsancıklar')

    def test_preview_and_html_rejected(self):
        for data in (pdf('Insanciklar', 1), b'<html>Download PDF</html>'):
            with self.assertRaises(ValueError):
                library.inspect_document(data, 'Insanciklar')

    def test_index_does_not_pick_first_book(self):
        page = b'<a href="wrong.pdf">Other Book</a><a href="right.pdf">Insanciklar</a>'
        with patch('library.read_url', side_effect=[page, pdf('Insanciklar')]) as read:
            result = library.resolve('https://example.com/books', 'Insanciklar')
        self.assertEqual(result[0]['url'], 'https://example.com/right.pdf')
        self.assertEqual(read.call_count, 2)

    def test_lazy_reader_images_keep_order(self):
        parser = library.ReaderImages('https://example.com/chapter/')
        parser.feed('<img src="logo.png"><div id="reader"><img data-src="2.jpg">'
                    '<img data-original="10.jpg"><img src="2.jpg"></div>')
        self.assertEqual(parser.images, ['https://example.com/chapter/2.jpg', 'https://example.com/chapter/10.jpg'])

    def test_download_checks_bytes_not_extension(self):
        with app.test_client() as client, patch('library.read_url', return_value=b'<html>403</html>'):
            response = client.post('/convert', json={'mode': 'book', 'url': 'https://example.com/book.pdf',
                                                    'title': 'Insanciklar', 'format': 'pdf'})
        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(response.is_json)

    def test_no_result_is_not_fake_success(self):
        with app.test_client() as client, patch('library.search', return_value={'results': [], 'catalog': [], 'warnings': []}):
            response = client.post('/search', json={'mode': 'book', 'book_title': 'Insanciklar'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['results'], [])
        self.assertIn('doğrulanmış', response.json['message'])

    def test_incomplete_manga_not_packaged(self):
        with app.test_client() as client, patch('app.extract_image_urls', return_value=['https://example.com/1.jpg']), \
                patch('library.read_url', side_effect=OSError('unavailable')):
            response = client.post('/convert', json={'mode': 'manga', 'url': 'https://example.com/chapter', 'format': 'cbz'})
        self.assertEqual(response.status_code, 502)


if __name__ == '__main__':
    unittest.main()
