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

def get_html(url, encoding=None):
  r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
  r.raise_for_status()
  if encoding:
    r.encoding = encoding
  return r.text

# ── Tanaka (English page, buyback = "TANAKA retail buying price" for SILVER) ─
def parse_tanaka(html):
  soup = BeautifulSoup(html, "html.parser")
  for table in soup.find_all("table"):
    for row in table.find_all("tr"):
      cells = row.find_all(["td","th"])
      if not cells:
        continue
      if cells[0].get_text(strip=True) == "SILVER" and len(cells) >= 4:
        buy_text = cells[3].get_text(strip=True).replace(",", "").replace(" yen", "")
        val = float(re.search(r"[\d.]+", buy_text).group())
        if 50 <= val <= 5000:
          return val
  raise ValueError("Tanaka SILVER buyback row not found")

# ── Nihon Material ────────────────────────────────────────────────────────────
def parse_nihon(html):
  soup = BeautifulSoup(html, "html.parser")
  text = soup.get_text(" ", strip=True)
  m = re.search(
    r'銀\s+([\d,]+(?:\.\d+)?)\s*円.*?([\d,]+(?:\.\d+)?)\s*円',
    text
  )
  if m:
    buyback = float(m.group(2).replace(",", ""))
    if 50 <= buyback <= 5000:
      return buyback
  idx = text.find("銀")
  if idx != -1:
    tail = text[idx:idx+200]
    prices = re.findall(r'([\d,]+(?:\.\d+)?)\s*円', tail)
    candidates = [float(p.replace(",","")) for p in prices if 50 <= float(p.replace(",","")) <= 5000]
    if len(candidates) >= 2:
      return candidates[1]
    if candidates:
      return candidates[0]
  raise ValueError("Nihon Material silver buyback not found")

# ── Mitsubishi ────────────────────────────────────────────────────────────────
def parse_mitsubishi(html):
  soup = BeautifulSoup(html, "html.parser")
  for table in soup.find_all("table"):
    for row in table.find_all("tr"):
      cells = row.find_all("td")
      if not cells:
        continue
      row_text = " ".join(c.get_text(strip=True) for c in cells)
      if "店頭価格" in row_text:
        prices = [
          float(p.replace(",", ""))
          for p in re.findall(r'([\d,]+(?:\.\d+)?)\s*円/g', row_text)
          if 50 <= float(p.replace(",", "")) <= 5000
        ]
        if len(prices) >= 2:
          return prices[1]
        if len(prices) == 1:
          return prices[0]
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

# ── Nanboya ───────────────────────────────────────────────────────────────────
def parse_nanboya(html):
  soup = BeautifulSoup(html, "html.parser")
  text = soup.get_text(" ", strip=True)

  # Method 1: expert commentary "銀相場はXXX円"
  m = re.search(r'銀相場は\s*([\d,]+)\s*円', text)
  if m:
    val = float(m.group(1).replace(",", ""))
    if 50 <= val <= 5000:
      return val

  # Method 2: look for price near "銀" that is plausible yen/g value
  # Must be > 100 to avoid accidentally grabbing "1000" purity marker
  for pattern in [
    r'銀[^\d]{0,30}?([\d,]+(?:\.\d+)?)\s*円/g',
    r'銀[^\d]{0,30}?([\d,]+(?:\.\d+)?)\s*円',
  ]:
    matches = re.findall(pattern, text)
    for m in matches:
      val = float(m.replace(",", ""))
      # Must be plausible yen/g silver price — NOT 1000 (purity marker)
      if 100 <= val <= 999:
        return val

  # Method 3: 今日の買取相場価格
  m = re.search(r'今日の買取相場価格.*?([\d,]+)\s*円', text, re.DOTALL)
  if m:
    val = float(m.group(1).replace(",",""))
    if 100 <= val <= 999:
      return val

  raise ValueError("Nanboya silver price not found")

# ── Daikichi ──────────────────────────────────────────────────────────────────
def parse_daikichi(html):
  soup = BeautifulSoup(html, "html.parser")
  text = soup.get_text(" ", strip=True)
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

  v, err = safe_get("tanaka", lambda: parse_tanaka(get_html(SOURCES["tanaka"])))
  if err: out["errors"].append(err)
  if v:   out["prices_jpy_per_g"]["tanaka_silver_buy"] = v

  v, err = safe_get("nihon", lambda: parse_nihon(get_html(SOURCES["nihon"], encoding="euc-jp")))
  if err: out["errors"].append(err)
  if v:   out["prices_jpy_per_g"]["nihon_silver_buy"] = v

  v, err = safe_get("mitsubishi", lambda: parse_mitsubishi(get_html(SOURCES["mitsubishi"])))
  if err: out["errors"].append(err)
  if v:   out["prices_jpy_per_g"]["mitsubishi_silver_buy"] = v

  v, err = safe_get("nanboya", lambda: parse_nanboya(get_html(SOURCES["nanboya"])))
  if err: out["errors"].append(err)
  if v:   out["prices_jpy_per_g"]["nanboya_sv1000"] = v

  v, err = safe_get("daikichi", lambda: parse_daikichi(get_html(SOURCES["daikichi"])))
  if err: out["errors"].append(err)
  if v:   out["prices_jpy_per_g"]["daikichi_sv1000"] = v

  with open("prices.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

  print("prices.json updated:")
  for k, v in out["prices_jpy_per_g"].items():
    print(f"  {k}: {v}")
  if out["errors"]:
    print("Warnings:")
    for e in out["errors"]:
      print(" -", e)

if __name__ == "__main__":
  main()
