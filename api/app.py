import asyncio
from concurrent.futures import ThreadPoolExecutor
import httpx
import yfinance as yf
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Startpage Market API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

STOCK_SYMBOLS = [
    "THYAO.IS", "GARAN.IS", "AKBNK.IS", "EREGL.IS",
    "ASELS.IS", "SISE.IS", "KCHOL.IS", "XU100.IS",
    "^GSPC", "^NDX", "^GDAXI", "CL=F",
]

YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finance.yahoo.com/",
}

_executor = ThreadPoolExecutor(max_workers=4)


# ── helpers ──────────────────────────────────────────────

def _ticker_history(sym: str):
    """Fetch price + change via yfinance .history(). Returns (price, change_pct) or (None, None)."""
    try:
        hist = yf.Ticker(sym).history(period="5d", interval="1d")
        if hist.empty:
            return None, None
        price = float(hist["Close"].iloc[-1])
        prev  = float(hist["Close"].iloc[-2]) if len(hist) > 1 else None
        chg   = (price - prev) / prev * 100 if prev else None
        return round(price, 2), (round(chg, 2) if chg is not None else None)
    except Exception:
        return None, None


async def _yahoo_quote_httpx(client: httpx.AsyncClient, sym: str):
    """Direct Yahoo Finance v8/chart call — server-side, no CORS."""
    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"
        r = await client.get(url, timeout=10)
        if r.status_code != 200:
            return None, None
        meta = r.json()["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        prev  = meta.get("chartPreviousClose")
        if price is None:
            return None, None
        chg = (price - prev) / prev * 100 if prev else None
        return round(price, 2), (round(chg, 2) if chg is not None else None)
    except Exception:
        return None, None


async def fetch_stocks():
    result = {}
    loop = asyncio.get_event_loop()

    # Try yfinance history() in thread pool first
    futures = {sym: loop.run_in_executor(_executor, _ticker_history, sym) for sym in STOCK_SYMBOLS}
    yf_results = {sym: await fut for sym, fut in futures.items()}

    missing = [sym for sym, (p, _) in yf_results.items() if p is None]

    # Fallback: direct httpx calls for any that failed
    if missing:
        async with httpx.AsyncClient(headers=YF_HEADERS, follow_redirects=True) as client:
            httpx_results = await asyncio.gather(
                *[_yahoo_quote_httpx(client, sym) for sym in missing]
            )
        for sym, (price, chg) in zip(missing, httpx_results):
            yf_results[sym] = (price, chg)

    for sym, (price, chg) in yf_results.items():
        if price is not None:
            result[sym] = {"price": price, "change_pct": chg}

    return result


async def fetch_fx():
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://api.frankfurter.app/latest",
                params={"from": "USD", "to": "TRY,EUR,GBP"},
            )
            r.raise_for_status()
            rates = r.json().get("rates", {})
            t, e, g = rates.get("TRY"), rates.get("EUR"), rates.get("GBP")
            return {
                "usd_try": round(t, 4)           if t else None,
                "eur_try": round(t / e, 4)        if t and e else None,
                "gbp_try": round(t / g, 4)        if t and g else None,
                "eur_usd": round(1 / e, 4)        if e else None,
            }
    except Exception:
        return {}


async def fetch_crypto():
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids": "bitcoin,ethereum,solana",
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                },
            )
            r.raise_for_status()
            data = r.json()
        return {
            coin: {
                "price":     data[coin].get("usd"),
                "change24h": data[coin].get("usd_24h_change"),
            }
            for coin in ("bitcoin", "ethereum", "solana")
            if coin in data
        }
    except Exception:
        return {}


async def fetch_metals():
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get("https://api.metals.live/v1/spot")
            r.raise_for_status()
            arr = r.json()
        gold = silver = platinum = None
        for item in arr:
            if item.get("gold")     is not None: gold     = item["gold"]
            if item.get("silver")   is not None: silver   = item["silver"]
            if item.get("platinum") is not None: platinum = item["platinum"]
        return {"gold": gold, "silver": silver, "platinum": platinum}
    except Exception:
        return {}


# ── endpoints ────────────────────────────────────────────

@app.get("/market")
async def market():
    fx_task     = asyncio.create_task(fetch_fx())
    crypto_task = asyncio.create_task(fetch_crypto())
    metals_task = asyncio.create_task(fetch_metals())
    stocks      = await fetch_stocks()

    return {
        "fx":     await fx_task,
        "crypto": await crypto_task,
        "metals": await metals_task,
        "stocks": stocks,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
