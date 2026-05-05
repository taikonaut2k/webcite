#!/usr/bin/env python3
"""
Monitor umisake.com domain expiration and registration status.
Runs daily via cron. Alerts when status changes.
"""
import whois, json, os, smtplib
from datetime import datetime, timezone

DOMAIN = "umisake.com"
STATUS_FILE = os.path.expanduser("~/archive-clone/domain_status.json")

def check_domain():
    try:
        w = whois.whois(DOMAIN)
        now = datetime.now(timezone.utc)
        
        status = {
            "domain": DOMAIN,
            "checked_at": now.isoformat(),
            "registrar": str(w.registrar or "Unknown"),
            "creation_date": str(w.creation_date),
            "expiration_date": str(w.expiration_date),
            "updated_date": str(w.updated_date),
            "status": w.status,
            "name_servers": w.name_servers,
            "is_registered": bool(w.text and len(w.text) > 50),
            "dnssec": str(w.dnssec),
        }
        
        # Detect phase
        if status["is_registered"]:
            # Check if expired
            exp = w.expiration_date
            if isinstance(exp, list):
                exp = exp[0]
            if exp and exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            
            if exp and now > exp:
                days_overdue = (now - exp).days
                if days_overdue < 30:
                    status["phase"] = f"GRACE PERIOD (day {days_overdue}/30)"
                elif days_overdue < 60:
                    status["phase"] = f"REDEMPTION PERIOD (day {days_overdue-30}/30)"
                elif days_overdue < 65:
                    status["phase"] = "PENDING DELETE"
                else:
                    status["phase"] = "AVAILABLE FOR REGISTRATION"
            else:
                days_left = (exp - now).days if exp else "?"
                status["phase"] = f"REGISTERED ({days_left} days until expiry)"
        else:
            status["phase"] = "AVAILABLE FOR REGISTRATION"
        
        return status
    except Exception as e:
        return {"domain": DOMAIN, "error": str(e), "phase": "UNKNOWN"}

def load_previous():
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE) as f:
            return json.load(f)
    return None

def save_status(status):
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)

# ── Main ──
status = check_domain()
previous = load_previous()
save_status(status)

phase = status.get("phase", "UNKNOWN")
print(f"[{status['checked_at'][:19]}] umisake.com: {phase}")

# Detect changes
if previous and previous.get("phase") != phase:
    print(f"\n⚠ STATUS CHANGE DETECTED!")
    print(f"  Before: {previous.get('phase')}")
    print(f"  After:  {phase}")
    print(f"\n  === ALERT: umisake.com status changed! ===")
    print(f"  Previous: {previous.get('phase')}")
    print(f"  Current:  {phase}")
    print(f"  Check: https://www.godaddy.com/whois/results.aspx?domain=UMISAKE.COM")

# If available, alert immediately
if "AVAILABLE" in phase:
    print(f"\n🎯 DOMAIN IS AVAILABLE!")
    print(f"  Register immediately at: https://www.godaddy.com/domains/search?domainToBuy=umisake.com")
    print(f"  Or use a backorder service: https://www.dropcatch.com/domain/umisake.com")

# Print full status
print(f"\nFull status saved to: {STATUS_FILE}")
