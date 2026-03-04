import json
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; JPMIbot/1.0)"

SOURCES = {
  "tanaka":     "https://gold.tanaka.co.jp/commodity/souba/english/index.php",
  "nihon":      "https://material.co.jp/market.php",
  "mitsubishi": "https://gold.mmc.co.jp/market/silver-price/",
  "nanboya":    "https://nanboya.com/gold-kaitori/silver/silver-souba/",
  "daikichi":   "https://www.kaitori-daikichi.jp/list/gold/silver/souba/",
}

def get_comex_and_fx():
  """Fetch COMEX silver spot (USD/oz) and USD/JPY rate."""
  comex_usd = None
  usd_jpy = None

  # Try goldprice.org JSON feed (no API key required)
  try:
    r = requests.get(
      "https://data-asg.goldprice.org/dbXRates/USD",
      headers={"User-Agent": UA, "Referer": "https://goldprice.org/"},
      timeout=15
    )
    if r.ok:
      d = r.json()
      # Returns: {"items": [{"xagPrice": 32.45, ...}]}
      items = d.get("items", [])
      if items:
        val = items[0].get("xagPrice")
        if val and 10 < float(val) < 500:
          comex_usd = float(val)
  except Exception:
    pass

  # Fallback: scrape silverprice.org
  if not comex_usd:
    try:
      r = requests.get(
        "https://data-asg.goldprice.org/dbXRates/JPY",
        headers={"User-Agent": UA, "Referer": "https://goldprice.org/"},
        timeout=15
      )
      if r.ok:
        d = r.json()
        items = d.get("items", [])
        if items:
          # xagPrice is in JPY/oz when base is JPY
          jpy_oz = items[0].get("xagPrice")
          jpy_usd = items[0].get("usdXJpy")  # USD/JPY rate
          if jpy_usd and 50 < float(jpy_usd) < 300:
            usd_jpy = float(jpy_usd)
          if jpy_oz and jpy_usd and jpy_oz > 100:
            comex_usd = float(jpy_oz) / float(jpy_usd)
    except Exception:
      pass

  # USD/JPY: frankfurter (no key required)
  if not usd_jpy:
    try:
      r = requests.get("https://api.frankfurter.app/latest?from=USD&to=JPY", timeout=10)
      if r.ok:
        d = r.json()
        rate = d.get("rates", {}).get("JPY")
        if rate and 50 < float(rate) < 300:
          usd_jpy = float(rate)
    except Exception:
      pass

  return comex_usd, usd_jpy

def get_html(url, encoding=None):
  r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
  r.raise_for_status()
  if encoding:
    r.encoding = encoding
  return r.text

# ── Tanaka (English page, buyback = "TANAKA retail buying price" for SILVER) ─
def parse_tanaka(html):
  soup = BeautifulSoup(html, "html.parser")
  # Table layout: SILVER | retail_sell | sell_change | retail_buy | buy_change
  # Find the SILVER row in the first price table
  for table in soup.find_all("table"):
    for row in table.find_all("tr"):
      cells = row.find_all(["td","th"])
      if not cells:
        continue
      if cells[0].get_text(strip=True) == "SILVER" and len(cells) >= 4:
        # cells[0]=SILVER, [1]=sell, [2]=sell_change, [3]=buy, [4]=buy_change
        buy_text = cells[3].get_text(strip=True).replace(",", "").replace(" yen", "")
        val = float(re.search(r"[\d.]+", buy_text).group())
        if 50 <= val <= 5000:
          return val
  raise ValueError("Tanaka SILVER buyback row not found")

# ── Nihon Material (EUC-JP page, buyback = 買取 price for 銀) ─────────────
def parse_nihon(html):
  soup = BeautifulSoup(html, "html.parser")
  text = soup.get_text(" ", strip=True)
  # Table row: 銀 | 小売価格 diff | 買取価格 diff
  # Look for pattern: 銀 NNN.NN円 ±NNN.NN円 NNN.NN円
  m = re.search(
    r'銀\s+([\d,]+(?:\.\d+)?)\s*円.*?([\d,]+(?:\.\d+)?)\s*円',
    text
  )
  if m:
    buyback = float(m.group(2).replace(",", ""))
    if 50 <= buyback <= 5000:
      return buyback
  # Fallback: find all prices near 銀
  idx = text.find("銀")
  if idx != -1:
    tail = text[idx:idx+200]
    prices = re.findall(r'([\d,]+(?:\.\d+)?)\s*円', tail)
    candidates = [float(p.replace(",","")) for p in prices if 50 <= float(p.replace(",","")) <= 5000]
    if len(candidates) >= 2:
      return candidates[1]  # second price = buyback
    if candidates:
      return candidates[0]
  raise ValueError("Nihon Material silver buyback not found")

