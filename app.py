import os
import re
import io
import zipfile
import urllib.request
import urllib.parse
from flask import Flask, request, render_template, send_file, jsonify
from PIL import Image

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['TEMPLATES_AUTO_RELOAD'] = True

BLOCKED_MANGA_DOMAINS = {
    'sadscans.net': 'Bu kaynak anti-bot korumasi kullandigi icin uygulama icinden otomatik cekilemiyor.'
}

def get_blocked_manga_reason(url):
    host = urllib.parse.urlparse((url or '').strip()).netloc.lower()
    for domain, reason in BLOCKED_MANGA_DOMAINS.items():
        if domain in host:
            return reason
    return None

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
        
        manga_id = data['data'][0]['id']
        manga_title = list(data['data'][0]['attributes']['title'].values())[0]
        
        # Get Turkish chapters
        feed_url = (f"https://api.mangadex.org/manga/{manga_id}/feed"
                    f"?translatedLanguage[]=tr&limit=100&order[chapter]=asc")
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

def verify_link(url):
    # Converts Google Drive links for validation
    download_url = url
    if 'drive.google.com' in url:
        drive_match = re.search(r'/file/d/([0-9a-zA-Z_-]+)', url)
        if drive_match:
            drive_id = drive_match.group(1)
            download_url = f"https://drive.google.com/uc?export=download&id={drive_id}"
            
    try:
        req = urllib.request.Request(
            download_url,
            method='GET', # Some servers block HEAD requests, GET is safer but we can read only first few bytes
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        )
        # Timeout of 10 seconds to keep it safe for Google Drive
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            # Check if status code is successful
            if resp.status == 200:
                # Check Content-Type header
                content_type = resp.headers.get('Content-Type', '').lower()
                if 'html' in content_type:
                    # Reject HTML pages unless it is a known cloud drive
                    if not any(x in download_url for x in ['drive.google.com', 'yadi.sk', 'disk.yandex', 'mega.nz', 'mediafire.com']):
                        return False
                
                # Read a small amount of bytes to ensure we can fetch the body
                resp.read(100)
                return True
    except Exception as e:
        print(f"Validation failed for {url}: {e}")
    return False

def is_probably_direct_file(url):
    lowered = (url or '').lower()
    return (
        lowered.endswith('.pdf') or
        lowered.endswith('.epub') or
        '.pdf?' in lowered or
        '.epub?' in lowered or
        'download' in lowered
    )

PREVIEW_HINTS = (
    'preview', 'sample', 'excerpt', 'demo', 'deneme', 'tanitim', 'tanıtım',
    'onizleme', 'önizleme', 'teaser', 'free-chapter'
)

def looks_like_preview_url(url):
    lowered = urllib.parse.unquote((url or '').lower())
    return any(hint in lowered for hint in PREVIEW_HINTS)

def passes_book_quality_gate(url):
    if looks_like_preview_url(url):
        return False

    lowered = (url or '').lower()
    if '.pdf' not in lowered and '.epub' not in lowered:
        return True

    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            # Check content length header if available
            cl = resp.headers.get('Content-Length')
            if cl:
                size = int(cl)
                if '.pdf' in lowered and size < 250000:
                    return False
                if '.epub' in lowered and size < 100000:
                    return False
                return True
            
            # If no Content-Length header, read up to 250KB to check size
            data = resp.read(250000)
            if '.pdf' in lowered and len(data) < 250000:
                return False
            if '.epub' in lowered and len(data) < 100000:
                return False
            return True
    except Exception as e:
        print(f"Quality gate failed for {url}: {e}")
        return not looks_like_preview_url(url)

EXCLUDED_DOMAINS = [
    'facebook.com', 'twitter.com', 'instagram.com', 'youtube.com', 'pinterest.com', 
    't.me', 'reddit.com', 'scribd.com', 'bdebooks.com', 'oceanofpdf.com', 
    'z-lib', 'libgen', 'pdfdrive', 'epubpub', 'tumblr.com', 'linkedin.com'
]

