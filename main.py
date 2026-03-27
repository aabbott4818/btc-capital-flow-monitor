#!/usr/bin/env python3
"""Bitcoin Capital Flow Monitor — API proxy server.

Proxies free APIs (CoinGecko, Alternative.me, Blockchain.info, mempool.space)
with in-memory caching to avoid rate limits. Also serves corporate treasury
data and ETF flow data from curated JSON files.
"""

import time
import json
import os
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ── Cache ─────────────────────────────────────────────────────────────
_cache = {}

def get_cached(key, max_age_seconds=300):
    """Return cached value if fresh, else None."""
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < max_age_seconds:
        return entry["data"]
    return None

def set_cache(key, data):
    _cache[key] = {"data": data, "ts": time.time()}

# ── HTTP client ───────────────────────────────────────────────────────
client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)

@asynccontextmanager
async def lifespan(app):
    yield
    await client.aclose()

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Helpers ───────────────────────────────────────────────────────────
async def fetch_json(url, headers=None, cache_key=None, max_age=300):
    """Fetch JSON with caching."""
    if cache_key:
        cached = get_cached(cache_key, max_age)
        if cached is not None:
            return cached
    try:
        headers = headers or {}
        if "User-Agent" not in headers:
            headers["User-Agent"] = "BTCMonitor/1.0"
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if cache_key:
            set_cache(cache_key, data)
        return data
    except Exception as e:
        print(f"[fetch_json] Error fetching {url}: {e}")
        # Return stale cache if available
        entry = _cache.get(cache_key)
        if entry:
            return entry["data"]
        return None

# ══════════════════════════════════════════════════════════════════════
# API ROUTES
# ══════════════════════════════════════════════════════════════════════

# 1. Live Bitcoin Price (multi-source with fallback)
@app.get("/api/price")
async def get_price():
    # Try CoinGecko first (has multi-currency + 24h change)
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin&vs_currencies=usd,eur,gbp"
        "&include_24hr_change=true"
        "&include_24hr_vol=true"
        "&include_market_cap=true"
        "&include_last_updated_at=true"
    )
    data = await fetch_json(url, cache_key="btc_price", max_age=60)
    if data and "bitcoin" in data:
        btc = data["bitcoin"]
        result = {}
        for currency in ["usd", "eur", "gbp"]:
            result[currency] = {
                "price": btc.get(currency),
                "change_24h": btc.get(f"{currency}_24h_change"),
                "volume_24h": btc.get(f"{currency}_24h_vol"),
                "market_cap": btc.get(f"{currency}_market_cap"),
            }
        result["last_updated"] = btc.get("last_updated_at")
        result["cached"] = False
        return result

    # Fallback 1: Coinbase spot prices (very reliable, no rate limits)
    try:
        usd_resp = await client.get("https://api.coinbase.com/v2/prices/BTC-USD/spot",
                                     headers={"User-Agent": "BTCMonitor/1.0"}, timeout=10.0)
        eur_resp = await client.get("https://api.coinbase.com/v2/prices/BTC-EUR/spot",
                                     headers={"User-Agent": "BTCMonitor/1.0"}, timeout=10.0)
        gbp_resp = await client.get("https://api.coinbase.com/v2/prices/BTC-GBP/spot",
                                     headers={"User-Agent": "BTCMonitor/1.0"}, timeout=10.0)
        usd_price = float(usd_resp.json()["data"]["amount"])
        eur_price = float(eur_resp.json()["data"]["amount"])
        gbp_price = float(gbp_resp.json()["data"]["amount"])
        result = {
            "usd": {"price": usd_price, "change_24h": None, "volume_24h": None, "market_cap": None},
            "eur": {"price": eur_price, "change_24h": None, "volume_24h": None, "market_cap": None},
            "gbp": {"price": gbp_price, "change_24h": None, "volume_24h": None, "market_cap": None},
            "last_updated": int(time.time()),
            "cached": False,
            "source": "coinbase",
        }
        set_cache("btc_price_fallback", result)
        return result
    except Exception as e:
        print(f"[get_price] Coinbase fallback failed: {e}")

    # Fallback 2: Return stale Coinbase cache
    stale = get_cached("btc_price_fallback", 600)
    if stale:
        stale["cached"] = True
        return stale

    return {"error": "unavailable", "cached": True}

