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


def load_existing_prices():
    try:
        with open("prices.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def set_price_or_keep_existing(out, key, value, source_name):
    prices = out.setdefault("prices_jpy_per_g", {})
    existing = prices.get(key)

    # NEVER overwrite manual keys
    if key in MANUAL_KEYS:
        print(f"{source_name}: manual key preserved, not overwritten")
        return

    if is_valid_silver_price(value):
        prices[key] = value
        print(f"{source_name}: saved {value}")
        return

    if is_valid_silver_price(existing):
        msg = f"{source_name}: invalid fetch, kept previous valid value {existing}"
        out["errors"].append(msg)
        print(msg)
        return

    prices.pop(key, None)
    msg = f"{source_name}: invalid fetch and no previous valid value available"
    out["errors"].append(msg)
    print(msg)


def main():
    existing = load_existing_prices()

    # Preserve manual + Mercari + FX + COMEX values
    preserved_prices = existing.get("prices_jpy_per_g", {}).copy()

    out = existing
    out["updated_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["sources"] = SOURCES
    out["errors"] = []

    parsers = {
        "tanaka": parse_tanaka,
        "nihon": parse_nihon,
        "mitsubishi": parse_mitsubishi,
        "daikichi": parse_daikichi,
    }

    for name, url in SOURCES.items():
        encoding = "euc-jp" if name == "nihon" else None

        key = (
            f"{name}_silver_buy"
            if name in ["tanaka", "nihon", "mitsubishi"]
            else f"{name}_sv1000"
        )

        try:
            html = get_html(url, encoding=encoding)
            val = parsers[name](html)

            print(f"{name}: fetched {val}")
            set_price_or_keep_existing(out, key, val, name)

        except Exception as e:
            msg = f"{name}: {type(e).__name__}: {e}"
            out["errors"].append(msg)
            print(msg)

            existing_value = out.setdefault("prices_jpy_per_g", {}).get(key)
            if is_valid_silver_price(existing_value):
                keep_msg = f"{name}: kept previous valid value {existing_value}"
                out["errors"].append(keep_msg)
                print(keep_msg)
            else:
                out["prices_jpy_per_g"].pop(key, None)
                no_value_msg = f"{name}: no previous valid value available"
                out["errors"].append(no_value_msg)
                print(no_value_msg)

    # Restore manual keys and manually managed data
    for k, v in preserved_prices.items():
        if (
            k.startswith("mercari")
            or k == "nanboya_sv1000"
            or k == "usd_jpy"
            or k == "comex_silver_usd_oz"
            or k == "comex_silver_jpy_g"
        ):
            out.setdefault("prices_jpy_per_g", {})[k] = v

    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("prices.json updated")
    print("Final prices:")
    for k in [
        "tanaka_silver_buy",
        "nihon_silver_buy",
        "mitsubishi_silver_buy",
        "daikichi_sv1000",
        "nanboya_sv1000",
        "mercari_mspi_b",
        "usd_jpy",
        "comex_silver_usd_oz",
        "comex_silver_jpy_g",
    ]:
        value = out.get("prices_jpy_per_g", {}).get(k)
        print(f"  {k}: {value}")

    if out["errors"]:
        print("Errors / warnings:")
        for error in out["errors"]:
            print(f"  - {error}")


if __name__ == "__main__":
    main()
