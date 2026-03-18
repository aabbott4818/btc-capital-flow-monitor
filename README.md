# Bitcoin Capital Flow Monitor

Live Bitcoin capital flow dashboard with real-time data from CoinGecko, Alternative.me, mempool.space, and BitcoinTreasuries.net.

## Features

- **Live BTC price** (60-second refresh)
- **Fear & Greed Index** (live from Alternative.me)
- **Halving countdown** (calculated from live block height via mempool.space)
- **Hash rate** (live from mempool.space)
- **Corporate treasury tracker** with live P&L
- **ETF flow data** (curated from public filings)
- **Power Law corridor** with fair value / support / resistance bands
- **DCA Calculator** with 5-year and 10-year projections
- **On-Chain metrics** (curated; live data requires Glassnode/CryptoQuant API keys)
- Auto-refresh every 5 minutes
- Mobile-first responsive design
- Plotly.js interactive charts

## Architecture

- **Backend**: FastAPI (Python) — proxies external APIs with in-memory caching
- **Frontend**: Single-page HTML/CSS/JS with Plotly charts
- Both served from the same process on the same port

## Deploy to Render (Free)

### One-Click Deploy

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/YOUR_USERNAME/btc-capital-flow-monitor)

### Manual Deploy

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) and sign up (free)
3. Click **New** → **Web Service**
4. Connect your GitHub repo
5. Configure:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: Free
6. Click **Create Web Service**

Your app will be live at `https://your-service-name.onrender.com` within a few minutes.

### Note on Free Tier

Render's free tier spins down after 15 minutes of inactivity. The first request after sleep takes ~30 seconds to wake up, then it runs normally. For always-on, upgrade to the $7/month Starter plan.

## Adding API Keys (Optional)

For live on-chain data (MVRV, NUPL, Puell Multiple, etc.), add these environment variables in Render:

- `GLASSNODE_API_KEY` — from [glassnode.com](https://glassnode.com)
- `CRYPTOQUANT_API_KEY` — from [cryptoquant.com](https://cryptoquant.com)

The app will gracefully fall back to curated estimates if keys are not provided.

## Local Development

```bash
pip install -r requirements.txt
python main.py
```

Open http://localhost:8000

## Data Sources

| Data | Source | Refresh |
|------|--------|---------|
| BTC Price | CoinGecko (free) | 60 seconds |
| Fear & Greed | Alternative.me (free) | 1 hour |
| Block Height | mempool.space (free) | 5 minutes |
| Hash Rate | mempool.space (free) | 1 hour |
| Treasury | Curated / BitcoinTreasuries.net | Daily |
| ETF Flows | Curated from public filings | Daily |
| On-Chain Metrics | Requires Glassnode/CryptoQuant | — |

---

Created with [Perplexity Computer](https://www.perplexity.ai/computer)
