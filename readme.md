# Parataxis Volume Tracker

KOSDAQ trading volume tracker for four companies in our portfolio — Parataxis Korea (288330), Parataxis Ethereum (290560), Bitmax (377030), and Bitplanet (049470).

## What it does

- Daily trading volume charts for all four companies pulled from Naver Finance
- Volume calculated as shares traded × closing price so it's comparable across companies
- KRW/USD toggle with live exchange rate conversion
- Date range filters — 1M, 3M, 6M, 12M, 24M
- Three chart views: bar chart (absolute volume), pie chart (market share), line chart (market share over time)
- Stats cards showing latest volume, 30-day average, highest, and lowest
- REST API at `/api/volume` used by our internal executive dashboard

## Why I built it

We needed a way to track trading activity across our KOSDAQ holdings in one place rather than checking each stock individually on Naver or HTS. Also feeds into the broader internal dashboard so execs can see volume trends alongside price and news data.

## Stack

Python 3.12 · Flask · Chart.js · Naver Finance · flask-cors · Railway (deployment)
