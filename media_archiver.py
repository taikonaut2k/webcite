"""
media_archiver.py — Full-page media capture for WebCite.

Captures a web page in its ORIGINAL FORM:
1. Renders the page with Playwright/Scrapling (JS executed, lazy images loaded)
2. Downloads all images to local assets/
3. Detects videos (mp4 direct or HLS m3u8) and makes them playable:
   - direct mp4 → downloaded locally
   - HLS m3u8 → served via /proxy with segment rewriting
4. Rewrites the DOM so every media URL points at the WebCite server
   (works even where the origin is blocked — e.g. CNN from China)
5. Saves page_full.html (self-consistent, media-rich original page)
"""

import json, os, re, time, urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote

MEDIA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "*/*",
}

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".svg")
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".m4v")


def _safe_name(url, idx, kind):
    """Build a local asset filename from a URL."""
    path = urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext not in IMAGE_EXTS + VIDEO_EXTS:
        ext = ".jpg" if kind == "img" else ".mp4"
    return f"{kind}_{idx:03d}{ext}"


def _download(url, dest, timeout=25, max_bytes=None):
    """Download a media file. Returns True on success."""
    try:
        req = urllib.request.Request(url, headers=MEDIA_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if max_bytes and len(data) > max_bytes:
                return False
            if len(data) < 500:  # likely an error page / placeholder
                return False
            Path(dest).write_bytes(data)
            return True
    except Exception:
        return False


def _extract_media_urls(html, base_url):
    """Extract every media URL from rendered HTML + embedded JSON."""
    urls = set()

    # <img src / srcset / data-src / data-original>
    for m in re.finditer(r'<img[^>]*?src=["\']([^"\']+)["\']', html, re.I):
        urls.add(m.group(1))
    for m in re.finditer(r'<img[^>]*?data-src=["\']([^"\']+)["\']', html, re.I):
        urls.add(m.group(1))
    for m in re.finditer(r'<img[^>]*?data-original=["\']([^"\']+)["\']', html, re.I):
        urls.add(m.group(1))
    for m in re.finditer(r'srcset=["\']([^"\']+)["\']', html, re.I):
        for part in m.group(1).split(","):
            u = part.strip().split(" ")[0]
            if u:
                urls.add(u)

    # CSS background-image: url(...)
    for m in re.finditer(r'background-image\s*:\s*url\(["\']?([^"\'\s)]+)["\']?\)', html, re.I):
        urls.add(m.group(1))

    # <video src / poster> and <source src>
    for m in re.finditer(r'<video[^>]*?src=["\']([^"\']+)["\']', html, re.I):
        urls.add(m.group(1))
    for m in re.finditer(r'<video[^>]*?poster=["\']([^"\']+)["\']', html, re.I):
        urls.add(m.group(1))
    for m in re.finditer(r'<source[^>]*?src=["\']([^"\']+)["\']', html, re.I):
        urls.add(m.group(1))

    # og:image / twitter:image
    for m in re.finditer(r'''<meta[^>]+(?:property|name)=["'](?:og:image|twitter:image)[^"']*["'][^>]*content=["']([^"']+)["']''', html, re.I):
        urls.add(m.group(1))

    # JSON-LD + embedded JSON: image contentUrl / video contentUrl
    for m in re.finditer(r'"contentUrl"\s*:\s*"([^"]+)"', html):
        urls.add(m.group(1))
    for m in re.finditer(r'"url"\s*:\s*"(https://media\.cnn\.com[^"]+\.(?:jpg|jpeg|png|webp|mp4))"', html, re.I):
        urls.add(m.group(1))
    for m in re.finditer(r'"https://media\.cnn\.com[^"]+\.(?:JPG|jpg|jpeg|png|webp|mp4)"', html):
        urls.add(m.group(0).strip('"'))

    # HLS playlists
    for m in re.finditer(r'https?://[^"\'\s<>]+\.m3u8[^"\'\s<>]*', html):
        urls.add(m.group(0))

    # Absolute-ize
    abs_urls = set()
    for u in urls:
        u = u.strip()
        if u.startswith("data:") or u.startswith("javascript:") or u.startswith("about:"):
            continue
        if u.startswith("//"):
            u = "https:" + u
        try:
            abs_urls.add(urljoin(base_url, u))
        except Exception:
            continue
    return abs_urls


def _rewrite_html(html, mapping):
    """Replace original URLs with local/proxy paths in HTML."""
    for orig, repl in mapping.items():
        # exact attribute matches
        html = html.replace(f'"{orig}"', f'"{repl}"')
        html = html.replace(f"'{orig}'", f"'{repl}'")
        # srcset entries (space-separated)
        html = html.replace(f" {orig} ", f" {repl} ")
    return html


def capture_full_page(url, archive_dir, timeout=50, max_images=40, max_video_mb=150):
    """
    Full-page media capture. Returns result dict.
    - archive_dir: Path where page_full.html + assets/ will be written
    """
    archive_dir = Path(archive_dir)
    assets_dir = archive_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "success": False,
        "method": "full_page",
        "html": None,
        "images_downloaded": 0,
        "videos_found": 0,
        "videos_downloaded": 0,
        "hls_streams": 0,
        "title": "",
        "render_method": "",
        "error": None,
    }

    # ── 1. Render the page (browser first, HTTP/TLS fallback) ──
    html = None
    title = ""
    try:
        from scrapling.fetchers import StealthyFetcher
        try:
            page = StealthyFetcher.fetch(url, headless=True, timeout=timeout)
            html = str(page)
            title = page.css('h1::text').get() or page.css('title::text').get() or ""
        except Exception as be:
            result["error"] = f"browser render failed: {str(be)[:100]}"
            html = None
    except ImportError:
        result["error"] = "scrapling not installed"
        html = None

    # Fallback: Scrapling HTTP fetcher with TLS impersonation (no browser needed)
    if not html or len(html) < 1000:
        try:
            from scrapling.fetchers import Fetcher
            page = Fetcher.get(url, impersonate='chrome', stealthy_headers=True)
            html = page.body
            if isinstance(html, bytes):
                html = html.decode('utf-8', errors='replace')
            title = page.css('h1::text').get() or page.css('title::text').get() or ""
        except Exception as fe:
            if not result["error"]:
                result["error"] = f"http render failed: {str(fe)[:100]}"

    if not html:
        result["error"] = result.get("error") or "render failed"
        return result
    result["title"] = title.strip() if title else ""
    result["render_method"] = "http"

    if not html or len(html) < 1000:
        result["error"] = "empty rendered page"
        return result

    # ── 2. Extract media URLs ──
    media_urls = _extract_media_urls(html, url)
    images = [u for u in media_urls if urlparse(u).path.lower().endswith(IMAGE_EXTS)
              or "media.cnn.com/api/v1/images" in u]
    videos = [u for u in media_urls
              if urlparse(u).path.lower().endswith(VIDEO_EXTS) or ".m3u8" in u]

    mapping = {}
    img_idx = 0
    vid_idx = 0

    # ── 3. Download images ──
    for img_url in sorted(images):
        if img_idx >= max_images:
            break
        if any(x in img_url.lower() for x in ['icon', 'logo', 'placeholder', 'fallback', 'apple-news', 'bg.', 'crop=w_60']):
            continue
        fname = _safe_name(img_url, img_idx, "img")
        dest = assets_dir / fname
        if _download(img_url, dest):
            mapping[img_url] = f"assets/{fname}"
            img_idx += 1
            result["images_downloaded"] = img_idx

    # ── 4. Handle videos ──
    for vid_url in sorted(videos):
        if ".m3u8" in vid_url:
            # HLS: serve via proxy (server streams + rewrites segments)
            mapping[vid_url] = f"/proxy?url={quote(vid_url, safe='')}"
            result["hls_streams"] += 1
            result["videos_found"] += 1
        elif urlparse(vid_url).path.lower().endswith(VIDEO_EXTS):
            fname = _safe_name(vid_url, vid_idx, "vid")
            dest = assets_dir / fname
            if _download(vid_url, dest, timeout=40, max_bytes=max_video_mb * 1024 * 1024):
                mapping[vid_url] = f"assets/{fname}"
                vid_idx += 1
                result["videos_downloaded"] = vid_idx
                result["videos_found"] += 1

    # ── 5. Rewrite HTML ──
    # Save pristine copy FIRST (article builder needs original URLs)
    (archive_dir / "page_orig.html").write_text(html, encoding="utf-8")
    new_html = _rewrite_html(html, mapping)

    # Inject a small banner so it's clear this is an archived copy
    banner = (
        '<div style="position:fixed;top:0;left:0;right:0;z-index:999999;'
        'background:#1f6feb;color:#fff;font:13px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;'
        'padding:6px 14px;text-align:center;">'
        '📄 WebCite archived copy — media served by WebCite</div>'
        '<div style="height:28px"></div>'
    )
    if "</body>" in new_html:
        new_html = new_html.replace("</body>", banner + "</body>")
    else:
        new_html = banner + new_html

    (archive_dir / "page_full.html").write_text(new_html, encoding="utf-8")
    result["html"] = new_html
    result["success"] = True
    return result
