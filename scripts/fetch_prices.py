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

# Nanboya is still safest as a manual fallback unless the scrape clearly finds
# the plain Sv1000 value. Update this when you manually verify a new value.
NANBOYA_SV1000_FALLBACK = 376.0


def get_html(url, encoding=None):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    if encoding:
        r.encoding = encoding
    return r.text


def is_valid_silver_price(price):
    return price is not None and 200 <= price <= 600


def safe_float(value):
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


# ---------------- Tanaka ----------------
# SILVER row, buying column (4th cell)
def parse_tanaka(html):
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 4:
                continue
            label = cells[0].get_text(" ", strip=True).upper()
            if label == "SILVER":
                buy_text = cells[3].get_text(" ", strip=True)
                m = re.search(r"([\d,]+(?:\.\d+)?)", buy_text)
                if m:
                    val = safe_float(m.group(1))
                    if is_valid_silver_price(val):
                        return val
    raise ValueError("Tanaka SILVER buying price not found")


# ---------------- Nihon ----------------
# Target silver buyback specifically, not arbitrary nearby numbers.
def parse_nihon(html):
    soup = BeautifulSoup(html, "html.parser")

    # Try row/table-based extraction first
    for row in soup.find_all("tr"):
        row_text = row.get_text(" ", strip=True)
        if "銀" not in row_text:
            continue

        # Silver row usually contains sell and buy values; buy is the lower one.
        nums = [
            safe_float(x)
            for x in re.findall(r"([\d,]+(?:\.\d+)?)\s*円", row_text)
        ]
        nums = [n for n in nums if is_valid_silver_price(n)]
        if len(nums) >= 2:
            return min(nums)

    # Fallback: look only in the silver section and take the lower of first two valid values
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
# Must return BUYBACK, not retail selling.
# In the visible row the first valid price is retail, second valid price is buyback.
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
# Try to find plain Sv1000 row, not インゴット. If not trustworthy, use fallback.
def parse_nanboya(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    # Prefer plain Sv1000, avoid インゴット / IG
    matches = re.finditer(r"Sv1000.*?([\d,]+(?:\.\d+)?)\s*円", text, re.S)
    for m in matches:
        snippet = m.group(0)
        if "インゴット" in snippet or re.search(r"\bIG\b", snippet, re.I):
            continue
        val = safe_float(m.group(1))
        if is_valid_silver_price(val):
            return val

    if is_valid_silver_price(NANBOYA_SV1000_FALLBACK):
        return NANBOYA_SV1000_FALLBACK

    raise ValueError("Nanboya price not found")


# ---------------- Daikichi ----------------
# Prefer the explicit 1g row price.
def parse_daikichi(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    m = re.search(r"1g\s*([\d,]+(?:\.\d+)?)\s*円", text)
    if m:
        val = safe_float(m.group(1))
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

    # -------- preserve MSPI / Mercari / FX / COMEX block ----------
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

    # ------- fetch live prices -------
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

    # -------- restore preserved MSPI / FX / COMEX ----------
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
