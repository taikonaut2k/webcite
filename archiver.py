"""
Archiver Core — Multi-strategy web page capture system.

Strategies (tried in order):
1. r.jina.ai reader proxy → best for paywalled news, returns clean markdown
2. Scrapling StealthyFetcher → best for JS-heavy, Cloudflare-protected sites
3. Scrapling Fetcher (HTTP/TLS) → fallback for simple sites
4. Direct curl → last resort

Saves: raw HTML, markdown version, metadata, screenshot (optional).
"""

import json, os, hashlib, time, re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import subprocess

ARCHIVES_DIR = Path.home() / "archive-clone" / "archived_sites"
INDEX_PATH = Path.home() / "archive-clone" / "archives" / "index.json"

ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)

# ── Content Cleaning ────────────────────────────────────────────────

def clean_markdown(text):
    """
    Strip navigation, ads, footers, and list clutter from r.jina.ai markdown output.
    Keeps only the article content between the first real heading and the 
    last meaningful paragraph.
    
    Handles both # (H1) and ##/### (H2/H3) headings. Falls back to including
    all lines after "Markdown Content:" if no heading is found.
    """
    lines = text.split('\n')
    cleaned = []
    in_article = False
    article_end = False
    passed_markdown_header = False
    non_blank_count = 0  # count non-blank lines scanned before article starts
    
    # Known navigation/boilerplate patterns to skip (case-insensitive)
    skip_patterns = [
        r'^ad feedback',
        r'^cnn values your feedback',
        r'^how relevant is this ad',
        r'^did you encounter any',
        r'^video player was slow',
        r'^ad never loaded',
        r'^thank you',
        r'^your effort and contribution',
        r'^close',
        r'^cancel submit',
        r'^\[x\]',
        r'^follow cnn',
        r'^download the cnn app',
        r'^sign in',
        r'^my account',
        r'^edition',
        r'^hot stocks',
        r'^fear & greed',
        r'^latest market news',
        r'^something isn\'t loading',
        r'^markets$',
        r'^subscribe$',
        r'^listen$',
        r'^watch$',
        r'^\* \* \*$',
        r'^\[\]\(http',
        r'^link copied!',
        r'^see all topics',
        r'^facebook tweet',
        r'^\+[0-9]+\.[0-9]+%?$',
        r'^[0-9]+\.[0-9]+$',
        r'^[A-Z]{2,5}$',  # Stock tickers like NVDA, INTC
        r'^[\s]*$',
    ]
    skip_regex = re.compile('|'.join(skip_patterns), re.IGNORECASE)
    
    for line in lines:
        stripped = line.strip()
        
        # Track when we pass the "Markdown Content:" header
        if stripped.lower().startswith('markdown content'):
            passed_markdown_header = True
            continue
        
        # Detect article start: first heading (#, ##, or ###) that's not site branding
        heading_match = re.match(r'^#{1,3}\s', stripped)
        if heading_match and not in_article:
            # Check it's a real article heading, not CNN/Bloomberg branding
            if not any(brand in stripped.lower() for brand in ['bloomberg', 'cnn business', 'cnn logo', 'skip to content']):
                in_article = True
                cleaned.append(stripped)
                continue
        
        if not in_article:
            # Fallback: if we've passed "Markdown Content:" and scanned 3+ non-blank
            # lines with no heading found, treat everything as article content
            if passed_markdown_header and stripped:
                non_blank_count += 1
                if non_blank_count >= 3:
                    in_article = True
                    cleaned.append(stripped)
                    continue
            continue
        
        # Detect article end: common footer markers
        if any(marker in stripped.lower() for marker in [
            'cnn\'s .* contributed to this report',
            'contributing to this report',
            '™ & ©',
            'terms of use',
            'privacy policy',
            'manage cookies',
            'all rights reserved',
        ]):
            if re.search(r'cnn\'s|contributing|rights reserved', stripped.lower()):
                cleaned.append(stripped)
                article_end = True
                continue
        
        if article_end:
            continue
        
        # Skip navigation/boilerplate lines
        if skip_regex.match(stripped):
            continue
        
        # Skip lines that are just star-list navigation items
        if stripped.startswith('* ') and len(stripped) < 100:
            continue
        
        # Skip lines that are just URLs in markdown link format
        if re.match(r'^\[.*?\]\(https?://', stripped) and '|' not in stripped:
            continue
        
        # Skip stock data lines (numbers, tickers)
        if re.match(r'^[A-Z]{2,5}$', stripped):
            continue
        if re.match(r'^[+-]?\d+\.?\d*%?$', stripped) and len(stripped) < 15:
            continue
        
        # Skip standalone commas and punctuation-only lines
        if re.match(r'^[,\.;:\s]+$', stripped):
            continue
        
        # Skip update/publish timestamp lines
        if re.match(r'^updated|^published', stripped, re.IGNORECASE):
            continue
        
        cleaned.append(stripped)
    
    return '\n'.join(cleaned).strip()


