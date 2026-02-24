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

  m = re.search(r"SILVER\s+(\d+(?:\.\d+)?)\s+yen.*?\s(\d+(?:\.\d+)?)\s+yen", text)
  if m:
    return float(m.group(2))

  idx = text.find("SILVER")
  if idx == -1:
    raise ValueError("SILVER row not found on Tanaka page")
  tail = text[idx: idx + 600]
  nums = re.findall(r"(\d+(?:\.\d+)?)", tail)
  if len(nums) < 2:
    raise ValueError("Could not parse Tanaka SILVER buyback")
  return float(nums[1])

def parse_tokuriki_silver_buy(html: str) -> float:
  soup = BeautifulSoup(html, "html.parser")
  text = soup.get_text(" ", strip=True)

  idx = text.find("買取価格")
  tail = text[idx: idx + 1500] if idx != -1 else text
  m = re.search(r"銀\s+(\d+(?:\.\d+)?)円/g", tail)
  if not m:
    raise ValueError("Tokuriki silver buyback not found")
  return float(m.group(1))

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

def main():
  out = {
    "updated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "prices_jpy_per_g": {},
    "sources": SOURCES,
  }

  out["prices_jpy_per_g"]["tanaka_silver_buy"] = parse_tanaka_silver_buy(get_html(SOURCES["tanaka"]))
  out["prices_jpy_per_g"]["tokuriki_silver_buy"] = parse_tokuriki_silver_buy(get_html(SOURCES["tokuriki"]))
  out["prices_jpy_per_g"]["nanboya_sv1000"] = parse_nanboya_sv1000(get_html(SOURCES["nanboya"]))
  out["prices_jpy_per_g"]["daikichi_sv1000"] = parse_daikichi_sv1000(get_html(SOURCES["daikichi"]))

  with open("prices.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

  print("prices.json updated:", out["prices_jpy_per_g"])

if __name__ == "__main__":
  main()