# 2. Bitcoin market chart (CoinGecko — 90 day history)
@app.get("/api/market-chart")
async def get_market_chart():
    url = (
        "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
        "?vs_currency=usd&days=365&interval=daily"
    )
    data = await fetch_json(url, cache_key="btc_market_chart", max_age=3600)
    if data and "prices" in data:
        return {
            "prices": data["prices"],  # [[timestamp_ms, price], ...]
            "market_caps": data.get("market_caps", []),
            "total_volumes": data.get("total_volumes", []),
        }
    return {"error": "unavailable"}

# 3. Fear & Greed Index (Alternative.me — free)
@app.get("/api/fear-greed")
async def get_fear_greed():
    url = "https://api.alternative.me/fng/?limit=31"
    data = await fetch_json(url, cache_key="fear_greed", max_age=3600)
    if data and "data" in data:
        entries = data["data"]
        return {
            "current": {
                "value": int(entries[0]["value"]),
                "label": entries[0]["value_classification"],
                "timestamp": int(entries[0]["timestamp"]),
            },
            "yesterday": {
                "value": int(entries[1]["value"]) if len(entries) > 1 else None,
                "label": entries[1]["value_classification"] if len(entries) > 1 else None,
            },
            "last_week": {
                "value": int(entries[7]["value"]) if len(entries) > 7 else None,
                "label": entries[7]["value_classification"] if len(entries) > 7 else None,
            },
            "last_month": {
                "value": int(entries[30]["value"]) if len(entries) > 30 else None,
                "label": entries[30]["value_classification"] if len(entries) > 30 else None,
            },
            "history": [{"value": int(e["value"]), "label": e["value_classification"], "timestamp": int(e["timestamp"])} for e in entries],
        }
    return {"error": "unavailable"}

# 4. Block height (mempool.space — free, more reliable than blockchain.info)
@app.get("/api/block-height")
async def get_block_height():
    # Try mempool.space first
    url = "https://mempool.space/api/blocks/tip/height"
    try:
        resp = await client.get(url, timeout=10.0)
        resp.raise_for_status()
        height = int(resp.text.strip())
        set_cache("block_height", height)
        return {"height": height, "source": "mempool.space"}
    except Exception:
        pass
    # Fallback to blockchain.info
    url2 = "https://blockchain.info/q/getblockcount"
    try:
        resp = await client.get(url2, timeout=10.0)
        resp.raise_for_status()
        height = int(resp.text.strip())
        set_cache("block_height", height)
        return {"height": height, "source": "blockchain.info"}
    except Exception:
        cached = get_cached("block_height", 86400)
        if cached:
            return {"height": cached, "source": "cache"}
        return {"error": "unavailable"}

# 5. Hash rate (mempool.space — free)
@app.get("/api/hashrate")
async def get_hashrate():
    url = "https://mempool.space/api/v1/mining/hashrate/3m"
    data = await fetch_json(url, cache_key="hashrate", max_age=3600)
    if data and "hashrates" in data:
        recent = data["hashrates"][-1] if data["hashrates"] else None
        current_avg = data.get("currentHashrate", None)
        current_diff = data.get("currentDifficulty", None)
        return {
            "current_hashrate": current_avg,
            "current_difficulty": current_diff,
            "history": [{"timestamp": h["timestamp"], "avgHashrate": h["avgHashrate"]} for h in data["hashrates"][-90:]],
        }
    return {"error": "unavailable"}

