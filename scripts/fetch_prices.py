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
    return price is not None and 200 <= price <= 600


# ── Tanaka ───────────────────────────────────────────────────────────────────
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


# ── Nihon ─────────────────────────────────────────────────────────────────────
def parse_nihon(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    idx = text.find("銀")
    if idx != -1:
        snippet = text[idx:idx + 300]
        nums = re.findall(r"([\d,]+)\s*円", snippet)
        nums = [float(n.replace(",", "")) for n in nums]
        nums = [n for n in nums if 200 <= n <= 600]
        if nums:
            return min(nums)

    raise ValueError("Nihon silver not found")


# ── Mitsubishi ────────────────────────────────────────────────────────────────
def parse_mitsubishi(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    m = re.search(r"店頭価格.*?([\d,]+)\s*円/g", text)
    if m:
        return float(m.group(1).replace(",", ""))

    raise ValueError("Mitsubishi not found")


# ── Nanboya (STRICT SV1000 ONLY) ─────────────────────────────────────────────
def parse_nanboya(html):
    soup = BeautifulSoup(html, "html.parser")

    # find Sv1000 row ONLY (middle row)
    for row in soup.find_all("tr"):
        txt = row.get_text(" ", strip=True)

        # must be plain Sv1000
        if "Sv1000" in txt and "インゴット" not in txt:

            m = re.search(r"([\d,]+)\s*円", txt)
            if m:
                val = float(m.group(1).replace(",", ""))

                if is_valid_silver_price(val):
                    return val

    raise ValueError("Nanboya Sv1000 not found")


# ── Daikichi ──────────────────────────────────────────────────────────────────
def parse_daikichi(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    m = re.search(r"1g\s*([\d,]+)\s*円", text)
    if m:
        val = float(m.group(1).replace(",", ""))
        if is_valid_silver_price(val):
            return val

    raise ValueError("Daikichi not found")


def safe_get(name, fn):
    try:
        return fn(), None
    except Exception as e:
        return None, f"{name}: {e}"


def main():
    out = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "prices_jpy_per_g": {},
        "errors": [],
        "sources": SOURCES
    }

    v, err = safe_get("tanaka", lambda: parse_tanaka(get_html(SOURCES["tanaka"])))
    if v: out["prices_jpy_per_g"]["tanaka_silver_buy"] = v

    v, err = safe_get("nihon", lambda: parse_nihon(get_html(SOURCES["nihon"], "euc-jp")))
    if v: out["prices_jpy_per_g"]["nihon_silver_buy"] = v

    v, err = safe_get("mitsubishi", lambda: parse_mitsubishi(get_html(SOURCES["mitsubishi"])))
    if v: out["prices_jpy_per_g"]["mitsubishi_silver_buy"] = v

    v, err = safe_get("nanboya", lambda: parse_nanboya(get_html(SOURCES["nanboya"])))
    if v: out["prices_jpy_per_g"]["nanboya_sv1000"] = v

    v, err = safe_get("daikichi", lambda: parse_daikichi(get_html(SOURCES["daikichi"])))
    if v: out["prices_jpy_per_g"]["daikichi_sv1000"] = v

    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
