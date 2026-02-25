import json
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; JPSIbot/1.0)"

SOURCES = {
  "tanaka": "https://gold.tanaka.co.jp/commodity/souba/english/index.php",
  "tokuriki": "https://www.tokuriki-kanda.co.jp/goldetc/market/",  # fixed: was /goldetc/
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

  # Typical English format: "SILVER <sell> yen ... <buy> yen"
  m = re.search(r"SILVER\s+(\d+(?:\.\d+)?)\s+yen.*?\s(\d+(?:\.\d+)?)\s+yen", text)
  if m:
    return float(m.group(2))  # buyback

  # Fallback: locate "SILVER" then take second number nearby
  idx = text.find("SILVER")
  if idx == -1:
    raise ValueError("SILVER row not found on Tanaka page")
  tail = text[idx: idx + 800]
  nums = re.findall(r"(\d+(?:\.\d+)?)", tail)
  if len(nums) < 2:
    raise ValueError("Could not parse Tanaka SILVER buyback")
  return float(nums[1])

def parse_tokuriki_silver_buy(html: str) -> float:
  """
  Parses the Tokuriki market page (/goldetc/market/) for silver buyback price.
  The page shows a table with 銀 row containing 小売価格 and 買取価格 in yen/g.
  We want 買取価格 (buyback).
  """
  soup = BeautifulSoup(html, "html.parser")
  text = soup.get_text(" ", strip=True)

  # Pattern 1: direct yen/g format near 銀 - look for two prices and take second (buy)
  # e.g. "銀 493.90円/g ... 476.85円/g"
  m = re.search(r"銀\s*[\s\S]{0,600}?(\d{3,4}\.\d{2})円/g[\s\S]{0,300}?(\d{3,4}\.\d{2})円/g", text)
  if m:
    val = float(m.group(2))
    if 50.0 <= val <= 5000.0:
      return val

  # Pattern 2: 買取価格 near 銀
  anchor = text.find("買取価格")
  if anchor != -1:
    tail = text[anchor: anchor + 2000]
    patterns = [
      r"銀\s*(?:\(g\))?\s*[\s\S]{0,200}?(\d{3,4}\.\d{2})",
      r"(\d{3,4}\.\d{2})\s*円",
    ]
    for pat in patterns:
      for m2 in re.finditer(pat, tail):
        val = float(m2.group(1))
        if 50.0 <= val <= 5000.0:
          return val

  # Pattern 3: kg price -> convert to per gram
  # Tokuriki sometimes shows 銀(kg) with price like "476,850"
  m3 = re.search(r"銀\(kg\)[\s\S]{0,400}?(\d{3,4}),(\d{3})[\s\S]{0,200}?(\d{3,4}),(\d{3})", text)
  if m3:
    buy_kg = float(m3.group(3) + m3.group(4))
    val = buy_kg / 1000.0
    if 50.0 <= val <= 5000.0:
      return val

  # Pattern 4: broad search for any per-gram silver price
  for pat in [
    r"(?:銀|シルバー)\s*[:：]?\s*(\d{3,4}\.\d{2})\s*円\s*/\s*g",
    r"(\d{3,4}\.\d{2})\s*円\s*/\s*g",
  ]:
    for m4 in re.finditer(pat, text):
      val = float(m4.group(1))
      if 50.0 <= val <= 5000.0:
        return val

  raise ValueError("Tokuriki silver buyback not found on market page")

def parse_nanboya_sv1000(html: str) -> float:
  """
  Nanboya shows silver in yen/g. We want the Sv1000 (.999 silver) buyback per gram.
  Typical format: price is shown as a number near Sv1000 or 純銀.
  Must be in realistic JPY/g range (300-2000), NOT a 4-digit standalone number like 1000.
  """
  soup = BeautifulSoup(html, "html.parser")
  text = soup.get_text(" ", strip=True)

  # Look for Sv1000 row: price should be a 3-4 digit number in realistic range
  # Pattern: Sv1000 followed by price and 円 (NOT just bare "1000")
  patterns = [
    r"Sv1000[\s\S]{0,200}?(\d{3,4}(?:\.\d+)?)\s*円\s*/\s*g",
    r"Sv1000[\s\S]{0,200}?(\d{3,4}(?:\.\d+)?)\s*円",
    r"純銀[\s\S]{0,200}?(\d{3,4}(?:\.\d+)?)\s*円\s*/\s*g",
    r"純銀[\s\S]{0,200}?(\d{3,4}(?:\.\d+)?)\s*円",
  ]

  for pat in patterns:
    m = re.search(pat, text)
    if m:
      val = float(m.group(1))
      # Filter: must be in realistic silver per-gram range, not a category code
      if 200.0 <= val <= 2000.0 and val != 1000.0:
        return val

  # Fallback: find any realistic ¥/g near 銀
  idx = text.find("銀")
  if idx != -1:
    tail = text[idx: idx + 1000]
    for m2 in re.finditer(r"(\d{3,4}(?:\.\d+)?)\s*円", tail):
      val = float(m2.group(1))
      if 200.0 <= val <= 2000.0 and val != 1000.0:
        return val

  raise ValueError("Nanboya Sv1000 realistic price not found")

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

  # Tanaka (RS)
  v, err = safe_get("tanaka", lambda: parse_tanaka_silver_buy(get_html(SOURCES["tanaka"])))
  if err: out["errors"].append(err)
  if v is not None: out["prices_jpy_per_g"]["tanaka_silver_buy"] = v

  # Tokuriki (RS)
  v, err = safe_get("tokuriki", lambda: parse_tokuriki_silver_buy(get_html(SOURCES["tokuriki"])))
  if err: out["errors"].append(err)
  if v is not None: out["prices_jpy_per_g"]["tokuriki_silver_buy"] = v

  # Nanboya (DB - pawn/dealer proxy)
  v, err = safe_get("nanboya", lambda: parse_nanboya_sv1000(get_html(SOURCES["nanboya"])))
  if err: out["errors"].append(err)
  if v is not None: out["prices_jpy_per_g"]["nanboya_sv1000"] = v

  # Daikichi (DB - pawn/dealer proxy)
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
