"""
app.py — Parataxis Volume Tracker backend
Flask API serving historical trading volume for Korean KOSDAQ stocks.
Primary source: Naver Finance | Fallback: Yahoo Finance
"""

import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

SEOUL = ZoneInfo("Asia/Seoul")

# ── Stock configuration ────────────────────────────────────────────────────────
STOCKS = {
    "parataxis_eth": {"name": "Parataxis Ethereum", "ticker": "290560", "yahoo": "290560.KQ"},
    "bitmax":        {"name": "Bitmax",              "ticker": "377030", "yahoo": "377030.KQ"},
    "bitplanet":     {"name": "Bitplanet",           "ticker": "049470", "yahoo": "049470.KQ"},
}

# ── In-memory cache ────────────────────────────────────────────────────────────
_cache: dict = {}
CACHE_TTL = 30   # 30 seconds — matches frontend auto-refresh


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and (time.monotonic() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data):
    _cache[key] = {"data": data, "ts": time.monotonic()}


# ── Naver Finance ──────────────────────────────────────────────────────────────
def _fetch_naver(ticker: str, period: str) -> dict | None:
    """
    Fetch historical volume from Naver Finance.
    Returns {"dates": [...], "volumes": [...], "source": "naver"} or None.
    """
    # Map period to Naver timeframe params
    period_map = {
        "1d":  ("day",   1),
        "5d":  ("day",   5),
        "1m":  ("day",   30),
        "1y":  ("day",   365),
        "5y":  ("month", 60),
        "all": ("month", 120),
    }
    timeframe, count = period_map.get(period, ("day", 30))

    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={ticker}&timeframe={timeframe}&count={count}&requestType=0"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer":    "https://finance.naver.com/",
            "Accept":     "text/xml,application/xml",
        }
        with httpx.Client(timeout=10, headers=headers, follow_redirects=True) as client:
            r = client.get(url)
        if r.status_code != 200:
            log.warning("[naver] %s returned %d", ticker, r.status_code)
            return None

        # Naver returns pipe-delimited data inside <item> tags
        # Format: date|open|high|low|close|volume
        lines = r.text.split("<item data=\"")
        dates, volumes = [], []
        for line in lines[1:]:
            raw = line.split("\"")[0]
            parts = raw.split("|")
            if len(parts) >= 6:
                try:
                    date_str = parts[0]
                    volume   = int(parts[5])
                    # Format date: YYYYMMDD -> YYYY-MM-DD
                    formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
                    dates.append(formatted)
                    volumes.append(volume)
                except (ValueError, IndexError):
                    continue

        if not dates:
            return None

        return {"dates": dates, "volumes": volumes, "source": "naver"}

    except Exception as exc:
        log.warning("[naver] fetch error for %s: %s", ticker, exc)
        return None


# ── Yahoo Finance fallback ─────────────────────────────────────────────────────
def _fetch_yahoo(yahoo_ticker: str, period: str) -> dict | None:
    """
    Fetch historical volume from Yahoo Finance as fallback.
    Returns {"dates": [...], "volumes": [...], "source": "yahoo"} or None.
    """
    period_map = {
        "1d":  ("1d",  "5m"),
        "5d":  ("5d",  "1h"),
        "1m":  ("1mo", "1d"),
        "1y":  ("1y",  "1d"),
        "5y":  ("5y",  "1wk"),
        "all": ("max", "1mo"),
    }
    range_val, interval = period_map.get(period, ("1mo", "1d"))

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_ticker}?range={range_val}&interval={interval}"

    try:
        with httpx.Client(timeout=10, headers={"User-Agent": "Mozilla/5.0 (compatible; bot)"}) as client:
            r = client.get(url)
        if r.status_code != 200:
            log.warning("[yahoo] %s returned %d", yahoo_ticker, r.status_code)
            return None

        data = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None

        timestamps = result[0].get("timestamp", [])
        volumes    = result[0].get("indicators", {}).get("quote", [{}])[0].get("volume", [])

        if not timestamps or not volumes:
            return None

        dates = []
        vols  = []
        for ts, vol in zip(timestamps, volumes):
            if vol is None:
                continue
            dt = datetime.fromtimestamp(ts, tz=SEOUL)
            dates.append(dt.strftime("%Y-%m-%d"))
            vols.append(int(vol))

        if not dates:
            return None

        return {"dates": dates, "volumes": vols, "source": "yahoo"}

    except Exception as exc:
        log.warning("[yahoo] fetch error for %s: %s", yahoo_ticker, exc)
        return None


# ── API routes ─────────────────────────────────────────────────────────────────
@app.route("/api/volume")
def api_volume():
    """
    GET /api/volume?period=1m
    Returns volume data for all three stocks.
    """
    period = request.args.get("period", "1m").lower()
    valid_periods = {"1d", "5d", "1m", "1y", "5y", "all"}
    if period not in valid_periods:
        period = "1m"

    response = {}
    for key, stock in STOCKS.items():
        cache_key = f"{key}:{period}"
        cached = _cache_get(cache_key)
        if cached:
            response[key] = cached
            continue

        # Try Naver first
        data = _fetch_naver(stock["ticker"], period)

        # Fallback to Yahoo
        if not data:
            log.info("[%s] Naver failed, trying Yahoo...", key)
            data = _fetch_yahoo(stock["yahoo"], period)

        if data:
            data["name"] = stock["name"]
            _cache_set(cache_key, data)
            response[key] = data
        else:
            response[key] = {
                "name": stock["name"],
                "dates": [],
                "volumes": [],
                "source": "error",
                "error": "All sources failed"
            }

    return jsonify({
        "data":       response,
        "period":     period,
        "updated_at": datetime.now(SEOUL).strftime("%Y-%m-%d %H:%M KST"),
    })


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