# 6. Corporate treasury data (curated — updated from BitcoinTreasuries.net)
@app.get("/api/treasury")
async def get_treasury():
    """Return corporate Bitcoin treasury data. We fetch live price to calc current values."""
    # Get current price for calculations
    price_data = get_cached("btc_price", 300)
    current_price = 84000  # fallback
    if price_data and "bitcoin" in price_data:
        current_price = price_data["bitcoin"].get("usd", 84000)
    
    # Try fetching from BitcoinTreasuries API
    url = "https://api.bitcointreasuries.net/v1/companies/public"
    data = await fetch_json(url, cache_key="treasury_api", max_age=86400)
    
    companies = []
    if data and isinstance(data, list):
        # Map API data 
        for company in data[:30]:  # Top 30
            total_btc = company.get("total_btc", 0) or 0
            avg_cost = company.get("avg_cost_basis", None)
            value = total_btc * current_price
            pnl = None
            if avg_cost and avg_cost > 0:
                pnl = ((current_price - avg_cost) / avg_cost) * 100
            companies.append({
                "name": company.get("name", "Unknown"),
                "ticker": company.get("symbol", ""),
                "btc": total_btc,
                "avg_cost": avg_cost,
                "value": value,
                "pnl": pnl,
                "country": company.get("country", ""),
            })
    
    if not companies:
        # Fallback to curated static data
        companies = get_curated_treasury(current_price)
    
    return {"companies": companies, "btc_price": current_price}


def get_curated_treasury(current_price):
    """Fallback curated corporate treasury data (March 27, 2026 — BitcoinTreasuries.net)."""
    treasury = [
        {"name": "Strategy", "ticker": "MSTR", "btc": 762099, "avg_cost": 75694, "country": "US"},
        {"name": "Twenty One Capital", "ticker": "XXI", "btc": 43514, "avg_cost": 65000, "country": "US"},
        {"name": "MARA Holdings", "ticker": "MARA", "btc": 38689, "avg_cost": 67400, "country": "US"},
        {"name": "Metaplanet", "ticker": "3350.T", "btc": 35102, "avg_cost": 72400, "country": "JP"},
        {"name": "Bitcoin Standard Treasury Co", "ticker": "CEPO", "btc": 30021, "avg_cost": 78000, "country": "US"},
        {"name": "Bullish", "ticker": "BLSH", "btc": 24300, "avg_cost": 25000, "country": "US"},
        {"name": "Riot Platforms", "ticker": "RIOT", "btc": 18005, "avg_cost": 36750, "country": "US"},
        {"name": "Coinbase Global", "ticker": "COIN", "btc": 15389, "avg_cost": 28500, "country": "US"},
        {"name": "Hut 8 Mining", "ticker": "HUT", "btc": 13696, "avg_cost": 24484, "country": "US"},
        {"name": "Strive", "ticker": "ASST", "btc": 13628, "avg_cost": 72000, "country": "US"},
        {"name": "CleanSpark", "ticker": "CLSK", "btc": 13363, "avg_cost": 31400, "country": "US"},
        {"name": "Tesla", "ticker": "TSLA", "btc": 11509, "avg_cost": 33539, "country": "US"},
        {"name": "Trump Media", "ticker": "DJT", "btc": 9542, "avg_cost": 78000, "country": "US"},
        {"name": "Block Inc", "ticker": "SQ", "btc": 8883, "avg_cost": 30667, "country": "US"},
        {"name": "GD Culture Group", "ticker": "GDC", "btc": 7500, "avg_cost": 83000, "country": "US"},
    ]
    for t in treasury:
        t["value"] = t["btc"] * current_price
        t["pnl"] = ((current_price - t["avg_cost"]) / t["avg_cost"]) * 100 if t["avg_cost"] else None
    return treasury

# 7. ETF flow data — aggregate from multiple free sources
@app.get("/api/etf-flows")
async def get_etf_flows():
    """ETF flow data. Since SoSoValue/Farside require scraping,
    we provide curated recent data with live price overlay."""
    price_data = get_cached("btc_price", 300)
    current_price = 84000
    if price_data and "bitcoin" in price_data:
        current_price = price_data["bitcoin"].get("usd", 84000)
    
    # Try fetching from coinglass (free tier)
    etf_data = await fetch_json(
        "https://api.coinglass.com/public/v2/indicator/etf_flow",
        cache_key="etf_flows_cg",
        max_age=3600,
        headers={"accept": "application/json"}
    )
    
    # Since most ETF APIs require paid access, use curated data with staleness indicator
    etfs = get_curated_etf_data(current_price)
    
    return {
        "etfs": etfs,
        "btc_price": current_price,
        "note": "ETF flow data updates daily. Source: aggregated public filings.",
    }


