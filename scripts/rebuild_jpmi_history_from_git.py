import json
import subprocess
from statistics import median
from datetime import datetime, timezone, timedelta

PRICES_FILE = "prices.json"
PRICES_HISTORY_FILE = "prices-history.json"
LIVE_HISTORY_FILE = "jpmi-history.json"
OUTPUT_FILE = "jpmi-history-rebuilt.json"

START_DATE = "2026-03-01"

TROY_OUNCE_GRAMS = 31.1035

W_RS = 0.55
W_DB = 0.30
W_SR = 0.15


def run_git(args):
    return subprocess.check_output(["git"] + args, text=True).strip()


def safe_float(value):
    try:
        if value is None:
            return None
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def valid_price(value):
    value = safe_float(value)
    return value is not None and 200 <= value <= 700


def valid_usd_jpy(value):
    value = safe_float(value)
    return value is not None and 80 <= value <= 250


def valid_comex_usd_oz(value):
    value = safe_float(value)
    return value is not None and 5 <= value <= 300


def round_or_none(value):
    value = safe_float(value)
    return round(value, 2) if valid_price(value) else None


def round_usd_jpy_or_none(value):
    value = safe_float(value)
    return round(value, 4) if valid_usd_jpy(value) else None


def round_comex_usd_or_none(value):
    value = safe_float(value)
    return round(value, 4) if valid_comex_usd_oz(value) else None


def current_jst_date():
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date().isoformat()


def is_weekend(date_string):
    try:
        return datetime.fromisoformat(date_string).weekday() >= 5
    except Exception:
        return False


def clean_refinery_values(values):
    valid = {k: safe_float(v) for k, v in values.items() if valid_price(v)}

    if len(valid) < 3:
        return valid, {}

    med = median(valid.values())
    cleaned = {}
    outliers = {}

    for key, value in valid.items():
        abs_gap = abs(value - med)
        pct_gap = abs_gap / med if med else 0

        if abs_gap > 60 and pct_gap > 0.15:
            outliers[key] = value
        else:
            cleaned[key] = value

    return cleaned, outliers


def avg(values):
    cleaned = [safe_float(v) for v in values if valid_price(v)]
    if not cleaned:
        return None
    return round(sum(cleaned) / len(cleaned), 2)


def weighted_jpmi(rs, db, sr):
    total = 0
    weight = 0

    if valid_price(rs):
        total += rs * W_RS
        weight += W_RS

    if valid_price(db):
        total += db * W_DB
        weight += W_DB

    if valid_price(sr):
        total += sr * W_SR
        weight += W_SR

    if weight == 0:
        return None

    return round(total / weight, 2)


def premium_pct(physical, comex):
    physical = safe_float(physical)
    comex = safe_float(comex)

    if physical is None or comex is None or comex == 0:
        return None

    return round(((physical - comex) / comex) * 100, 2)


def calculate_comex_usd_oz(comex_jpy_g, usd_jpy):
    comex_jpy_g = safe_float(comex_jpy_g)
    usd_jpy = safe_float(usd_jpy)

    if not valid_price(comex_jpy_g) or not valid_usd_jpy(usd_jpy):
        return None

    return round((comex_jpy_g * TROY_OUNCE_GRAMS) / usd_jpy, 4)


