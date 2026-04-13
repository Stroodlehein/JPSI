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


def get_html(url, encoding=None):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    if encoding:
        r.encoding = encoding
    return r.text


def safe_float(value):
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def is_valid_silver_price(price):
    return price is not None and 200 <= price <= 600


# ---------------- Tanaka ----------------
# Parse the SILVER line from page text and return the BUYING price (second number).
def parse_tanaka(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    # Example:
    # SILVER 426.69 yen -6.38 yen 409.64 yen -6.38 yen
    m = re.search(
        r"SILVER\s+([\d,]+(?:\.\d+)?)\s+yen\s+[-+−]?\d+(?:\.\d+)?\s+yen\s+([\d,]+(?:\.\d+)?)\s+yen",
        text,
        re.I,
    )
    if m:
        val = safe_float(m.group(2))
        if is_valid_silver_price(val):
            return val

    # Fallback: line-based parse
    for line in soup.get_text("\n", strip=True).splitlines():
        if "SILVER" not in line.upper():
            continue
        nums = [safe_float(x) for x in re.findall(r"([\d,]+(?:\.\d+)?)", line)]
        nums = [n for n in nums if is_valid_silver_price(n)]
        if len(nums) >= 2:
            return nums[1]

    raise ValueError("Tanaka SILVER buying price not found")


# ---------------- Nihon ----------------
# Silver row contains sell and buy; buyback is the lower of the two valid prices.
def parse_nihon(html):
    soup = BeautifulSoup(html, "html.parser")

    for row in soup.find_all("tr"):
        row_text = row.get_text(" ", strip=True)
        if "銀" not in row_text:
            continue

        nums = [
            safe_float(x)
            for x in re.findall(r"([\d,]+(?:\.\d+)?)\s*円", row_text)
        ]
        nums = [n for n in nums if is_valid_silver_price(n)]
        if len(nums) >= 2:
            return min(nums)

    text = soup.get_text(" ", strip=True)
    idx = text.find("銀")
    if idx != -1:
        snippet = text[idx:idx + 400]
        nums = [
            safe_float(x)
            for x in re.findall(r"([\d,]+(?:\.\d+)?)\s*円", snippet)
        ]
        nums = [n for n in nums if is_valid_silver_price(n)]
        if len(nums) >= 2:
            return min(nums)

    raise ValueError("Nihon Material silver buyback not found")


# ---------------- Mitsubishi ----------------
# 店頭価格 row: first valid number = retail sell, second = buyback.
def parse_mitsubishi(html):
    soup = BeautifulSoup(html, "html.parser")

    for row in soup.find_all("tr"):
        row_text = row.get_text(" ", strip=True)
        if "店頭価格" not in row_text:
            continue

        nums = [
            safe_float(x)
            for x in re.findall(r"([\d,]+(?:\.\d+)?)\s*円/g", row_text)
        ]
        nums = [n for n in nums if is_valid_silver_price(n)]

        if len(nums) >= 2:
            return nums[1]
        if len(nums) == 1:
            return nums[0]

    text = soup.get_text(" ", strip=True)
    idx = text.find("店頭価格")
    if idx != -1:
        snippet = text[idx:idx + 300]
        nums = [
            safe_float(x)
            for x in re.findall(r"([\d,]+(?:\.\d+)?)\s*円/g", snippet)
        ]
        nums = [n for n in nums if is_valid_silver_price(n)]
        if len(nums) >= 2:
            return nums[1]
        if len(nums) == 1:
            return nums[0]

    raise ValueError("Mitsubishi silver buyback not found")


# ---------------- Nanboya ----------------
# Static HTML does not expose the Sv1000 numeric cell.
# Use the published commentary line:
# "2026年4月13日(月)の銀相場は401円..."
def parse_nanboya(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    patterns = [
        r"今日の銀相場は\s*([\d,]+(?:\.\d+)?)\s*円",
        r"\d{4}年\d{1,2}月\d{1,2}日.*?銀相場は\s*([\d,]+(?:\.\d+)?)\s*円",
    ]

    for pat in patterns:
        m = re.search(pat, text, re.S)
        if m:
            val = safe_float(m.group(1))
            if is_valid_silver_price(val):
                return val

    raise ValueError("Nanboya published silver price not found")


# ---------------- Daikichi ----------------
# Explicit 1g row price.
def parse_daikichi(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    m = re.search(r"1g\s*([\d,]+(?:\.\d+)?)\s*円", text)
    if m:
        val = safe_float(m.group(1))
        if is_valid_silver_price(val):
            return val

    raise ValueError("Daikichi 1g price not found")


def safe_get(name, fn):
    try:
        return fn(), None
    except Exception as e:
        return None, f"{name}: {type(e).__name__}: {e}"


def load_existing_prices():
    try:
        with open("prices.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def set_price_or_keep_existing(out, key, value, source_name):
    prices = out.setdefault("prices_jpy_per_g", {})
    existing = prices.get(key)

    if is_valid_silver_price(value):
        prices[key] = value
        return

    if is_valid_silver_price(existing):
        out["errors"].append(f"{source_name}: fetch failed, kept previous valid value {existing}")
        return

    prices.pop(key, None)
    out["errors"].append(f"{source_name}: fetch failed and no previous valid value available")


def main():
    existing = load_existing_prices()

    # Preserve Mercari / FX / COMEX block
    preserved = {
        "mercari_listings": existing.get("mercari_listings"),
        "mspi_updated_at_utc": existing.get("mspi_updated_at_utc"),
        "mspi_updated_date": existing.get("mspi_updated_date"),
        "comex_updated_at_utc": existing.get("comex_updated_at_utc"),
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

    v, err = safe_get("tanaka", lambda: parse_tanaka(get_html(SOURCES["tanaka"])))
    if err:
        out["errors"].append(err)
    set_price_or_keep_existing(out, "tanaka_silver_buy", v, "tanaka")

    v, err = safe_get("nihon", lambda: parse_nihon(get_html(SOURCES["nihon"], encoding="euc-jp")))
    if err:
        out["errors"].append(err)
    set_price_or_keep_existing(out, "nihon_silver_buy", v, "nihon")

    v, err = safe_get("mitsubishi", lambda: parse_mitsubishi(get_html(SOURCES["mitsubishi"])))
    if err:
        out["errors"].append(err)
    set_price_or_keep_existing(out, "mitsubishi_silver_buy", v, "mitsubishi")

    v, err = safe_get("nanboya", lambda: parse_nanboya(get_html(SOURCES["nanboya"])))
    if err:
        out["errors"].append(err)
    set_price_or_keep_existing(out, "nanboya_sv1000", v, "nanboya")

    v, err = safe_get("daikichi", lambda: parse_daikichi(get_html(SOURCES["daikichi"])))
    if err:
        out["errors"].append(err)
    set_price_or_keep_existing(out, "daikichi_sv1000", v, "daikichi")

    # Restore preserved blocks
    out["mercari_listings"] = preserved["mercari_listings"]
    out["mspi_updated_at_utc"] = preserved["mspi_updated_at_utc"]
    out["mspi_updated_date"] = preserved["mspi_updated_date"]
    out["comex_updated_at_utc"] = preserved["comex_updated_at_utc"]

    out.setdefault("prices_jpy_per_g", {})
    for k, v in preserved_prices.items():
        out["prices_jpy_per_g"][k] = v

    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("prices.json updated safely")
    for k in [
        "tanaka_silver_buy",
        "nihon_silver_buy",
        "mitsubishi_silver_buy",
        "nanboya_sv1000",
        "daikichi_sv1000",
        "mercari_mspi_b",
        "mercari_mspi_b_listings",
        "mercari_mspi_b_avg_jpy",
        "usd_jpy",
        "comex_silver_usd_oz",
        "comex_silver_jpy_g",
    ]:
        if k in out.get("prices_jpy_per_g", {}):
            print(f"  {k}: {out['prices_jpy_per_g'][k]}")

    if out["errors"]:
        print("Warnings:")
        for e in out["errors"]:
            print(" -", e)


if __name__ == "__main__":
    main()