BOOK_QUERY_STOPWORDS = {
    've', 'ile', 'bir', 'bu', 'su', 'the', 'book', 'kitap', 'turkce', 'turkçe',
    'pdf', 'epub', 'roman', 'seri'
}

def normalize_search_text(text):
    text = (text or '').lower()
    replacements = str.maketrans({
        'ı': 'i', 'İ': 'i', 'ö': 'o', 'ü': 'u', 'ş': 's', 'ç': 'c', 'ğ': 'g'
    })
    text = text.translate(replacements)
    text = urllib.parse.unquote(text)
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

def tokenize_search_text(text):
    return re.findall(r'[a-z0-9]+', normalize_search_text(text))

def score_book_candidate(query, candidate_text):
    query_tokens = tokenize_search_text(query)
    candidate_tokens = tokenize_search_text(candidate_text)
    candidate_token_set = set(candidate_tokens)

    if not query_tokens:
        return 0

    query_numbers = [token for token in query_tokens if token.isdigit()]
    query_words = [
        token for token in query_tokens
        if not token.isdigit() and len(token) > 1 and token not in BOOK_QUERY_STOPWORDS
    ]

    if query_numbers and not all(number in candidate_token_set for number in query_numbers):
        return -1

    matched_words = sum(1 for token in query_words if token in candidate_token_set)
    if len(query_words) <= 3:
        minimum_word_matches = len(query_words)
    else:
        minimum_word_matches = len(query_words) - 1
        
    if query_words and matched_words < minimum_word_matches:
        return -1

    normalized_query = normalize_search_text(query)
    normalized_candidate = normalize_search_text(candidate_text)

    score = 0
    if normalized_query and normalized_query in normalized_candidate:
        score += 60

    score += matched_words * 14
    score += len(query_numbers) * 35

    if normalized_candidate.startswith(normalized_query):
        score += 12

    return score

def deep_scrape_page(page_url, query=''):
    # Avoid scraping direct files or known cloud drives
    if any(x in page_url for x in ['drive.google.com', 'yadi.sk', 'disk.yandex', 'mega.nz', 'mediafire.com']) or page_url.endswith('.pdf') or page_url.endswith('.epub'):
        return {'url': page_url, 'context': page_url, 'title': ''}
        
    try:
        req = urllib.request.Request(
            page_url,
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        )
        with urllib.request.urlopen(req, timeout=6.0) as resp:
            html = resp.read().decode('utf-8', errors='ignore')

        page_title = ''
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        if title_match:
            page_title = re.sub(r'\s+', ' ', title_match.group(1)).strip()
            
        query_tokens = tokenize_search_text(query) if query else []
        candidates = []
        
        found_links = re.findall(r'href="([^"]+)"', html)
        for link in found_links:
            link = link.strip()
            is_direct = (
                any(x in link for x in ['drive.google.com/file/d/', 'yadi.sk', 'disk.yandex', 'mediafire.com', 'mega.nz'])
                or link.endswith('.pdf')
                or link.endswith('.epub')
            )
            if is_direct:
                if link.startswith('/'):
                    parsed_base = urllib.parse.urlparse(page_url)
                    link = f"{parsed_base.scheme}://{parsed_base.netloc}{link}"
                
                link_score = 0
                if query_tokens:
                    link_norm = normalize_search_text(link)
                    link_score = sum(1 for token in query_tokens if token in link_norm)
                
                candidates.append((link, link_score))
                
        if candidates:
            # Sort candidates by query token matches descending
            candidates.sort(key=lambda x: -x[1])
            best_link, best_score = candidates[0]
            
            # If it's a directory listing (many files) and nothing matches the query, don't return a random file!
            if query_tokens and len(candidates) > 3 and best_score == 0:
                return {'url': page_url, 'context': page_url, 'title': page_title}
                
            return {'url': best_link, 'context': f'{page_url} {page_title} {best_link}', 'title': page_title}
            
    except Exception as e:
        print(f"Deep scraping failed for {page_url}: {e}")
    return {'url': page_url, 'context': page_url, 'title': ''}

