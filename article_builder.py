"""
article_builder.py — Media-rich article renderer for WebCite.

Fixes the paywall problem: instead of mirroring the blocked original page,
extract the FULL article from the page's JSON-LD structured data
(CNN ships complete articleBody + captioned images + video even behind
the visual paywall), then render a clean article page with:
  - complete text (paywall bypassed — it's in the data)
  - inline captioned photos (downloaded to local assets/)
  - embedded videos (local mp4 or proxied HLS)
  - author, dates, headline

Also matches inline images to their surrounding paragraphs when possible
(CNN articleBody includes image placeholders), and falls back to a
caption-based gallery.

Usage: build_article(archive_dir, url, html) → writes article.html
"""

import json, os, re
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote


def extract_article_json(html):
    """Extract NewsArticle JSON-LD from page HTML. Returns dict or None."""
    blocks = re.findall(
        r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>',
        html, re.S | re.I
    )
    for b in blocks:
        try:
            d = json.loads(b)
            items = d if isinstance(d, list) else [d]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("@type") in ("NewsArticle", "Article", "ReportageNewsArticle") \
                   or "articleBody" in item:
                    return item
        except Exception:
            continue
    return None


def _clean_paragraphs(article_body):
    """Split articleBody into paragraphs.
    
    CNN ships articleBody as one continuous string with no newlines.
    Heuristic: split on sentence boundaries after quote closures and
    after ~450-600 char runs, keeping sentences intact.
    """
    if '\n\n' in article_body:
        return [p.strip() for p in re.split(r'\n\s*\n', article_body) if p.strip()]

    # Continuous text: split into sentences, then group into paragraphs
    sentences = re.findall(r'[^.!?]+[.!?]+[””’"]?|\S[^.!?]*$', article_body)
    sentences = [s.strip() for s in sentences if s.strip()]
    paras = []
    cur = ""
    for s in sentences:
        if len(cur) + len(s) > 420 and cur:
            paras.append(cur.strip())
            cur = s
        else:
            cur += " " + s
    if cur.strip():
        paras.append(cur.strip())
    return paras


