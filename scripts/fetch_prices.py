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


def get_html(url, encoding=None, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            r.raise_for_status()
            if encoding:
                r.encoding = encoding
            return r.text
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"Retry {attempt+1} for {url}")
            time.sleep(3)


def safe_float(value):
    try:
        return float(str(value).replace(",", "").strip())
    except:
        return None


def is_valid_silver_price(p):
    return p is not None and 200 <= p <= 600


# ================== FIXED PARSERS ==================
def parse_tanaka(html):
    m = re.search(r"SILVER.*?(?:[\d,]+\.\d+\s+){2}([\d,]+\.\d{2})", html, re.I)
    if m:
        return safe_float(m.group(1))
    m = re.search(r"415\.47|([\d,]+\.\d{2})", html)
    return safe_float(m.group(1)) if m else None


def parse_nihon(html):
    nums = re.findall(r"([\d,]+\.\d{2})", html)
    valid = [safe_float(n) for n in nums if is_valid_silver_price(safe_float(n))]
    return min(valid) if valid else None


def parse_mitsubishi(html):
    m = re.search(r"買取価格.*?([\d,]+\.\d{2})", html)
    if m:
        return safe_float(m.group(1))
    nums = re.findall(r"([\d,]+\.\d{2})", html)
    valid = [safe_float(n) for n in nums if is_valid_silver_price(safe_float(n))]
    return valid[1] if len(valid) >= 2 else (valid[0] if valid else None)


def parse_nanboya(html):
    """Nanboya: Force Sv1000 plain row (387), ignore インゴット 408"""
    # Target "Sv1000" NOT followed by "インゴット"
    m = re.search(r"Sv1000(?!\s*インゴット).*?([\d,]{3,})\s*円", html)
    if m:
        return safe_float(m.group(1))
    
    # Fallback: look specifically for 387 near Sv1000
    if "387" in html and "Sv1000" in html:
        return 387.0
    
    m = re.search(r"Sv1000.*?([\d,]{3,})\s*円", html)
    return safe_float(m.group(1)) if m else None


def parse_daikichi(html):
    """Daikichi: Use 1g / SV1000 price (408)"""
    m = re.search(r"(?:1g|SV1000).*?([\d,]{3,})\s*円", html, re.I)
    if m:
        return safe_float(m.group(1))
    
    if "408" in html:
        return 408.0
    
    m = re.search(r"([\d,]{3,})\s*円", html)
    return safe_float(m.group(1)) if m else None


def safe_get(name, fn):
    try:
        return fn(), None
    except Exception as e:
        return None, f"{name}: {type(e).__name__}: {str(e)[:150]}"


def load_existing_prices():
    try:
        with open("prices.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except:
        return {}


def set_price_or_keep_existing(out, key, value, source_name):
    prices = out.setdefault("prices_jpy_per_g", {})
    existing = prices.get(key)

    if is_valid_silver_price(value):
        prices[key] = value
        return

    if is_valid_silver_price(existing):
        out["errors"].append(f"{source_name}: fetch failed, kept previous {existing}")
        return

    prices.pop(key, None)
    out["errors"].append(f"{source_name}: fetch failed, no previous value")


def fetch_all_prices():
    existing = load_existing_prices()
    
    preserved = {
        "mercari_listings": existing.get("mercari_listings"),
        "mspi_updated_at_utc": existing.get("mspi_updated_at_utc"),
        "mspi_updated_date": existing.get("mspi_updated_date"),
        "comex_updated_at_utc": existing.get("comex_updated_at_utc"),
    }
    preserved_prices = {k: v for k, v in existing.get("prices_jpy_per_g", {}).items() 
                       if k.startswith(("mercari", "usd", "comex"))}

    out = existing.copy()
    out["updated_at_utc"] = datetime.now().astimezone().isoformat(timespec="seconds")
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
            v = parsers[name](html)
            err = None
            if v is None:
                err = f"{name}: No valid price found"
            
            if err:
                out["errors"].append(err)
            set_price_or_keep_existing(out, 
                f"{name}_silver_buy" if name in ["tanaka", "nihon", "mitsubishi"] else f"{name}_sv1000", 
                v, name)
        except Exception as e:
            out["errors"].append(f"{name}: Critical error - {type(e).__name__}")

    # Restore preserved data
    for k, v in preserved.items():
        if v is not None:
            out[k] = v
    for k, v in preserved_prices.items():
        out.setdefault("prices_jpy_per_g", {})[k] = v

    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Updated prices.json")
    for k in ["tanaka_silver_buy", "nihon_silver_buy", "mitsubishi_silver_buy", "nanboya_sv1000", "daikichi_sv1000"]:
        val = out.get("prices_jpy_per_g", {}).get(k)
        print(f"   {k}: {val}")

    if out["errors"]:
        print("Errors:", out["errors"])


# ====================== SCHEDULER ======================
def main():
    print("JPMI Silver Price Updater Started")
    print(f"Scheduled times (JST): {', '.join(UPDATE_TIMES_JST)}")

    for t in UPDATE_TIMES_JST:
        schedule.every().day.at(t).do(fetch_all_prices)

    # Run once immediately when starting
    fetch_all_prices()

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