def extract_search_targets(html):
    redirects = re.findall(r'href="([^"]*/RU=[^"]+)"', html)
    links = []
    for redirect_url in redirects:
        match = re.search(r'/RU=([^/]+)', redirect_url)
        if not match:
            continue
        target = urllib.parse.unquote(match.group(1))
        if 'yahoo' in target or 'yimg' in target:
            continue
        links.append(target)
    return list(dict.fromkeys(links))

def titleize_slug_text(text):
    words = [word for word in re.split(r'\s+', text.strip()) if word]
    lower_words = {'ve', 'ile', 'de', 'da', 'the', 'of', 'and'}
    result = []
    for index, word in enumerate(words):
        if word.isdigit():
            result.append(word)
        elif index > 0 and word in lower_words:
            result.append(word)
        else:
            result.append(word[:1].upper() + word[1:])
    return ' '.join(result)

def clean_suggestion_title(page_title, fallback_url):
    title = re.sub(r'\s+', ' ', (page_title or '')).strip()
    if title:
        title = re.split(r'\s[\-|•|]\s', title)[0].strip()
        title = re.sub(r'\b(pdf|epub)\b', '', title, flags=re.IGNORECASE).strip(' -|:')
        if len(title) >= 3:
            return title

    parsed = urllib.parse.urlparse(fallback_url)
    slug = parsed.path.rstrip('/').split('/')[-1]
    slug = urllib.parse.unquote(slug)
    slug = re.sub(r'\.(html|htm|php|aspx?)$', '', slug, flags=re.IGNORECASE)
    slug = slug.replace('-', ' ').replace('_', ' ').strip()
    return titleize_slug_text(slug) if slug else parsed.netloc

def resolve_book_candidate(source_url, requested_title=''):
    source_url = (source_url or '').strip()
    if not source_url:
        return []

    domain = urllib.parse.urlparse(source_url).netloc
    if any(ex in domain.lower() for ex in EXCLUDED_DOMAINS):
        return []

    scrape_result = deep_scrape_page(source_url, requested_title)
    final_link = scrape_result['url']
    candidate_context = f"{source_url} {scrape_result.get('context', '')} {final_link}"
    match_score = score_book_candidate(requested_title or source_url, candidate_context)
    if requested_title and match_score < 0:
        return []

    is_epub = '.epub' in final_link.lower() or 'epub' in final_link.lower()
    is_pdf = '.pdf' in final_link.lower() or 'pdf' in final_link.lower() or 'drive.google.com' in final_link
    if not (is_epub or is_pdf):
        return []

    if not (verify_link(final_link) or is_probably_direct_file(final_link)):
        return []

    if not passes_book_quality_gate(final_link):
        return []

    return [{
        'title': clean_suggestion_title(scrape_result.get('title', ''), final_link),
        'url': final_link,
        'domain': urllib.parse.urlparse(final_link).netloc,
        'format': 'epub' if is_epub else 'pdf'
    }]

def fetch_book_suggestions(query, limit=5):
    query = (query or '').strip()
    if len(query) < 2:
        return []

    url = "https://suggestqueries.google.com/complete/search?client=firefox&q=" + urllib.parse.quote(query)
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            import json
            data = json.loads(resp.read().decode('utf-8'))
            
        suggestions = []
        if isinstance(data, list) and len(data) > 1:
            raw_suggestions = data[1]
            for s in raw_suggestions:
                # Clean suggestion: remove common search suffixes
                s_clean = re.sub(r'\b(pdf|epub|indir|oku|kitap|pdf oku|epub indir)\b', '', s, flags=re.IGNORECASE).strip()
                s_clean = re.sub(r'\s+', ' ', s_clean)
                s_clean = titleize_slug_text(s_clean)
                
                if s_clean and s_clean not in [x['title'] for x in suggestions]:
                    suggestions.append({
                        'title': s_clean,
                        'domain': 'Google Öneri',
                        'url': ''
                    })
                    if len(suggestions) >= limit:
                        break
        return suggestions
    except Exception as e:
        print(f"Book suggestion error for query '{query}': {e}")
        return []

