import os
import re
import io
import zipfile
import html
import urllib.request
import urllib.parse
from flask import Flask, request, render_template, send_file, jsonify
from PIL import Image
import library

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['TEMPLATES_AUTO_RELOAD'] = True

def slugify(text):
    text = text.lower()
    text = text.replace('ı', 'i').replace('ö', 'o').replace('ü', 'u').replace('ş', 's').replace('ç', 'c').replace('ğ', 'g')
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '-', text)
    return text.strip('-')

def search_tortuga(manga_name, chapter_num):
    slug = slugify(manga_name)
    direct_url = f"https://tortugaceviri.com/manga/{slug}/{slug}-{chapter_num}/"
    
    # Try direct URL first
    try:
        req = urllib.request.Request(
            direct_url, 
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        )
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                return direct_url
    except Exception:
        pass
        
    # Search the site if direct URL fails
    try:
        search_url = f"https://tortugaceviri.com/?s={urllib.parse.quote(manga_name)}"
        req = urllib.request.Request(
            search_url, 
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        )
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')
            
        # Find all manga links in search results
        manga_links = re.findall(r'href="(https://tortugaceviri\.com/manga/[^"/]+/)"', html)
        manga_links = list(set(manga_links))
        
        # Look for chapters inside the manga page
        for ml in manga_links:
            try:
                ml_req = urllib.request.Request(
                    ml,
                    headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
                )
                with urllib.request.urlopen(ml_req) as ml_resp:
                    ml_html = ml_resp.read().decode('utf-8')
                
                # Look for chapter link containing the chapter number
                # e.g., href="https://tortugaceviri.com/manga/one-piece/one-piece-1183/"
                chapter_pattern = rf'href="(https://tortugaceviri\.com/manga/[^"]+-{chapter_num}/?)"'
                matches = re.findall(chapter_pattern, ml_html)
                if matches:
                    return matches[0]
            except Exception:
                continue
    except Exception:
        pass
        
    return None

