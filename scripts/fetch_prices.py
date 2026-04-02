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


def is_valid_silver_price(price):
    # Guardrail: keep obvious garbage like 1014 out of the silver index
    return price is not None and 200 <= price <= 600


# ── Tanaka (English page, buyback = "TANAKA retail buying price" for SILVER) ──
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
                if not m:
                    continue
                val = float(m.group())
                if is_valid_silver_price(val):
                    return val
    raise ValueError("Tanaka SILVER buyback row not found")


# ── Nihon Material ────────────────────────────────────────────────────────────
def parse_nihon(html):
    soup = BeautifulSoup(html, "html.parser")
    full_text = soup.get_text(" ", strip=True)

    # Page-wide no-data state for silver buyback
    if "銀" in full_text and re.search(r"買\s*[-－ー—]{1,}\s*円", full_text):
        # Don't trust fallback numbers if buyback is explicitly unavailable
        silver_zone = full_text[full_text.find("銀"): full_text.find("銀") + 300] if "銀" in full_text else full_text
        if re.search(r"買\s*[-－ー—]{1,}\s*円", silver_zone):
            return None

    # 1) Best path: find the actual silver row and extract row-local prices only.
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        cell_texts = [c.get_text(" ", strip=True) for c in cells]
        joined = " ".join(cell_texts)

        # Must be the silver row
        if "銀" not in joined:
            continue

        # Explicit no-data row like "買 -- 円"
        if re.search(r"買\s*[-－ー—]{1,}\s*円", joined):
            return None

        # Try to extract prices from that row only
        nums = [
            float(x.replace(",", ""))
            for x in re.findall(r"([\d,]+(?:\.\d+)?)\s*円", joined)
        ]
        nums = [n for n in nums if 100 <= n <= 5000]

        # Nihon row usually has sell / buy / change.
        # We want the lower of the two main row prices, not change.
        if len(nums) >= 2:
            top_two = sorted(nums, reverse=True)[:2]
            buyback = min(top_two)
            if is_valid_silver_price(buyback):
                return buyback

    # 2) Fallback: use page text, but tightly around the silver section
    idx = full_text.find("銀")
    if idx != -1:
        tail = full_text[idx:idx + 300]

        if re.search(r"買\s*[-－ー—]{1,}\s*円", tail):
            return None

        nums = [
            float(x.replace(",", ""))
            for x in re.findall(r"([\d,]+(?:\.\d+)?)\s*円", tail)
        ]
        nums = [n for n in nums if 100 <= n <= 5000]
        if len(nums) >= 2:
            top_two = sorted(nums, reverse=True)[:2]
            buyback = min(top_two)
            if is_valid_silver_price(buyback):
                return buyback

    raise ValueError("Nihon Material silver buyback not found")


# ── Mitsubishi ────────────────────────────────────────────────────────────────
def parse_mitsubishi(html):
    soup = BeautifulSoup(html, "html.parser")

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            row_text = " ".join(c.get_text(strip=True) for c in cells)
            if "店頭価格" in row_text:
                prices = [
                    float(p.replace(",", ""))
                    for p in re.findall(r"([\d,]+(?:\.\d+)?)\s*円/g", row_text)
                    if 50 <= float(p.replace(",", "")) <= 5000
                ]
                if len(prices) >= 2 and is_valid_silver_price(prices[1]):
                    return prices[1]
                if len(prices) == 1 and is_valid_silver_price(prices[0]):
                    return prices[0]

    text = soup.get_text(" ", strip=True)
    idx = text.find("店頭価格")
    if idx != -1:
        snippet = text[idx:idx + 400]
        prices = [
            float(p.replace(",", ""))
            for p in re.findall(r"([\d,]+(?:\.\d+)?)\s*円/g", snippet)
            if 50 <= float(p.replace(",", "")) <= 5000
        ]
        if len(prices) >= 2 and is_valid_silver_price(prices[1]):
            return prices[1]
        if prices and is_valid_silver_price(prices[0]):
            return prices[0]

    raise ValueError("Mitsubishi silver buyback not found")


# ── Nanboya ───────────────────────────────────────────────────────────────────
def parse_nanboya(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    # Prefer plain Sv1000 row, not "Sv1000 インゴット"
    m = re.search(r"Sv1000\s*\n?\s*([\d,]+)\s*円", text)
    if m:
        val = float(m.group(1).replace(",", ""))
        if is_valid_silver_price(val):
            return val

    # Fallback: old commentary-style text
    m = re.search(r"銀相場は\s*([\d,]+)\s*円", text)
    if m:
        val = float(m.group(1).replace(",", ""))
        if is_valid_silver_price(val):
            return val

    raise ValueError("Nanboya Sv1000 price not found")


# ── Daikichi ──────────────────────────────────────────────────────────────────
def parse_daikichi(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    # Prefer the lower detail table row: "1g ... 400 円"
    m = re.search(r"1g\s*([\d,]+)\s*円", text)
    if m:
        val = float(m.group(1).replace(",", ""))
        if is_valid_silver_price(val):
            return val

    raise ValueError("Daikichi 1g table price not found")


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


def set_or_remove_price(out, key, value, source_name):
    prices = out.setdefault("prices_jpy_per_g", {})
    if is_valid_silver_price(value):
        prices[key] = value
    else:
        # Remove stale bad values so they don't keep poisoning the index
        prices.pop(key, None)
        if value is not None:
            out["errors"].append(f"{source_name}: invalid silver price skipped: {value}")


def main():
    out = load_existing_prices()
    out["updated_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out.setdefault("prices_jpy_per_g", {})
    out["errors"] = []
    out["sources"] = SOURCES

    v, err = safe_get("tanaka", lambda: parse_tanaka(get_html(SOURCES["tanaka"])))
    if err:
        out["errors"].append(err)
    set_or_remove_price(out, "tanaka_silver_buy", v, "tanaka")

    v, err = safe_get("nihon", lambda: parse_nihon(get_html(SOURCES["nihon"], encoding="euc-jp")))
    if err:
        out["errors"].append(err)
    set_or_remove_price(out, "nihon_silver_buy", v, "nihon")

    v, err = safe_get("mitsubishi", lambda: parse_mitsubishi(get_html(SOURCES["mitsubishi"])))
    if err:
        out["errors"].append(err)
    set_or_remove_price(out, "mitsubishi_silver_buy", v, "mitsubishi")

    v, err = safe_get("nanboya", lambda: parse_nanboya(get_html(SOURCES["nanboya"])))
    if err:
        out["errors"].append(err)
    set_or_remove_price(out, "nanboya_sv1000", v, "nanboya")

    v, err = safe_get("daikichi", lambda: parse_daikichi(get_html(SOURCES["daikichi"])))
    if err:
        out["errors"].append(err)
    set_or_remove_price(out, "daikichi_sv1000", v, "daikichi")

    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("prices.json updated:")
    for k in [
        "tanaka_silver_buy",
        "nihon_silver_buy",
        "mitsubishi_silver_buy",
        "nanboya_sv1000",
        "daikichi_sv1000",
        "mercari_mspi_b",
        "mercari_mspi_b_listings",
        "mercari_mspi_b_avg_jpy",
    ]:
        if k in out.get("prices_jpy_per_g", {}):
            print(f"  {k}: {out['prices_jpy_per_g'][k]}")

    if out["errors"]:
        print("Warnings:")
        for e in out["errors"]:
            print(" -", e)


if __name__ == "__main__":
    main()