def clean_raw_text(text):
    """
    Remove lines starting with * (navigation items) from raw text.
    """
    lines = text.split('\n')
    cleaned = [l for l in lines if not l.strip().startswith('* ')]
    return '\n'.join(cleaned).strip()

def load_index():
    if INDEX_PATH.exists():
        with open(INDEX_PATH) as f:
            return json.load(f)
    return {"archives": [], "total": 0}

def save_index(index):
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX_PATH, "w") as f:
        json.dump(index, f, indent=2)

def generate_id(url):
    """Generate a short unique archive ID from URL + timestamp."""
    raw = f"{url}{time.time()}".encode()
    return hashlib.sha256(raw).hexdigest()[:12]

def sanitize_filename(url):
    """Create a safe filesystem name from a URL."""
    name = re.sub(r'[^a-zA-Z0-9]', '_', url)
    return name[:100]

# ── Capture Strategies ──────────────────────────────────────────────

def capture_via_jina(url, timeout=30):
    """Strategy 1: r.jina.ai reader proxy. Best for paywalled news."""
    try:
        r = subprocess.run([
            "curl", "-sL", "-m", str(timeout),
            f"https://r.jina.ai/{url}",
            "-H", "Accept: text/plain",
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        ], capture_output=True, text=True, timeout=timeout+5)
        
        if r.returncode == 0 and r.stdout and len(r.stdout) > 200:
            # Extract title from r.jina.ai's header (first line is "Title: ...")
            title = "Untitled"
            for line in r.stdout.split('\n'):
                if line.startswith('Title: '):
                    title = line.replace('Title: ', '', 1).strip()
                    break
            
            cleaned_md = clean_markdown(r.stdout)
            
            # Fallback: if cleaning stripped everything, use raw text with header lines removed
            if len(cleaned_md.strip()) < 100:
                # Strip the Title:/URL Source:/Published Time: header lines
                raw_lines = r.stdout.split('\n')
                body_start = 0
                for i, line in enumerate(raw_lines):
                    if line.strip().lower().startswith('markdown content'):
                        body_start = i + 1
                        break
                fallback_text = '\n'.join(raw_lines[body_start:]).strip()
                # Also strip the leading image/URL boilerplate
                fallback_lines = [l for l in fallback_text.split('\n')
                                  if not re.match(r'^\[!\[.*?\]\(.*?\)\]\(.*?\)', l.strip())
                                  and not l.strip().startswith('![')]
                fallback_text = '\n'.join(fallback_lines).strip()
                if len(fallback_text) > len(cleaned_md):
                    cleaned_md = fallback_text
            
            return {
                "success": True,
                "method": "r.jina.ai",
                "markdown": cleaned_md,
                "raw_text": clean_raw_text(cleaned_md),
                "html": None,
                "title": title,
                "note": "Captured via reader proxy"
            }
    except Exception as e:
        pass
    return {"success": False, "method": "r.jina.ai"}

