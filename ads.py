"""
WebCite ad manager.

Config-driven advertising layer. Reads from environment variables:

  AD_NETWORK   - "adsense" | "adsterra" | "aads" | "none" (default)
  AD_CLIENT    - AdSense publisher ID (e.g. ca-pub-XXXXXXXXXXXXXXXX)
  AD_SLOT_TOP  - AdSense slot ID for the top banner
  AD_SLOT_MID  - AdSense slot ID for in-content ads

Behavior:
  - If a network + client are configured, real ad code is rendered.
  - Otherwise, "house ads" are shown — self-promotion slots that
    push the Premium API tier (our actual revenue stream). House ads
    are income with zero external dependency and zero policy risk.
  - Ads are lazy-loaded client-side (IntersectionObserver) so they
    NEVER slow down initial page render — consistent with our
    speed-first design.
"""

import os

def ad_config():
    """Return dict of ad settings from environment."""
    network = os.environ.get("AD_NETWORK", "none").lower()
    client = os.environ.get("AD_CLIENT", "").strip()
    real_network = network in ("adsense", "adsterra", "aads") and client != ""
    return {
        "network": network,
        "client": client,
        "slot_top": os.environ.get("AD_SLOT_TOP", "").strip(),
        "slot_mid": os.environ.get("AD_SLOT_MID", "").strip(),
        "ads_on": True,  # ad slots always render — house ads by default
        "real_network": real_network,
        "enabled": real_network,
    }

def render_ad(slot="top"):
    """
    Return HTML for an ad slot. Falls back to a house ad.
    slot: "top" (leaderboard) or "mid" (in-content rectangle).
    """
    cfg = ad_config()
    slot_id = cfg.get("slot_top") if slot == "top" else cfg.get("slot_mid")

    # ── House ad (default): promote our own Premium API tier ──
    if not cfg.get("real_network"):
        if slot == "top":
            return """<div class="house-ad" data-slot="top" id="houseAdTop">
  <div class="house-ad-inner">
    <div class="house-ad-icon">⚡</div>
    <div class="house-ad-text">
      <strong>Need to archive at scale?</strong>
      <span>The WebCite API lets your tools archive pages programmatically — 60 captures/min, priority queue, bulk support.</span>
    </div>
    <a class="house-ad-cta" href="/account">Try the API →</a>
  </div>
</div>"""
        return """<div class="house-ad" data-slot="mid" id="houseAdMid">
  <div class="house-ad-inner">
    <div class="house-ad-text">
      <strong>⚡ WebCite API</strong>
      <span>Bulk archiving for developers &amp; researchers. From $9/mo.</span>
    </div>
    <a class="house-ad-cta" href="/account">Learn more</a>
  </div>
</div>"""

    # ── Real ad network code ──
    if cfg["network"] == "adsense":
        if slot == "top":
            return f"""<div class="ad-slot ad-slot-top" id="adSlotTop" data-client="{cfg['client']}" data-slot="{slot_id}">
  <ins class="adsbygoogle" style="display:block" data-ad-client="{cfg['client']}" data-ad-slot="{slot_id}" data-ad-format="auto" data-full-width-responsive="true"></ins>
</div>"""
        return f"""<div class="ad-slot ad-slot-mid" id="adSlotMid" data-client="{cfg['client']}" data-slot="{slot_id}">
  <ins class="adsbygoogle" style="display:block" data-ad-client="{cfg['client']}" data-ad-slot="{slot_id}" data-ad-format="auto" data-full-width-responsive="true"></ins>
</div>"""

    if cfg["network"] == "adsterra":
        # Adsterra banner placeholder — paste their script via env AD_SCRIPT_TOP/MID if needed
        return f"""<div class="ad-slot" id="adSlot{slot.title()}">
  <script async="async" data-cfasync="false" src="//pl{cfg['client']}.adsterratrade.com/{slot_id}/banner.js"></script>
</div>"""

    if cfg["network"] == "aads":
        # A-ADS (crypto ad network) — simple iframe-based
        return f"""<div class="ad-slot" id="adSlot{slot.title()}">
  <iframe src="https://aads.com/display/{slot_id}?client={cfg['client']}" width="728" height="90" frameborder="0" scrolling="no"></iframe>
</div>"""

    return ""  # none


def ad_head_scripts():
    """Any scripts needed in <head> for the configured network."""
    cfg = ad_config()
    if not cfg.get("real_network"):
        return ""
    if cfg["network"] == "adsense":
        return f"""<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={cfg['client']}" crossorigin="anonymous"></script>"""
    return ""