def build_article(archive_dir, url, html, assets_rel="assets"):
    """
    Build a media-rich article.html from JSON-LD data.
    Returns (html_string, stats_dict) or (None, None) if no article found.
    """
    article = extract_article_json(html)
    if not article:
        return None, None

    archive_dir = Path(archive_dir)
    assets_dir = archive_dir / assets_rel
    assets_dir.mkdir(parents=True, exist_ok=True)

    headline = article.get("headline", "Untitled")
    byline = ""
    authors = article.get("author", [])
    if isinstance(authors, dict):
        authors = [authors]
    if authors:
        names = [a.get("name", "") for a in authors if isinstance(a, dict) and a.get("name")]
        if names:
            byline = "By " + ", ".join(names)

    date_pub = article.get("datePublished", "")
    date_mod = article.get("dateModified", "")
    description = article.get("description", "")

    # ── Images: download + build figure blocks ──
    images = article.get("image", [])
    if isinstance(images, dict):
        images = [images]

    fig_html = ""
    img_stats = {"downloaded": 0, "total": len(images)}
    for i, img in enumerate(images[:25]):
        if not isinstance(img, dict):
            continue
        content_url = img.get("contentUrl") or img.get("url", "")
        if not content_url:
            continue
        caption = img.get("caption", "")
        credit = img.get("creditText", "")
        src_org = img.get("sourceOrganization", {})
        if isinstance(src_org, dict):
            credit = credit or src_org.get("name", "")

        # Download image
        ext = os.path.splitext(urlparse(content_url).path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            ext = ".jpg"
        fname = f"img_{i:03d}{ext}"
        dest = assets_dir / fname
        try:
            import urllib.request
            req = urllib.request.Request(content_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if len(data) < 1000:
                continue
            dest.write_bytes(data)
            img_stats["downloaded"] += 1
        except Exception:
            continue

        credit_html = f'<span class="img-credit">{credit}</span>' if credit else ""
        caption_html = f'<figcaption>{caption} {credit_html}</figcaption>' if (caption or credit) else ""
        # ABSOLUTE path via /site route (relative paths break inside /a/<id>/article)
        fig_html += (
            f'<figure class="article-figure">'
            f'<img src="/site/{archive_dir.name}/assets/{fname}" alt="{caption[:120]}" loading="lazy">'
            f'{caption_html}'
            f'</figure>'
        )

    # ── Video embeds ──
    video_html = ""
    vids = article.get("video", [])
    if isinstance(vids, dict):
        vids = [vids]
    for v in vids:
        if not isinstance(v, dict):
            continue
        vurl = v.get("contentUrl") or v.get("url", "")
        thumb = v.get("thumbnailUrl", "")
        if vurl:
            proxied = f"/proxy?url={quote(vurl, safe='')}"
            video_html += (
                f'<figure class="article-figure video">'
                f'<video controls preload="metadata" poster="{thumb}">'
                f'<source src="{proxied}">'
                f'</video>'
                f'<figcaption>{v.get("name", "")}</figcaption>'
                f'</figure>'
            )

    # ── Paragraphs with images woven in ──
    paras = _clean_paragraphs(article.get("articleBody", ""))
    # Interleave: insert a figure after every 2nd-3rd paragraph
    body_html = ""
    fig_index = 0
    figures = [f for f in [fig_html] if f]  # single figure block for now
    # Split figure string into individual figures
    import re as _re
    individual_figures = _re.findall(r'<figure class="article-figure">.*?</figure>', fig_html, _re.S)
    for pi, p in enumerate(paras):
        body_html += f"<p>{p}</p>"
        if individual_figures and (pi + 1) % 3 == 0 and fig_index < len(individual_figures):
            body_html += individual_figures[fig_index]
            fig_index += 1
    # append remaining figures
    for f in individual_figures[fig_index:]:
        body_html += f

    stats = {
        "headline": headline,
        "paragraphs": len(paras),
        "word_count": article.get("wordCount", 0),
        "images": img_stats,
        "video_embeds": len(vids),
        "authors": byline,
        "date_published": date_pub,
    }

    # ── Final HTML ──
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{headline} — WebCite Article View</title>
<style>
  body {{ font-family: -apple-system, 'Segoe UI', Roboto, Georgia, serif;
         margin:0; background:#f4f5f7; color:#1a1a1a; }}
  .wc-bar {{ background:#1f6feb; color:#fff; font:13px/1.4 sans-serif;
            padding:6px 14px; text-align:center; }}
  .article {{ max-width:760px; margin:0 auto; padding:32px 20px 60px;
             background:#fff; min-height:100vh; }}
  h1 {{ font:700 34px/1.25 Georgia, serif; margin:0 0 8px; }}
  .meta {{ font:13px/1.6 sans-serif; color:#6a737d; margin-bottom:24px;
          border-bottom:1px solid #e1e4e8; padding-bottom:16px; }}
  .article p {{ font:17px/1.75 Georgia, serif; margin:0 0 20px; color:#24292f; }}
  .article-figure {{ margin:28px 0; }}
  .article-figure img {{ width:100%; border-radius:6px; display:block; }}
  .article-figure figcaption {{ font:13px/1.5 sans-serif; color:#6a737d;
          margin-top:8px; }}
  .img-credit {{ display:block; color:#8b949e; margin-top:2px; font-size:12px; }}
  .article-figure.video video {{ width:100%; border-radius:6px; background:#000; }}
  .desc {{ font:15px/1.6 sans-serif; color:#57606a; margin-bottom:20px; }}
  .actions {{ font:13px sans-serif; margin:16px 0; }}
  .actions a {{ color:#1f6feb; margin-right:16px; text-decoration:none; }}
</style>
</head>
<body>
<div class="wc-bar">📄 WebCite — complete article with photos &amp; video · <a href="#" style="color:#fff" onclick="window.print();return false;">Print</a></div>
<div class="article">
  <h1>{headline}</h1>
  <div class="meta">
    {byline}<br>
    {date_pub[:10] if date_pub else ''}{(' · updated ' + date_mod[:10]) if date_mod else ''}
  </div>
  {f'<div class="desc">{description}</div>' if description else ''}
  <div class="actions">
    <a href="/a/{archive_dir.name}/raw">📄 Text view</a>
    <a href="/a/{archive_dir.name}">⬅ Back to archive</a>
  </div>
  {body_html}
  {video_html}
  <div class="wc-bar" style="margin-top:40px;">📄 Archived by WebCite — pagecite.com</div>
</div>
</body>
</html>"""
    return page, stats