def load_json_file(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def get_commit_date(commit):
    raw = run_git(["show", "-s", "--format=%cI", commit])
    return raw[:10]


def get_prices_json_at_commit(commit):
    try:
        raw = run_git(["show", f"{commit}:{PRICES_FILE}"])
        return json.loads(raw)
    except Exception:
        return None


def build_rs_db_from_git():
    commits_raw = run_git([
        "log",
        "--reverse",
        f"--since={START_DATE}",
        "--format=%H",
        "--",
        PRICES_FILE
    ])

    commits = [c for c in commits_raw.splitlines() if c.strip()]
    daily = {}

    for commit in commits:
        date = get_commit_date(commit)
        data = get_prices_json_at_commit(commit)

        if not data:
            continue

        prices = data.get("prices_jpy_per_g", {})

        tanaka = safe_float(prices.get("tanaka_silver_buy"))
        nihon = safe_float(prices.get("nihon_silver_buy"))
        mitsubishi = safe_float(prices.get("mitsubishi_silver_buy"))
        nanboya = safe_float(prices.get("nanboya_sv1000"))
        daikichi = safe_float(prices.get("daikichi_sv1000"))

        usd_jpy = safe_float(prices.get("usd_jpy"))
        comex_usd_oz = safe_float(prices.get("comex_silver_usd_oz"))
        comex_jpy_g = safe_float(prices.get("comex_silver_jpy_g"))

        if not valid_comex_usd_oz(comex_usd_oz):
            comex_usd_oz = calculate_comex_usd_oz(comex_jpy_g, usd_jpy)

        refinery_raw = {
            "tanaka_silver_buy": tanaka,
            "nihon_silver_buy": nihon,
            "mitsubishi_silver_buy": mitsubishi,
        }

        refinery_clean, refinery_outliers = clean_refinery_values(refinery_raw)

        rs = avg(refinery_clean.values())
        db = avg([nanboya, daikichi])

        daily[date] = {
            "date": date,
            "observation_time_jst": "19:00",
            "rs_jpy_g": rs,
            "db_jpy_g": db,
            "comex_jpy_g": round_or_none(comex_jpy_g),
            "comex_usd_oz": round_comex_usd_or_none(comex_usd_oz),
            "usd_jpy": round_usd_jpy_or_none(usd_jpy),
            "components": {
                "tanaka_silver_buy": round_or_none(tanaka),
                "nihon_silver_buy": round_or_none(nihon),
                "mitsubishi_silver_buy": round_or_none(mitsubishi),
                "nanboya_sv1000": round_or_none(nanboya),
                "daikichi_sv1000": round_or_none(daikichi),
            },
            "cleaning": {
                "rs_components_used": list(refinery_clean.keys()),
                "rs_outliers_excluded": {
                    key: round(value, 2)
                    for key, value in refinery_outliers.items()
                },
            },
            "source": "rebuilt_from_git_prices_json",
            "git_commit": commit,
        }

    return daily


def build_market_history():
    rows = load_json_file(PRICES_HISTORY_FILE, [])
    daily = {}

    for row in rows:
        t = row.get("t") or row.get("date")
        if not t:
            continue

        date = str(t)[:10]

        comex_jpy_g = safe_float(
            row.get("comex_jpy_g")
            or row.get("comex_silver_jpy_g")
        )

        comex_usd_oz = safe_float(
            row.get("comex_usd_oz")
            or row.get("comex_silver_usd_oz")
        )

        usd_jpy = safe_float(row.get("usd_jpy"))

        mspi = safe_float(
            row.get("mspi_b_jpy_g")
            or row.get("mercari_mspi_b")
        )

        if not valid_comex_usd_oz(comex_usd_oz):
            comex_usd_oz = calculate_comex_usd_oz(comex_jpy_g, usd_jpy)

        if date not in daily:
            daily[date] = {
                "date": date,
                "mspi_b_jpy_g": None,
                "comex_jpy_g": None,
                "comex_usd_oz": None,
                "usd_jpy": None,
            }

        if valid_price(mspi):
            daily[date]["mspi_b_jpy_g"] = round(mspi, 2)

        if valid_price(comex_jpy_g):
            daily[date]["comex_jpy_g"] = round(comex_jpy_g, 2)

        if valid_comex_usd_oz(comex_usd_oz):
            daily[date]["comex_usd_oz"] = round(comex_usd_oz, 4)

        if valid_usd_jpy(usd_jpy):
            daily[date]["usd_jpy"] = round(usd_jpy, 4)

    return daily


def load_live_real_rows():
    rows = load_json_file(LIVE_HISTORY_FILE, [])
    out = {}

    for row in rows:
        date = row.get("date")
        if not date:
            continue

        if row.get("components") and row.get("data_quality"):
            out[date] = row

    return out


def main():
    print("Rebuilding JPMI history from Git commit history with outlier cleaning...")

    today_jst = current_jst_date()

    rs_db_history = build_rs_db_from_git()
    market_history = build_market_history()
    live_rows = load_live_real_rows()

    all_dates = sorted(
        date for date in (set(rs_db_history) | set(market_history) | set(live_rows))
        if date < today_jst and not is_weekend(date)
    )

    rebuilt = []

    for date in all_dates:
        base = {
            "date": date,
            "observation_time_jst": "19:00",
            "rs_jpy_g": None,
            "db_jpy_g": None,
            "mspi_b_jpy_g": None,
            "comex_jpy_g": None,
            "comex_usd_oz": None,
            "usd_jpy": None,
            "jpmi_ag_jpy_g": None,
            "premium_pct": None,
            "components": {},
            "cleaning": {},
            "data_quality": {},
            "source": "rebuilt_history",
        }

        if date in rs_db_history:
            base.update(rs_db_history[date])

        if date in market_history:
            market_row = market_history[date]

            if market_row.get("mspi_b_jpy_g") is not None:
                base["mspi_b_jpy_g"] = market_row.get("mspi_b_jpy_g")

            if market_row.get("comex_jpy_g") is not None:
                base["comex_jpy_g"] = market_row.get("comex_jpy_g")

            if market_row.get("comex_usd_oz") is not None:
                base["comex_usd_oz"] = market_row.get("comex_usd_oz")

            if market_row.get("usd_jpy") is not None:
                base["usd_jpy"] = market_row.get("usd_jpy")

        if date in live_rows:
            base.update(live_rows[date])
            base["source"] = "live_jpmi_history"

        if not valid_comex_usd_oz(base.get("comex_usd_oz")):
            base["comex_usd_oz"] = calculate_comex_usd_oz(
                base.get("comex_jpy_g"),
                base.get("usd_jpy")
            )

        rs = safe_float(base.get("rs_jpy_g"))
        db = safe_float(base.get("db_jpy_g"))
        sr = safe_float(base.get("mspi_b_jpy_g"))
        comex = safe_float(base.get("comex_jpy_g"))

        base["jpmi_ag_jpy_g"] = weighted_jpmi(rs, db, sr)
        base["premium_pct"] = premium_pct(base["jpmi_ag_jpy_g"], comex)

        base["data_quality"] = {
            "has_rs": valid_price(rs),
            "has_db": valid_price(db),
            "has_mspi_b": valid_price(sr),
            "has_comex": valid_price(comex),
            "has_comex_usd_oz": valid_comex_usd_oz(base.get("comex_usd_oz")),
            "has_usd_jpy": valid_usd_jpy(base.get("usd_jpy")),
            "jpmi_component_count": sum([
                1 if valid_price(rs) else 0,
                1 if valid_price(db) else 0,
                1 if valid_price(sr) else 0,
            ]),
        }

        rebuilt.append(base)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(rebuilt, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(rebuilt)} rows to {OUTPUT_FILE}")
    print(f"Excluded current JST date from rebuild: {today_jst}")
    print("Excluded Saturday/Sunday rows from rebuilt history.")
    print("This script does NOT overwrite jpmi-history.json.")


if __name__ == "__main__":
    main()