def capture_via_scrapling_stealth(url, timeout=45, archive_dir=None):
    """Strategy 2: Scrapling StealthyFetcher. Best for JS/Cloudflare sites.
    Also captures a full-page screenshot using the SAME browser session."""
    try:
        from scrapling.fetchers import StealthyFetcher
        from playwright.sync_api import sync_playwright
        import re, os
        
        page = StealthyFetcher.fetch(url, headless=True, timeout=timeout)
        
        title = page.css('h1::text').get() or page.css('title::text').get() or ""
        body = page.css('p::text').getall()
        all_text = page.css('body::text').getall()
        full_text = '\n'.join(t.strip() for t in all_text if t.strip())
        html = str(page)
        
        result = {
            "success": True,
            "method": "scrapling_stealth",
            "html": html,
            "markdown": None,
            "raw_text": full_text,
            "title": title.strip() if title else "Untitled",
            "paragraphs": len(body),
            "screenshot": None,
            "downloaded_images": 0,
            "assets_dir": None,
            "note": "Captured via headless browser"
        }
        
        # Take screenshot using Playwright (in same session if possible)
        if archive_dir:
            try:
                import os as _os
                # Try system Chromium if Playwright's bundled one isn't available
                chrome_paths = [
                    _os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", ""),
                    "/usr/bin/chromium-browser",
                    "/usr/bin/chromium",
                    "/snap/bin/chromium",
                ]
                launch_opts = {
                    "headless": True,
                    "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
                }
                for cp in chrome_paths:
                    if cp and _os.path.exists(cp):
                        launch_opts["executable_path"] = cp
                        break
                
                with sync_playwright() as p:
                    browser = p.chromium.launch(**launch_opts)
                    context = browser.new_context(viewport={"width": 1920, "height": 1080})
                    pw_page = context.new_page()
                    pw_page.goto(url, wait_until="load", timeout=30000)
                    pw_page.wait_for_timeout(5000)
                    ss_path = str(archive_dir / "screenshot.png")
                    pw_page.screenshot(path=ss_path, full_page=True)
                    vp_path = str(archive_dir / "screenshot_viewport.png")
                    pw_page.screenshot(path=vp_path, full_page=False)
                    browser.close()
                    result["screenshot"] = "screenshot.png"
                    result["screenshot_viewport"] = "screenshot_viewport.png"
                    print(f"    ✅ Screenshot taken ({_os.path.getsize(ss_path)//1024} KB)")
            except Exception as sce:
                print(f"    ⚠ Screenshot skipped: {sce}")
        
        # Download images
        if archive_dir and html:
            assets_dir = archive_dir / "assets"
            assets_dir.mkdir(exist_ok=True)
            import urllib.request
            from urllib.parse import urljoin, urlparse
            img_urls = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html)
            downloaded = 0
            for img_url in img_urls[:50]:
                try:
                    full_url = urljoin(url, img_url)
                    parsed = urlparse(full_url)
                    if not parsed.scheme.startswith('http'):
                        continue
                    ext = os.path.splitext(parsed.path)[1] or '.jpg'
                    img_name = f"img_{downloaded}{ext}"
                    img_path = assets_dir / img_name
                    urllib.request.urlretrieve(full_url, img_path)
                    html = html.replace(img_url, f"assets/{img_name}", 1)
                    downloaded += 1
                except:
                    pass
            result["html"] = html
            result["downloaded_images"] = downloaded
            result["assets_dir"] = str(assets_dir)
        
        return result
    except ImportError:
        return {"success": False, "method": "scrapling_stealth", "error": "scrapling not installed"}
    except Exception as e:
        return {"success": False, "method": "scrapling_stealth", "error": str(e)}

def capture_via_scrapling_fetcher(url, timeout=20, archive_dir=None):
    """Strategy 3: Scrapling HTTP fetcher with TLS impersonation.
    Downloads images from the HTML when possible."""
    try:
        from scrapling.fetchers import Fetcher
        import re, os as _os
        from urllib.parse import urljoin, urlparse
        import urllib.request
        
        page = Fetcher.get(url, impersonate='chrome', stealthy_headers=True)
        
        title = page.css('h1::text').get() or page.css('title::text').get() or ""
        body = page.css('p::text').getall()
        all_text = page.css('body::text').getall()
        full_text = '\n'.join(t.strip() for t in all_text if t.strip())
        html = page.body  # response body = full HTML
        if isinstance(html, bytes):
            html = html.decode('utf-8', errors='replace')
        
        result = {
            "success": True,
            "method": "scrapling_http",
            "html": html,
            "markdown": None,
            "raw_text": full_text,
            "title": title.strip() if title else "Untitled",
            "paragraphs": len(body),
            "downloaded_images": 0,
            "assets_dir": None,
            "note": "Captured via HTTP with TLS fingerprint"
        }
        
        # Download images if archive_dir provided
        if archive_dir and html:
            assets_dir = archive_dir / "assets"
            assets_dir.mkdir(exist_ok=True)
            img_urls = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html)
            downloaded = 0
            for img_url in img_urls[:50]:
                try:
                    full_url = urljoin(url, img_url)
                    parsed = urlparse(full_url)
                    if not parsed.scheme.startswith('http'):
                        continue
                    ext = _os.path.splitext(parsed.path)[1] or '.jpg'
                    img_name = f"img_{downloaded}{ext}"
                    img_path = assets_dir / img_name
                    urllib.request.urlretrieve(full_url, img_path)
                    html = html.replace(img_url, f"assets/{img_name}", 1)
                    downloaded += 1
                except:
                    pass
            result["html"] = html
            result["downloaded_images"] = downloaded
            result["assets_dir"] = str(assets_dir)
        
        return result
    except ImportError:
        return {"success": False, "method": "scrapling_http", "error": "scrapling not installed"}
    except Exception as e:
        return {"success": False, "method": "scrapling_http", "error": str(e)}

