# Parataxis Volume Tracker

Historical trading volume dashboard for Parataxis Ethereum (290560), Bitmax (241590), and Bitplanet (034230) on KOSDAQ.

## Stack

- **Backend**: Flask + httpx
- **Frontend**: Vanilla JS + Chart.js
- **Data**: Naver Finance (primary) → Yahoo Finance (fallback)
- **Deploy**: Railway

## Local setup

```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:5000
```

## Railway deployment

1. Push this folder to a GitHub repo
2. In Railway: New Project → Deploy from GitHub → select the repo
3. Railway auto-detects the Procfile and deploys
4. No environment variables required

## Changing stock tickers

Edit the `STOCKS` dict in `app.py`:

```python
STOCKS = {
    "parataxis_eth": {"name": "Parataxis Ethereum", "ticker": "290560", "yahoo": "290560.KQ"},
    "bitmax":        {"name": "Bitmax",              "ticker": "241590", "yahoo": "241590.KQ"},
    "bitplanet":     {"name": "Bitplanet",           "ticker": "034230", "yahoo": "034230.KQ"},
}
```

- `ticker` = Naver Finance ticker (6-digit KOSDAQ code)
- `yahoo`  = Yahoo Finance symbol (add `.KQ` suffix for KOSDAQ)

Also update the matching `STOCKS` object in `templates/index.html` for the frontend labels and colors.