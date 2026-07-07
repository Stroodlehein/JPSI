import json
import re
from datetime import datetime, timezone, timedelta

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

JPMI_HISTORY_FILE = "jpmi-history.json"
JPMI_DAILY_OBSERVATION_HOUR_JST = 19  # Daily history rows are created/updated from 19:00 JST onward


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


def average_valid(values):
    valid = []
    for value in values:
        number = safe_float(value)
        if is_valid_silver_price(number):
            valid.append(number)

    if not valid:
        return None

    return round(sum(valid) / len(valid), 2)


def calculate_premium_pct(physical, comex):
    physical = safe_float(physical)
    comex = safe_float(comex)

    if physical is None or comex is None or comex == 0:
        return None

    return round(((physical - comex) / comex) * 100, 2)


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


def load_jpmi_history():
    try:
        with open(JPMI_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"{JPMI_HISTORY_FILE}: failed to load existing history: {e}")
        return []


def save_jpmi_history(history):
    history = sorted(history, key=lambda row: row.get("date", ""))

    with open(JPMI_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"{JPMI_HISTORY_FILE} updated with {len(history)} rows")


def latest_history_value_before(history, date_jst, key):
    prior_rows = [
        row for row in history
        if row.get("date") and row.get("date") < date_jst and row.get(key) is not None
    ]

    if not prior_rows:
        return None

    prior_rows = sorted(prior_rows, key=lambda row: row.get("date", ""))
    return safe_float(prior_rows[-1].get(key))


def append_daily_jpmi_history_if_ready(out):
    utc_now = datetime.now(timezone.utc)
    jst_now = utc_now + timedelta(hours=9)
    date_jst = jst_now.date().isoformat()

    if jst_now.hour < JPMI_DAILY_OBSERVATION_HOUR_JST:
        print(
            f"JPMI history skipped: official daily observation is "
            f"{JPMI_DAILY_OBSERVATION_HOUR_JST}:00 JST or later. "
            f"Current JST hour: {jst_now.hour}"
        )
        return

    history = load_jpmi_history()

    prices = out.get("prices_jpy_per_g", {})

    tanaka = safe_float(prices.get("tanaka_silver_buy"))
    nihon = safe_float(prices.get("nihon_silver_buy"))
    mitsubishi = safe_float(prices.get("mitsubishi_silver_buy"))
    daikichi = safe_float(prices.get("daikichi_sv1000"))
    nanboya = safe_float(prices.get("nanboya_sv1000"))
    mspi_b = safe_float(prices.get("mercari_mspi_b"))
    comex = safe_float(prices.get("comex_silver_jpy_g"))
    comex_usd = safe_float(prices.get("comex_silver_usd_oz"))
    usd_jpy = safe_float(prices.get("usd_jpy"))

    # COMEX USD/oz resilience:
    # 1. Prefer the live/manual comex_silver_usd_oz value from prices.json.
    # 2. If missing, calculate from COMEX JPY/g and USD/JPY.
    # 3. If still missing, carry forward the most recent prior history value so the premium terminal does not show a blank.
    comex_usd_source = "prices_json"

    if comex_usd is None and comex is not None and usd_jpy not in (None, 0):
        comex_usd = round((comex * 31.1035) / usd_jpy, 4)
        comex_usd_source = "calculated_from_comex_jpy_g_and_usd_jpy"
        print(f"COMEX USD/oz calculated from COMEX JPY/g and USD/JPY: {comex_usd}")

    if comex_usd is None:
        carried_forward_comex_usd = latest_history_value_before(history, date_jst, "comex_usd_oz")
        if carried_forward_comex_usd is not None:
            comex_usd = carried_forward_comex_usd
            comex_usd_source = "carried_forward_from_previous_history_row"
            warning = (
                f"COMEX USD/oz missing for {date_jst}; carried forward previous "
                f"history value {comex_usd}"
            )
            out["errors"].append(warning)
            print(warning)
        else:
            comex_usd_source = "missing"
            warning = f"COMEX USD/oz missing for {date_jst}; no previous history value available"
            out["errors"].append(warning)
            print(warning)

    rs = average_valid([tanaka, nihon, mitsubishi])
    db = average_valid([daikichi, nanboya])

    jpmi_components = [
        value for value in [rs, db, mspi_b]
        if is_valid_silver_price(value)
    ]

    jpmi_ag = (
        round(sum(jpmi_components) / len(jpmi_components), 2)
        if jpmi_components
        else None
    )

    premium_pct = calculate_premium_pct(jpmi_ag, comex)

    row = {
        "date": date_jst,
        "observation_time_jst": "19:00",
        "updated_at_utc": utc_now.isoformat(timespec="seconds"),
        "jpmi_ag_jpy_g": jpmi_ag,
        "rs_jpy_g": rs,
        "db_jpy_g": db,
        "mspi_b_jpy_g": round(mspi_b, 2) if is_valid_silver_price(mspi_b) else None,
        "comex_jpy_g": round(comex, 2) if is_valid_silver_price(comex) else None,
        "comex_usd_oz": round(comex_usd, 4) if comex_usd is not None else None,
        "comex_usd_oz_source": comex_usd_source,
        "usd_jpy": round(usd_jpy, 4) if usd_jpy is not None else None,
        "premium_pct": premium_pct,
        "components": {
            "tanaka_silver_buy": round(tanaka, 2) if is_valid_silver_price(tanaka) else None,
            "nihon_silver_buy": round(nihon, 2) if is_valid_silver_price(nihon) else None,
            "mitsubishi_silver_buy": round(mitsubishi, 2) if is_valid_silver_price(mitsubishi) else None,
            "daikichi_sv1000": round(daikichi, 2) if is_valid_silver_price(daikichi) else None,
            "nanboya_sv1000": round(nanboya, 2) if is_valid_silver_price(nanboya) else None,
        },
        "data_quality": {
            "has_rs": rs is not None,
            "has_db": db is not None,
            "has_mspi_b": is_valid_silver_price(mspi_b),
            "has_comex": is_valid_silver_price(comex),
            "has_comex_usd_oz": comex_usd is not None,
            "has_usd_jpy": usd_jpy is not None,
            "comex_usd_oz_source": comex_usd_source,
            "jpmi_component_count": len(jpmi_components),
        },
    }

    existing_index = next(
        (i for i, existing_row in enumerate(history) if existing_row.get("date") == date_jst),
        None
    )

    if existing_index is None:
        history.append(row)
        action = "created"
    else:
        history[existing_index] = row
        action = "updated"

    save_jpmi_history(history)

    print(f"JPMI daily premium history row {action}:")
    print(json.dumps(row, ensure_ascii=False, indent=2))


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

    append_daily_jpmi_history_if_ready(out)

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
