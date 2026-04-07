import json
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; JPMIbot/1.0)"

SOURCES = {
    "tanaka": "https://gold.tanaka.co.jp/commodity/souba/english/index.php",
    "nihon": "https://material.co.jp/market.php",
    "mitsubishi": "https://gold.mmc.co.jp/market/silver-price/",
    "nanboya": "https://nanboya.com/gold-kaitori/silver/silver-souba/",
    "daikichi": "https://www.kaitori-daikichi.jp/list/gold/silver/souba/",
}

NANBOYA_SV1000_FALLBACK = 380.0


def get_html(url, encoding=None):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    if encoding:
        r.encoding = encoding
    return r.text


def is_valid_silver_price(price):
    return price is not None and 200 <= price <= 600


# ---------------- Tanaka ----------------
def parse_tanaka(html):
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            if cells[0].get_text(strip=True) == "SILVER" and len(cells) >= 4:
                buy_text = cells[3].get_text(strip=True).replace(",", "").replace(" yen", "")
                m = re.search(r"[\d.]+", buy_text)
                if m:
                    val = float(m.group())
                    if is_valid_silver_price(val):
                        return val
    raise ValueError("Tanaka SILVER buyback row not found")


# ---------------- Nihon ----------------
def parse_nihon(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    idx = text.find("銀")
    if idx != -1:
        snippet = text[idx:idx+300]
        nums = [
            float(x.replace(",", ""))
            for x in re.findall(r"([\d,]+(?:\.\d+)?)\s*円", snippet)
        ]
        nums = [n for n in nums if 200 <= n <= 600]
        if nums:
            return min(nums)

    raise ValueError("Nihon Material silver buyback not found")


# ---------------- Mitsubishi ----------------
def parse_mitsubishi(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    prices = [
        float(x.replace(",", ""))
        for x in re.findall(r"([\d,]+(?:\.\d+)?)\s*円/g", text)
    ]

    prices = [p for p in prices if is_valid_silver_price(p)]

    if prices:
        return prices[0]

    raise ValueError("Mitsubishi silver buyback not found")


# ---------------- Nanboya ----------------
def parse_nanboya(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    m = re.search(r"Sv1000(?!\s*インゴット).*?([\d,]+)\s*円", text, re.S)
    if m:
        val = float(m.group(1).replace(",", ""))
        if is_valid_silver_price(val):
            return val

    return NANBOYA_SV1000_FALLBACK


# ---------------- Daikichi ----------------
def parse_daikichi(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    m = re.search(r"1g\s*([\d,]+)\s*円", text)
    if m:
        val = float(m.group(1).replace(",", ""))
        if is_valid_silver_price(val):
            return val

    raise ValueError("Daikichi price not found")


def safe_get(name, fn):
    try:
        return fn(), None
    except Exception as e:
        return None, f"{name}: {type(e).__name__}: {e}"


def load_existing_prices():
    try:
        with open("prices.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def set_price(out, key, value):
    prices = out.setdefault("prices_jpy_per_g", {})
    if is_valid_silver_price(value):
        prices[key] = value


def main():
    existing = load_existing_prices()

    # -------- preserve MSPI / Mercari block ----------
    preserved = {
        "mercari_listings": existing.get("mercari_listings"),
        "mspi_updated_at_utc": existing.get("mspi_updated_at_utc"),
        "mspi_updated_date": existing.get("mspi_updated_date"),
    }

    preserved_prices = {}
    for k in [
        "mercari_mspi_b",
        "mercari_mspi_b_listings",
        "mercari_mspi_b_avg_jpy",
        "usd_jpy",
        "comex_silver_usd_oz",
        "comex_silver_jpy_g",
    ]:
        if k in existing.get("prices_jpy_per_g", {}):
            preserved_prices[k] = existing["prices_jpy_per_g"][k]

    out = existing
    out["updated_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["sources"] = SOURCES
    out["errors"] = []

    # ------- fetch live prices -------
    v, err = safe_get("tanaka", lambda: parse_tanaka(get_html(SOURCES["tanaka"])))
    set_price(out, "tanaka_silver_buy", v)

    v, err = safe_get("nihon", lambda: parse_nihon(get_html(SOURCES["nihon"], encoding="euc-jp")))
    set_price(out, "nihon_silver_buy", v)

    v, err = safe_get("mitsubishi", lambda: parse_mitsubishi(get_html(SOURCES["mitsubishi"])))
    set_price(out, "mitsubishi_silver_buy", v)

    v, err = safe_get("nanboya", lambda: parse_nanboya(get_html(SOURCES["nanboya"])))
    set_price(out, "nanboya_sv1000", v)

    v, err = safe_get("daikichi", lambda: parse_daikichi(get_html(SOURCES["daikichi"])))
    set_price(out, "daikichi_sv1000", v)

    # -------- restore preserved MSPI ----------
    out["mercari_listings"] = preserved["mercari_listings"]
    out["mspi_updated_at_utc"] = preserved["mspi_updated_at_utc"]
    out["mspi_updated_date"] = preserved["mspi_updated_date"]

    for k, v in preserved_prices.items():
        out["prices_jpy_per_g"][k] = v

    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("prices.json updated safely (MSPI preserved)")


if __name__ == "__main__":
    main()
