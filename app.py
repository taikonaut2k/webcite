#!/usr/bin/env python3
"""
WebCite — Modern Web Archiving Service
A clone of archive.today with improved capture using Scrapling + r.jina.ai.

Run: python3 app.py
Then open: http://localhost:5000
"""

from flask import Flask, request, render_template, jsonify, redirect, url_for, send_from_directory, Response
import json, os, time, threading, re
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, unquote
import urllib.request

# Import our archiver
from archiver import capture_url, get_archive, search_archives, load_index, ARCHIVES_DIR
from media_archiver import capture_full_page

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()

# Rate limiting (simple in-memory)
RATE_LIMITS = {}
RATE_LIMIT_WINDOW = 60  # seconds
FREE_MAX_PER_MINUTE = 3
PREMIUM_MAX_PER_MINUTE = 60

# ── API Keys (simple file-based) ────────────────────────────────────
API_KEYS_FILE = Path.home() / "archive-clone" / "api_keys.json"

def load_api_keys():
    if API_KEYS_FILE.exists():
        with open(API_KEYS_FILE) as f:
            return json.load(f)
    return {}

def save_api_keys(keys):
    API_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(API_KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)

def check_rate_limit(client_ip, api_key=None):
    """Check if client is within rate limits."""
    now = time.time()
    key = api_key or client_ip
    is_premium = api_key and api_key in load_api_keys()
    
    # Clean old entries
    for k in list(RATE_LIMITS.keys()):
        RATE_LIMITS[k] = [t for t in RATE_LIMITS.get(k, []) if now - t < RATE_LIMIT_WINDOW]
        if not RATE_LIMITS[k]:
            del RATE_LIMITS[k]
    
    if key not in RATE_LIMITS:
        RATE_LIMITS[key] = []
    
    limit = PREMIUM_MAX_PER_MINUTE if is_premium else FREE_MAX_PER_MINUTE
    if len(RATE_LIMITS[key]) >= limit:
        return False, limit
    
    RATE_LIMITS[key].append(now)
    return True, limit

# ═══════════════════════════════════════════════════════════════════
#  WEB ROUTES
# ═══════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    """Homepage — archive.today style."""
    recent = load_index().get("archives", [])[:20]
    return render_template("index.html", recent=recent)

@app.route("/archive", methods=["POST"])
def archive():
    """Submit a URL for archiving."""
    url = request.form.get("url", "").strip()
    api_key = request.form.get("api_key", "")
    client_ip = request.remote_addr or "unknown"
    
    if not url:
        return render_template("index.html", error="Please enter a URL")
    
    # Rate limit check
    allowed, limit = check_rate_limit(client_ip, api_key if api_key else None)
    if not allowed:
        return render_template("index.html", error=f"Rate limit exceeded. Max {limit} per minute. Use an API key for higher limits.")
    
    # Capture in background
    result = {"status": "processing", "url": url}
    
    def do_capture(url, result_dict):
        try:
            record = capture_url(url)
            result_dict["status"] = "complete" if record["success"] else "failed"
            result_dict["record"] = record
            result_dict["id"] = record.get("id", "")
        except Exception as e:
            result_dict["status"] = "error"
            result_dict["error"] = str(e)
    
    capture_thread = threading.Thread(target=do_capture, args=(url, result))
    capture_thread.start()
    capture_thread.join(timeout=120)
    
    if result["status"] == "complete":
        return redirect(url_for("view", archive_id=result["id"]))
    elif result["status"] == "failed":
        return render_template("index.html", 
            error=f"Capture failed. Tried multiple strategies. URL may be unreachable or requires authentication.",
            url=url,
            note=result.get("record", {}).get("note", ""))
    else:
        return render_template("index.html", error=f"Capture timed out: {result.get('error', 'unknown error')}")
    
@app.route("/a/<archive_id>")
def view(archive_id):
    """View an archived page."""
    record = get_archive(archive_id)
    if not record:
        return render_template("index.html", error=f"Archive not found: {archive_id}")
    
    return render_template("archive.html", record=record)

@app.route("/a/<archive_id>/raw")
def view_raw(archive_id):
    """View archived page as raw text."""
    record = get_archive(archive_id)
    if not record:
        return "Archive not found", 404
    
    content = record.get("content", {}).get("text") or record.get("content", {}).get("markdown") or ""
    return f"<pre style='white-space:pre-wrap;word-wrap:break-word;'>{content}</pre>"

@app.route("/a/<archive_id>/md")
def view_md(archive_id):
    """View archived page as markdown."""
    record = get_archive(archive_id)
    if not record:
        return "Archive not found", 404
    
    content = record.get("content", {}).get("markdown") or ""
    return f"<pre style='white-space:pre-wrap;word-wrap:break-word;'>{content}</pre>"

@app.route("/search")
def search():
    """Search archived pages."""
    q = request.args.get("q", "").strip()
    results = []
    if q:
        results = search_archives(q)
    return render_template("search.html", query=q, results=results)

# ═══════════════════════════════════════════════════════════════════
#  MEDIA PROXY (stream remote media through WebCite)
#  — Lets users watch archived videos even when the origin is blocked
#  — Supports HLS (.m3u8): rewrites segment URLs to keep them proxied
#  — Supports HTTP Range for video seeking
# ═══════════════════════════════════════════════════════════════════

MEDIA_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

