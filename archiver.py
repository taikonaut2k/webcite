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


def html_to_text(html):
    """Strip HTML tags and extract readable text from raw HTML."""
    import re
    # Remove scripts and styles
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Find article body / main content area first (best effort)
    body = re.search(r'<body[^>]*>(.*)</body>', text, re.DOTALL | re.IGNORECASE)
    if body:
        text = body.group(1)
    
    # Replace common block elements with newlines
    text = re.sub(r'</?(?:p|div|h[1-6]|li|br|tr|blockquote|section|article|header|footer)\b[^>]*>', '\n', text, flags=re.IGNORECASE)
    
    # Strip all remaining tags
    text = re.sub(r'<[^>]+>', ' ', text)
    
    # Decode common HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    
    # Collapse whitespace
    lines = text.split('\n')
    lines = [re.sub(r'\s+', ' ', l).strip() for l in lines]
    text = '\n'.join(l for l in lines if l)
    
    return text.strip()

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
            
            # Detect false successes: r.jina.ai sometimes returns 200 with a block page
            title_lower = title.lower()
            if title_lower in ('error', 'blocked', 'denied', 'forbidden', 'captcha', 'please wait', 'just a moment'):
                return {"success": False, "method": "r.jina.ai", "error": f"reader proxy blocked: {title}"}
            
            cleaned_md = clean_markdown(r.stdout)
            
            # Fallback thresholds: if cleaned output is tiny even after trying,
            # consider this a failure so the chain continues to curl_real etc.
            if len(cleaned_md.strip()) < 300:
                # Try raw text fallback from body
                raw_lines = r.stdout.split('\n')
                body_start = 0
                for i, line in enumerate(raw_lines):
                    if line.strip().lower().startswith('markdown content'):
                        body_start = i + 1
                        break
                fallback_text = '\n'.join(raw_lines[body_start:]).strip()
                fallback_lines = [l for l in fallback_text.split('\n')
                                  if not re.match(r'^\[!\[.*?\]\(.*?\)\]\(.*?\)', l.strip())
                                  and not l.strip().startswith('![')]
                fallback_text = '\n'.join(fallback_lines).strip()
                
                if len(fallback_text) > len(cleaned_md):
                    cleaned_md = fallback_text
                
                # If still tiny after all fallback attempts, let next strategy try
                if len(cleaned_md.strip()) < 300:
                    return {"success": False, "method": "r.jina.ai", "error": "reader proxy returned no meaningful content"}
            
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

def capture_via_fallback_proxy(url, timeout=30):
    """Strategy 1b: Fallback reader proxy (md.dhr.wtf) if r.jina.ai fails or returns nothing."""
    try:
        r = subprocess.run([
            "curl", "-sL", "-m", str(timeout),
            f"https://md.dhr.wtf/?url={url}",
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        ], capture_output=True, text=True, timeout=timeout+5)
        
        if r.returncode == 0 and r.stdout and len(r.stdout) > 300:
            title = "Untitled"
            for line in r.stdout.split('\n'):
                line_s = line.strip()
                if line_s.startswith('# ') and len(line_s) > 3:
                    title = line_s.replace('# ', '', 1).strip()
                    break
            
            # Detect block pages
            if title.lower() in ('error', 'blocked', 'denied', 'forbidden', 'captcha', 'please wait', 'just a moment'):
                return {"success": False, "method": "md.dhr.wtf", "error": f"proxy blocked: {title}"}
            
            # Also check raw HTML for Cloudflare/CAPTCHA when no heading was found
            if title == "Untitled":
                lowered = r.stdout.lower()
                if any(kw in lowered for kw in ['just a moment', 'cloudflare', 'checking your browser', 'attention required', 'enable javascript', 'captcha']):
                    return {"success": False, "method": "md.dhr.wtf", "error": "proxy returned challenge page"}
            
            cleaned = r.stdout.strip()
            
            # If content is too short (< 300 meaningful chars), let chain continue
            if len(cleaned) < 300:
                return {"success": False, "method": "md.dhr.wtf", "error": "proxy returned no meaningful content"}
            
            return {
                "success": True,
                "method": "md.dhr.wtf",
                "markdown": cleaned,
                "raw_text": clean_raw_text(cleaned),
                "html": None,
                "title": title,
                "note": "Captured via fallback reader proxy"
            }
    except Exception as e:
        pass
    return {"success": False, "method": "md.dhr.wtf"}

