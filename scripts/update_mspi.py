"""
update_mspi.py — Update MSPI-B street price
Reads listings from environment variables set by GitHub Actions.
"""

import json
import os
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICES_JSON = os.path.join(REPO_ROOT, "prices.json")
HISTORY_JSON = os.path.join(REPO_ROOT, "prices-history.json")

OZ = 31.1035

def main():
    # Build listings from environment variables L1_COIN/L1_JPY/L1_JPYG etc.
    listings = []
    for i in range(1, 11):
        coin  = os.environ.get(f"L{i}_COIN", "").strip()
        jpy   = os.environ.get(f"L{i}_JPY",  "").strip()
        jpy_g = os.environ.get(f"L{i}_JPYG", "").strip()
        if not coin or not jpy or not jpy_g:
            continue
        try:
            listings.append({
                "coin":  coin,
                "jpy":   int(float(jpy)),
                "jpy_g": round(float(jpy_g), 2)
            })
        except ValueError:
            print(f"Skipping listing {i} — invalid values")

    if not listings:
        print("Error: no valid listings found in environment variables")
        sys.exit(1)

    mspi_b  = round(sum(l["jpy_g"] for l in listings) / len(listings), 2)
    avg_jpy = round(sum(l["jpy"]   for l in listings) / len(listings))

    print(f"Listings: {len(listings)}")
    print(f"MSPI-B:   ¥{mspi_b}/g")

    # Load existing prices.json
    try:
        with open(PRICES_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"prices_jpy_per_g": {}, "errors": []}

    now   = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    data["prices_jpy_per_g"]["mercari_mspi_b"]           = mspi_b
    data["prices_jpy_per_g"]["mercari_mspi_b_listings"]  = len(listings)
    data["prices_jpy_per_g"]["mercari_mspi_b_avg_jpy"]   = avg_jpy
    data["mercari_listings"]    = listings
    data["mspi_updated_at_utc"] = now.isoformat(timespec="seconds")
    data["mspi_updated_date"]   = today

    with open(PRICES_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("prices.json updated")

    # Append snapshot to history
    p         = data["prices_jpy_per_g"]
    usd_jpy   = p.get("usd_jpy")
    comex_usd = p.get("comex_silver_usd_oz")
    comex_jpy_g = p.get("comex_silver_jpy_g")

    snapshot = {
        "t":            now.isoformat(timespec="minutes"),
        "mspi_b_jpy_g": mspi_b,
        "listings":     len(listings),
    }
    if comex_jpy_g:  snapshot["comex_jpy_g"]  = comex_jpy_g
    if comex_usd:    snapshot["comex_usd"]     = comex_usd
    if usd_jpy:      snapshot["usd_jpy"]       = usd_jpy
    if comex_usd and usd_jpy:
        street_usd  = (mspi_b * OZ) / usd_jpy
        premium_pct = (street_usd - comex_usd) / comex_usd * 100
        snapshot["premium_pct"] = round(premium_pct, 2)
        print(f"Premium vs COMEX: {premium_pct:+.2f}%")

    try:
        with open(HISTORY_JSON, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        history = []

    history.append(snapshot)
    if len(history) > 4320:
        history = history[-4320:]

    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",", ":"))

    print(f"prices-history.json updated — {len(history)} total entries")

if __name__ == "__main__":
    main()
