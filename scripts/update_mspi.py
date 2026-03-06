"""
update_mspi.py — Manually update MSPI-B street price
Usage: python scripts/update_mspi.py <price_jpy_per_g> [num_listings]
Example: python scripts/update_mspi.py 675.16 6
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
    if len(sys.argv) < 2:
        print("Usage: python scripts/update_mspi.py <price_jpy_per_g> [num_listings]")
        sys.exit(1)

    mspi_b = float(sys.argv[1])
    listings = int(sys.argv[2]) if len(sys.argv) > 2 else None

    if not (50 <= mspi_b <= 5000):
        print(f"Error: price {mspi_b} looks wrong (expected 50–5000 JPY/g)")
        sys.exit(1)

    # Load existing prices.json
    try:
        with open(PRICES_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"prices_jpy_per_g": {}, "errors": []}

    # Update MSPI-B
    data["prices_jpy_per_g"]["mercari_mspi_b"] = round(mspi_b, 2)
    if listings:
        data["prices_jpy_per_g"]["mercari_mspi_b_listings"] = listings

    now = datetime.now(timezone.utc)
    data["mspi_updated_at_utc"] = now.isoformat(timespec="seconds")

    with open(PRICES_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"prices.json updated — MSPI-B: ¥{mspi_b:.2f}/g")

    # Append to history
    usd_jpy = data["prices_jpy_per_g"].get("usd_jpy")
    comex_usd = data["prices_jpy_per_g"].get("comex_silver_usd_oz")
    comex_jpy_g = data["prices_jpy_per_g"].get("comex_silver_jpy_g")

    snapshot = {
        "t": now.isoformat(timespec="minutes"),
        "mspi_b_jpy_g": round(mspi_b, 2),
    }
    if listings:
        snapshot["listings"] = listings
    if comex_jpy_g:
        snapshot["comex_jpy_g"] = comex_jpy_g
    if comex_usd:
        snapshot["comex_usd"] = comex_usd
    if usd_jpy:
        snapshot["usd_jpy"] = usd_jpy

    # Premium %
    if comex_usd and usd_jpy:
        street_usd = (mspi_b * OZ) / usd_jpy
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
