"""Catalog discovery and fail-closed document verification."""
import io
import ipaddress
import json
import os
import re
import socket
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, wait
from functools import lru_cache
from html.parser import HTMLParser

POOL = ThreadPoolExecutor(max_workers=8)
MAX_FILE = 32 * 1024 * 1024


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username:
        raise ValueError('Geçersiz kaynak bağlantısı.')
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError('Bu ağ adresi kaynak olarak kullanılamaz.')


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def normalized(value):
    value = urllib.parse.unquote(value or '').lower().replace('ı', 'i')
    value = ''.join(c for c in unicodedata.normalize('NFKD', value)
                    if not unicodedata.combining(c))
    return ' '.join(re.findall(r'\w+', value, flags=re.UNICODE))


def title_matches(query, title, prefix=False):
    query, title = normalized(query), normalized(title)
    if not query or not title:
        return False
    if prefix:
        return title.startswith(query)
    # A title is a phrase, not a bag of words from unrelated links.
    return query == title or bool(re.search(r'(?<!\w)' + re.escape(query) + r'(?!\w)', title))


def read_url(url, limit=MAX_FILE, timeout=6):
    validate_url(url)
    req = urllib.request.Request(url, headers={'User-Agent': 'BookCollector/1.0'})
    with urllib.request.build_opener(PublicRedirect()).open(req, timeout=timeout) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Dosya çevrimiçi işlem sınırını aşıyor (32 MB).')
    return data


def json_url(url):
    return json.loads(read_url(url, 2 * 1024 * 1024, 6))


def google_catalog(query):
    params = {'q': 'intitle:' + query, 'maxResults': 12}
    if os.environ.get('GOOGLE_BOOKS_API_KEY'):
        params['key'] = os.environ['GOOGLE_BOOKS_API_KEY']
    data = json_url('https://www.googleapis.com/books/v1/volumes?' +
                    urllib.parse.urlencode(params))
    rows = []
    for item in data.get('items', []):
        info, access = item.get('volumeInfo', {}), item.get('accessInfo', {})
        rows.append({'title': info.get('title', ''), 'author': ', '.join(info.get('authors', [])),
                     'url': info.get('infoLink', ''), 'domain': 'Google Books',
                     'downloads': [access.get(fmt, {}).get('downloadLink') for fmt in ('epub', 'pdf')
                                   if access.get(fmt, {}).get('downloadLink')]})
    return rows


def open_catalog(query):
    data = json_url('https://openlibrary.org/search.json?' + urllib.parse.urlencode({
        'title': query, 'limit': 12, 'fields': 'key,title,author_name'}))
    return [{'title': row.get('title', ''), 'author': ', '.join(row.get('author_name', [])),
             'url': 'https://openlibrary.org' + row['key'], 'domain': 'Open Library',
             'downloads': []} for row in data.get('docs', [])]


@lru_cache(maxsize=128)
def catalog_cached(query, bucket):
    futures = [POOL.submit(provider, query) for provider in (google_catalog, open_catalog)]
    done, pending = wait(futures, timeout=7)
    rows, failures = [], []
    for future in done:
        try:
            rows.extend(future.result())
        except Exception:
            failures.append('Katalog kaynağına ulaşılamadı.')
    for future in pending:
        future.cancel()
        failures.append('Katalog yanıt süresi doldu.')
    unique = {}
    for row in rows:
        if title_matches(query, row['title'], prefix=True):
            unique.setdefault((normalized(row['title']), normalized(row['author'])), row)
    return list(unique.values())[:8], failures


