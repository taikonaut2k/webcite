# WebCite

A modern web archiving service — improved version of archive.today.

Captures web pages using **4 strategies** in order:
1. **r.jina.ai reader proxy** — catches paywalled news (Bloomberg, NYT, WSJ)
2. **Scrapling StealthyFetcher** — headless browser for JS/Cloudflare sites
3. **Scrapling HTTP fetcher** — TLS fingerprint impersonation
4. **Direct curl** — fallback

Built with Flask + Scrapling. Deploy on Render.

## Quick Start

```bash
pip install -r requirements.txt
python3 app.py
# → http://localhost:5000
```

## API

```
POST /api/archive    — Archive a URL (X-API-Key header)
GET  /api/archive/ID — Retrieve archived content
```

## Deploy

Push to GitHub → Connect to Render.com → Set custom domain.
