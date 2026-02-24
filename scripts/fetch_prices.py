import json
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; JPSIbot/1.0)"

SOURCES = {
  "tanaka": "https://gold.tanaka.co.jp/commodity/souba/english/index.php",
  "tokuriki": "https://www.tokuriki-kanda.co.jp/goldetc/",
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
  Tokuriki page formatting can vary.
  We'll try multiple patterns:
  1) 銀 <number> 円/g
  2) シルバー <number> 円/g
  3) 銀 <number> 円 (no /g)
  and prefer the first match that looks like a realistic JPY/gram price.
  """
  soup = BeautifulSoup(html, "html.parser")
  text = soup.get_text(" ", strip=True)

  # Narrow search area near keywords if present
  anchor_idx = text.find("買取")
  tail = text[anchor_idx: anchor_idx + 4000] if anchor_idx != -1 else text

  patterns = [
    r"(?:銀|シルバー)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*円\s*/\s*g",
    r"(?:銀|シルバー)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*円/g",
    r"(?:銀|シルバー)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*円\s*g",
    r"(?:銀|シルバー)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*円",
  ]

  candidates = []
  for pat in patterns:
    for m in re.finditer(pat, tail):
      try:
        val = float(m.group(1))
        # sanity filter for plausible JPY/g silver range (avoid random years, phone numbers etc)
        if 50.0 <= val <= 2000.0:
          candidates.append(val)
      except Exception:
        pass
    if candidates:
      break

  if not candidates:
    raise ValueError("Tokuriki silver buyback not found")

  return candidates[0]

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

  # Tokuriki (no longer fatal)
  v, err = safe_get("tokuriki", lambda: parse_tokuriki_silver_buy(get_html(SOURCES["tokuriki"])))
  if err: out["errors"].append(err)
  if v is not None: out["prices_jpy_per_g"]["tokuriki_silver_buy"] = v

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