def catalog(query):
    return catalog_cached(query.strip()[:200], int(time.time() // 60))


def inspect_document(data, requested_title):
    if data.startswith(b'%PDF-'):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or len(reader.pages) <= 3:
            raise ValueError('Dosya şifreli veya kısa bir önizleme; tam kitap doğrulanamadı.')
        title = str((reader.metadata or {}).get('/Title', '')).strip()
        # Cover/title pages provide evidence independent of the search result URL.
        front = '\n'.join(page.extract_text() or '' for page in reader.pages[:3])
        if not title_matches(requested_title, title) and not title_matches(requested_title, front):
            raise ValueError('PDF içindeki başlık aranan kitapla doğrulanamadı.')
        if re.search(r'\b(preview|sample|deneme sürümü|önizleme)\b', front, re.I):
            raise ValueError('Bu dosya bir önizleme içeriyor.')
        return {'format': 'pdf', 'title': title if title_matches(requested_title, title) else requested_title,
                'pages': len(reader.pages), 'verified': True}
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError('EPUB açılmış boyutu işlem sınırını aşıyor.')
            if archive.read('mimetype').strip() != b'application/epub+zip':
                raise ValueError('Geçerli EPUB değil.')
            container = ET.fromstring(archive.read('META-INF/container.xml'))
            path = container.find('.//{*}rootfile').attrib['full-path']
            package = ET.fromstring(archive.read(path))
            title = package.findtext('.//{*}title', '')
            if not title_matches(requested_title, title):
                raise ValueError('EPUB başlığı aranan kitapla uyuşmuyor.')
            if not package.findall('.//{*}spine/{*}itemref'):
                raise ValueError('EPUB içinde okunabilir bölüm bulunamadı.')
            return {'format': 'epub', 'title': title, 'verified': True}
    raise ValueError('Kaynak gerçek PDF veya EPUB döndürmedi.')


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.current = [dict(attrs).get('href', ''), '']

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            self.links.append(self.current)
            self.current = None


class ReaderImages(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base = base
        self.images = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        marker = attrs.get('class', '') + ' ' + attrs.get('id', '')
        inside = any(self.stack) or bool(re.search(r'reading-content|reader|chapter|manga-page', marker, re.I))
        if tag not in ('img', 'br', 'hr', 'input', 'meta', 'link', 'source', 'area', 'wbr', 'embed', 'param', 'base'):
            self.stack.append(inside)
        if tag == 'img' and inside:
            source = attrs.get('data-src') or attrs.get('data-lazy-src') or attrs.get('data-original') or attrs.get('src', '')
            source = urllib.parse.urljoin(self.base, source.strip())
            if source.startswith(('http://', 'https://')) and source not in self.images:
                self.images.append(source)

    def handle_endtag(self, tag):
        if self.stack and tag not in ('img', 'br', 'hr', 'input', 'meta', 'link', 'source'):
            self.stack.pop()


def resolve(url, title):
    data = read_url(url)
    if data.startswith(b'%PDF-') or data.startswith(b'PK'):
        return [dict(inspect_document(data, title), url=url, domain=urllib.parse.urlsplit(url).netloc)]
    parser = Links()
    parser.feed(data.decode('utf-8', errors='replace'))
    results = []
    for href, label in parser.links:
        link = urllib.parse.urljoin(url, href)
        filename = urllib.parse.unquote(urllib.parse.urlsplit(link).path).split('/')[-1]
        if not filename.lower().endswith(('.pdf', '.epub')):
            continue
        if not title_matches(title, label) and not title_matches(title, filename.replace('-', ' ').replace('_', ' ')):
            continue
        try:
            results.append(dict(inspect_document(read_url(link), title), url=link,
                                domain=urllib.parse.urlsplit(link).netloc))
        except Exception:
            continue
        if len(results) >= 2:
            break
    return results


def search(title, discover):
    rows, warnings = catalog(title)
    links = [link for row in rows if title_matches(title, row['title']) for link in row['downloads']]
    sources = [POOL.submit(discover, '"' + title.replace('"', '') + '" (filetype:pdf OR filetype:epub)'),
               POOL.submit(archive_links, title)]
    done_sources, pending_sources = wait(sources, timeout=10)
    for future in done_sources:
        try:
            links.extend(future.result()[:8])
        except Exception:
            warnings.append('Dosya arama kaynağına ulaşılamadı.')
    for future in pending_sources:
        future.cancel()
        warnings.append('Bir arama kaynağının yanıt süresi doldu.')
    if not links:
        warnings.append('Arama sağlayıcılarından doğrulanabilecek dosya bağlantısı alınamadı.')
    futures = [POOL.submit(resolve, link, title) for link in dict.fromkeys(links)]
    done, pending = wait(futures, timeout=9)
    results = {}
    for future in done:
        try:
            for row in future.result():
                results[row['url']] = row
        except Exception:
            warnings.append('Bir kaynağın dosyası doğrulanamadı.')
    for future in pending:
        future.cancel()
    if pending:
        warnings.append('Bazı kaynakların yanıt süresi doldu.')
    return {'results': list(results.values())[:5], 'catalog': rows, 'warnings': list(set(warnings)),
            'epub_not_found': not any(row['format'] == 'epub' for row in results.values())}


def archive_links(title):
    query = 'title:("' + title.replace('"', '') + '") AND mediatype:texts'
    data = json_url('https://archive.org/advancedsearch.php?' + urllib.parse.urlencode({
        'q': query, 'output': 'json', 'rows': 3, 'fl[]': 'identifier,title'}))
    links = []
    for row in data.get('response', {}).get('docs', []):
        if not title_matches(title, row.get('title', '')):
            continue
        identifier = urllib.parse.quote(row['identifier'], safe='')
        metadata = json_url('https://archive.org/metadata/' + identifier)
        if metadata.get('is_dark') or metadata.get('metadata', {}).get('access-restricted-item') == 'true':
            continue
        for file in metadata.get('files', []):
            name = file.get('name', '')
            if not file.get('private') and name.lower().endswith(('.pdf', '.epub')):
                links.append('https://archive.org/download/' + identifier + '/' + urllib.parse.quote(name))
    return links
