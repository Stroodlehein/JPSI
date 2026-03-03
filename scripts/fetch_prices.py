import json
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; JPMIbot/1.0)"

SOURCES = {
  "tanaka": "https://gold.tanaka.co.jp/commodity/souba/english/index.php",
  "mitsubishi": "https://gold.mmc.co.jp/market/silver-price/",
  "nanboya": "https://nanboya.com/gold-kaitori/silver/silver-souba/",
  "daikichi": "https://www.kaitori-daikichi.jp/list/gold/silver/souba/",
}

def get_html(url: str) -> str:
  r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
  r.raise_for_status()
  return r.text

def parse_tanaka_silver_buy(html: str) -> float:
  soup = BeautifulSoup(html, "html.parser")
  text = soup.get_text(" ", strip=True)
  m = re.search(r"SILVER\s+(\d+(?:\.\d+)?)\s+yen.*?\s(\d+(?:\.\d+)?)\s+yen", text)
  if m:
    return float(m.group(2))
  idx = text.find("SILVER")
  if idx == -1:
    raise ValueError("SILVER row not found on Tanaka page")
  tail = text[idx: idx + 800]
  nums = re.findall(r"(\d+(?:\.\d+)?)", tail)
  if len(nums) < 2:
    raise ValueError("Could not parse Tanaka SILVER buyback")
  return float(nums[1])

def parse_mitsubishi_silver_buy(html: str) -> float:
  """
  Parse Mitsubishi GOLDPARK silver buyback price (買取価格).
  Targets the 店頭価格 row in the 最新の価格 table.
  Row format: 店頭価格 | 小売価格 | 前日比 | 買取価格 | 前日比
  """
  soup = BeautifulSoup(html, "html.parser")
  tables = soup.find_all("table")
  for table in tables:
    rows = table.find_all("tr")
    for row in rows:
      cells = row.find_all("td")
      if not cells:
        continue
      row_text = " ".join(c.get_text(strip=True) for c in cells)
      if "店頭価格" in row_text:
        prices = re.findall(r"([\d,]+(?:\.\d+)?)\s*円/g", row_text)
        if len(prices) >= 2:
          buyback = float(prices[1].replace(",", ""))
          if 50.0 <= buyback <= 5000.0:
            return buyback
  # Fallback
  text = soup.get_text(" ", strip=True)
  idx = text.find("店頭価格")
  if idx != -1:
    tail = text[idx: idx + 300]
    prices = re.findall(r"([\d,]+(?:\.\d+)?)\s*円/g", tail)
    if len(prices) >= 2:
      buyback = float(prices[1].replace(",", ""))
      if 50.0 <= buyback <= 5000.0:
        return buyback
  raise ValueError("Mitsubishi silver buyback price not found")

def parse_nanboya_sv1000(html: str) -> float:
  soup = BeautifulSoup(html, "html.parser")
  text = soup.get_text(" ", strip=True)
  m = re.search(r"Sv1000.*?(\d{2,4})\s*円", text)
  if not m:
    raise ValueError("Nanboya Sv1000 price not found")
  return float(m.group(1))

def parse_daikichi_sv1000(html: str) -> float:
  soup = BeautifulSoup(html, "html.parser")
  text = soup.get_text(" ", strip=True)
  m = re.search(r"1g\s+(\d{2,4})\s*円", text)
  if not m:
    raise ValueError("Daikichi 1g price not found")
  return float(m.group(1))

def safe_get(name: str, fn):
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

  # Tanaka
  v, err = safe_get("tanaka", lambda: parse_tanaka_silver_buy(get_html(SOURCES["tanaka"])))
  if err: out["errors"].append(err)
  if v is not None: out["prices_jpy_per_g"]["tanaka_silver_buy"] = v

  # Mitsubishi (replaces Tokuriki)
  v, err = safe_get("mitsubishi", lambda: parse_mitsubishi_silver_buy(get_html(SOURCES["mitsubishi"])))
  if err: out["errors"].append(err)
  if v is not None: out["prices_jpy_per_g"]["mitsubishi_silver_buy"] = v

  # Nanboya
  v, err = safe_get("nanboya", lambda: parse_nanboya_sv1000(get_html(SOURCES["nanboya"])))
  if err: out["errors"].append(err)
  if v is not None: out["prices_jpy_per_g"]["nanboya_sv1000"] = v

  # Daikichi
  v, err = safe_get("daikichi", lambda: parse_daikichi_sv1000(get_html(SOURCES["daikichi"])))
  if err: out["errors"].append(err)
  if v is not None: out["prices_jpy_per_g"]["daikichi_sv1000"] = v

  with open("prices.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

  print("prices.json updated:", out["prices_jpy_per_g"])
  if out["errors"]:
    print("Warnings:")
    for e in out["errors"]:
      print(" -", e)

if __name__ == "__main__":
  main()
