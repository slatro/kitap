# Book Collector

Flask application for book discovery and manga PDF/CBZ packaging.

## Run

```sh
python3 -m pip install -r requirements.txt
python3 -m flask --app app run --port 5006
python3 -B -m unittest -v test_library
```

## Search

Live suggestions use Google Books and Open Library catalog metadata, with a
one-minute bounded cache. Suggestions are catalog entries, not download claims.
An optional `GOOGLE_BOOKS_API_KEY` environment variable supplies your Google Books
quota. Never commit an API key. Public Internet Archive files and web search
results are checked independently before being offered for download.

PDF validation requires parseable content, more than three pages, and matching
metadata or text on the first three pages. EPUB validation checks the container,
title and reading order. Downloads repeat verification. Scanned PDFs without
extractable title evidence may be rejected. These checks do not prove that every
page of a published edition is present; no universal completeness guarantee is
made. A network failure is never treated as a verified file.

## Manga

The direct URL reader supports exposed chapter image elements, including lazy
image attributes and relative URLs. MangaDex uses its chapter API. Failed pages
stop packaging instead of silently producing an incomplete chapter.

The direct-link tab also packages local JPG/PNG/WebP images into CBZ entirely in
the browser, ordered naturally by filename. No upload is performed. Up to 500
images / 200 MB can be packaged. Login-only, DRM-protected or server-blocked
content and JavaScript-only readers without exposed images are not universally
supported.

## Deployment

`vercel.json` routes to the Flask entrypoint. GitHub pushes trigger deployment
only if the repository is connected in Vercel. Online documents are capped at
32 MB before parsing. Serverless runtime and response-size limits can impose
smaller limits; large manga jobs should run locally or on a persistent worker
with object storage. Browser-only CBZ creation does not use serverless uploads.
