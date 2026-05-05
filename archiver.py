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

# ── Index Management ────────────────────────────────────────────────

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
            
            return {
                "success": True,
                "method": "r.jina.ai",
                "markdown": r.stdout,
                "raw_text": r.stdout,
                "html": None,
                "title": title,
                "note": "Captured via reader proxy"
            }
    except Exception as e:
        pass
    return {"success": False, "method": "r.jina.ai"}

def capture_via_scrapling_stealth(url, timeout=45):
    """Strategy 2: Scrapling StealthyFetcher. Best for JS/Cloudflare sites."""
    try:
        from scrapling.fetchers import StealthyFetcher
        
        page = StealthyFetcher.fetch(url, headless=True, timeout=timeout)
        
        title = page.css('h1::text').get() or page.css('title::text').get() or ""
        body = page.css('p::text').getall()
        
        # Extract all text content
        all_text = page.css('body::text').getall()
        full_text = '\n'.join(t.strip() for t in all_text if t.strip())
        
        # Get raw HTML
        html = str(page)
        
        return {
            "success": True,
            "method": "scrapling_stealth",
            "html": html,
            "markdown": None,
            "raw_text": full_text,
            "title": title.strip() if title else "Untitled",
            "paragraphs": len(body),
            "note": "Captured via headless browser"
        }
    except ImportError:
        return {"success": False, "method": "scrapling_stealth", "error": "scrapling not installed"}
    except Exception as e:
        return {"success": False, "method": "scrapling_stealth", "error": str(e)}

def capture_via_scrapling_fetcher(url, timeout=20):
    """Strategy 3: Scrapling HTTP fetcher with TLS impersonation."""
    try:
        from scrapling.fetchers import Fetcher
        
        page = Fetcher.get(url, impersonate='chrome', stealthy_headers=True)
        
        title = page.css('h1::text').get() or page.css('title::text').get() or ""
        body = page.css('p::text').getall()
        all_text = page.css('body::text').getall()
        full_text = '\n'.join(t.strip() for t in all_text if t.strip())
        html = str(page)
        
        return {
            "success": True,
            "method": "scrapling_http",
            "html": html,
            "markdown": None,
            "raw_text": full_text,
            "title": title.strip() if title else "Untitled",
            "paragraphs": len(body),
            "note": "Captured via HTTP with TLS fingerprint"
        }
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
    
    # Try strategies in order
    strategies = [
        ("r.jina.ai reader proxy", capture_via_jina),
        ("Scrapling Stealth (headless)", capture_via_scrapling_stealth),
        ("Scrapling HTTP (TLS)", capture_via_scrapling_fetcher),
        ("Direct curl", capture_via_curl),
    ]
    
    for name, strategy_fn in strategies:
        print(f"  Trying {name}...")
        result = strategy_fn(url)
        strategies_used.append(name)
        if result["success"]:
            print(f"  ✅ {name} succeeded")
            break
        print(f"  ❌ {name} failed")
    
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
