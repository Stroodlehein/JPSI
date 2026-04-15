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
    "daikichi": "https://www.kaitori-daikichi.jp/list/gold/silver/souba/",
}

# Nanboya removed from auto fetch
MANUAL_KEYS = ["nanboya_sv1000"]


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
# Parse the flattened SILVER line and return the BUYING price (second valid number).
def parse_tanaka(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    # Example live structure:
    # SILVER 456.50 yen +19.14 yen 439.45 yen +19.14 yen
    m = re.search(
        r"SILVER\s+([\d,]+(?:\.\d+)?)\s+yen\s+[+\-−]?\d+(?:\.\d+)?\s+yen\s+([\d,]+(?:\.\d+)?)\s+yen",
        text,
        re.I,
    )
    if m:
        val = safe_float(m.group(2))
        if is_valid_silver_price(val):
            return val

    # Fallback: find the SILVER line and take the second valid silver-range number
    for line in soup.get_text("\n", strip=True).splitlines():
        if "SILVER" not in line.upper():
            continue
        nums = [safe_float(x) for x in re.findall(r"([\d,]+(?:\.\d+)?)", line)]
        nums = [n for n in nums if is_valid_silver_price(n)]
        if len(nums) >= 2:
            return nums[1]

    raise ValueError("Tanaka not found")


# ---------------- Nihon ----------------
def parse_nihon(html):
    soup = BeautifulSoup(html, "html.parser")

    for row in soup.find_all("tr"):
        txt = row.get_text(" ", strip=True)
        if "銀" not in txt:
            continue

        nums = re.findall(r"([\d,]+\.\d+)", txt)
        nums = [safe_float(n) for n in nums if is_valid_silver_price(safe_float(n))]

        if len(nums) >= 2:
            return min(nums)

    raise ValueError("Nihon not found")


# ---------------- Mitsubishi ----------------
def parse_mitsubishi(html):
    soup = BeautifulSoup(html, "html.parser")

    for row in soup.find_all("tr"):
        txt = row.get_text(" ", strip=True)

        if "店頭価格" not in txt:
            continue

        nums = re.findall(r"([\d,]+\.\d+)\s*円/g", txt)
        nums = [safe_float(n) for n in nums if is_valid_silver_price(safe_float(n))]

        if len(nums) >= 2:
            return nums[1]

    raise ValueError("Mitsubishi not found")


# ---------------- Daikichi ----------------
def parse_daikichi(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    m = re.search(r"1g\s*([\d,]{3,})\s*円", text)

    if m:
        val = safe_float(m.group(1))
        if is_valid_silver_price(val):
            return val

    raise ValueError("Daikichi not found")


def safe_get(name, fn):
    try:
        return fn(), None
    except Exception as e:
        return None, f"{name}: {type(e).__name__}: {e}"


def load_existing_prices():
    try:
        with open("prices.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def set_price_or_keep_existing(out, key, value, source_name):
    prices = out.setdefault("prices_jpy_per_g", {})
    existing = prices.get(key)

    # NEVER overwrite manual keys
    if key in MANUAL_KEYS:
        return

    if is_valid_silver_price(value):
        prices[key] = value
        return

    if is_valid_silver_price(existing):
        return

    prices.pop(key, None)


def main():
    existing = load_existing_prices()

    # preserve manual + mercari + fx
    preserved_prices = existing.get("prices_jpy_per_g", {}).copy()

    out = existing
    out["updated_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["sources"] = SOURCES
    out["errors"] = []

    # auto sources
    parsers = {
        "tanaka": parse_tanaka,
        "nihon": parse_nihon,
        "mitsubishi": parse_mitsubishi,
        "daikichi": parse_daikichi,
    }

    for name, url in SOURCES.items():
        try:
            encoding = "euc-jp" if name == "nihon" else None
            html = get_html(url, encoding=encoding)

            val = parsers[name](html)

            key = (
                f"{name}_silver_buy"
                if name in ["tanaka", "nihon", "mitsubishi"]
                else f"{name}_sv1000"
            )

            set_price_or_keep_existing(out, key, val, name)

        except Exception:
            pass

    # restore manual keys (Nanboya + Mercari)
    for k, v in preserved_prices.items():
        if k.startswith("mercari") or k == "nanboya_sv1000":
            out.setdefault("prices_jpy_per_g", {})[k] = v

    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("prices.json updated")


if __name__ == "__main__":
    main()