@app.route("/proxy")
def proxy_media():
    """Stream a remote media URL through WebCite."""
    target = request.args.get("url", "")
    if not target:
        return "Missing url param", 400
    if not target.startswith(('http://', 'https://')):
        return "Invalid url", 400

    try:
        headers = {"User-Agent": MEDIA_UA, "Accept": "*/*", "Referer": target}
        # Forward Range header for video seeking
        rng = request.headers.get("Range")
        if rng:
            headers["Range"] = rng

        req = urllib.request.Request(target, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            content = resp.read()
            status = resp.status
            ctype = resp.headers.get("Content-Type", "application/octet-stream")

        # ── HLS playlist: rewrite segment URLs to keep them proxied ──
        if target.endswith(".m3u8") or ".m3u8?" in target:
            text = content.decode("utf-8", errors="replace")
            base = target[:target.rfind("/") + 1]
            lines = []
            for line in text.split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    seg_url = line if line.startswith("http") else base + line
                    lines.append(f"/proxy?url={urllib.parse.quote(seg_url, safe='')}")
                else:
                    lines.append(line)
            content = "\n".join(lines).encode()
            ctype = "application/vnd.apple.mpegurl"

        resp_headers = {"Content-Type": ctype, "Cache-Control": "public, max-age=3600"}
        if rng and status == 206:
            resp_headers["Content-Range"] = resp.headers.get("Content-Range", "")
        return Response(content, status=status, headers=resp_headers)
    except Exception as e:
        return f"Proxy error: {e}", 502

@app.route("/a/<archive_id>/original")
def view_original(archive_id):
    """View archived page in original form (media-rich HTML)."""
    archive_dir = ARCHIVES_DIR / archive_id
    full_path = archive_dir / "page_full.html"
    if not full_path.exists():
        return "No full-page capture available for this archive", 404
    return send_from_directory(str(archive_dir), "page_full.html")


@app.route("/site/<archive_id>/<path:filename>")
def serve_archive_file(archive_id, filename):
    """Serve archived files (screenshots, assets)."""
    archive_dir = ARCHIVES_DIR / archive_id
    if not archive_dir.exists():
        return "Not found", 404
    return send_from_directory(str(archive_dir), filename)

# ═══════════════════════════════════════════════════════════════════
#  API ENDPOINTS (for developers / monetization)
# ═══════════════════════════════════════════════════════════════════

@app.route("/api/archive", methods=["POST"])
def api_archive():
    """API endpoint: archive a URL."""
    data = request.get_json(silent=True) or request.form
    url = data.get("url", "").strip()
    api_key = request.headers.get("X-API-Key") or data.get("api_key", "")
    
    if not url:
        return jsonify({"error": "url is required"}), 400
    
    # Validate API key for API access
    keys = load_api_keys()
    if not api_key or api_key not in keys:
        return jsonify({"error": "Valid API key required. Get one at /account"}), 401
    
    # Rate limit check
    allowed, limit = check_rate_limit("api", api_key)
    if not allowed:
        return jsonify({"error": f"Rate limit exceeded"}), 429
    
    record = capture_url(url)
    
    return jsonify({
        "status": "success" if record["success"] else "failed",
        "id": record["id"],
        "url": record["url"],
        "title": record["title"],
        "timestamp": record["timestamp"],
        "method": record["method"],
        "capture_time_seconds": record["capture_time_seconds"],
        "view_url": f"/a/{record['id']}",
        "api_url": f"/api/archive/{record['id']}"
    })

@app.route("/api/archive/<archive_id>")
def api_get_archive(archive_id):
    """API endpoint: retrieve an archived page."""
    api_key = request.headers.get("X-API-Key") or request.args.get("api_key", "")
    
    keys = load_api_keys()
    if not api_key or api_key not in keys:
        return jsonify({"error": "Valid API key required"}), 401
    
    record = get_archive(archive_id)
    if not record:
        return jsonify({"error": "Archive not found"}), 404
    
    return jsonify({
        "id": record["id"],
        "url": record["url"],
        "title": record["title"],
        "timestamp": record["timestamp"],
        "method": record["method"],
        "text_length": record.get("text_length", 0),
        "text": record.get("content", {}).get("text", ""),
        "markdown": record.get("content", {}).get("markdown", ""),
    })

# ═══════════════════════════════════════════════════════════════════
#  ACCOUNT / API KEY MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

@app.route("/account")
def account():
    """Account page — get API key."""
    return render_template("account.html")

@app.route("/api/generate-key", methods=["POST"])
def generate_key():
    """Generate a free API key (demo purposes)."""
    import hashlib, secrets
    email = request.form.get("email", "").strip()
    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400
    
    keys = load_api_keys()
    # Generate simple key
    raw = f"{email}:{secrets.token_hex(16)}"
    api_key = hashlib.sha256(raw.encode()).hexdigest()[:32]
    keys[api_key] = {
        "email": email,
        "tier": "free",
        "created": datetime.utcnow().isoformat() + "Z",
        "monthly_captures": 100
    }
    save_api_keys(keys)
    
    return render_template("account.html", api_key=api_key, email=email)

# ═══════════════════════════════════════════════════════════════════
#  STATIC FILES
# ═══════════════════════════════════════════════════════════════════

@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory("static", path)

# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print()
    print("╔═══════════════════════════════════════════════════╗")
    print("║   🌐 WebCite — Modern Web Archiving Service      ║")
    print("╠═══════════════════════════════════════════════════╣")
    print("║   Powered by Scrapling + r.jina.ai               ║")
    print("║                                                   ║")
    print("║   Open: http://localhost:5000                     ║")
    print("║   API:  http://localhost:5000/api/archive         ║")
    print("╚═══════════════════════════════════════════════════╝")
    print()
    app.run(host="0.0.0.0", port=5000, debug=True)