def search_mangadenizi(manga_name, chapter_num):
    # Find manga page via Yahoo Search
    query = f"site:mangadenizi.net/manga/ {manga_name}"
    search_url = "https://search.yahoo.com/search?p=" + urllib.parse.quote(query)
    try:
        req = urllib.request.Request(
            search_url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            
        redirects = re.findall(r'href="([^"]*/RU=[^"]+)"', html)
        manga_page = None
        for r in redirects:
            match = re.search(r'/RU=([^/]+)', r)
            if match:
                target = urllib.parse.unquote(match.group(1))
                if 'mangadenizi.net/manga/' in target and not target.endswith('/manga/random') and not target.endswith('/manga'):
                    manga_page = target
                    break
                    
        if not manga_page:
            return None
            
        # Scrape the manga page for the chapter link
        req = urllib.request.Request(
            manga_page,
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            html_page = resp.read().decode('utf-8')
            
        chapter_links = re.findall(r'href="([^"]+)"', html_page)
        chapter_url = None
        for cl in set(chapter_links):
            if f'/{chapter_num}' in cl or f'-{chapter_num}' in cl:
                if cl.startswith('http'):
                    chapter_url = cl
                else:
                    chapter_url = f"https://www.mangadenizi.net{cl}"
                break
                
        return chapter_url
    except Exception as e:
        print(f"MangaDenizi search error: {e}")
        return None


def search_mangadex(manga_name, chapter_num):
    """Search MangaDex open API for Turkish translated chapters."""
    try:
        from curl_cffi import requests as cf_requests
        # Search for the manga
        search_url = f"https://api.mangadex.org/manga?title={urllib.parse.quote(manga_name)}&limit=5&availableTranslatedLanguage[]=tr"
        resp = cf_requests.get(search_url, impersonate='chrome120', timeout=10)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        if not data.get('data'):
            return None
        
        exact = next((item for item in data['data'] if any(
            library.normalized(manga_name) == library.normalized(value)
            for names in [item['attributes']['title']] + item['attributes'].get('altTitles', [])
            for value in names.values())), None)
        if not exact:
            return None
        manga_id = exact['id']
        manga_title = list(exact['attributes']['title'].values())[0]
        
        # Get Turkish chapters
        feed_url = (f"https://api.mangadex.org/manga/{manga_id}/feed"
                    f"?translatedLanguage[]=tr&limit=100&chapter={urllib.parse.quote(str(chapter_num))}&order[chapter]=asc")
        feed_resp = cf_requests.get(feed_url, impersonate='chrome120', timeout=10)
        if feed_resp.status_code != 200:
            return None
        
        feed_data = feed_resp.json()
        chapters = feed_data.get('data', [])
        
        # Find the requested chapter number
        target_ch = str(chapter_num).strip()
        matched = None
        for ch in chapters:
            ch_num = ch['attributes'].get('chapter', '')
            if ch_num and (ch_num == target_ch or ch_num.lstrip('0') == target_ch.lstrip('0')):
                matched = ch
                break
        
        if not matched:
            return None
        
        ch_id = matched['id']
        return f"mangadex:{ch_id}:{manga_title}"
    except Exception as e:
        print(f"MangaDex search error: {e}")
        return None

SEARCH_ENGINE_DOMAINS = (
    'search.yahoo.com', 'r.search.yahoo.com', 'duckduckgo.com', 'www.bing.com',
    'bing.com', 'www.google.com', 'google.com'
)

def normalize_search_result_url(raw_url):
    raw_url = html.unescape((raw_url or '').strip())
    if not raw_url:
        return ''

    if raw_url.startswith('//'):
        raw_url = 'https:' + raw_url

    if raw_url.startswith('/'):
        if raw_url.startswith('/RU='):
            return ''
        if raw_url.startswith('/l/?uddg='):
            parsed = urllib.parse.urlparse('https://duckduckgo.com' + raw_url)
            uddg = urllib.parse.parse_qs(parsed.query).get('uddg', [''])[0]
            return urllib.parse.unquote(uddg)
        return ''

    parsed = urllib.parse.urlparse(raw_url)
    host = parsed.netloc.lower()

    if 'yahoo.com' in host:
        match = re.search(r'/RU=([^/]+)', raw_url)
        if match:
            return urllib.parse.unquote(match.group(1))

    if 'duckduckgo.com' in host:
        uddg = urllib.parse.parse_qs(parsed.query).get('uddg', [''])[0]
        if uddg:
            return urllib.parse.unquote(uddg)

    return raw_url

def extract_search_targets(page_html):
    redirects = re.findall(r'href="([^"]+)"', page_html)
    links = []
    for redirect_url in redirects:
        target = normalize_search_result_url(redirect_url)
        if not target:
            continue

        parsed = urllib.parse.urlparse(target)
        if parsed.scheme not in ('http', 'https'):
            continue
        host = parsed.netloc.lower()
        if not host or any(engine_host in host for engine_host in SEARCH_ENGINE_DOMAINS):
            continue
        if 'yimg' in host:
            continue
        if target.startswith('javascript:') or target.startswith('mailto:'):
            continue
        links.append(target)
    return list(dict.fromkeys(links))

def fetch_search_result_targets(query):
    search_urls = [
        "https://search.yahoo.com/search?p=" + urllib.parse.quote(query),
        "https://www.bing.com/search?q=" + urllib.parse.quote(query)
    ]

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    for search_url in search_urls:
        try:
            req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                page_html = resp.read().decode('utf-8', errors='ignore')
            targets = extract_search_targets(page_html)
            if targets:
                return targets
        except Exception as e:
            print(f"Search fetch failed for {search_url}: {e}")

    return []

def extract_image_urls(url):
    html = library.read_url(url, limit=4 * 1024 * 1024).decode('utf-8', errors='replace')
        
    if 'mangadenizi.net' in url:
        raw_urls = re.findall(r'(https?:\\u002F\\u002Fimg\.mangadenizi\.net\\u002Freader-images\\[^"]+)', html)
        img_urls = [u.replace(r'\u002F', '/').replace(r'\u002f', '/') for u in raw_urls]
        # Filter to keep only this chapter's pages
        chapter_match = re.search(r'/(\d+)/?$', url)
        if chapter_match:
            chap_num = chapter_match.group(1)
            img_urls = [u for u in img_urls if f'/{chap_num}/' in u]
        img_urls = list(dict.fromkeys(img_urls))
        return img_urls

    img_urls = re.findall(r'class="wp-manga-chapter-img"[^>]*src="\s*([^"]+)\s*"', html)
    if not img_urls:
        img_urls = re.findall(r'src="\s*([^"]+)\s*"[^>]*class="wp-manga-chapter-img"', html)
    if not img_urls:
        img_urls = re.findall(r'src="\s*(https://tortugaceviri\.com/wp-content/uploads/WP-manga/data/[^"]+)\s*"', html)
        
    if not img_urls:
        parser = library.ReaderImages(url)
        parser.feed(html)
        img_urls = parser.images
    seen = set()
    img_urls = [urllib.parse.urljoin(url, x.strip()) for x in img_urls if not (x.strip() in seen or seen.add(x.strip()))]
    return img_urls

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/search', methods=['POST'])
def search_endpoint():
    data = request.get_json() or {}
    mode = data.get('mode') # 'manga' or 'book'
    
    if mode == 'manga':
        manga_name = data.get('manga_name')
        chapter_num = data.get('chapter_num')
        if not manga_name or not chapter_num:
            return jsonify({'error': 'Manga adı ve bölüm numarası gereklidir.'}), 400
            
        results = []
        
        # 1. Search Tortuga Çeviri
        tortuga_url = search_tortuga(manga_name, chapter_num)
        if tortuga_url:
            results.append({
                'source': 'Tortuga Çeviri',
                'url': tortuga_url,
                'domain': 'tortugaceviri.com'
            })
            
        # 2. Search MangaDenizi
        denizi_url = search_mangadenizi(manga_name, chapter_num)
        if denizi_url:
            results.append({
                'source': 'MangaDenizi',
                'url': denizi_url,
                'domain': 'mangadenizi.net'
            })

        # 3. Search MangaDex (Turkish translations, open API)
        mangadex_result = search_mangadex(manga_name, chapter_num)
        if mangadex_result:
            parts = mangadex_result.split(':', 2)
            ch_id = parts[1]
            manga_title_dex = parts[2] if len(parts) > 2 else manga_name
            results.append({
                'source': 'MangaDex (TR)',
                'url': f'mangadex:{ch_id}:{manga_title_dex}',
                'domain': 'mangadex.org'
            })
            
        if results:
            return jsonify({'success': True, 'results': results})
        else:
            return jsonify({'error': 'Belirtilen manga veya bölüm bulunamadı.'}), 404
            
    elif mode == 'book':
        book_title = data.get('book_title')
        book_author = data.get('book_author')
        if not book_title:
            return jsonify({'error': 'Kitap adı gereklidir.'}), 400
            
        search_res = library.search(book_title, fetch_search_result_targets)
        results = search_res.get('results', [])
        epub_not_found = search_res.get('epub_not_found', True)
        if results:
            return jsonify({
                'success': True, 
                'results': results,
                'epub_not_found': epub_not_found
            })
        else:
            return jsonify(dict(search_res, success=True,
                message='Bu başlık için doğrulanmış indirilebilir dosya bulunamadı. Katalog kaydı dosya erişimi anlamına gelmez.'))
            
    return jsonify({'error': 'Geçersiz arama modu.'}), 400

@app.route('/suggest-books', methods=['POST'])
def suggest_books_endpoint():
    data = request.get_json() or {}
    query = data.get('query', '')
    if not isinstance(query, str) or len(query.strip()) < 2:
        return jsonify(success=True, results=[])
    rows, warnings = library.catalog(query)
    return jsonify(success=True, results=rows, warnings=warnings)

@app.route('/resolve-book', methods=['POST'])
def resolve_book_endpoint():
    data = request.get_json() or {}
    source_url = data.get('url', '')
    requested_title = data.get('title', '')
    try:
        results = library.resolve(source_url, requested_title)
    except Exception:
        results = []
    if results:
        return jsonify({'success': True, 'results': results})
    return jsonify({'error': 'Secilen kaynak hizli cozumlemede indirilebilir dosyaya donusturulemedi.'}), 404

def convert_pdf_to_epub(pdf_bytes, title="Untitled", author="Unknown"):
    try:
        title, author = html.escape(title), html.escape(author)
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if not any((page.extract_text() or '').strip() for page in reader.pages[:5]):
            return None
        epub_io = io.BytesIO()
        
        with zipfile.ZipFile(epub_io, 'w', zipfile.ZIP_DEFLATED) as epub:
            epub.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            
            container_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>'''
            epub.writestr('META-INF/container.xml', container_xml)
            
            spine_items = []
            manifest_items = []
            
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                text_html = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br/>')
                
                page_html = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
    <title>Sayfa {i+1}</title>
</head>
<body>
    <div style="font-family: sans-serif; line-height: 1.5; padding: 10px;">
        <h3 style="text-align: center; color: #cca43b; font-family: serif;">Sayfa {i+1}</h3>
        <p>{text_html}</p>
    </div>
</body>
</html>'''
                filename = f'OEBPS/page_{i+1}.xhtml'
                epub.writestr(filename, page_html)
                
                manifest_items.append(f'<item id="page_{i+1}" href="page_{i+1}.xhtml" media-type="application/xhtml+xml"/>')
                spine_items.append(f'<itemref idref="page_{i+1}"/>')
                
            manifest_str = "\n        ".join(manifest_items)
            spine_str = "\n        ".join(spine_items)
            
            content_opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookID" version="2.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
        <dc:title>{title}</dc:title>
        <dc:creator>{author}</dc:creator>
        <dc:language>tr</dc:language>
        <dc:identifier id="BookID">urn:uuid:12345678-1234-1234-1234-123456789012</dc:identifier>
    </metadata>
    <manifest>
        <item id="ncx" href="toc.xml" media-type="application/x-dtbncx+xml"/>
        {manifest_str}
    </manifest>
    <spine toc="ncx">
        {spine_str}
    </spine>
</package>'''
            epub.writestr('OEBPS/content.opf', content_opf)
            
            toc_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
    <head>
        <meta name="dtb:uid" content="urn:uuid:12345678-1234-1234-1234-123456789012"/>
        <meta name="dtb:depth" content="1"/>
        <meta name="dtb:totalPageCount" content="0"/>
        <meta name="dtb:maxPageNumber" content="0"/>
    </head>
    <docTitle>
        <text>{title}</text>
    </docTitle>
    <navMap>
        <navPoint id="navpoint-1" playOrder="1">
            <navLabel>
                <text>Baslangic</text>
            </navLabel>
            <content src="page_1.xhtml"/>
        </navPoint>
    </navMap>
</ncx>'''
            epub.writestr('OEBPS/toc.xml', toc_xml)
            
        return epub_io.getvalue()
    except Exception as e:
        print(f"EPUB conversion error: {e}")
        return None

@app.route('/convert', methods=['POST'])
def convert():
    data = request.get_json() or {}
    url = data.get('url')
    output_format = data.get('format', 'cbz').lower()
    mode = data.get('mode', 'manga') # 'manga' or 'book'
    book_title = data.get('title', 'Kitap')
    book_author = data.get('author', 'Bilinmeyen Yazar')
    
    if not url:
        return jsonify({'error': 'Lütfen geçerli bir URL girin.'}), 400

    # Book Download & Package Flow
    if mode == 'book':
        try:
            if output_format not in ('pdf', 'epub'):
                return jsonify(error='Geçersiz kitap biçimi.'), 400
            download_url = url
            # If Google Drive, convert to direct download url
            if 'drive.google.com' in url:
                drive_match = re.search(r'/file/d/([0-9a-zA-Z_-]+)', url)
                if drive_match:
                    drive_id = drive_match.group(1)
                    download_url = f"https://drive.google.com/uc?export=download&id={drive_id}"
            
            file_data = library.read_url(download_url)
            evidence = library.inspect_document(file_data, book_title)
            src_ext = evidence['format']
            if src_ext == 'epub' and output_format == 'pdf':
                return jsonify(error='Bu kaynak EPUB. PDF dönüşümü desteklenmiyor; EPUB seçin.'), 422
                
            # Perform PDF to EPUB conversion on request
            if src_ext == 'pdf' and output_format == 'epub':
                converted_data = convert_pdf_to_epub(file_data, book_title, book_author)
                if converted_data:
                    file_data = converted_data
                    src_ext = 'epub'
                else:
                    return jsonify(error='EPUB dönüşümü tamamlanamadı. Orijinal PDF biçimini seçin.'), 422
                    
            # Name the file
            filename = f"{slugify(book_title) or 'kitap'}.{src_ext}"
                
            memory_file = io.BytesIO(file_data)
            mimetype = 'application/pdf' if src_ext == 'pdf' else 'application/epub+zip'
            
            return send_file(
                memory_file,
                mimetype=mimetype,
                as_attachment=True,
                download_name=filename
            )
        except Exception as e:
            return jsonify({
                'error': f'Dosya indirilirken hata oluştu: {str(e)}. Bu kaynak doğrudan dosya indirmeye izin vermiyor olabilir.'
            }), 500

    # Manga Download & Package Flow

    # Handle MangaDex protocol (mangadex:{chapter_id}:{title})
    if url.startswith('mangadex:'):
        try:
            parts = url.split(':', 2)
            ch_id = parts[1]
            manga_title = parts[2] if len(parts) > 2 else 'Manga'
            
            from curl_cffi import requests as cf_requests
            # Get the at-home server URL and page hashes
            at_home_resp = cf_requests.get(
                f'https://api.mangadex.org/at-home/server/{ch_id}',
                impersonate='chrome120', timeout=10
            )
            at_home_data = at_home_resp.json()
            base_url = at_home_data['baseUrl']
            ch_hash = at_home_data['chapter']['hash']
            pages = at_home_data['chapter']['data']  # High quality
            
            chapter_name = f"{manga_title}_ch{ch_id[:8]}".replace(' ', '_')
            
            FALLBACK_CDN = 'https://uploads.mangadex.org'
            downloaded_images = []
            for idx, page_file in enumerate(pages):
                ext = page_file.split('.')[-1].lower() if '.' in page_file else 'jpg'
                img_data = None
                for cdn_base in [base_url, FALLBACK_CDN]:
                    img_url = f"{cdn_base}/data/{ch_hash}/{page_file}"
                    try:
                        img_resp = cf_requests.get(img_url, impersonate='chrome120', timeout=30)
                        if img_resp.status_code == 200 and len(img_resp.content) > 1000:
                            img_data = img_resp.content
                            break
                    except Exception as e:
                        print(f"MangaDex CDN error ({cdn_base}): {e}")
                if img_data:
                    with Image.open(io.BytesIO(img_data)) as check:
                        check.verify()
                    downloaded_images.append((f"page_{idx:03d}.{ext}", img_data))
                else:
                    return jsonify(error=f'{idx + 1}. sayfa indirilemedi. Eksik bölüm paketlenmedi.'), 502
            
            if not downloaded_images:
                return jsonify({'error': 'MangaDex görselleri indirilemedi.'}), 500
            
            if output_format == 'cbz':
                memory_file = io.BytesIO()
                with zipfile.ZipFile(memory_file, 'w') as zf:
                    for fname, img_data in downloaded_images:
                        zf.writestr(fname, img_data)
                memory_file.seek(0)
                return send_file(memory_file, mimetype='application/zip',
                                 as_attachment=True, download_name=f"{chapter_name}.cbz")
            else:  # pdf
                images = []
                for fname, img_data in downloaded_images:
                    try:
                        img = Image.open(io.BytesIO(img_data))
                        if img.mode != 'RGB':
                            img = img.convert('RGB')
                        images.append(img)
                    except Exception:
                        pass
                if not images:
                    return jsonify({'error': 'PDF oluşturulurken görsel hatası.'}), 500
                memory_file = io.BytesIO()
                images[0].save(memory_file, "PDF", save_all=True, append_images=images[1:])
                memory_file.seek(0)
                return send_file(memory_file, mimetype='application/pdf',
                                 as_attachment=True, download_name=f"{chapter_name}.pdf")
        except Exception as e:
            return jsonify({'error': f'MangaDex indirme hatası: {str(e)}'}), 500

    if not url.startswith('http'):
        return jsonify({'error': 'Lütfen geçerli bir manga linki girin.'}), 400

    try:
        img_urls = extract_image_urls(url)
    except Exception as e:
        return jsonify({'error': f'Sayfa bilgisi çekilemedi: {str(e)}'}), 500
        
    if not img_urls:
        return jsonify({'error': 'Manga sayfaları bulunamadı.'}), 400

    # Extract chapter name for filename
    chapter_match = re.search(r'one-piece-([0-9a-zA-Z-]+)/?$', url)
    if chapter_match:
        chapter_name = f"One_Piece_{chapter_match.group(1)}"
    else:
        # Generic chapter name extraction
        parts = [p for p in url.split('/') if p]
        chapter_name = parts[-1] if parts else "Manga_Chapter"

    downloaded_images = []
    for idx, img_url in enumerate(img_urls):
        try:
            img_req = urllib.request.Request(
                img_url,
                headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
            )
            img_data = library.read_url(img_url, limit=16 * 1024 * 1024, timeout=10)
            with Image.open(io.BytesIO(img_data)) as check:
                check.verify()
                
            ext = 'jpg'
            if '.png' in img_url.lower():
                ext = 'png'
            elif '.webp' in img_url.lower():
                ext = 'webp'
                
            downloaded_images.append((f"page_{idx:03d}.{ext}", img_data))
        except Exception as e:
            return jsonify(error=f'{idx + 1}. sayfa indirilemedi. Eksik bölüm paketlenmedi.'), 502

    if not downloaded_images:
        return jsonify({'error': 'Görseller indirilemedi.'}), 500

    if output_format == 'cbz':
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w') as zf:
            for filename, img_data in downloaded_images:
                zf.writestr(filename, img_data)
        memory_file.seek(0)
        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f"{chapter_name}.cbz"
        )
    elif output_format == 'pdf':
        images = []
        for filename, img_data in downloaded_images:
            try:
                img = Image.open(io.BytesIO(img_data))
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                images.append(img)
            except Exception as e:
                print(f"Error processing image for PDF {filename}: {e}")
                
        if not images:
            return jsonify({'error': 'PDF oluşturulurken görsel hatası oluştu.'}), 500
            
        memory_file = io.BytesIO()
        images[0].save(memory_file, "PDF", save_all=True, append_images=images[1:])
        memory_file.seek(0)
        return send_file(
            memory_file,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"{chapter_name}.pdf"
        )
    else:
        return jsonify({'error': 'Geçersiz format seçildi.'}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5005, debug=True)