def capture_via_curl(url, timeout=15):
    """Strategy 4: Direct curl fallback."""
    try:
        r = subprocess.run([
            "curl", "-sL", "-m", str(timeout),
            url,
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "-H", "Accept: text/html,application/xhtml+xml"
        ], capture_output=True, text=True, timeout=timeout+5)
        
        if r.returncode == 0 and r.stdout and len(r.stdout) > 200:
            return {
                "success": True,
                "method": "curl",
                "html": r.stdout,
                "markdown": None,
                "raw_text": r.stdout,
                "note": "Captured via direct HTTP"
            }
    except Exception as e:
        pass
    return {"success": False, "method": "curl"}

# ── Main Capture ────────────────────────────────────────────────────

def capture_url(url, premium=False):
    """
    Capture a URL using the best available strategy.
    Returns archive record dict.
    """
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
    
    aid = generate_id(url)
    safe_name = sanitize_filename(url)
    archive_dir = ARCHIVES_DIR / aid
    archive_dir.mkdir(parents=True, exist_ok=True)
    
    start_time = time.time()
    strategies_used = []
    result = None
    
    # Try strategies in order (reader proxy first for fast clean text)
    strategies = [
        ("r.jina.ai reader proxy", capture_via_jina),
        ("Scrapling HTTP (TLS)", capture_via_scrapling_fetcher),
        ("Direct curl", capture_via_curl),
    ]
    
    for name, strategy_fn in strategies:
        print(f"  Trying {name}...")
        if name == "Scrapling HTTP (TLS)":
            result = strategy_fn(url, archive_dir=archive_dir)
        else:
            result = strategy_fn(url)
        strategies_used.append(name)
        if result["success"]:
            print(f"  ✅ {name} succeeded")
            break
    
    # If text capture succeeded but no screenshot, clean the text output
    if result["success"] and result.get("raw_text"):
        result["raw_text"] = clean_raw_text(result["raw_text"])
    
    elapsed = round(time.time() - start_time, 2)
    
    # Build archive record
    record = {
        "id": aid,
        "url": url,
        "domain": parsed.netloc or url,
        "title": result.get("title", "Untitled"),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "method": result.get("method", "none"),
        "strategies_tried": strategies_used,
        "success": result["success"],
        "capture_time_seconds": elapsed,
        "paragraphs": result.get("paragraphs", 0),
        "text_length": len(result.get("raw_text", "")),
        "note": result.get("note", ""),
        "storage": {
            "dir": str(archive_dir),
            "html_file": "page.html",
            "markdown_file": "page.md",
            "metadata_file": "metadata.json",
        }
    }
    
    # Save files
    if result.get("html"):
        with open(archive_dir / "page.html", "w", encoding="utf-8") as f:
            f.write(result["html"])
    
    if result.get("markdown"):
        with open(archive_dir / "page.md", "w", encoding="utf-8") as f:
            f.write(result["markdown"])
    
    # Also save raw text
    if result.get("raw_text"):
        with open(archive_dir / "text.txt", "w", encoding="utf-8") as f:
            f.write(result["raw_text"])
    
    # Update record with screenshot info
    if result.get("screenshot"):
        record["has_screenshot"] = True
        record["screenshot_file"] = result["screenshot"]
    if result.get("screenshot_viewport"):
        record["screenshot_viewport_file"] = result["screenshot_viewport"]
    if result.get("downloaded_images", 0) > 0:
        record["downloaded_images"] = result["downloaded_images"]
    
    # Save metadata
    with open(archive_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, default=str)
    
    # Update index
    index = load_index()
    index["archives"].insert(0, {
        "id": aid,
        "url": url,
        "domain": record["domain"],
        "title": record["title"],
        "timestamp": record["timestamp"],
        "method": record["method"],
        "success": result["success"],
        "text_length": record["text_length"],
    })
    index["total"] = len(index["archives"])
    save_index(index)
    
    return record

def get_archive(archive_id):
    """Retrieve an archived record by ID."""
    archive_dir = ARCHIVES_DIR / archive_id
    meta_path = archive_dir / "metadata.json"
    if not meta_path.exists():
        return None
    
    with open(meta_path) as f:
        record = json.load(f)
    
    # Load content
    html_path = archive_dir / "page.html"
    md_path = archive_dir / "page.md"
    text_path = archive_dir / "text.txt"
    
    record["content"] = {}
    if html_path.exists():
        with open(html_path) as f:
            record["content"]["html"] = f.read()
    if md_path.exists():
        with open(md_path) as f:
            record["content"]["markdown"] = f.read()
    if text_path.exists():
        with open(text_path) as f:
            record["content"]["text"] = f.read()
    
    return record

def search_archives(query):
    """Search archived pages by URL, title, or domain."""
    index = load_index()
    query = query.lower()
    results = []
    
    for entry in index["archives"]:
        if (query in entry["url"].lower() or 
            query in entry["title"].lower() or 
            query in entry["domain"].lower()):
            results.append(entry)
    
    return results
