"""
app.py — Parataxis Volume Tracker v2
Flask API serving KRW/USD turnover volume for Korean KOSDAQ stocks.
Volume = shares_traded × closing_price (KRW), converted to USD via live FX rate.
Primary source: Naver Finance | Fallback: Yahoo Finance
"""

import logging
import time
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import httpx
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

SEOUL = ZoneInfo("Asia/Seoul")

# ── Stock configuration ────────────────────────────────────────────────────────
STOCKS = {
    "parataxis_korea": {"name": "Parataxis Korea",    "ticker": "288330", "yahoo": "288330.KQ", "halted_after": "2026-04-07"},
    "parataxis_eth":   {"name": "Parataxis Ethereum", "ticker": "290560", "yahoo": "290560.KQ", "halted_after": None},
    "bitmax":          {"name": "Bitmax",              "ticker": "377030", "yahoo": "377030.KQ", "halted_after": None},
    "bitplanet":       {"name": "Bitplanet",           "ticker": "049470", "yahoo": "049470.KQ", "halted_after": None},
}

# ── In-memory cache ────────────────────────────────────────────────────────────
_cache: dict = {}
CACHE_TTL    = 30    # seconds
FX_CACHE_TTL = 300   # seconds (5 min)


def _cache_get(key: str, ttl: int = CACHE_TTL):
    entry = _cache.get(key)
    if entry and (time.monotonic() - entry["ts"]) < ttl:
        return entry["data"]
    return None


def _cache_set(key: str, data):
    _cache[key] = {"data": data, "ts": time.monotonic()}


# ── FX rate ────────────────────────────────────────────────────────────────────
def get_usd_krw_rate() -> float:
    cached = _cache_get("fx:usd_krw", FX_CACHE_TTL)
    if cached:
        return cached

    try:
        with httpx.Client(timeout=8) as client:
            r = client.get("https://open.er-api.com/v6/latest/USD")
        if r.status_code == 200:
            rate = r.json()["rates"]["KRW"]
            _cache_set("fx:usd_krw", rate)
            log.info("[fx] USD/KRW = %.2f (ExchangeRate-API)", rate)
            return rate
    except Exception as e:
        log.warning("[fx] ExchangeRate-API failed: %s", e)

    try:
        with httpx.Client(timeout=8) as client:
            r = client.get("https://api.frankfurter.app/latest?from=USD&to=KRW")
        if r.status_code == 200:
            rate = r.json()["rates"]["KRW"]
            _cache_set("fx:usd_krw", rate)
            log.info("[fx] USD/KRW = %.2f (Frankfurter)", rate)
            return rate
    except Exception as e:
        log.warning("[fx] Frankfurter failed: %s", e)

    log.warning("[fx] All FX sources failed, using fallback 1350.0")
    return 1350.0


# ── Period helpers ─────────────────────────────────────────────────────────────
def _period_to_naver(period: str):
    return {
        "1m":  ("day",   30),
        "3m":  ("day",   90),
        "6m":  ("day",   180),
        "12m": ("day",   365),
        "24m": ("month", 24),
    }.get(period, ("day", 30))


def _period_to_yahoo(period: str):
    return {
        "1m":  ("1mo", "1d"),
        "3m":  ("3mo", "1d"),
        "6m":  ("6mo", "1d"),
        "12m": ("1y",  "1d"),
        "24m": ("2y",  "1wk"),
    }.get(period, ("1mo", "1d"))


# ── Naver Finance ──────────────────────────────────────────────────────────────
def _fetch_naver(ticker: str, period: str, halted_after=None) -> dict | None:
    timeframe, count = _period_to_naver(period)
    url = (f"https://fchart.stock.naver.com/sise.nhn"
           f"?symbol={ticker}&timeframe={timeframe}&count={count}&requestType=0")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Referer":    "https://finance.naver.com/",
    }
    try:
        with httpx.Client(timeout=10, headers=headers, follow_redirects=True) as client:
            r = client.get(url)
        if r.status_code != 200:
            return None

        lines = r.text.split('<item data="')
        dates, volumes, closes = [], [], []
        for line in lines[1:]:
            raw   = line.split('"')[0]
            parts = raw.split("|")
            if len(parts) >= 6:
                try:
                    ds  = parts[0]
                    fmt = f"{ds[:4]}-{ds[4:6]}-{ds[6:]}"
                    if halted_after and fmt > halted_after:
                        continue
                    dates.append(fmt)
                    volumes.append(int(parts[5]))
                    closes.append(float(parts[4]))
                except (ValueError, IndexError):
                    continue

        return {"dates": dates, "volumes": volumes, "closes": closes, "source": "naver"} if dates else None
    except Exception as exc:
        log.warning("[naver] %s: %s", ticker, exc)
        return None