def build_book_queries(title, author=None):
    queries = []
    if author:
        queries.append(f'{title} {author} filetype:epub')
        queries.append(f'{title} {author} filetype:pdf')
        queries.append(f'{title} {author} türkçe epub pdf')
    else:
        queries.append(f'{title} filetype:epub')
        queries.append(f'{title} filetype:pdf')
        queries.append(f'{title} türkçe epub pdf')
    return list(dict.fromkeys(queries))

def search_books_for_queries(queries, requested_title):
    for query in queries:
        url = "https://search.yahoo.com/search?p=" + urllib.parse.quote(query)
        print(f"DEBUG SEARCH - Querying Yahoo: {query}")
        
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                html = resp.read().decode('utf-8', errors='ignore')
                
            links = extract_search_targets(html)
            candidates = []
            for link in set(links):
                domain = urllib.parse.urlparse(link).netloc
                if any(ex in domain.lower() for ex in EXCLUDED_DOMAINS):
                    continue
                    
                scrape_result = deep_scrape_page(link, requested_title)
                final_link = scrape_result['url']
                candidate_context = f"{link} {scrape_result.get('context', '')} {final_link}"
                match_score = score_book_candidate(requested_title, candidate_context)
                if match_score < 0:
                    continue
                
                is_epub = '.epub' in final_link.lower() or 'epub' in final_link.lower()
                is_pdf = '.pdf' in final_link.lower() or 'pdf' in final_link.lower() or 'drive.google.com' in final_link
                
                if is_epub or is_pdf:
                    domain_name = urllib.parse.urlparse(final_link).netloc
                    result_item = {
                        'title': clean_suggestion_title(scrape_result.get('title', ''), final_link),
                        'url': final_link,
                        'domain': domain_name,
                        'format': 'epub' if is_epub else 'pdf',
                        'score': match_score
                    }
                    
                    if result_item['url'] not in [r['url'] for r in candidates]:
                        if (verify_link(result_item['url']) or is_probably_direct_file(result_item['url'])) and passes_book_quality_gate(result_item['url']):
                            candidates.append(result_item)
 
            if candidates:
                candidates.sort(key=lambda item: (item['format'] != 'epub', -item['score']))
                epub_found = any(item['format'] == 'epub' for item in candidates)
                final_results = [
                    {
                        'title': item['title'],
                        'url': item['url'],
                        'domain': item['domain'],
                        'format': item['format']
                    }
                    for item in candidates[:5]
                ]
                print(f"DEBUG SEARCH - Found {len(final_results)} ranked matches for query: {query}")
                return {
                    'results': final_results[:5],
                    'epub_not_found': not epub_found
                }
        except Exception as e:
            print(f"Book search error for query '{query}': {e}")
    return None

def search_books(title, author=None):
    direct_result = search_books_for_queries(build_book_queries(title, author), title)
    if direct_result:
        return direct_result

    suggestion_results = fetch_book_suggestions(title, limit=3)
    for suggestion in suggestion_results:
        suggestion_title = suggestion['title']
        if normalize_search_text(suggestion_title) == normalize_search_text(title):
            continue
        suggestion_result = search_books_for_queries(build_book_queries(suggestion_title, author), title)
        if suggestion_result:
            return suggestion_result

    return {'results': [], 'epub_not_found': True}

