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
    except:
        return None


def is_valid_silver_price(p):
    return p is not None and 200 <= p <= 600


# ---------------- Tanaka ----------------
def parse_tanaka(html):
    soup = BeautifulSoup(html, "html.parser")

    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        if cells[0].get_text(strip=True).upper() == "SILVER":
            if len(cells) >= 4:
                val = safe_float(cells[3].get_text())
                if is_valid_silver_price(val):
                    return val

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


# ---------------- Nanboya FIX ----------------
def parse_nanboya(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    # target plain Sv1000 row only (NOT インゴット)
    m = re.search(r"Sv1000(?!\s*インゴット).*?([\d,]{3,})\s*円", text, re.S)

    if m:
        val = safe_float(m.group(1))
        if is_valid_silver_price(val):
            return val

    raise ValueError("Nanboya Sv1000 not found")


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


def set_price(out, key, value):
    prices = out.setdefault("prices_jpy_per_g", {})

    if is_valid_silver_price(value):
        prices[key] = value


def main():
    existing = load_existing_prices()

    out = existing
    out["updated_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["sources"] = SOURCES
    out["errors"] = []

    parsers = {
        "tanaka": parse_tanaka,
        "nihon": parse_nihon,
        "mitsubishi": parse_mitsubishi,
        "nanboya": parse_nanboya,
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

            set_price(out, key, val)

        except Exception as e:
            out["errors"].append(f"{name}: {e}")

    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("prices.json updated")


if __name__ == "__main__":
    main()