# ── Yahoo Finance fallback ─────────────────────────────────────────────────────
def _fetch_yahoo(yahoo_ticker: str, period: str, halted_after=None) -> dict | None:
    range_val, interval = _period_to_yahoo(period)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_ticker}?range={range_val}&interval={interval}"
    try:
        with httpx.Client(timeout=10, headers={"User-Agent": "Mozilla/5.0 (compatible; bot)"}) as client:
            r = client.get(url)
        if r.status_code != 200:
            return None

        result = r.json().get("chart", {}).get("result", [])
        if not result:
            return None

        timestamps = result[0].get("timestamp", [])
        quote      = result[0].get("indicators", {}).get("quote", [{}])[0]
        raw_vols   = quote.get("volume", [])
        raw_close  = quote.get("close", [])

        dates, volumes, closes = [], [], []
        for ts, vol, close in zip(timestamps, raw_vols, raw_close):
            if vol is None or close is None:
                continue
            fmt = datetime.fromtimestamp(ts, tz=SEOUL).strftime("%Y-%m-%d")
            if halted_after and fmt > halted_after:
                continue
            dates.append(fmt)
            volumes.append(int(vol))
            closes.append(float(close))

        return {"dates": dates, "volumes": volumes, "closes": closes, "source": "yahoo"} if dates else None
    except Exception as exc:
        log.warning("[yahoo] %s: %s", yahoo_ticker, exc)
        return None


# ── Helpers ────────────────────────────────────────────────────────────────────
def _compute_turnover(raw: dict, fx_rate: float) -> dict:
    krw = [v * c for v, c in zip(raw["volumes"], raw["closes"])]
    usd = [t / fx_rate for t in krw]
    return {"dates": raw["dates"], "turnover_krw": krw, "turnover_usd": usd, "source": raw["source"]}


def _thirty_day_avg(turnover: list, dates: list) -> float | None:
    if not dates:
        return None
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    recent = [t for d, t in zip(dates, turnover) if d >= cutoff] or turnover[-30:]
    return sum(recent) / len(recent) if recent else None


# ── API ────────────────────────────────────────────────────────────────────────
@app.route("/api/volume")
def api_volume():
    period   = request.args.get("period",   "1m").lower()
    currency = request.args.get("currency", "usd").lower()
    if period not in {"1m", "3m", "6m", "12m", "24m"}:
        period = "1m"
    if currency not in ("krw", "usd"):
        currency = "usd"

    fx_rate  = get_usd_krw_rate()
    response = {}

    for key, stock in STOCKS.items():
        cache_key = f"{key}:{period}"
        cached    = _cache_get(cache_key)
        if not cached:
            raw = _fetch_naver(stock["ticker"], period, stock.get("halted_after"))
            if not raw:
                raw = _fetch_yahoo(stock["yahoo"], period, stock.get("halted_after"))
            if raw:
                cached = _compute_turnover(raw, fx_rate)
                _cache_set(cache_key, cached)

        if cached:
            turnover = cached["turnover_usd"] if currency == "usd" else cached["turnover_krw"]
            response[key] = {
                "name":    stock["name"],
                "dates":   cached["dates"],
                "turnover": turnover,
                "avg_30d": _thirty_day_avg(turnover, cached["dates"]),
                "source":  cached["source"],
                "halted":  stock.get("halted_after") is not None,
            }
        else:
            response[key] = {
                "name": stock["name"], "dates": [], "turnover": [],
                "avg_30d": None, "source": "error", "error": "All sources failed",
                "halted": stock.get("halted_after") is not None,
            }

    # ── Market share (line) ──────────────────────────────────────────────────
    all_dates   = sorted({d for v in response.values() for d in v.get("dates", [])})
    date_totals = {d: 0.0 for d in all_dates}
    for v in response.values():
        for d, t in zip(v.get("dates", []), v.get("turnover", [])):
            date_totals[d] += t

    market_share = {}
    for key, v in response.items():
        shares = [
            round(t / date_totals[d] * 100, 2) if date_totals.get(d, 0) > 0 else 0
            for d, t in zip(v.get("dates", []), v.get("turnover", []))
        ]
        market_share[key] = {"name": v["name"], "dates": v.get("dates", []), "shares": shares}

    # ── Pie chart ────────────────────────────────────────────────────────────
    totals   = {k: sum(v.get("turnover", [])) for k, v in response.items()}
    total_all = sum(totals.values())
    pie = {
        k: {"name": response[k]["name"], "total": totals[k],
            "pct": round(totals[k] / total_all * 100, 2) if total_all > 0 else 0}
        for k in totals
    }

    return jsonify({
        "data": response, "market_share": market_share, "pie": pie,
        "period": period, "currency": currency, "fx_rate": fx_rate,
        "updated_at": datetime.now(SEOUL).strftime("%Y-%m-%d %H:%M KST"),
    })


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
