import json
import requests
from datetime import datetime, timezone

UA = "Mozilla/5.0 (compatible; JPMIbot/1.0)"

def get_comex_and_fx():
  comex_usd = None
  usd_jpy = None

  # Primary: goldprice.org USD feed
  try:
    r = requests.get(
      "https://data-asg.goldprice.org/dbXRates/USD",
      headers={"User-Agent": UA, "Referer": "https://goldprice.org/"},
      timeout=15
    )
    if r.ok:
      items = r.json().get("items", [])
      if items:
        val = items[0].get("xagPrice")
        if val and 10 < float(val) < 500:
          comex_usd = float(val)
  except Exception:
    pass

  # Fallback: goldprice.org JPY feed (also gives USD/JPY rate)
  try:
    r = requests.get(
      "https://data-asg.goldprice.org/dbXRates/JPY",
      headers={"User-Agent": UA, "Referer": "https://goldprice.org/"},
      timeout=15
    )
    if r.ok:
      items = r.json().get("items", [])
      if items:
        jpy_usd = items[0].get("usdXJpy")
        if jpy_usd and 50 < float(jpy_usd) < 300:
          usd_jpy = float(jpy_usd)
        if not comex_usd:
          jpy_oz = items[0].get("xagPrice")
          if jpy_oz and jpy_usd and jpy_oz > 100:
            comex_usd = float(jpy_oz) / float(jpy_usd)
  except Exception:
    pass

  # USD/JPY fallback: frankfurter
  if not usd_jpy:
    try:
      r = requests.get("https://api.frankfurter.app/latest?from=USD&to=JPY", timeout=10)
      if r.ok:
        rate = r.json().get("rates", {}).get("JPY")
        if rate and 50 < float(rate) < 300:
          usd_jpy = float(rate)
    except Exception:
      pass

  return comex_usd, usd_jpy

def main():
  # Load existing prices.json
  try:
    with open("prices.json", "r", encoding="utf-8") as f:
      data = json.load(f)
  except Exception:
    data = {"prices_jpy_per_g": {}, "errors": []}

  comex_usd, usd_jpy = get_comex_and_fx()

  if comex_usd:
    data["prices_jpy_per_g"]["comex_silver_usd_oz"] = round(comex_usd, 4)
    print(f"COMEX: ${comex_usd:.4f}/oz")
  else:
    print("COMEX: failed to fetch")

  if usd_jpy:
    data["prices_jpy_per_g"]["usd_jpy"] = round(usd_jpy, 4)
    print(f"USD/JPY: ¥{usd_jpy:.4f}")
  else:
    print("USD/JPY: failed to fetch")

  if comex_usd and usd_jpy:
    data["prices_jpy_per_g"]["comex_silver_jpy_g"] = round((comex_usd * usd_jpy) / 31.1035, 2)

  # Update the COMEX timestamp separately so we know when it last refreshed
  data["comex_updated_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

  with open("prices.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

  print("prices.json updated with COMEX data")

if __name__ == "__main__":
  main()