# ── Mitsubishi (HTML table, 店頭価格 row, buyback = 2nd plausible 円/g value) ─
def parse_mitsubishi(html):
  soup = BeautifulSoup(html, "html.parser")
  for table in soup.find_all("table"):
    for row in table.find_all("tr"):
      cells = row.find_all("td")
      if not cells:
        continue
      row_text = " ".join(c.get_text(strip=True) for c in cells)
      if "店頭価格" in row_text:
        # Filter to only plausible silver prices (50-5000 yen/g)
        # This excludes daily change values like 29.04
        prices = [
          float(p.replace(",", ""))
          for p in re.findall(r'([\d,]+(?:\.\d+)?)\s*円/g', row_text)
          if 50 <= float(p.replace(",", "")) <= 5000
        ]
        if len(prices) >= 2:
          return prices[1]  # index 0 = 小売, index 1 = 買取
        if len(prices) == 1:
          return prices[0]
  # Fallback: plain text scan
  text = soup.get_text(" ", strip=True)
  idx = text.find("店頭価格")
  if idx != -1:
    snippet = text[idx:idx+400]
    prices = [
      float(p.replace(",", ""))
      for p in re.findall(r'([\d,]+(?:\.\d+)?)\s*円/g', snippet)
      if 50 <= float(p.replace(",", "")) <= 5000
    ]
    if len(prices) >= 2:
      return prices[1]
    if prices:
      return prices[0]
  raise ValueError("Mitsubishi silver buyback not found")

# ── Nanboya (prices JS-rendered; parse expert commentary for today's price) ─
def parse_nanboya(html):
  soup = BeautifulSoup(html, "html.parser")
  text = soup.get_text(" ", strip=True)
  # Expert commentary contains e.g. "銀相場は489円と"
  m = re.search(r'銀相場は\s*([\d,]+)\s*円', text)
  if m:
    val = float(m.group(1).replace(",", ""))
    if 50 <= val <= 5000:
      return val
  # Fallback: look for "今日の買取相場価格" section with a price
  m = re.search(r'今日の買取相場価格.*?([\d,]+)\s*円', text, re.DOTALL)
  if m:
    val = float(m.group(1).replace(",",""))
    if 50 <= val <= 5000:
      return val
  raise ValueError("Nanboya silver price not found")

# ── Daikichi (plain HTML, SV1000 NNN円) ───────────────────────────────────
def parse_daikichi(html):
  soup = BeautifulSoup(html, "html.parser")
  text = soup.get_text(" ", strip=True)
  # "SV1000\n482円" or "SV1000 482円"
  m = re.search(r'SV1000\D{0,10}?([\d,]+)\s*円', text)
  if m:
    val = float(m.group(1).replace(",",""))
    if 50 <= val <= 5000:
      return val
  raise ValueError("Daikichi SV1000 price not found")

def safe_get(name, fn):
  try:
    return fn(), None
  except Exception as e:
    return None, f"{name}: {type(e).__name__}: {e}"

def main():
  out = {
    "updated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "prices_jpy_per_g": {},
    "errors": [],
    "sources": SOURCES,
  }

def main():
  out = {
    "updated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "prices_jpy_per_g": {},
    "errors": [],
    "sources": SOURCES,
  }

  # Fetch COMEX silver price and USD/JPY rate
  comex_usd, usd_jpy = get_comex_and_fx()
  if comex_usd:
    out["prices_jpy_per_g"]["comex_silver_usd_oz"] = comex_usd
  if usd_jpy:
    out["prices_jpy_per_g"]["usd_jpy"] = usd_jpy
  if comex_usd and usd_jpy:
    out["prices_jpy_per_g"]["comex_silver_jpy_g"] = round((comex_usd * usd_jpy) / 31.1035, 2)

  v, err = safe_get("tanaka",     lambda: parse_tanaka(get_html(SOURCES["tanaka"])))
  if err: out["errors"].append(err)
  if v:   out["prices_jpy_per_g"]["tanaka_silver_buy"] = v

  v, err = safe_get("nihon",      lambda: parse_nihon(get_html(SOURCES["nihon"], encoding="euc-jp")))
  if err: out["errors"].append(err)
  if v:   out["prices_jpy_per_g"]["nihon_silver_buy"] = v

  v, err = safe_get("mitsubishi", lambda: parse_mitsubishi(get_html(SOURCES["mitsubishi"])))
  if err: out["errors"].append(err)
  if v:   out["prices_jpy_per_g"]["mitsubishi_silver_buy"] = v

  v, err = safe_get("nanboya",    lambda: parse_nanboya(get_html(SOURCES["nanboya"])))
  if err: out["errors"].append(err)
  if v:   out["prices_jpy_per_g"]["nanboya_sv1000"] = v

  v, err = safe_get("daikichi",   lambda: parse_daikichi(get_html(SOURCES["daikichi"])))
  if err: out["errors"].append(err)
  if v:   out["prices_jpy_per_g"]["daikichi_sv1000"] = v

  with open("prices.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

  print("prices.json updated:", out["prices_jpy_per_g"])
  if out["errors"]:
    print("Warnings:")
    for e in out["errors"]:
      print(" -", e)

if __name__ == "__main__":
  main()
