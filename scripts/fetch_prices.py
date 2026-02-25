"""
JPMI Price Fetcher
Fetches silver prices from Japanese domestic sources and writes prices.json

Sources:
  RS (Refinery Spot):
    - Tanaka Kikinzoku     — gold.tanaka.co.jp (English page, UTF-8)
    - Nihon Material       — material.co.jp/market.php (EUC-JP, static HTML)
    - Mitsubishi Materials — gold.mmc.co.jp/market/silver-price/ (UTF-8, static HTML)
                             NOTE: Mitsubishi publishes RETAIL sell price + buyback via
                             savings scheme only (no physical silver OTC buyback).
                             We capture both small_retail and buyback for transparency.
  DB (Dealer / Pawn Bid):
    - Nanboya              — nanboya.com/gold-kaitori/silver/silver-souba/
    - Daikichi             — kaitori-daikichi.jp/list/gold/silver/souba/
"""

import json
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; JPMIbot/1.0)"

SOURCES = {
    "tanaka":     "https://gold.tanaka.co.jp/commodity/souba/english/index.php",
    "nihon":      "https://www.material.co.jp/market.php",
    "mitsubishi": "https://gold.mmc.co.jp/market/silver-price/",
    "nanboya":    "https://nanboya.com/gold-kaitori/silver/silver-souba/",
    "daikichi":   "https://www.kaitori-daikichi.jp/list/gold/silver/souba/",
}


def get_html(url: str, encoding: str = None) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    if encoding:
        r.encoding = encoding
    return r.text