def capture_via_cnn_lite(url, timeout=20, archive_dir=None):
    """Strategy 2: CNN lite mode — rewrite cnn.com URLs to lite.cnn.com for clean HTML.
    Also extracts hero/article images from the regular CNN page metadata."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if 'cnn.com' not in parsed.netloc:
        return {"success": False, "method": "cnn_lite", "error": "not a CNN URL"}
    
    lite_url = f"https://lite.cnn.com{parsed.path}"
    # Strip trailing /index.html if present
    if lite_url.endswith('/index.html'):
        lite_url = lite_url[:-11]
    
    try:
        r = subprocess.run([
            "curl", "-s", "-L", "--max-time", str(timeout),
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "-H", "Accept-Language: en-US,en;q=0.9",
            lite_url
        ], capture_output=True, text=True, timeout=timeout+5)
        
        if r.returncode == 0 and len(r.stdout) > 500 and "captcha" not in r.stdout.lower():
            title = "Untitled"
            m = re.search(r'<title[^>]*>(.*?)</title>', r.stdout, re.IGNORECASE | re.DOTALL)
            if m:
                title = m.group(1).strip()
            
            result = {
                "success": True,
                "method": "cnn_lite",
                "html": r.stdout,
                "markdown": None,
                "raw_text": r.stdout,
                "title": title,
                "note": "Captured via CNN lite mode"
            }
            
            return result
    except:
        pass
    return {"success": False, "method": "cnn_lite"}

def capture_via_curl_real(url, timeout=20):
    """Direct curl with real browser headers — works where reader proxies fail (e.g. CNN)."""
    try:
        r = subprocess.run([
            "curl", "-s", "-L", "--max-time", str(timeout),
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "-H", "Accept-Language: en-US,en;q=0.9",
            url
        ], capture_output=True, text=True, timeout=timeout+5)

        if r.returncode == 0 and len(r.stdout) > 500 and "captcha" not in r.stdout.lower():
            # Try to extract a title from the HTML
            title = "Untitled"
            m = re.search(r'<title[^>]*>(.*?)</title>', r.stdout, re.IGNORECASE | re.DOTALL)
            if m:
                title = m.group(1).strip()

            return {
                "success": True,
                "method": "curl_real",
                "html": r.stdout,
                "markdown": None,
                "raw_text": r.stdout,
                "title": title,
                "note": "Captured via direct curl with browser headers"
            }
    except:
        pass
    return {"success": False, "method": "curl_real"}

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
        ("md.dhr.wtf fallback proxy", capture_via_fallback_proxy),
        ("CNN lite mode", capture_via_cnn_lite),
        ("curl_real browser headers", capture_via_curl_real),
        ("Scrapling HTTP (TLS)", capture_via_scrapling_fetcher),
        ("Direct curl", capture_via_curl),
    ]
    
    for name, strategy_fn in strategies:
        print(f"  Trying {name}...")
        if name in ("Scrapling HTTP (TLS)", "CNN lite mode"):
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
    
    # Also save raw text (convert HTML to plain text when needed)
    raw_text = result.get("raw_text", "")
    if raw_text:
        # Detect if raw_text is HTML (from curl strategies) and convert to readable text
        stripped = raw_text.strip()
        if stripped.startswith("<!") or stripped.startswith("<html"):
            text_content = html_to_text(raw_text)
        else:
            text_content = raw_text
        with open(archive_dir / "text.txt", "w", encoding="utf-8") as f:
            f.write(text_content)
        # Update the record's text_length to reflect cleaned text length
        record["text_length"] = len(text_content)
    
    # ── Image extraction for CNN articles ──────────────────────────
    # If the capture succeeded with text but no images, try to extract
    # images from the regular CNN page metadata (works for any strategy).
    if result["success"] and 'cnn.com' in parsed.netloc and not result.get("downloaded_images", 0):
        try:
            assets_dir = archive_dir / "assets"
            assets_dir.mkdir(exist_ok=True)
            
            meta = subprocess.run([
                "curl", "-s", "-L", "--max-time", "12", "--range", "150000-650000", url,
                "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "-H", "Accept-Language: en-US,en;q=0.9"
            ], capture_output=True, text=True, timeout=15)
            
            if meta.returncode == 0:
                import urllib.request
                img_urls = set()
                html_meta = meta.stdout
                
                # og:image — the hero photo
                og = re.search(r'''<meta[^>]+property=["']og:image["'][^>]*content=["']([^"']+)["']''', html_meta)
                if og:
                    img_urls.add(og.group(1))
                # JSON-LD images (structured data)
                for j in re.findall(r'''<script[^>]+type=["']application/ld\+json["'][^>]*>(.*?)</script>''', html_meta, re.DOTALL):
                    for u in re.findall(r'"https://media\.cnn\.com[^"]*\.(?:jpg|jpeg|png|webp)"', j, re.IGNORECASE):
                        img_urls.add(u.strip('"'))
                # stellar/prod images from embedded config (article images in JSON blobs)
                for u in re.findall(r'"https://media\.cnn\.com/api/v1/images/stellar/prod/[^"]+\.(?:JPG|jpg|jpeg|png|webp)"', html_meta):
                    base = u.strip('"').split('?')[0]
                    if not any(x in base.lower() for x in ['icon', 'logo', 'placeholder', 'fallback', 'style', 'apple-news', 'bg.']):
                        img_urls.add(u.strip('"'))
                
                downloaded = 0
                img_exts = []
                for img_url in list(img_urls)[:5]:
                    try:
                        ext = re.search(r'\.(jpg|jpeg|png|webp)', img_url, re.IGNORECASE)
                        ext = (ext.group(1) if ext else 'jpg').lower()
                        urllib.request.urlretrieve(img_url, assets_dir / f"img_{downloaded}.{ext}")
                        img_exts.append(ext)
                        downloaded += 1
                    except:
                        pass
                
                if downloaded:
                    result["downloaded_images"] = downloaded
                    result["assets_dir"] = str(assets_dir)
                    record["downloaded_images"] = downloaded
                    record["image_files"] = [f"assets/img_{i}.{ext}" for i, ext in enumerate(img_exts)]
        except:
            pass
    
    # Update record with screenshot info
    if result.get("screenshot"):
        record["has_screenshot"] = True
        record["screenshot_file"] = result["screenshot"]
    if result.get("screenshot_viewport"):
        record["screenshot_viewport_file"] = result["screenshot_viewport"]
    if result.get("downloaded_images", 0) > 0:
        record["downloaded_images"] = result["downloaded_images"]
    
    # ── Full-page media capture (original form with photos + videos) ──
    # Runs after text capture succeeds. Best-effort: never fails the archive.
    if result["success"]:
        try:
            from media_archiver import capture_full_page
            print(f"  📸 Full-page media capture for {url}...")
            fp = capture_full_page(url, archive_dir)
            if fp["success"]:
                record["full_page"] = {
                    "available": True,
                    "images_downloaded": fp["images_downloaded"],
                    "videos_downloaded": fp["videos_downloaded"],
                    "hls_streams": fp["hls_streams"],
                    "videos_found": fp["videos_found"],
                    "file": "page_full.html",
                }
                print(f"    ✅ Full page: {fp['images_downloaded']} imgs, {fp['videos_downloaded']} vids, {fp['hls_streams']} HLS")
            else:
                record["full_page"] = {"available": False, "error": fp.get("error", "unknown")}
                print(f"    ⚠ Full page failed: {fp.get('error')}")
        except Exception as e:
            record["full_page"] = {"available": False, "error": str(e)}
            print(f"    ⚠ Full page exception: {e}")
    
    # ── Article view (full text + photos + video, paywall-bypassed) ──
    # Extracts the complete article from JSON-LD (CNN ships articleBody even
    # behind the visual paywall) and renders a clean media-rich page.
    if result["success"]:
        try:
            from article_builder import build_article
            print(f"  📰 Building media-rich article view...")
            # Get the full page HTML (from full-page capture if available,
            # otherwise fetch raw). Prefer the PRISTINE copy (original URLs)
            # so the article builder can download fresh images.
            full_html = None
            if (archive_dir / "page_orig.html").exists():
                full_html = (archive_dir / "page_orig.html").read_text(encoding="utf-8", errors="replace")
            elif (archive_dir / "page_full.html").exists():
                full_html = (archive_dir / "page_full.html").read_text(encoding="utf-8", errors="replace")
            else:
                full_html = result.get("html") or ""
                if not full_html and result.get("raw_text"):
                    full_html = result.get("raw_text")
            if full_html:
                article_html, stats = build_article(archive_dir, url, full_html)
                if article_html:
                    (archive_dir / "article.html").write_text(article_html, encoding="utf-8")
                    record["article_view"] = {
                        "available": True,
                        "headline": stats.get("headline", "")[:120],
                        "paragraphs": stats.get("paragraphs", 0),
                        "images_downloaded": stats.get("images", {}).get("downloaded", 0),
                        "video_embeds": stats.get("video_embeds", 0),
                        "file": "article.html",
                    }
                    print(f"    ✅ Article view: {stats['paragraphs']} paras, {stats['images']['downloaded']} imgs, {stats['video_embeds']} vids")
        except Exception as e:
            print(f"    ⚠ Article view exception: {e}")
    
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