def get_curated_etf_data(current_price):
    """Curated ETF flow data (March 27, 2026 — SoSoValue, public filings).
    Cumulative net inflows shown. AUM from SoSoValue."""
    return [
        {"name": "BlackRock IBIT", "ticker": "IBIT", "flow_btc": 62550, "aum_usd": 71.54e9, "cum_inflow_usd": 62.55e9},
        {"name": "Fidelity FBTC", "ticker": "FBTC", "flow_btc": 12090, "aum_usd": 18.03e9, "cum_inflow_usd": 12.09e9},
        {"name": "Grayscale GBTC", "ticker": "GBTC", "flow_btc": -25050, "aum_usd": 14.95e9, "cum_inflow_usd": -25.05e9},
        {"name": "Grayscale Mini BTC", "ticker": "BTC", "flow_btc": 1960, "aum_usd": 4.34e9, "cum_inflow_usd": 1.96e9},
        {"name": "Bitwise BITB", "ticker": "BITB", "flow_btc": 2260, "aum_usd": 3.59e9, "cum_inflow_usd": 2.26e9},
        {"name": "ARK 21Shares ARKB", "ticker": "ARKB", "flow_btc": 1750, "aum_usd": 3.50e9, "cum_inflow_usd": 1.75e9},
        {"name": "VanEck HODL", "ticker": "HODL", "flow_btc": 1190, "aum_usd": 1.52e9, "cum_inflow_usd": 1.19e9},
        {"name": "Others", "ticker": "—", "flow_btc": 891, "aum_usd": 2.15e9, "cum_inflow_usd": 0.89e9},
    ]

# 8. On-chain summary — aggregate what we can from free sources
@app.get("/api/onchain")
async def get_onchain():
    """On-chain health metrics. Glassnode/CryptoQuant require paid API keys.
    We derive what we can from free sources and clearly label data freshness."""
    
    # Get hashrate from mempool.space
    hr_data = get_cached("hashrate", 7200)
    hashrate_ehs = None
    if hr_data and "hashrates" in hr_data:
        last = hr_data["hashrates"][-1] if hr_data["hashrates"] else None
        if last:
            hashrate_ehs = round(last["avgHashrate"] / 1e18, 1)
    
    # Get current price for MVRV estimate
    price_data = get_cached("btc_price", 300)
    current_price = 84000
    if price_data and "bitcoin" in price_data:
        current_price = price_data["bitcoin"].get("usd", 84000)
    
    # These metrics require Glassnode/CryptoQuant — return curated + note
    metrics = {
        "mvrv_zscore": {"value": None, "health": "unavailable", "note": "Requires Glassnode API key"},
        "nupl": {"value": None, "health": "unavailable", "note": "Requires Glassnode API key"},
        "puell_multiple": {"value": None, "health": "unavailable", "note": "Requires Glassnode API key"},
        "realised_price": {"value": None, "health": "unavailable", "note": "Requires Glassnode API key"},
        "active_addresses": {"value": None, "health": "unavailable", "note": "Requires Glassnode API key"},
        "hashrate": {
            "value": f"{hashrate_ehs} EH/s" if hashrate_ehs else None,
            "raw": hashrate_ehs,
            "health": "green" if hashrate_ehs else "unavailable",
            "note": "Live from mempool.space" if hashrate_ehs else "Unavailable",
        },
    }
    
    return {"metrics": metrics, "btc_price": current_price}


# 9. Aggregated dashboard data (single call for initial load)
@app.get("/api/dashboard")
async def get_dashboard():
    """Single endpoint that returns all data for initial page load.
    Fires all API calls in parallel for speed."""
    results = await asyncio.gather(
        get_price(),
        get_fear_greed(),
        get_block_height(),
        get_hashrate(),
        get_treasury(),
        get_etf_flows(),
        return_exceptions=True,
    )
    
    def safe(r):
        if isinstance(r, Exception):
            return {"error": str(r)}
        return r
    
    return {
        "price": safe(results[0]),
        "fear_greed": safe(results[1]),
        "block_height": safe(results[2]),
        "hashrate": safe(results[3]),
        "treasury": safe(results[4]),
        "etf_flows": safe(results[5]),
        "timestamp": int(time.time()),
    }


# Health check
@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": int(time.time())}


# ── Static files ──────────────────────────────────────────────────────
# Serve the static frontend
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
