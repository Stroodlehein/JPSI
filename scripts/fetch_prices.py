import json
import re
import time
import schedule
from datetime import datetime
import requests
from bs4 import BeautifulSoup

# ====================== CONFIG ======================
UA = "Mozilla/5.0 (compatible; JPMIbot/1.0 +https://japanphysicalmetals.jp)"

SOURCES = {
    "tanaka": "https://gold.tanaka.co.jp/commodity/souba/english/index.php",
    "nihon": "https://material.co.jp/market.php",
    "mitsubishi": "https://gold.mmc.co.jp/market/silver-price/",
    "nanboya": "https://nanboya.com/gold-kaitori/silver/silver-souba/",
    "daikichi": "https://www.kaitori-daikichi.jp/list/gold/silver/souba/",
}

UPDATE_TIMES_JST = ["09:45", "10:45", "12:15", "14:15", "16:15", "17:45"]
# ====================================================


def get_html(url, encoding=None, retries=2):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
            r.raise_for_status()
            if encoding:
                r.encoding = encoding
            return r.text
        except Exception as e:
            if attempt == retries:
                raise
            print(f"Retry {attempt+1} for {url}: {e}")
            time.sleep(2)


def is_valid_silver_price(price):
    return price is not None and 200 <= price <= 600


def safe_float(value):
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


# ---------------- Parsers (improved for current pages) ----------------
def parse_tanaka(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    m = re.search(r"SILVER.*?[\d,]+\.\d+.*?[\d,]+\.\d+.*?([\d,]+\.\d+)", text)
    if m:
        val = safe_float(m.group(1))
        if is_valid_silver_price(val):
            return val
    raise ValueError("Tanaka silver buyback not found")


def parse_nihon(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    nums = [safe_float(x) for x in re.findall(r"([\d,]+\.\d{2})", text)]
    nums = [n for n in nums if is_valid_silver_price(n)]
    if len(nums) >= 2:
        return min(nums)          # buyback is usually the smaller one
    raise ValueError("Nihon Material silver buyback not found")


def parse_mitsubishi(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    m = re.search(r"買取価格.*?([\d,]+\.\d{2})", text)
    if m:
        val = safe_float(m.group(1))
        if is_valid_silver_price(val):
            return val
    nums = [safe_float(x) for x in re.findall(r"([\d,]+\.\d{2})", text)]
    nums = [n for n in nums if is_valid_silver_price(n)]
    if len(nums) >= 2:
        return nums[1]            # second price is usually buyback
    raise ValueError("Mitsubishi silver buyback not found")


def parse_nanboya(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    patterns = [
        r"Sv1000.*?([\d,]+)\s*円",
        r"今日の銀相場は\s*([\d,]+)\s*円",
        r"\d{4}年.*?銀相場は\s*([\d,]+)\s*円",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            val = safe_float(m.group(1))
            if is_valid_silver_price(val):
                return val
    raise ValueError("Nanboya silver price not found")


def parse_daikichi(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    m = re.search(r"1g.*?([\d,]+)\s*円", text)
    if m:
        val = safe_float(m.group(1))
        if is_valid_silver_price(val):
            return val
    m = re.search(r"SV1000.*?([\d,]+)\s*円", text)
    if m:
        val = safe_float(m.group(1))
        if is_valid_silver_price(val):
            return val
    raise ValueError("Daikichi silver price not found")


def safe_get(name, fn):
    try:
        return fn(), None
    except Exception as e:
        return None, f"{name}: {type(e).__name__}: {str(e)[:180]}"


def load_existing_prices():
    try:
        with open("prices.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def set_price_or_keep_existing(out, key, value, source_name):
    prices = out.setdefault("prices_jpy_per_g", {})
    existing = prices.get(key)

    if is_valid_silver_price(value):
        prices[key] = value
        return

    if is_valid_silver_price(existing):
        out["errors"].append(f"{source_name}: fetch failed, kept previous value {existing}")
        return

    prices.pop(key, None)
    out["errors"].append(f"{source_name}: fetch failed, no previous value kept")


def fetch_all_prices():
    existing = load_existing_prices()
    preserved = {k: existing.get(k) for k in ["mercari_listings", "mspi_updated_at_utc", "mspi_updated_date", "comex_updated_at_utc"]}
    preserved_prices = {k: v for k, v in existing.get("prices_jpy_per_g", {}).items() 
                       if k.startswith(("mercari", "usd", "comex"))}

    out = existing.copy()
    out["updated_at_utc"] = datetime.now().astimezone().isoformat(timespec="seconds")
    out["sources"] = SOURCES
    out["errors"] = []

    for name, url in SOURCES.items():
        try:
            encoding = "euc-jp" if name == "nihon" else None
            html = get_html(url, encoding=encoding)

            if name == "tanaka":
                v, err = safe_get(name, lambda: parse_tanaka(html))
                key = "tanaka_silver_buy"
            elif name == "nihon":
                v, err = safe_get(name, lambda: parse_nihon(html))
                key = "nihon_silver_buy"
            elif name == "mitsubishi":
                v, err = safe_get(name, lambda: parse_mitsubishi(html))
                key = "mitsubishi_silver_buy"
            elif name == "nanboya":
                v, err = safe_get(name, lambda: parse_nanboya(html))
                key = "nanboya_sv1000"
            elif name == "daikichi":
                v, err = safe_get(name, lambda: parse_daikichi(html))
                key = "daikichi_sv1000"

            if err:
                out["errors"].append(err)
            set_price_or_keep_existing(out, key, v, name)

        except Exception as e:
            out["errors"].append(f"{name}: Critical - {type(e).__name__}")

    # Restore preserved data
    for k, v in preserved.items():
        if v is not None:
            out[k] = v
    for k, v in preserved_prices.items():
        out.setdefault("prices_jpy_per_g", {})[k] = v

    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] prices.json updated")
    for k in ["tanaka_silver_buy", "nihon_silver_buy", "mitsubishi_silver_buy", "nanboya_sv1000", "daikichi_sv1000"]:
        val = out.get("prices_jpy_per_g", {}).get(k)
        print(f"   {k}: {val}")

    if out["errors"]:
        print("Errors:", out["errors"])


# ====================== SCHEDULER ======================
def main():
    print("JPMI Silver Price Updater started")
    print(f"Will run at JST times: {', '.join(UPDATE_TIMES_JST)}")

    for t in UPDATE_TIMES_JST:
        schedule.every().day.at(t).do(fetch_all_prices)

    # Optional: run once immediately when starting
    fetch_all_prices()

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