def extract_image_urls(url):
    blocked_reason = get_blocked_manga_reason(url)
    if blocked_reason:
        raise ValueError(blocked_reason)

    req = urllib.request.Request(
        url, 
        headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    )
    with urllib.request.urlopen(req) as response:
        html = response.read().decode('utf-8')
        
    if 'mangadenizi.net' in url:
        raw_urls = re.findall(r'(https?:\\u002F\\u002Fimg\.mangadenizi\.net\\u002Freader-images\\[^"]+)', html)
        img_urls = [u.replace(r'\u002F', '/').replace(r'\u002f', '/') for u in raw_urls]
        # Filter to keep only this chapter's pages
        chapter_match = re.search(r'/(\d+)/?$', url)
        if chapter_match:
            chap_num = chapter_match.group(1)
            img_urls = [u for u in img_urls if f'/{chap_num}/' in u]
        img_urls = list(set(img_urls))
        img_urls.sort()
        return img_urls

    img_urls = re.findall(r'class="wp-manga-chapter-img"[^>]*src="\s*([^"]+)\s*"', html)
    if not img_urls:
        img_urls = re.findall(r'src="\s*([^"]+)\s*"[^>]*class="wp-manga-chapter-img"', html)
    if not img_urls:
        img_urls = re.findall(r'src="\s*(https://tortugaceviri\.com/wp-content/uploads/WP-manga/data/[^"]+)\s*"', html)
        
    seen = set()
    img_urls = [x.strip() for x in img_urls if not (x.strip() in seen or seen.add(x.strip()))]
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
            
        search_res = search_books(book_title, book_author)
        results = search_res.get('results', [])
        epub_not_found = search_res.get('epub_not_found', True)
        if results:
            return jsonify({
                'success': True, 
                'results': results,
                'epub_not_found': epub_not_found
            })
        else:
            return jsonify({'error': 'Arama kriterlerine uygun e-kitap bulunamadı.'}), 404
            
    return jsonify({'error': 'Geçersiz arama modu.'}), 400

@app.route('/suggest-books', methods=['POST'])
def suggest_books_endpoint():
    data = request.get_json() or {}
    query = data.get('query', '')
    return jsonify({
        'success': True,
        'results': fetch_book_suggestions(query)
    })

@app.route('/resolve-book', methods=['POST'])
def resolve_book_endpoint():
    data = request.get_json() or {}
    source_url = data.get('url', '')
    requested_title = data.get('title', '')
    results = resolve_book_candidate(source_url, requested_title)
    if results:
        return jsonify({'success': True, 'results': results})
    return jsonify({'error': 'Secilen kaynak hizli cozumlemede indirilebilir dosyaya donusturulemedi.'}), 404

def convert_pdf_to_epub(pdf_bytes, title="Untitled", author="Unknown"):
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
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
            download_url = url
            # If Google Drive, convert to direct download url
            if 'drive.google.com' in url:
                drive_match = re.search(r'/file/d/([0-9a-zA-Z_-]+)', url)
                if drive_match:
                    drive_id = drive_match.group(1)
                    download_url = f"https://drive.google.com/uc?export=download&id={drive_id}"
            
            req = urllib.request.Request(
                download_url, 
                headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            )
            with urllib.request.urlopen(req) as response:
                file_data = response.read()
            
            # Figure out format of source file
            src_ext = 'pdf'
            if '.epub' in url.lower():
                src_ext = 'epub'
                
            # Perform PDF to EPUB conversion on request
            if src_ext == 'pdf' and output_format == 'epub':
                converted_data = convert_pdf_to_epub(file_data, book_title, book_author)
                if converted_data:
                    file_data = converted_data
                    src_ext = 'epub'
                    
            # Name the file
            filename = url.split('/')[-1]
            if not filename or '.' not in filename or output_format == 'epub':
                filename = f"{book_title.replace(' ', '_')}.{src_ext}"
                
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
                    downloaded_images.append((f"page_{idx:03d}.{ext}", img_data))
                else:
                    print(f"Skipping page {idx}: all CDNs failed")
            
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

    blocked_reason = get_blocked_manga_reason(url)
    if blocked_reason:
        return jsonify({
            'error': f'{blocked_reason} Telif ve site korumasini asan bir indirme akisi ekleyemem.'
        }), 400

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
            with urllib.request.urlopen(img_req) as img_resp:
                img_data = img_resp.read()
                
            ext = 'jpg'
            if '.png' in img_url.lower():
                ext = 'png'
            elif '.webp' in img_url.lower():
                ext = 'webp'
                
            downloaded_images.append((f"page_{idx:03d}.{ext}", img_data))
        except Exception as e:
            print(f"Error downloading {img_url}: {e}")

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