# ── Tanaka (English page, UTF-8) ─────────────────────────────────────────────
def parse_tanaka(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    # Format: "SILVER <sell> yen / g <buy> yen / g"
    m = re.search(r"SILVER\s+([\d,]+(?:\.\d+)?)\s+yen.*?\s([\d,]+(?:\.\d+)?)\s+yen", text)
    if m:
        sell = float(m.group(1).replace(",", ""))
        buy  = float(m.group(2).replace(",", ""))
        if 10 < buy < 50000:
            return {"silver_buy": buy, "silver_sell": sell}
    # Fallback: find SILVER row and grab second price number
    idx = text.find("SILVER")
    if idx == -1:
        raise ValueError("SILVER row not found on Tanaka page")
    tail = text[idx: idx + 600]
    nums = [float(n.replace(",", "")) for n in re.findall(r"([\d,]+(?:\.\d+)?)", tail)
            if 10 < float(n.replace(",", "")) < 50000]
    if len(nums) < 2:
        raise ValueError(f"Could not parse Tanaka SILVER prices — found: {nums}")
    return {"silver_sell": nums[0], "silver_buy": nums[1]}


# ── Nihon Material (EUC-JP static table) ─────────────────────────────────────
def parse_nihon(html: str) -> dict:
    """
    Table structure (decoded from EUC-JP):
    | 金属 | 小売価格 | 前日比 | 買取価格 | 前日比 |
    | 銀   | 488.29円 | ...   | 471.24円 | ...   |
    The silver row is the 4th data row (after 金, プラチナ, パラジウム).
    We match by finding the row that contains both a sell and buy price for 銀.
    """
    soup = BeautifulSoup(html, "html.parser")
    # Find all table cells, walk rows looking for the 銀 row
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if not cells:
                continue
            # Row starts with 銀 (silver kanji)
            if cells[0] == "銀":
                # Extract numbers from cells[1] (sell) and cells[3] (buy)
                sell_m = re.search(r"([\d,]+(?:\.\d+)?)", cells[1])
                buy_m  = re.search(r"([\d,]+(?:\.\d+)?)", cells[3])
                if sell_m and buy_m:
                    sell = float(sell_m.group(1).replace(",", ""))
                    buy  = float(buy_m.group(1).replace(",", ""))
                    if 10 < buy < 50000:
                        return {"silver_buy": buy, "silver_sell": sell}
    # Fallback: regex on full text
    text = soup.get_text(" ", strip=True)
    # Pattern around 銀: "銀 NNN.NN円 ±N.NN円 NNN.NN円"
    m = re.search(r"銀\s+([\d,]+(?:\.\d+)?)円[^0-9]+([\d,]+(?:\.\d+)?)円[^0-9]+([\d,]+(?:\.\d+)?)円", text)
    if m:
        sell = float(m.group(1).replace(",", ""))
        buy  = float(m.group(3).replace(",", ""))
        if 10 < buy < 50000:
            return {"silver_buy": buy, "silver_sell": sell}
    raise ValueError("Nihon Material: could not parse 銀 row")


# ── Mitsubishi Materials (UTF-8 static HTML) ─────────────────────────────────
def parse_mitsubishi(html: str) -> dict:
    """
    The 最新の価格 section contains a table:
    | 店頭価格 | 小売価格 500.39円/g | ... | 買取価格 483.89円/g | ... |

    Important context for JPMI:
    - Mitsubishi does NOT do OTC physical silver buyback at their shops
      ("銀地金の売買は行っておりません")
    - The 買取価格 here applies to their savings scheme (マイ・ゴールドパートナー)
    - We capture both sell (retail reference) and scheme buyback for transparency
    - These are included as RS data points, labelled clearly in prices.json
    """
    soup = BeautifulSoup(html, "html.parser")

    # Strategy: find all price-like strings near 買取価格 and 小売価格 in 最新の価格 section
    text = soup.get_text(" ", strip=True)

    sell, buy = None, None

    # Pattern: "店頭価格 NNN.NN円/g ... NNN.NN円/g ..." (sell comes before buy in table)
    # Look for the 最新の価格 block
    idx = text.find("最新の価格")
    if idx == -1:
        idx = 0
    block = text[idx: idx + 600]

    # Extract only meaningful prices (>100 yen/g) — excludes daily-change values like +16.83
    prices = [float(m) for m in re.findall(r"([\d,]+\.\d+)円/g", block)
              if float(m) > 100]

    # Order in block: 店頭sell, 店頭buy, Web_sell, Web_buy
    if len(prices) >= 2:
        sell = prices[0]
        buy  = prices[1]
    elif len(prices) == 1:
        sell = prices[0]

    # Fallback: try table-based parsing if text block failed
    if sell is None:
        soup2 = BeautifulSoup(html, "html.parser")
        for td in soup2.find_all(["td", "th"]):
            m2 = re.search(r"(\d{3,4}\.\d+)円/g", td.get_text())
            if m2:
                v = float(m2.group(1))
                if v > 100:
                    if sell is None:
                        sell = v
                    elif buy is None:
                        buy = v
                        break

    if sell is None:
        raise ValueError("Mitsubishi: could not parse 最新の価格 block")

    result = {"silver_sell": sell}
    if buy is not None:
        result["silver_scheme_buy"] = buy  # savings-scheme buyback, NOT OTC

    return result


# ── Nanboya (DB) ──────────────────────────────────────────────────────────────
def parse_nanboya(html: str) -> dict:
    """
    Nanboya publishes a daily commentary: "銀相場はXXX円と前日比から..."
    This is the per-gram Sv1000 buyback rate — the most reliable signal.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    m = re.search(r"銀相場は(\d{2,4})円", text)
    if m:
        val = float(m.group(1))
        if 50 <= val <= 5000:
            return {"sv1000_buy": val}

    # Fallback patterns
    for pat in [
        r"Sv1000\s*[\s\S]{0,300}?(\d{3,4}(?:\.\d+)?)\s*円/g",
        r"1g\s*あたり\s*(\d{3,4}(?:\.\d+)?)\s*円",
    ]:
        m2 = re.search(pat, text)
        if m2:
            val = float(m2.group(1))
            if 50 <= val <= 5000 and val != 1000:
                return {"sv1000_buy": val}

    raise ValueError("Nanboya: daily silver rate not found")


# ── Daikichi (DB) ─────────────────────────────────────────────────────────────
def parse_daikichi(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    m = re.search(r"1g\s+(\d{2,4})\s*円", text)
    if not m:
        raise ValueError("Daikichi: 1g price not found")
    return {"sv1000_buy": float(m.group(1))}


# ── Runner ────────────────────────────────────────────────────────────────────
def safe_fetch(name: str, fn):
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
        "notes": {
            "mitsubishi_silver_scheme_buy": (
                "Mitsubishi's 買取価格 applies to their マイ・ゴールドパートナー savings scheme only. "
                "Physical silver OTC buyback is not available at their stores. "
                "Included as RS reference price, not a direct retail exit price."
            ),
            "tokuriki": "Removed — market page uses JavaScript rendering, unavailable to scraper.",
        },
    }

    p = out["prices_jpy_per_g"]

    # RS — Tanaka
    result, err = safe_fetch("tanaka", lambda: parse_tanaka(get_html(SOURCES["tanaka"])))
    if err:   out["errors"].append(err)
    if result:
        p["tanaka_silver_buy"]  = result["silver_buy"]
        p["tanaka_silver_sell"] = result.get("silver_sell")

    # RS — Nihon Material (EUC-JP)
    result, err = safe_fetch("nihon", lambda: parse_nihon(get_html(SOURCES["nihon"], encoding="euc-jp")))
    if err:   out["errors"].append(err)
    if result:
        p["nihon_silver_buy"]  = result["silver_buy"]
        p["nihon_silver_sell"] = result.get("silver_sell")

    # RS — Mitsubishi (retail sell + scheme buy)
    result, err = safe_fetch("mitsubishi", lambda: parse_mitsubishi(get_html(SOURCES["mitsubishi"])))
    if err:   out["errors"].append(err)
    if result:
        p["mitsubishi_silver_sell"]        = result.get("silver_sell")
        p["mitsubishi_silver_scheme_buy"]  = result.get("silver_scheme_buy")

    # DB — Nanboya
    result, err = safe_fetch("nanboya", lambda: parse_nanboya(get_html(SOURCES["nanboya"])))
    if err:   out["errors"].append(err)
    if result:
        p["nanboya_sv1000"] = result["sv1000_buy"]

    # DB — Daikichi
    result, err = safe_fetch("daikichi", lambda: parse_daikichi(get_html(SOURCES["daikichi"])))
    if err:   out["errors"].append(err)
    if result:
        p["daikichi_sv1000"] = result["sv1000_buy"]

    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("prices.json written:")
    for k, v in p.items():
        print(f"  {k}: {v}")
    if out["errors"]:
        print("Warnings:")
        for e in out["errors"]:
            print(" -", e)


if __name__ == "__main__":
    main()
